"""Synchronization engine: pull file messages from Telegram channels into DB.

Uses Telethon iter_messages() to traverse channel history, extracts media
messages (photo/video/audio/document/sticker/voice), deduplicates against
existing files, and batch-inserts into the files table while maintaining
a SyncTask record for progress tracking and cancellation support.
"""

import json
import base64
import re
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, File, SyncTask
from config import Settings
from services.telegram_client import get_telegram_service, AuthState


# Regex for safe filename fallbacks
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]")


def _safe_basename(name: str) -> str:
    return _SAFE_NAME.sub("_", name)


def _require_authorized():
    """Get an authorized Telegram service or raise RuntimeError."""
    svc = get_telegram_service()
    if svc is None:
        raise RuntimeError("Telegram service not configured")
    if svc.auth_state != AuthState.AUTHORIZED:
        raise RuntimeError("Telegram client not authorized")
    return svc


def _get_file_ref(message) -> Optional[str]:
    """Extract and base64-encode the Telegram file reference from a message.

    File references are binary data needed to locate media for download.
    Storing them as base64 allows later reconstruction for direct downloads
    without re-fetching the message.
    """
    if not message or not message.media:
        return None
    media = message.media
    file_ref = None
    # Check document first — real MessageMediaPhoto has no .document
    if hasattr(media, "document") and media.document is not None:
        file_ref = getattr(media.document, "file_reference", None)
    elif hasattr(media, "photo") and media.photo is not None:
        file_ref = getattr(media.photo, "file_reference", None)
    # Must be real bytes, not a mock
    if file_ref and isinstance(file_ref, bytes):
        return base64.b64encode(file_ref).decode("ascii")
    return None


def _detect_file_type(doc_attrs: list) -> str:
    """Determine file type from DocumentAttribute instances.

    Priority order: video > audio > sticker > voice > animated > document.
    Duck-types via __class__.__name__ to work with both real types and mocks.
    """
    type_checks = [
        ("video", "DocumentAttributeVideo"),
        ("audio", "DocumentAttributeAudio"),
        ("sticker", "DocumentAttributeSticker"),
        ("voice", "voice"),  # seen in some Telethon versions
    ]
    for ft, cls_name in type_checks:
        for attr in doc_attrs:
            attr_name = type(attr).__name__
            if attr_name == cls_name or cls_name.lower() in attr_name.lower():
                return ft
    return "document"


def _extract_file_info(message) -> Optional[dict]:
    """Extract file metadata from a single Telegram message.

    Returns None if the message has no media.
    Returns a dict compatible with the File model fields (plus tg_ref string).
    """
    if not message or not message.media:
        return None

    info = {
        "message_id": message.id,
        "file_size": 0,
        "file_name": "",
        "mime_type": "application/octet-stream",
        "file_type": "document",
        "tg_ref": _get_file_ref(message),
    }

    media = message.media

    # --- Document (checked first — real MessageMediaPhoto has no .document) ---
    if hasattr(media, "document") and media.document is not None:
        doc = media.document
        info["file_size"] = getattr(doc, "size", 0) or 0
        info["mime_type"] = getattr(doc, "mime_type", "application/octet-stream") or "application/octet-stream"

        doc_attrs = getattr(doc, "attributes", []) or []
        info["file_type"] = _detect_file_type(doc_attrs)

        # Extract filename from attributes
        for attr in doc_attrs:
            fname = getattr(attr, "file_name", None)
            if fname and isinstance(fname, str) and fname.strip():
                info["file_name"] = fname
                break

        # Fallback name if no filename attribute
        if not info["file_name"]:
            info["file_name"] = f"{info['file_type']}_{message.id}"

        return info

    # --- Photo ---
    if hasattr(media, "photo") and media.photo is not None:
        info["file_type"] = "photo"
        info["mime_type"] = "image/jpeg"
        info["file_name"] = f"photo_{message.id}.jpg"
        # Largest photo size
        sizes = getattr(media.photo, "sizes", []) or []
        if sizes:
            largest = max(sizes, key=lambda s: getattr(s, "size", 0) or 0)
            info["file_size"] = getattr(largest, "size", 0)
        return info

    # Other media types (unsupported) — skip
    return None


async def _batch_insert_files(
    batch: List[dict],
    db_session: AsyncSession,
    task: SyncTask,
    channel_id: int,
) -> tuple:
    """Batch-insert extracted file records with deduplication.

    Checks existing message_ids in the channel via a single query to avoid
    N+1 selects, then inserts only new records.

    Returns (synced_count, skipped_count).
    """
    synced = 0
    skipped = 0

    batch_msg_ids = [f["message_id"] for f in batch]
    existing_result = await db_session.execute(
        select(File.message_id).where(
            File.channel_id == channel_id,
            File.message_id.in_(batch_msg_ids),
        )
    )
    existing_ids = {row[0] for row in existing_result.all()}

    errors = []
    for info in batch:
        if info["message_id"] in existing_ids:
            skipped += 1
            continue
        try:
            f = File(
                channel_id=channel_id,
                message_id=info["message_id"],
                file_name=info["file_name"],
                file_size=info["file_size"],
                mime_type=info["mime_type"],
                file_type=info["file_type"],
                tg_ref=info.get("tg_ref"),
            )
            db_session.add(f)
            existing_ids.add(info["message_id"])  # track within this batch
            synced += 1
        except Exception as e:
            errors.append(f"msg_id={info['message_id']}: {e}")

    await db_session.commit()

    task.synced_files += synced
    task.skipped_files += skipped
    if errors:
        existing_errors = json.loads(task.errors or "[]")
        existing_errors.extend(errors)
        task.errors = json.dumps(existing_errors)

    return synced, skipped


async def sync_channel(
    channel_id: int,
    db_session: AsyncSession,
    settings: Settings,
    task_id: Optional[str] = None,
) -> SyncTask:
    """Run a full/incremental sync for a single channel.

    Traverses the channel's message history via Telethon iter_messages(),
    extracts media file metadata, deduplicates against existing records,
    and batch-inserts into the files table.

    Args:
        channel_id: Database ID of the channel to sync.
        db_session: Active SQLAlchemy async session.
        settings: Application settings (batch_size, etc.).
        task_id: Optional UUID of an existing pending SyncTask to reuse.
                 When provided, the existing task is looked up and updated
                 instead of creating a new one, fixing the task-ID disconnect
                 between the API and the sync engine.

    Returns:
        The SyncTask ORM record (committed, status = "completed" or "failed").

    Raises:
        ValueError: If channel_id is not found in the database.
        RuntimeError: If Telegram client is not authorized.
    """
    # 1. Load channel
    channel = await db_session.get(Channel, channel_id)
    if channel is None:
        raise ValueError(f"Channel with id={channel_id} not found")

    # 2. Get authorized client
    svc = _require_authorized()
    client = await svc.get_client()

    # 3. Reuse or create SyncTask
    if task_id is not None:
        task = await db_session.get(SyncTask, task_id)
        if task is None:
            raise ValueError(f"SyncTask with id={task_id} not found")
        # Transition pending→running, preserving the original task record
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        await db_session.commit()
    else:
        now = datetime.now(timezone.utc)
        task = SyncTask(
            channel_id=channel_id,
            status="running",
            started_at=now,
        )
        db_session.add(task)
        await db_session.commit()

    logger.info(
        "Sync started: channel_id={} task_id={}",
        channel_id, task.id,
    )

    try:
        # 4. Resolve Telegram entity
        entity = await client.get_entity(channel.tg_id)

        # 5. Configure iter_messages args
        iter_kwargs = {"limit": settings.sync_bulk_api_limit}
        if channel.last_sync:
            # Incremental: only messages after last_sync
            # offset_date should be slightly before last_sync to catch edge cases
            iter_kwargs["offset_date"] = channel.last_sync

        # 6. Iterate messages in batches
        batch: List[dict] = []
        total_processed = 0

        async for message in client.iter_messages(entity, **iter_kwargs):
            # Check cancellation
            await db_session.refresh(task)
            if task.status == "cancelled":
                logger.info("Sync cancelled: task_id={}", task.id)
                break

            total_processed += 1
            file_info = _extract_file_info(message)
            if file_info:
                batch.append(file_info)

            if len(batch) >= settings.sync_batch_size:
                s, k = await _batch_insert_files(batch, db_session, task, channel_id)
                # Update total_files in real-time so the frontend can show
                # "synced / total" progress during polling (Bug #2 fix)
                task.total_files = total_processed
                await db_session.commit()
                logger.debug(
                    "Batch insert: synced={} skipped={} batch_size={} total_processed={}",
                    s, k, len(batch), total_processed,
                )
                batch = []

        # 7. Flush remaining batch
        if batch:
            s, k = await _batch_insert_files(batch, db_session, task, channel_id)
            logger.debug(
                "Final batch insert: synced={} skipped={} batch_size={}",
                s, k, len(batch),
            )

        # 8. Finalize task and update channel
        task.total_files = total_processed
        if task.status != "cancelled":
            task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        channel.last_sync = datetime.now(timezone.utc)

        # Update channel statistics (Bug #3 fix)
        file_count_result = await db_session.execute(
            select(func.count(File.id)).where(File.channel_id == channel_id)
        )
        channel.file_count = file_count_result.scalar() or 0

        total_size_result = await db_session.execute(
            select(func.sum(File.file_size)).where(File.channel_id == channel_id)
        )
        channel.total_size = total_size_result.scalar() or 0

        await db_session.commit()

        logger.info(
            "Sync completed: channel_id={} task_id={} "
            "total={} synced={} skipped={} status={}",
            channel_id, task.id,
            task.total_files, task.synced_files,
            task.skipped_files, task.status,
        )

    except Exception as e:
        logger.error("Sync failed: channel_id={} task_id={} error={}", channel_id, task.id, e)
        # Mark task as failed, preserve partial progress
        await db_session.refresh(task)
        task.status = "failed"
        existing_errors = json.loads(task.errors or "[]")
        existing_errors.append(str(e))
        task.errors = json.dumps(existing_errors)
        task.completed_at = datetime.now(timezone.utc)
        await db_session.commit()
        raise

    return task
