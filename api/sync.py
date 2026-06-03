"""Sync management API routes: trigger, list, detail, cancel."""

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Channel, SyncTask, File as FileModel, ThumbJob
from config import Settings
from services.telegram_client import get_telegram_service, AuthState

router = APIRouter(tags=["sync"])

# Track running background sync tasks for cancellation
_running_syncs: dict[str, asyncio.Task] = {}


def _require_authorized():
    """Get authorized Telegram service or raise HTTPException."""
    svc = get_telegram_service()
    if svc is None:
        raise HTTPException(
            status_code=400,
            detail="Telegram service not configured",
        )
    if svc.auth_state != AuthState.AUTHORIZED:
        raise HTTPException(
            status_code=400,
            detail="Telegram client not authorized. Please complete login via /api/auth first.",
        )
    return svc


def _sync_task_to_dict(task: SyncTask) -> dict:
    """Serialize a SyncTask to a JSON-safe dict, including phase/progress fields for
    the frontend multi-phase progress panel."""
    return {
        "id": task.id,
        "channel_id": task.channel_id,
        "status": task.status,
        "phase": task.phase,
        "progress": task.progress,
        "total_files": task.total_files,
        "synced_files": task.synced_files,
        "skipped_files": task.skipped_files,
        "errors": json.loads(task.errors) if task.errors else [],
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


async def _bg_sync(channel_id: int, task_id: str):
    """Background coroutine that runs the actual sync.

    Creates its own database session for isolation from the HTTP request.
    Passes task_id so sync_channel reuses the API-created task instead
    of creating a new one (Bug #1 fix).

    After sync completes successfully, triggers automatic thumbnail/cover
    generation for files missing thumbnails (post-sync phase).
    """
    from database import AsyncSessionLocal
    from services.sync_engine import sync_channel

    async with AsyncSessionLocal() as session:
        try:
            settings = Settings()
            await sync_channel(channel_id, session, settings, task_id=task_id)

            # Post-sync: auto-trigger thumbnail/cover generation for files
            # in this channel that don't yet have thumbnails.
            try:
                async with AsyncSessionLocal() as ps_session:
                    created = await _trigger_post_sync_thumbs(ps_session, channel_id)
                    if created > 0:
                        logger.info("Post-sync: created {} thumb/cover jobs for channel_id={}",
                                    created, channel_id)
            except Exception as e:
                logger.warning("Post-sync thumb trigger failed (non-fatal): channel_id={} error={}",
                               channel_id, e)
        except Exception as e:
            logger.error("Background sync failed: channel_id={} task_id={} error={}",
                         channel_id, task_id, e)
            # F2: If task never transitioned away from "pending", mark as failed
            # (otherwise it stays "pending" forever — the frontend keeps polling).
            try:
                async with AsyncSessionLocal() as s:
                    t = await s.get(SyncTask, task_id)
                    if t and t.status == "pending":
                        t.status = "failed"
                        t.completed_at = datetime.now(timezone.utc)
                        t.errors = json.dumps([{"error": str(e)[:500]}])
                        await s.commit()
                        logger.info("Marked task {} as failed due to sync error", task_id)
            except Exception as inner:
                logger.error("Failed to mark task {} as failed: {}", task_id, inner)
        finally:
            _running_syncs.pop(task_id, None)


async def _trigger_post_sync_thumbs(session: AsyncSession, channel_id: int) -> int:
    """Create ThumbJob + enqueue for files in channel that need thumbnails/covers.

    Queries files with thumb_path IS NULL and file_type in ("photo","sticker","video"),
    then skips files that already have a pending/processing ThumbJob (anti-duplicate).
    Creates ThumbJob records and enqueues them into the ThumbnailWorkerPool.

    Returns:
        Number of ThumbJob records created.

    Scenario coverage:
        S1: photo files → ThumbJob created + Pillow thumbnail in worker pool
        S2: video files → ThumbJob created + ffmpeg cover in worker pool
        S3: mixed types → only photo/sticker/video get jobs, documents ignored
        S4: all files have thumbs → returns 0, nothing to do
        S5: some files have pending job → skip those, only create for remaining
        S6: worker pool not available → returns 0, logs warning
    """
    from services.task_queue import get_thumb_worker_pool, _SUPPORTED_TYPES

    # Check worker pool availability (S6)
    pool = get_thumb_worker_pool()
    if pool is None:
        logger.warning("Thumbnail worker pool not available, skip post-sync thumb generation")
        return 0

    # Query files needing thumbs/covers (photo, sticker, video only)
    result = await session.execute(
        select(FileModel).where(
            FileModel.channel_id == channel_id,
            FileModel.thumb_path.is_(None),
            FileModel.file_type.in_(_SUPPORTED_TYPES),
        )
    )
    files_needing_thumb = result.scalars().all()

    if not files_needing_thumb:
        logger.debug("Post-sync: all files in channel_id={} already have thumbnails, nothing to do", channel_id)
        return 0

    # Get file_ids that already have pending/processing ThumbJob (S5 anti-duplicate)
    file_ids_needing = {f.id for f in files_needing_thumb}
    existing_jobs_result = await session.execute(
        select(ThumbJob.file_id).where(
            ThumbJob.file_id.in_(file_ids_needing),
            ThumbJob.status.in_(["pending", "processing"]),
        )
    )
    existing_file_ids = set(existing_jobs_result.scalars().all())

    # Create ThumbJob + enqueue for files without existing pending job
    created = 0
    skipped = 0
    for f in files_needing_thumb:
        if f.id in existing_file_ids:
            skipped += 1
            continue

        job = ThumbJob(
            id=str(uuid.uuid4()),
            file_id=f.id,
            file_name=f.file_name,
            mime_type=f.mime_type,
            status="pending",
            priority=3,  # post-sync jobs: normal priority
        )
        session.add(job)
        await session.flush()  # get job.id without full commit yet
        pool.enqueue(str(job.id), f.id, f.file_type)
        created += 1

    await session.commit()

    if skipped > 0:
        logger.info(
            "Post-sync thumb jobs: created={} skipped_existing={} for channel_id={}",
            created, skipped, channel_id,
        )
    else:
        logger.debug("Post-sync thumb jobs: created={} for channel_id={}", created, channel_id)

    return created


# ---------------------------------------------------------------------------
# POST /api/channels/{channel_id}/sync
# ---------------------------------------------------------------------------
@router.post("/api/channels/{channel_id}/sync", status_code=202)
async def trigger_sync(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Trigger a sync for a channel (background async).

    Returns 202 with task_id on success.
    Returns 404 if channel not found.
    Returns 400 if Telegram not authorized.
    Returns 409 if a sync is already running for this channel.
    """
    # 1. Verify channel exists
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=404,
            detail=f"Channel with id={channel_id} not found",
        )

    # 2. Verify Telegram is authorized
    _require_authorized()

    # 3. Check no running sync for this channel
    result = await db.execute(
        select(SyncTask).where(
            SyncTask.channel_id == channel_id,
            SyncTask.status == "running",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A sync is already in progress for this channel",
        )

    # 4. Create a pending SyncTask (actual sync runs in background)
    task = SyncTask(
        channel_id=channel_id,
        status="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 5. Launch background sync
    bg_task = asyncio.create_task(_bg_sync(channel_id, task.id))
    _running_syncs[task.id] = bg_task

    logger.info("Sync triggered: channel_id={} task_id={}", channel_id, task.id)
    # F1: Use _sync_task_to_dict so the frontend receives "id" field
    # (matching activeSync.id used by pollActiveSync), plus all progress fields.
    return _sync_task_to_dict(task)


# ---------------------------------------------------------------------------
# GET /api/channels/{channel_id}/sync/tasks
# ---------------------------------------------------------------------------
@router.get("/api/channels/{channel_id}/sync/tasks")
async def list_sync_tasks(channel_id: int, db: AsyncSession = Depends(get_db)):
    """List all sync tasks for a channel, newest first."""
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=404,
            detail=f"Channel with id={channel_id} not found",
        )

    result = await db.execute(
        select(SyncTask)
        .where(SyncTask.channel_id == channel_id)
        .order_by(desc(SyncTask.created_at))
    )
    tasks = result.scalars().all()
    return [_sync_task_to_dict(t) for t in tasks]


# ---------------------------------------------------------------------------
# GET /api/sync/tasks/{task_id}
# ---------------------------------------------------------------------------
@router.get("/api/sync/tasks/{task_id}")
async def get_sync_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single sync task by its UUID."""
    task = await db.get(SyncTask, task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sync task with id={task_id} not found",
        )
    return _sync_task_to_dict(task)


# ---------------------------------------------------------------------------
# POST /api/sync/tasks/{task_id}/cancel
# ---------------------------------------------------------------------------
@router.post("/api/sync/tasks/{task_id}/cancel")
async def cancel_sync_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Cancel a running sync task.

    Sets the task status to 'cancelled'. The sync loop checks this
    periodically and stops when it detects the cancellation.
    """
    task = await db.get(SyncTask, task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sync task with id={task_id} not found",
        )

    if task.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Task is not running (current status: {task.status})",
        )

    task.status = "cancelled"
    task.completed_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Sync cancelled: task_id={}", task_id)
    return _sync_task_to_dict(task)
