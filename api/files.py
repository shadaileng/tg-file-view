"""File management API routes: list, detail, download, cache."""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import select, func, desc as sa_desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Channel as ChannelModel, File as FileModel
from services.telegram_client import get_telegram_service, AuthState

router = APIRouter(tags=["files"])

# Cache directory (consistent with main.py mount point)
DATA_DIR = os.environ.get("TG_DATA_DIR", "./data")
CACHE_DIR = Path(DATA_DIR) / "cache"

# Safe filename: replace anything not alphanumeric, dot, dash, underscore with '_'
_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9._-]")


def _safe_filename(name: str) -> str:
    """Sanitize filename, replacing unsafe characters."""
    return _SAFE_NAME_PATTERN.sub("_", name)


def _file_to_dict(file_: FileModel) -> dict:
    """Serialize a File ORM object to a JSON-safe dict."""
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
        "cache_path": file_.cache_path,
        "is_cached": file_.is_cached,
        "cached_at": file_.cached_at.isoformat() if file_.cached_at else None,
        "accessed_at": file_.accessed_at.isoformat() if file_.accessed_at else None,
        "tg_ref": file_.tg_ref,
        "created_at": file_.created_at.isoformat() if file_.created_at else None,
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
        logger.error("Failed to get message tg_id={} msg_id={}: {}", tg_id, message_id, e)
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


async def _ensure_cached(
    file_: FileModel, db: AsyncSession
) -> Path:
    """Ensure the file is cached locally. Downloads from Telegram if needed.

    Integrates with CacheManager for LRU eviction and dynamic size limits:
    1. If already cached: refresh accessed_at and return path.
    2. If not cached: pre-check space -> evict if needed -> download ->
       update DB with timestamps -> post-check overflow.

    Returns the full path to the cached file.
    Updates file_ DB fields.
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
    now = datetime.utcnow()
    file_.cache_path = relative_path
    file_.is_cached = True
    file_.file_size = size
    file_.cached_at = now
    file_.accessed_at = now
    await db.commit()

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
    count_q = select(func.count(FileModel.id)).where(
        FileModel.channel_id == channel_id
    )
    total = (await db.execute(count_q)).scalar()

    # Paginated query — newest first by message_id
    q = (
        select(FileModel)
        .where(FileModel.channel_id == channel_id)
        .order_by(sa_desc(FileModel.message_id))
        .offset(offset)
        .limit(limit)
    )
    files = (await db.execute(q)).scalars().all()

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
    file_ = await db.get(FileModel, file_id)
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")
    return _file_to_dict(file_)


# ---------------------------------------------------------------------------
# Route 3: GET /api/files/{file_id}/download
# ---------------------------------------------------------------------------
@router.get("/api/files/{file_id}/download")
async def download_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Stream-download a file (cache-first, fallback to Telegram)."""
    file_ = await db.get(FileModel, file_id)
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")

    # Make sure it's cached (will download from Telegram if needed)
    full_path = await _ensure_cached(file_, db)

    return StreamingResponse(
        _file_stream(full_path),
        media_type=file_.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_.file_name}"',
            "Content-Length": str(file_.file_size),
        },
    )


# ---------------------------------------------------------------------------
# Route 4: POST /api/files/{file_id}/cache
# ---------------------------------------------------------------------------
@router.post("/api/files/{file_id}/cache")
async def cache_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Manually cache a file by downloading it from Telegram.

    Idempotent: does nothing if the file is already cached on disk.
    """
    file_ = await db.get(FileModel, file_id)
    if file_ is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")

    # Already cached — return current state (idempotent)
    if file_.is_cached and file_.cache_path:
        full_path = CACHE_DIR / file_.cache_path
        if full_path.exists():
            return _file_to_dict(file_)

    # Download and cache
    full_path = await _ensure_cached(file_, db)
    logger.info(
        "File cached: id={} path={} size={}", file_.id, file_.cache_path, file_.file_size
    )
    return _file_to_dict(file_)


# ---------------------------------------------------------------------------
# Route 5: DELETE /api/files/{file_id}/cache
# ---------------------------------------------------------------------------
@router.delete("/api/files/{file_id}/cache")
async def delete_cache(file_id: int, db: AsyncSession = Depends(get_db)):
    """Clear the local cache for a file.

    Idempotent: succeeds even if the file is not currently cached.
    """
    file_ = await db.get(FileModel, file_id)
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

    # Reset DB fields
    file_.cache_path = None
    file_.is_cached = False
    await db.commit()

    return {"status": "ok", "detail": f"Cache for file id={file_id} cleared"}
