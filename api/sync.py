"""Sync management API routes: trigger, list, detail, cancel."""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Channel, SyncTask
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
    """Serialize a SyncTask to a JSON-safe dict."""
    return {
        "id": task.id,
        "channel_id": task.channel_id,
        "status": task.status,
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
    """
    from database import AsyncSessionLocal
    from services.sync_engine import sync_channel

    async with AsyncSessionLocal() as session:
        try:
            settings = Settings()
            await sync_channel(channel_id, session, settings, task_id=task_id)
        except Exception as e:
            logger.error("Background sync failed: channel_id={} task_id={} error={}",
                         channel_id, task_id, e)
        finally:
            _running_syncs.pop(task_id, None)


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
    return {
        "task_id": task.id,
        "channel_id": channel_id,
        "status": "running",
    }


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
