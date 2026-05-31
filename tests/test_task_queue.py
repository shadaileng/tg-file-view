"""Tests for thumbnail worker pool and thumbnail generation (Step 6)."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from PIL import Image
from sqlalchemy import select

from models import Channel as ChannelModel, File as FileModel, ThumbJob
from services.task_queue import (
    generate_thumbnail,
    ThumbnailWorkerPool,
    _get_priority,
    get_thumb_worker_pool,
    set_thumb_worker_pool,
    reset_thumb_worker_pool,
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
    import uuid
    job = ThumbJob(
        id=kwargs.get("id", str(uuid.uuid4())),
        file_id=file_id,
        file_name=kwargs.get("file_name", "test.jpg"),
        mime_type=kwargs.get("mime_type", "image/jpeg"),
        status=kwargs.get("status", "pending"),
        priority=kwargs.get("priority", 3),
        attempt=kwargs.get("attempt", 0),
        max_retries=kwargs.get("max_retries", 3),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


def _make_test_image(path: Path, size=(100, 100), color=(255, 0, 0)):
    """Create a small test JPEG image on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    img.save(path, "JPEG")


# ---------------------------------------------------------------------------
# Unit: _get_priority
# ---------------------------------------------------------------------------

async def test_get_priority_photo():
    assert _get_priority("photo") == 3


async def test_get_priority_document():
    assert _get_priority("document") == 5


async def test_get_priority_unknown():
    """Unknown types get default priority 5."""
    assert _get_priority("audio") == 5
    assert _get_priority("unknown") == 5


# ---------------------------------------------------------------------------
# Unit: generate_thumbnail
# ---------------------------------------------------------------------------

async def test_generate_thumbnail_success(tmp_path: Path):
    """GIVEN a valid JPEG image WHEN generate_thumbnail THEN thumb file created."""
    source = tmp_path / "source.jpg"
    dest = tmp_path / "output" / "thumb.jpg"
    _make_test_image(source, size=(800, 600))

    result = generate_thumbnail(source, dest, max_width=320, max_height=240)
    assert result is True
    assert dest.exists()
    # Verify size constraints
    with Image.open(dest) as thumb:
        assert thumb.width <= 320
        assert thumb.height <= 240


async def test_generate_thumbnail_corrupt_file(tmp_path: Path):
    """GIVEN a non-image file WHEN generate_thumbnail THEN returns False."""
    source = tmp_path / "bad.jpg"
    source.write_text("not an image")

    dest = tmp_path / "output" / "thumb.jpg"
    result = generate_thumbnail(source, dest)
    assert result is False
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Integration: Worker pool processes a job
# ---------------------------------------------------------------------------

async def test_process_job_success(db_session, tmp_path: Path):
    """GIVEN cached file + pending job WHEN worker processes THEN job completed + thumb_path set."""
    from services.task_queue import ThumbnailWorkerPool

    ch = await _create_channel(db_session)

    # Create cached file (actual image on disk)
    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    cache_rel = f"{ch.id}/1_test.jpg"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    _make_test_image(cache_full, (400, 300))

    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_rel)

    job = await _create_job(db_session, f.id, status="pending")

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    await pool._process_job(str(job.id), f.id, 0)

    # Verify job
    await db_session.refresh(job)
    assert job.status == "completed"
    assert job.attempt == 1

    # Verify file
    await db_session.refresh(f)
    assert f.thumb_path == f"{ch.id}/{f.id}.jpg"
    assert f.thumb_type == "auto"

    # Verify thumbnail exists
    thumb_full = thumb_dir / f.thumb_path
    assert thumb_full.exists()

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S10 (retry): Failure then retry succeeds
# ---------------------------------------------------------------------------

async def test_retry_success(db_session, tmp_path: Path):
    """GIVEN first attempt fails (corrupt cache) WHEN worker retries with valid file THEN completed."""
    ch = await _create_channel(db_session)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"

    # First: create corrupt cache
    cache_rel = f"{ch.id}/1_test.jpg"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    cache_full.write_text("not an image")  # corrupt

    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_rel)

    job = await _create_job(db_session, f.id, status="pending", attempt=0)

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    # First attempt should fail (corrupt image)
    await pool._process_job(str(job.id), f.id, 0)
    await db_session.refresh(job)
    assert job.status == "pending"  # retry
    assert job.attempt == 1

    # Now fix the cache file
    _make_test_image(cache_full, (200, 200))

    # Second attempt should succeed
    await pool._process_job(str(job.id), f.id, 0)
    await db_session.refresh(job)
    assert job.status == "completed"
    assert job.attempt == 2

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S11 (retry exhausted): Reaching max retries
# ---------------------------------------------------------------------------

async def test_retry_exhausted(db_session, tmp_path: Path):
    """GIVEN always-corrupt file WHEN all 3 attempts fail THEN status=failed."""
    ch = await _create_channel(db_session)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"

    # Always corrupt cache
    cache_rel = f"{ch.id}/1_test.jpg"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    cache_full.write_text("not an image")

    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_rel)

    job = await _create_job(db_session, f.id, status="pending", attempt=0, max_retries=3)

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    # 3 attempts — should fail permanently
    for attempt_num in [1, 2, 3]:
        await pool._process_job(str(job.id), f.id, 0)
        await db_session.refresh(job)
        if attempt_num < 3:
            # Simulate the re-enqueue that happens in _handle_failure
            # (In real flow, _handle_failure re-enqueues; but here we call _process_job directly
            #  and _process_job calls _handle_failure which re-enqueues. Then worker picks it up.
            #  For direct test, we re-trigger manually since _process_job handles retry internally.)
            pass  # _process_job calls _handle_failure which sets back to pending + re-enqueues

    # After 3rd attempt: should be failed
    # Wait a bit for retry backoff
    await asyncio.sleep(0.1)
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempt == 3
    assert "corrupt" in (job.error_msg or "").lower() or "unsupported" in (job.error_msg or "").lower()

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S12 (load pending): Startup recovers pending jobs from DB
# ---------------------------------------------------------------------------

async def test_load_pending_on_startup(db_session, tmp_path: Path):
    """GIVEN 2 pending jobs in DB WHEN pool starts THEN both enqueued."""
    ch = await _create_channel(db_session)
    f1 = await _create_file(db_session, ch.id, message_id=1)
    f2 = await _create_file(db_session, ch.id, message_id=2)

    j1 = await _create_job(db_session, f1.id, status="pending")
    j2 = await _create_job(db_session, f2.id, status="pending")

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    # The pool.start would call _load_pending_jobs; we test it directly
    await pool._load_pending_jobs()

    # Both should be in queue
    assert not pool._queue.empty()
    items = []
    while not pool._queue.empty():
        items.append(pool._queue.get_nowait())
    assert len(items) == 2

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# Worker skips cancelled jobs
# ---------------------------------------------------------------------------

async def test_worker_skips_cancelled(db_session, tmp_path: Path):
    """GIVEN a cancelled job WHEN worker picks it up THEN silently skipped."""
    ch = await _create_channel(db_session)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    cache_rel = f"{ch.id}/1_test.jpg"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    _make_test_image(cache_full)

    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_rel)
    job = await _create_job(db_session, f.id, status="cancelled")

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    await pool._process_job(str(job.id), f.id, 0)

    # Should still be cancelled, not re-processed
    await db_session.refresh(job)
    assert job.status == "cancelled"

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# Pool start/stop lifecycle
# ---------------------------------------------------------------------------

async def test_pool_start_stop(tmp_path: Path):
    """GIVEN a fresh worker pool WHEN start then stop THEN no errors."""
    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"

    pool = ThumbnailWorkerPool(
        num_workers=2,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )

    await pool.start()
    assert len(pool._workers) == 2

    await pool.stop()
    assert len(pool._workers) == 0
    assert pool._shutdown is True
