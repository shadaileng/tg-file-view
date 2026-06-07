"""File management API routes: list, detail, download, view, cache."""

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import select, func, desc as sa_desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.utils import utc_iso
from database import get_db, AsyncSessionLocal
from models import Channel as ChannelModel, File as FileModel, CacheRecord as CacheRecordModel
from services.telegram_client import get_telegram_service, AuthState

router = APIRouter(tags=["files"])

# Cache directory (consistent with main.py mount point)
DATA_DIR = os.environ.get("TG_DATA_DIR", "./data")
CACHE_DIR = Path(DATA_DIR) / "cache"

# Safe filename: replace only Windows-illegal filename characters with '_'
# Preserves Chinese, spaces, parentheses, and other Unicode characters.
_INVALID_FS_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_filename(name: str) -> str:
    """Sanitize filename, replacing only filesystem-illegal characters."""
    return _INVALID_FS_CHARS.sub("_", name)


def _make_content_disposition(filename: str, disposition: str = "inline") -> str:
    """Build a safe Content-Disposition header value.

    Uses RFC 5987 encoding (filename*=UTF-8'') for non-ASCII filenames
    to avoid UnicodeEncodeError when Starlette encodes headers as latin-1.
    """
    try:
        filename.encode("latin-1")
        return f'{disposition}; filename="{filename}"'
    except UnicodeEncodeError:
        encoded = quote(filename, safe="")
        return f"{disposition}; filename*=UTF-8''{encoded}"


def _file_to_dict(file_: FileModel) -> dict:
    """Serialize a File ORM object to a JSON-safe dict.

    Cache status is determined by CacheRecord (the authoritative source).
    """
    cr = file_.cache_record
    is_caching = cr is not None and cr.status == "caching"
    is_cached = cr is not None and cr.status == "cached"
    is_failed = cr is not None and cr.status == "failed"
    return {
        "id": file_.id,
        "channel_id": file_.channel_id,
        "message_id": file_.message_id,
        "file_name": file_.file_name,
        "file_size": file_.file_size,
        "mime_type": file_.mime_type,
        "file_type": file_.file_type,
        "thumb_path": file_.thumb_path,
        "thumb_type": file_.thumb_type,
        "cache_path": cr.file_path if cr else file_.cache_path,
        "is_cached": is_cached,
        "is_caching": is_caching,
        "cache_error": cr.error_msg if is_failed else None,
        "cached_at": utc_iso(cr.cached_at if cr else file_.cached_at),
        "accessed_at": utc_iso(cr.accessed_at if cr else file_.accessed_at),
        "tg_ref": file_.tg_ref,
        "created_at": utc_iso(file_.created_at),
    }


def _require_authorized():
    """Get authorized Telegram service or raise HTTPException."""
    svc = get_telegram_service()
    if svc is None:
        raise HTTPException(
            status_code=400,
            detail="Telegram service not configured. Please set TG_API_ID and TG_API_HASH.",
        )
    if svc.auth_state != AuthState.AUTHORIZED:
        raise HTTPException(
            status_code=400,
            detail="Telegram client not authorized. Please complete login via /api/auth first.",
        )
    return svc


async def _download_from_telegram(
    svc, tg_id: int, message_id: int, target_path: Path
) -> int:
    """Download a single file from Telegram to local disk.

    Returns the file size in bytes.
    Raises HTTPException on failure.
    """
    client = await svc.get_client()
    try:
        entity = await client.get_entity(tg_id)
    except ValueError as e:
        logger.warning("Channel entity not found for tg_id={}: {}", tg_id, e)
        raise HTTPException(
            status_code=404,
            detail=f"Channel (tg_id={tg_id}) not found on Telegram",
        )

    try:
        message = await client.get_messages(entity, ids=message_id)
    except Exception as e:
        logger.error(
            "Failed to get message tg_id={} msg_id={}: {}", tg_id, message_id, e
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch message from Telegram: {e}",
        )

    if message is None or message.media is None:
        raise HTTPException(
            status_code=404,
            detail=f"Message id={message_id} not found or has no media",
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path = await client.download_media(message, file=str(target_path))
    except Exception as e:
        logger.error("Failed to download media: {}", e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to download file from Telegram: {e}",
        )

    if path is None:
        raise HTTPException(status_code=500, detail="Download returned None")

    return os.path.getsize(str(path))


def _file_stream(file_path: Path, chunk_size: int = 64 * 1024):
    """Synchronous generator yielding file chunks for StreamingResponse."""
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


async def _ensure_cached(file_: FileModel, db: AsyncSession) -> Path:
    """Ensure the file is cached locally. Downloads from Telegram if needed.

    Integrates with CacheManager for LRU eviction and dynamic size limits:
    1. If already cached: refresh accessed_at and return path.
    2. If not cached: pre-check space -> evict if needed -> download ->
       update DB with timestamps -> post-check overflow.

    Returns the full path to the cached file.
    Updates file_ DB fields and creates/updates CacheRecord.
    """
    from services.cache_manager import CacheManager

    # Check if already cached on disk — refresh LRU timestamp
    if file_.is_cached and file_.cache_path:
        full_path = CACHE_DIR / file_.cache_path
        if full_path.exists():
            await CacheManager.mark_accessed(db, file_)
            return full_path

    # Need to download
    svc = _require_authorized()
    channel = await db.get(ChannelModel, file_.channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found in database")

    # Pre-check: ensure cache has room (may evict LRU files)
    # If file_size is unknown (0), skip pre-check; will catch overflow
    # at post-check.
    if file_.file_size > 0:
        await CacheManager.check_and_evict(
            db, new_file_size=file_.file_size, new_file_id=file_.id
        )

    safe_name = _safe_filename(file_.file_name)
    relative_path = f"{file_.channel_id}/{file_.id}_{safe_name}"
    full_path = CACHE_DIR / relative_path

    size = await _download_from_telegram(
        svc, channel.tg_id, file_.message_id, full_path
    )

    # Update DB record with cache info and timestamps
    now = datetime.now(timezone.utc)
    file_.cache_path = relative_path
    file_.is_cached = True
    file_.file_size = size
    file_.cached_at = now
    file_.accessed_at = now
    await db.commit()

    # Create/update CacheRecord
    await CacheManager.create_record(db, file_)

    # Post-check: ensure cache is under limit (downloaded file may be
    # larger than estimated file_size, or concurrent downloads may have
    # pushed total over limit).
    await CacheManager.post_download_check(db)

    return full_path


# ---------------------------------------------------------------------------
# Route 1: GET /api/channels/{channel_id}/files
# ---------------------------------------------------------------------------
@router.get("/api/channels/{channel_id}/files")
async def list_files(
    channel_id: int,
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    db: AsyncSession = Depends(get_db),
):
    """List files in a channel with offset/limit pagination."""
    channel = await db.get(ChannelModel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=404, detail=f"Channel with id={channel_id} not found"
        )

    # Total count
    count_q = select(func.count(FileModel.id)).where(FileModel.channel_id == channel_id)
    total = (await db.execute(count_q)).scalar()

    # Paginated query — newest first by message_id
    q = (
        select(FileModel)
        .options(joinedload(FileModel.cache_record))
        .where(FileModel.channel_id == channel_id)
        .order_by(sa_desc(FileModel.message_id))
        .offset(offset)
        .limit(limit)
    )
    files = (await db.execute(q)).scalars().unique().all()

    return {
        "files": [_file_to_dict(f) for f in files],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# Route 2: GET /api/files/{file_id}
# ---------------------------------------------------------------------------
@router.get("/api/files/{file_id}")
async def get_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single file's detail by its database ID."""
    q = (
        select(FileModel)
        .options(joinedload(FileModel.cache_record))
        .where(FileModel.id == file_id)
    )
    result = await db.execute(q)
    file_ = result.scalars().unique().one_or_none()
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")
    return _file_to_dict(file_)


# ---------------------------------------------------------------------------
# Route 3: GET /api/files/{file_id}/download
# ---------------------------------------------------------------------------
@router.get("/api/files/{file_id}/download")
async def download_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Stream-download a file (cache-first, fallback to Telegram)."""
    q = (
        select(FileModel)
        .options(joinedload(FileModel.cache_record))
        .where(FileModel.id == file_id)
    )
    result = await db.execute(q)
    file_ = result.scalars().unique().one_or_none()
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")

    # Make sure it's cached (will download from Telegram if needed)
    full_path = await _ensure_cached(file_, db)

    return StreamingResponse(
        _file_stream(full_path),
        media_type=file_.mime_type,
        headers={
            "Content-Disposition": _make_content_disposition(
                file_.file_name, "attachment"
            ),
            "Content-Length": str(full_path.stat().st_size),
        },
    )


# ---------------------------------------------------------------------------
# Route 4: POST /api/files/{file_id}/cache
# ---------------------------------------------------------------------------
@router.post("/api/files/{file_id}/cache")
async def cache_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Manually cache a file by downloading it from Telegram (async background).

    - Creates a CacheRecord with status='caching'
    - Launches background task to download
    - Returns immediately with { status: 'caching' }
    - When user re-visits, list_files reads the current CacheRecord status
    """
    q = (
        select(FileModel)
        .options(joinedload(FileModel.cache_record))
        .where(FileModel.id == file_id)
    )
    result = await db.execute(q)
    file_ = result.scalars().unique().one_or_none()
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")

    cr = file_.cache_record

    # Already fully cached — return current state (idempotent)
    if cr and cr.status == "cached" and file_.cache_path:
        full_path = CACHE_DIR / file_.cache_path
        if full_path.exists():
            return _file_to_dict(file_)

    # If a caching task is already running, don't start another
    if cr and cr.status == "caching":
        return _file_to_dict(file_)

    # Create/update CacheRecord with status='caching'
    _svc = _require_authorized()
    now = datetime.now(timezone.utc)
    if cr:
        cr.status = "caching"
        cr.error_msg = None
    else:
        cr = CacheRecordModel(
            file_id=file_.id,
            file_path="",
            file_size=0,
            status="caching",
            cached_at=now,
            accessed_at=now,
        )
        db.add(cr)
    await db.commit()

    # Launch background download task
    asyncio.create_task(_background_cache(file_id))

    # Manually construct response from current state (identity map may be stale)
    return {
        "id": file_.id,
        "channel_id": file_.channel_id,
        "message_id": file_.message_id,
        "file_name": file_.file_name,
        "file_size": file_.file_size,
        "mime_type": file_.mime_type,
        "file_type": file_.file_type,
        "thumb_path": file_.thumb_path,
        "thumb_type": file_.thumb_type,
        "cache_path": None,
        "is_cached": False,
        "is_caching": True,
        "cache_error": None,
        "cached_at": None,
        "accessed_at": None,
        "tg_ref": file_.tg_ref,
        "created_at": utc_iso(file_.created_at),
    }


async def _background_cache(file_id: int) -> None:
    """Background task: download and cache a file, then update CacheRecord.

    Runs in a separate session to avoid cross-request DB conflicts.
    """
    from services.cache_manager import CacheManager

    try:
        async with AsyncSessionLocal() as session:
            # Reload file with cache_record in a fresh session
            q = (
                select(FileModel)
                .options(joinedload(FileModel.cache_record))
                .where(FileModel.id == file_id)
            )
            result = await session.execute(q)
            file_ = result.scalars().unique().one_or_none()
            if file_ is None:
                logger.error("Background cache: file {} not found", file_id)
                return

            channel = await session.get(ChannelModel, file_.channel_id)
            if channel is None:
                logger.error("Background cache: channel not found for file {}", file_id)
                return

            # Pre-check cache space
            if file_.file_size > 0:
                try:
                    await CacheManager.check_and_evict(
                        session, new_file_size=file_.file_size, new_file_id=file_.id
                    )
                except HTTPException as e:
                    logger.warning("Background cache pre-check failed: {}", e.detail)
                    cr = file_.cache_record
                    if cr:
                        cr.status = "failed"
                        cr.error_msg = e.detail
                        await session.commit()
                    return

            safe_name = _safe_filename(file_.file_name)
            relative_path = f"{file_.channel_id}/{file_.id}_{safe_name}"
            full_path = CACHE_DIR / relative_path

            # Download from Telegram
            svc = _require_authorized()
            try:
                client = await svc.get_client()
                entity = await client.get_entity(channel.tg_id)
                message = await client.get_messages(entity, ids=file_.message_id)
                if message is None or message.media is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Message id={file_.message_id} not found or has no media",
                    )
                full_path.parent.mkdir(parents=True, exist_ok=True)
                path = await client.download_media(message, file=str(full_path))
                if path is None:
                    raise HTTPException(status_code=500, detail="Download returned None")
                size = os.path.getsize(str(path))
            except HTTPException:
                raise
            except Exception as e:
                logger.error("Background cache download failed: {}", e)
                raise HTTPException(status_code=502, detail=str(e))

            # Update File + CacheRecord
            now = datetime.now(timezone.utc)
            file_.cache_path = relative_path
            file_.is_cached = True
            file_.file_size = size
            file_.cached_at = now
            file_.accessed_at = now

            cr = file_.cache_record
            if cr:
                cr.file_path = relative_path
                cr.file_size = size
                cr.status = "cached"
                cr.error_msg = None
                cr.cached_at = now
                cr.accessed_at = now

            await session.commit()

            # Post-download overflow check
            try:
                await CacheManager.post_download_check(session)
            except HTTPException as e:
                logger.warning("Background cache post-check: {}", e.detail)

            logger.info("Background cache completed: id={} path={} size={}", file_id, relative_path, size)

    except HTTPException as e:
        # Update CacheRecord to failed
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(FileModel)
                    .options(joinedload(FileModel.cache_record))
                    .where(FileModel.id == file_id)
                )
                file_ = result.scalars().unique().one_or_none()
                if file_ and file_.cache_record:
                    file_.cache_record.status = "failed"
                    file_.cache_record.error_msg = e.detail
                    await session.commit()
        except Exception:
            logger.exception("Failed to mark cache_record as failed")
        logger.error("Background cache failed: id={} error={}", file_id, e.detail)

    except Exception as e:
        logger.exception("Background cache unexpected error: id={}", file_id)


# ---------------------------------------------------------------------------
# Route 5: GET /api/files/{file_id}/view
# ---------------------------------------------------------------------------
async def _stream_from_telegram(
    svc, media, chunk_size: int = 64 * 1024
) -> AsyncGenerator[bytes, None]:
    """Stream file chunks directly from Telegram via iter_download — no disk writes.

    Pre-condition: the caller must have already fetched and validated
    the media object from Telegram.  This function only does the
    chunk-by-chunk streaming.
    """
    client = await svc.get_client()
    try:
        async for chunk in client.iter_download(media, request_size=chunk_size):
            yield chunk
    except Exception as e:
        logger.error("Failed to stream from Telegram: {}", e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to stream file from Telegram: {e}",
        )


@router.get("/api/files/{file_id}/view")
async def view_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Stream a file for inline preview in browser.

    - If cached on disk: stream from local cache (Content-Disposition: inline).
    - If not cached: stream directly from Telegram via iter_download — no
      disk write, no caching.  Content-Disposition: inline.

    Unlike /download (which forces attachment), this endpoint allows the
    browser to render the file inline (image, video, audio, PDF, etc.).
    """
    q = (
        select(FileModel)
        .options(joinedload(FileModel.cache_record))
        .where(FileModel.id == file_id)
    )
    result = await db.execute(q)
    file_ = result.scalars().unique().one_or_none()
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")

    # Check if cached on disk — stream from local cache
    if file_.is_cached and file_.cache_path:
        full_path = CACHE_DIR / file_.cache_path
        if full_path.exists():
            return StreamingResponse(
                _file_stream(full_path),
                media_type=file_.mime_type,
                headers={
                    "Content-Disposition": _make_content_disposition(
                        file_.file_name, "inline"
                    ),
                    "Content-Length": str(full_path.stat().st_size),
                },
            )

    # Not cached — validate Telegram connectivity BEFORE returning StreamingResponse
    svc = _require_authorized()
    channel = await db.get(ChannelModel, file_.channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found in database")

    # Pre-fetch and validate media from Telegram (raises HTTPException on failure)
    client = await svc.get_client()
    try:
        entity = await client.get_entity(channel.tg_id)
    except ValueError as e:
        logger.warning("Channel entity not found for tg_id={}: {}", channel.tg_id, e)
        raise HTTPException(
            status_code=404,
            detail=f"Channel (tg_id={channel.tg_id}) not found on Telegram",
        )

    try:
        message = await client.get_messages(entity, ids=file_.message_id)
    except Exception as e:
        logger.error(
            "Failed to get message tg_id={} msg_id={}: {}",
            channel.tg_id,
            file_.message_id,
            e,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch message from Telegram: {e}",
        )

    if message is None or message.media is None:
        raise HTTPException(
            status_code=404,
            detail=f"Message id={file_.message_id} not found or has no media",
        )

    # All pre-checks passed — stream the media chunks
    headers = {
        "Content-Disposition": _make_content_disposition(file_.file_name, "inline"),
    }

    return StreamingResponse(
        _stream_from_telegram(svc, message.media),
        media_type=file_.mime_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Route 6: DELETE /api/files/{file_id}/cache
# ---------------------------------------------------------------------------
@router.delete("/api/files/{file_id}/cache")
async def delete_cache(file_id: int, db: AsyncSession = Depends(get_db)):
    """Clear the local cache for a file.

    Idempotent: succeeds even if the file is not currently cached.
    Removes CacheRecord + disk file + resets File fields.
    """
    q = (
        select(FileModel)
        .options(joinedload(FileModel.cache_record))
        .where(FileModel.id == file_id)
    )
    result = await db.execute(q)
    file_ = result.scalars().unique().one_or_none()
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")

    # Remove disk file (if any)
    if file_.cache_path:
        full_path = CACHE_DIR / file_.cache_path
        if full_path.exists():
            try:
                os.remove(str(full_path))
                logger.info("Deleted cached file: {}", full_path)
            except OSError as e:
                logger.warning("Failed to delete cache file {}: {}", full_path, e)

    # Delete CacheRecord (if any)
    if file_.cache_record:
        await db.delete(file_.cache_record)

    # Reset DB fields
    file_.cache_path = None
    file_.is_cached = False
    file_.cached_at = None
    await db.commit()

    return {"status": "ok", "detail": f"Cache for file id={file_id} cleared"}
