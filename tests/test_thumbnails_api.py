"""Tests for thumbnail API endpoints (Step 6)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from sqlalchemy import select

from models import Channel as ChannelModel, File as FileModel, ThumbJob
from services.task_queue import (
    get_thumb_worker_pool,
    set_thumb_worker_pool,
    reset_thumb_worker_pool,
    ThumbnailWorkerPool,
)
from services.telegram_client import (
    AuthState,
    set_telegram_service,
    reset_telegram_service,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_channel(db_session, **kwargs) -> ChannelModel:
    ch = ChannelModel(
        tg_id=kwargs.get("tg_id", 123456789),
        username=kwargs.get("username", "testchan"),
        title=kwargs.get("title", "Test Channel"),
    )
    db_session.add(ch)
    await db_session.commit()
    await db_session.refresh(ch)
    return ch


async def _create_file(db_session, channel_id: int, **kwargs) -> FileModel:
    f = FileModel(
        channel_id=channel_id,
        message_id=kwargs.get("message_id", 100),
        file_name=kwargs.get("file_name", "test_image.jpg"),
        file_size=kwargs.get("file_size", 1024),
        mime_type=kwargs.get("mime_type", "image/jpeg"),
        file_type=kwargs.get("file_type", "photo"),
        is_cached=kwargs.get("is_cached", False),
        cache_path=kwargs.get("cache_path", None),
    )
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)
    return f


async def _create_job(db_session, file_id: int, **kwargs) -> ThumbJob:
    job = ThumbJob(
        id=kwargs.get("id", str(uuid.uuid4())),
        file_id=file_id,
        file_name=kwargs.get("file_name", "test.jpg"),
        mime_type=kwargs.get("mime_type", "image/jpeg"),
        status=kwargs.get("status", "pending"),
        priority=kwargs.get("priority", 3),
        attempt=kwargs.get("attempt", 0),
        max_retries=kwargs.get("max_retries", 3),
        error_msg=kwargs.get("error_msg", None),
        created_at=kwargs.get("created_at", datetime.now(timezone.utc)),
        started_at=kwargs.get("started_at", None),
        completed_at=kwargs.get("completed_at", None),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


def _mock_worker_pool():
    """Create a mock worker pool and set it globally."""
    pool = MagicMock(spec=ThumbnailWorkerPool)
    pool.enqueue = MagicMock()
    set_thumb_worker_pool(pool)
    return pool


# ---------------------------------------------------------------------------
# S1 — Happy Path: 手动触发单文件缩略图
# ---------------------------------------------------------------------------

async def test_trigger_single_file(db_session):
    """GIVEN file exists,no existing job WHEN POST /api/files/{id}/thumbnail THEN 202 + job_id."""
    from api.thumbnails import trigger_single_thumbnail

    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)

    pool = _mock_worker_pool()

    result = await trigger_single_thumbnail(file_id=f.id, db=db_session)
    assert result["status"] == "pending"
    assert "job_id" in result
    assert result["file_id"] == f.id

    # Verify job created in DB
    job = (await db_session.execute(
        select(ThumbJob).where(ThumbJob.file_id == f.id)
    )).scalar_one_or_none()
    assert job is not None
    assert job.status == "pending"

    # Verify enqueued
    pool.enqueue.assert_called_once()

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S2 — Happy Path: 查询任务列表（支持状态过滤）
# ---------------------------------------------------------------------------

async def test_list_jobs_with_filter(db_session):
    """GIVEN 5 jobs (2 pending, 1 processing, 2 completed) WHEN status=pending THEN 2 items."""
    from api.thumbnails import list_thumb_jobs

    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)

    await _create_job(db_session, f.id, status="pending")
    await _create_job(db_session, f.id, status="pending")
    await _create_job(db_session, f.id, status="processing")
    await _create_job(db_session, f.id, status="completed")
    await _create_job(db_session, f.id, status="completed")

    result = await list_thumb_jobs(status="pending", offset=0, limit=50, db=db_session)
    assert len(result["jobs"]) == 2
    assert result["total"] == 2
    assert all(j.status == "pending" for j in result["jobs"])


async def test_list_jobs_no_filter(db_session):
    """GIVEN 3 jobs WHEN no status filter THEN all returned."""
    from api.thumbnails import list_thumb_jobs

    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)

    await _create_job(db_session, f.id, status="pending")
    await _create_job(db_session, f.id, status="completed")
    await _create_job(db_session, f.id, status="failed")

    result = await list_thumb_jobs(status=None, offset=0, limit=50, db=db_session)
    assert result["total"] == 3


# ---------------------------------------------------------------------------
# S3 — Happy Path: 批量提交缩略图任务
# ---------------------------------------------------------------------------

async def test_generate_batch(db_session):
    """GIVEN 3 files WHEN batch submit THEN 3 jobs created."""
    from api.thumbnails import generate_batch, BatchGenerateRequest

    ch = await _create_channel(db_session)
    f1 = await _create_file(db_session, ch.id, message_id=1)
    f2 = await _create_file(db_session, ch.id, message_id=2)
    f3 = await _create_file(db_session, ch.id, message_id=3)

    pool = _mock_worker_pool()

    body = BatchGenerateRequest(file_ids=[f1.id, f2.id, f3.id])
    result = await generate_batch(body, db=db_session)

    assert result["total_created"] == 3
    assert result["total_requested"] == 3
    assert len(result["job_ids"]) == 3
    assert result["skipped_file_ids"] == []
    assert result["not_found_file_ids"] == []

    # Verify jobs in DB
    count = (await db_session.execute(
        select(ThumbJob).where(ThumbJob.file_id.in_([f1.id, f2.id, f3.id]))
    )).scalars().all()
    assert len(count) == 3

    # Verify enqueued 3 times
    assert pool.enqueue.call_count == 3

    reset_thumb_worker_pool()


async def test_generate_batch_skips_existing(db_session):
    """GIVEN 1 file already has pending job WHEN batch with same file THEN skipped."""
    from api.thumbnails import generate_batch, BatchGenerateRequest

    ch = await _create_channel(db_session)
    f1 = await _create_file(db_session, ch.id, message_id=1)
    f2 = await _create_file(db_session, ch.id, message_id=2)

    # Pre-create pending job for f1
    await _create_job(db_session, f1.id, status="pending")

    pool = _mock_worker_pool()

    body = BatchGenerateRequest(file_ids=[f1.id, f2.id])
    result = await generate_batch(body, db=db_session)

    assert result["total_created"] == 1  # only f2
    assert result["skipped_file_ids"] == [f1.id]

    reset_thumb_worker_pool()


async def test_generate_batch_not_found(db_session):
    """GIVEN some file_ids don't exist WHEN batch THEN reported in not_found."""
    from api.thumbnails import generate_batch, BatchGenerateRequest

    ch = await _create_channel(db_session)
    f1 = await _create_file(db_session, ch.id, message_id=1)

    pool = _mock_worker_pool()

    body = BatchGenerateRequest(file_ids=[f1.id, 99999])
    result = await generate_batch(body, db=db_session)

    assert result["total_created"] == 1
    assert result["not_found_file_ids"] == [99999]

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S4 — Happy Path: 查看单个任务详情
# ---------------------------------------------------------------------------

async def test_get_job_detail(db_session):
    """GIVEN job exists + file has thumb_path WHEN GET job detail THEN thumb_url included."""
    from api.thumbnails import get_thumb_job

    ch = await _create_channel(db_session)
    # File with thumb_path
    f = await _create_file(db_session, ch.id, is_cached=True)
    f.thumb_path = "1/42.jpg"
    await db_session.commit()

    job = await _create_job(db_session, f.id, status="completed")

    result = await get_thumb_job(str(job.id), db=db_session)
    assert result.id == str(job.id)
    assert result.status == "completed"
    assert result.thumb_url == "/thumbnails/1/42.jpg"


# ---------------------------------------------------------------------------
# S5 — Happy Path: 缩略图整体统计
# ---------------------------------------------------------------------------

async def test_stats(db_session):
    """GIVEN mixed status jobs WHEN GET stats THEN all statuses counted."""
    from api.thumbnails import thumb_stats

    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)

    await _create_job(db_session, f.id, status="pending")
    await _create_job(db_session, f.id, status="pending")
    await _create_job(db_session, f.id, status="processing")
    await _create_job(db_session, f.id, status="completed")
    await _create_job(db_session, f.id, status="completed")
    await _create_job(db_session, f.id, status="completed")
    await _create_job(db_session, f.id, status="failed")
    await _create_job(db_session, f.id, status="cancelled")

    result = await thumb_stats(db=db_session)
    assert result["pending"] == 2
    assert result["processing"] == 1
    assert result["completed"] == 3
    assert result["failed"] == 1
    assert result["cancelled"] == 1
    assert result["total"] == 8


# ---------------------------------------------------------------------------
# S6 — Edge: 文件不存在
# ---------------------------------------------------------------------------

async def test_trigger_file_not_found(db_session):
    """GIVEN file 999 doesn't exist WHEN POST trigger THEN 404."""
    from api.thumbnails import trigger_single_thumbnail
    from fastapi import HTTPException

    _mock_worker_pool()

    with pytest.raises(HTTPException) as exc:
        await trigger_single_thumbnail(file_id=999, db=db_session)
    assert exc.value.status_code == 404

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S7 — Edge: 重复提交
# ---------------------------------------------------------------------------

async def test_trigger_duplicate_job(db_session):
    """GIVEN file already has pending job WHEN trigger again THEN 409."""
    from api.thumbnails import trigger_single_thumbnail
    from fastapi import HTTPException

    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)
    await _create_job(db_session, f.id, status="pending")

    _mock_worker_pool()

    with pytest.raises(HTTPException) as exc:
        await trigger_single_thumbnail(file_id=f.id, db=db_session)
    assert exc.value.status_code == 409

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S8 — Edge: 取消等待中的任务
# ---------------------------------------------------------------------------

async def test_cancel_pending_job(db_session):
    """GIVEN pending job WHEN POST cancel THEN status=cancelled."""
    from api.thumbnails import cancel_thumb_job

    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)
    job = await _create_job(db_session, f.id, status="pending")

    result = await cancel_thumb_job(str(job.id), db=db_session)
    assert result["status"] == "cancelled"

    await db_session.refresh(job)
    assert job.status == "cancelled"
    assert job.completed_at is not None


# ---------------------------------------------------------------------------
# S9 — Edge: 取消已完成的任务
# ---------------------------------------------------------------------------

async def test_cancel_completed_job(db_session):
    """GIVEN completed job WHEN POST cancel THEN 400."""
    from api.thumbnails import cancel_thumb_job
    from fastapi import HTTPException

    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)
    job = await _create_job(db_session, f.id, status="completed")

    with pytest.raises(HTTPException) as exc:
        await cancel_thumb_job(str(job.id), db=db_session)
    assert exc.value.status_code == 400


async def test_cancel_nonexistent_job(db_session):
    """GIVEN non-existent job_id WHEN cancel THEN 404."""
    from api.thumbnails import cancel_thumb_job
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await cancel_thumb_job("non-existent-id", db=db_session)
    assert exc.value.status_code == 404
