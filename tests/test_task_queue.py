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
            # _handle_failure sets status to pending + commits.
            # Worker polls DB and picks it up — no manual enqueue needed.
            pass

    # After 3rd attempt: should be failed
    # Wait a bit for retry backoff
    await asyncio.sleep(0.1)
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempt == 3
    assert "corrupt" in (job.error_msg or "").lower() or "unsupported" in (job.error_msg or "").lower()

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S12 (startup recovery): Reset stale processing → pending
# ---------------------------------------------------------------------------

async def test_recover_stale_jobs_on_startup(db_session, tmp_path: Path):
    """GIVEN 1 processing + 2 pending jobs in DB WHEN _recover_stale_jobs THEN the processing
    job is reset to pending, all 3 remain in DB as pending."""
    ch = await _create_channel(db_session)
    f1 = await _create_file(db_session, ch.id, message_id=1)
    f2 = await _create_file(db_session, ch.id, message_id=2)
    f3 = await _create_file(db_session, ch.id, message_id=3)

    await _create_job(db_session, f1.id, status="processing")  # stale — should be reset
    await _create_job(db_session, f2.id, status="pending")
    await _create_job(db_session, f3.id, status="pending")

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    await pool._recover_stale_jobs()

    # All 3 should now be pending
    result = await db_session.execute(
        select(ThumbJob).where(ThumbJob.file_id.in_([f1.id, f2.id, f3.id]))
    )
    jobs = result.scalars().all()
    assert len(jobs) == 3
    for j in jobs:
        assert j.status == "pending"

    reset_thumb_worker_pool()


async def test_claim_next_atomic(db_session, tmp_path: Path):
    """GIVEN 2 pending jobs in DB WHEN _claim_next called twice THEN each job claimed atomically.
    Verifies that the CAS (Compare-And-Swap) mechanism prevents double-claiming."""
    ch = await _create_channel(db_session)
    f1 = await _create_file(db_session, ch.id, message_id=1)
    f2 = await _create_file(db_session, ch.id, message_id=2)

    j1 = await _create_job(db_session, f1.id, status="pending", priority=1)
    j2 = await _create_job(db_session, f2.id, status="pending", priority=2)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    # Claim first — should get the higher-priority job (j1 with priority=1)
    claimed_1 = await pool._claim_next(0)
    assert claimed_1 is not None
    claimed_job_id_1, claimed_file_id_1 = claimed_1
    assert claimed_file_id_1 == f1.id

    # Verify j1 is now processing
    await db_session.refresh(j1)
    assert j1.status == "processing"

    # Claim second — should get j2
    claimed_2 = await pool._claim_next(0)
    assert claimed_2 is not None
    claimed_job_id_2, claimed_file_id_2 = claimed_2
    assert claimed_file_id_2 == f2.id

    await db_session.refresh(j2)
    assert j2.status == "processing"

    # Claim third — no more pending
    claimed_3 = await pool._claim_next(0)
    assert claimed_3 is None

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


# ---------------------------------------------------------------------------
# Video worker process test
# ---------------------------------------------------------------------------

async def test_process_job_video_success(db_session, tmp_path: Path):
    """GIVEN cached video file + pending job WHEN worker processes + TG thumb available
    THEN job completed with telegram thumb type."""
    ch = await _create_channel(db_session)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    cache_rel = f"{ch.id}/1_test.mp4"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    cache_full.write_bytes(b"fake video data")

    f = await _create_file(
        db_session, ch.id,
        file_name="test.mp4",
        mime_type="video/mp4",
        file_type="video",
        is_cached=True,
        cache_path=cache_rel,
    )

    job = await _create_job(db_session, f.id, status="pending")

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    # Mock TG thumb download to return a valid image (simulating Telegram's thumbnail)
    tg_thumb_path = thumb_dir / f"{ch.id}/{f.id}.jpg"
    tg_thumb_path.parent.mkdir(parents=True, exist_ok=True)
    _make_test_image(tg_thumb_path, size=(160, 120), color=(0, 255, 0))

    import asyncio
    async def _mock_download_tg_thumb(fr):
        return tg_thumb_path

    pool._download_telegram_thumb = _mock_download_tg_thumb

    await pool._process_job(str(job.id), f.id, 0)

    await db_session.refresh(job)
    assert job.status == "completed"
    assert job.attempt == 1

    await db_session.refresh(f)
    assert f.thumb_path == f"{ch.id}/{f.id}.jpg"
    assert f.thumb_type == "telegram"

    thumb_full = thumb_dir / f.thumb_path
    assert thumb_full.exists()
    assert thumb_full.stat().st_size > 0

    reset_thumb_worker_pool()


async def test_process_job_video_no_tg_thumb(db_session, tmp_path: Path):
    """GIVEN video file but TG thumb unavailable WHEN worker processes THEN job fails.

    Video thumbnail generation no longer falls back to full download + ffmpeg.
    Only Telegram's pre-generated thumbnail is supported for videos.
    """
    ch = await _create_channel(db_session)
    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    cache_rel = f"{ch.id}/1_test.mp4"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    cache_full.write_bytes(b"fake video data")

    f = await _create_file(
        db_session, ch.id,
        file_name="test.mp4",
        mime_type="video/mp4",
        file_type="video",
        is_cached=True,
        cache_path=cache_rel,
    )

    job = await _create_job(db_session, f.id, status="pending", max_retries=3)

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    # Process 3 times to exhaust retries (TG thumb download will fail — no authorized service)
    for _ in range(3):
        await pool._process_job(str(job.id), f.id, 0)
        await db_session.refresh(job)
        await asyncio.sleep(0.05)

    await db_session.refresh(job)
    assert job.status == "failed"
    assert "tg thumb" in (job.error_msg or "").lower()

    reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# Timeout tests
# ---------------------------------------------------------------------------


async def test_job_timeout_triggers_mark_timeout(db_session, tmp_path: Path):
    """GIVEN a photo job with short timeout WHEN _process_job hangs THEN _worker_loop
    catches TimeoutError and calls _mark_job_timeout, marking the job as timed_out.

    S2 — Edge: task timeout terminates stuck job.
    """
    ch = await _create_channel(db_session)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    cache_rel = f"{ch.id}/1_test.png"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    _make_test_image(cache_full, (400, 300))

    f = await _create_file(
        db_session, ch.id,
        file_name="test.png", mime_type="image/png",
        file_type="photo", is_cached=True, cache_path=cache_rel,
    )
    job = await _create_job(db_session, f.id, status="pending", max_retries=3)

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir), cache_dir=str(cache_dir),
        job_timeout=0.5,  # 500ms — intentionally very short for testing
    )
    set_thumb_worker_pool(pool)

    # Replace generate_thumbnail with a slow operation to trigger timeout
    import services.task_queue as tq_module
    original_gen = tq_module.generate_thumbnail

    def _slow_gen(*a, **kw):
        import time
        time.sleep(2)  # sleeps 2s > 0.5s timeout
        return False

    tq_module.generate_thumbnail = _slow_gen

    try:
        # Simulate the worker loop's timeout wrapping
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                pool._process_job(str(job.id), f.id, 0),
                timeout=0.5,
            )

        # Call the timeout handler
        await pool._mark_job_timeout(str(job.id))
    finally:
        tq_module.generate_thumbnail = original_gen

    await db_session.refresh(job)
    assert job.status in ("failed", "pending")
    assert "timed out" in (job.error_msg or "").lower()

    reset_thumb_worker_pool()


async def test_job_timeout_retry(db_session, tmp_path: Path):
    """GIVEN timeout on first attempt WHEN max_retries=3 THEN job retries (status=pending)
    instead of permanently failing.

    S3 — Edge: timeout + retry until exhaustion.
    """
    ch = await _create_channel(db_session)
    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    cache_rel = f"{ch.id}/1_test.png"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    _make_test_image(cache_full, (400, 300))

    f = await _create_file(
        db_session, ch.id,
        file_name="test.png", mime_type="image/png",
        file_type="photo", is_cached=True, cache_path=cache_rel,
    )
    job = await _create_job(db_session, f.id, status="pending", attempt=1, max_retries=3)

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir), cache_dir=str(cache_dir),
    )
    set_thumb_worker_pool(pool)

    # Call timeout handler on a job that still has retries left
    await pool._mark_job_timeout(str(job.id))

    await db_session.refresh(job)
    assert job.status == "pending"  # retried, not failed
    assert "timed out" in (job.error_msg or "").lower()

    # Now exhaust all retries
    job.attempt = 3  # simulate that attempt counter was incremented
    await db_session.commit()
    await pool._mark_job_timeout(str(job.id))

    await db_session.refresh(job)
    assert job.status == "failed"  # exhausted, permanent fail
    assert "timed out" in (job.error_msg or "").lower()

    reset_thumb_worker_pool()


async def test_no_timeout_when_disabled(db_session, tmp_path: Path):
    """GIVEN job_timeout=0 WHEN processing THEN no timeout is applied (normal completion).

    S1 — Happy Path: timeout disabled, tasks run normally.
    """
    ch = await _create_channel(db_session)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    cache_rel = f"{ch.id}/1_test.png"
    cache_full = cache_dir / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    _make_test_image(cache_full, (200, 200))

    f = await _create_file(
        db_session, ch.id,
        file_name="test.png", mime_type="image/png",
        file_type="photo", is_cached=True, cache_path=cache_rel,
    )
    job = await _create_job(db_session, f.id, status="pending")

    pool = ThumbnailWorkerPool(
        num_workers=1,
        thumb_dir=str(thumb_dir), cache_dir=str(cache_dir),
        job_timeout=0,  # disabled
    )
    set_thumb_worker_pool(pool)

    # Should complete normally without timeout
    await pool._process_job(str(job.id), f.id, 0)

    await db_session.refresh(job)
    assert job.status == "completed"

    reset_thumb_worker_pool()
