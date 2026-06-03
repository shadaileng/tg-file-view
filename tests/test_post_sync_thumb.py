"""Tests for post-sync automatic thumbnail/cover generation trigger (feat/post-sync-auto-thumb).

Scenarios S1-S6 from the design doc.
"""

import asyncio
import uuid
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from models import Channel, File as FileModel, SyncTask, ThumbJob
from api.sync import _trigger_post_sync_thumbs
from services.task_queue import (
    ThumbnailWorkerPool,
    set_thumb_worker_pool,
    reset_thumb_worker_pool,
    _SUPPORTED_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_channel(db_session: AsyncSession, tg_id: int = 111,
                        title: str = "Test Channel") -> Channel:
    ch = Channel(tg_id=tg_id, username="test_ch", title=title)
    db_session.add(ch)
    await db_session.commit()
    await db_session.refresh(ch)
    return ch


async def _seed_file(db_session: AsyncSession, channel_id: int, **kwargs) -> FileModel:
    f = FileModel(
        channel_id=channel_id,
        message_id=kwargs.get("message_id", 100),
        file_name=kwargs.get("file_name", "test.jpg"),
        file_size=kwargs.get("file_size", 1024),
        mime_type=kwargs.get("mime_type", "image/jpeg"),
        file_type=kwargs.get("file_type", "photo"),
        thumb_path=kwargs.get("thumb_path", None),
    )
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)
    return f


def _make_mock_authorized_svc() -> MagicMock:
    """Create a mock Telegram service that appears authorized."""
    from services.telegram_client import AuthState
    svc = AsyncMock()
    svc.auth_state = AuthState.AUTHORIZED
    svc.is_authorized = AsyncMock(return_value=True)
    mock_client = AsyncMock()
    svc.get_client = AsyncMock(return_value=mock_client)
    return svc, mock_client


def _setup_mock_sync(svc, mock_client, messages=None):
    """Configure mock for a sync operation (iter_messages)."""
    from tests.test_sync_engine import _AsyncIter
    mock_client.get_entity = AsyncMock(return_value=MagicMock())
    if messages is not None:
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))
    else:
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter([]))


@pytest.fixture
async def client():
    """Async HTTP test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# S1: Happy Path — Post-sync creates photo thumb jobs
# ---------------------------------------------------------------------------

async def test_post_sync_photo_thumb_trigger(db_session, tmp_path: Path):
    """GIVEN 3 photo files with thumb_path=NULL WHEN post-sync trigger THEN 3 ThumbJobs created + enqueued."""
    ch = await _seed_channel(db_session, tg_id=1001)

    f1 = await _seed_file(db_session, ch.id, message_id=1, file_type="photo", thumb_path=None)
    f2 = await _seed_file(db_session, ch.id, message_id=2, file_type="photo", thumb_path=None)
    f3 = await _seed_file(db_session, ch.id, message_id=3, file_type="photo", thumb_path=None)

    # Create worker pool
    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    pool = ThumbnailWorkerPool(num_workers=1, thumb_dir=str(thumb_dir), cache_dir=str(cache_dir))
    set_thumb_worker_pool(pool)

    try:
        created = await _trigger_post_sync_thumbs(db_session, ch.id)
        assert created == 3

        # Verify ThumbJob records
        result = await db_session.execute(
            select(ThumbJob).where(ThumbJob.file_id.in_([f1.id, f2.id, f3.id]))
        )
        jobs = result.scalars().all()
        assert len(jobs) == 3
        for j in jobs:
            assert j.status == "pending"

        # Verify jobs are in queue
        queued = []
        while not pool._queue.empty():
            queued.append(pool._queue.get_nowait())
        assert len(queued) == 3
    finally:
        reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S2: Happy Path — Post-sync creates video cover jobs
# ---------------------------------------------------------------------------

async def test_post_sync_video_cover_trigger(db_session, tmp_path: Path):
    """GIVEN 2 video files with thumb_path=NULL WHEN post-sync THEN 2 ThumbJobs created."""
    ch = await _seed_channel(db_session, tg_id=1002)

    v1 = await _seed_file(db_session, ch.id, message_id=1, file_type="video",
                          file_name="test.mp4", mime_type="video/mp4", thumb_path=None)
    v2 = await _seed_file(db_session, ch.id, message_id=2, file_type="video",
                          file_name="test2.mp4", mime_type="video/mp4", thumb_path=None)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    pool = ThumbnailWorkerPool(num_workers=1, thumb_dir=str(thumb_dir), cache_dir=str(cache_dir))
    set_thumb_worker_pool(pool)

    try:
        created = await _trigger_post_sync_thumbs(db_session, ch.id)
        assert created == 2

        result = await db_session.execute(
            select(ThumbJob).where(ThumbJob.file_id.in_([v1.id, v2.id]))
        )
        jobs = result.scalars().all()
        assert len(jobs) == 2
    finally:
        reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S3: Mixed file types — only photo/sticker/video get jobs
# ---------------------------------------------------------------------------

async def test_post_sync_mixed_types(db_session, tmp_path: Path):
    """GIVEN 3 photo + 2 video + 1 document (all thumb_path=NULL)
    WHEN post-sync
    THEN only 5 jobs created (document skipped)."""
    ch = await _seed_channel(db_session, tg_id=1003)

    await _seed_file(db_session, ch.id, message_id=1, file_type="photo", thumb_path=None)
    await _seed_file(db_session, ch.id, message_id=2, file_type="photo", thumb_path=None)
    await _seed_file(db_session, ch.id, message_id=3, file_type="photo", thumb_path=None)
    await _seed_file(db_session, ch.id, message_id=4, file_type="video",
                     file_name="v1.mp4", mime_type="video/mp4", thumb_path=None)
    await _seed_file(db_session, ch.id, message_id=5, file_type="video",
                     file_name="v2.mp4", mime_type="video/mp4", thumb_path=None)
    await _seed_file(db_session, ch.id, message_id=6, file_type="document",
                     file_name="doc.pdf", mime_type="application/pdf", thumb_path=None)

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    pool = ThumbnailWorkerPool(num_workers=1, thumb_dir=str(thumb_dir), cache_dir=str(cache_dir))
    set_thumb_worker_pool(pool)

    try:
        created = await _trigger_post_sync_thumbs(db_session, ch.id)
        assert created == 5  # 3 photo + 2 video, document excluded
    finally:
        reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S4: All files already have thumbnails — skip
# ---------------------------------------------------------------------------

async def test_post_sync_all_have_thumbs(db_session, tmp_path: Path):
    """GIVEN all files have thumb_path set WHEN post-sync THEN returns 0, nothing created."""
    ch = await _seed_channel(db_session, tg_id=1004)

    await _seed_file(db_session, ch.id, message_id=1, file_type="photo",
                     thumb_path="thumbnails/1/1.jpg")
    await _seed_file(db_session, ch.id, message_id=2, file_type="photo",
                     thumb_path="thumbnails/1/2.jpg")

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    pool = ThumbnailWorkerPool(num_workers=1, thumb_dir=str(thumb_dir), cache_dir=str(cache_dir))
    set_thumb_worker_pool(pool)

    try:
        created = await _trigger_post_sync_thumbs(db_session, ch.id)
        assert created == 0

        # No jobs should exist
        result = await db_session.execute(select(ThumbJob))
        assert len(result.scalars().all()) == 0
    finally:
        reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S5: Some files already have pending jobs — skip duplicates
# ---------------------------------------------------------------------------

async def test_post_sync_skips_existing_jobs(db_session, tmp_path: Path):
    """GIVEN 5 files need thumbs, 2 have existing pending ThumbJob
    WHEN post-sync
    THEN only 3 new jobs (skip 2 duplicates)."""
    ch = await _seed_channel(db_session, tg_id=1005)

    f1 = await _seed_file(db_session, ch.id, message_id=1, file_type="photo", thumb_path=None)
    f2 = await _seed_file(db_session, ch.id, message_id=2, file_type="photo", thumb_path=None)
    f3 = await _seed_file(db_session, ch.id, message_id=3, file_type="photo", thumb_path=None)
    f4 = await _seed_file(db_session, ch.id, message_id=4, file_type="photo", thumb_path=None)
    f5 = await _seed_file(db_session, ch.id, message_id=5, file_type="photo", thumb_path=None)

    # Create 2 existing pending ThumbJobs
    j1 = ThumbJob(id=str(uuid.uuid4()), file_id=f1.id, file_name="1.jpg",
                  mime_type="image/jpeg", status="pending")
    j2 = ThumbJob(id=str(uuid.uuid4()), file_id=f2.id, file_name="2.jpg",
                  mime_type="image/jpeg", status="processing")
    db_session.add_all([j1, j2])
    await db_session.commit()

    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    pool = ThumbnailWorkerPool(num_workers=1, thumb_dir=str(thumb_dir), cache_dir=str(cache_dir))
    set_thumb_worker_pool(pool)

    try:
        created = await _trigger_post_sync_thumbs(db_session, ch.id)
        assert created == 3  # f3, f4, f5

        # Only 3 NEW jobs in queue (f1, f2 have existing)
        queued = []
        while not pool._queue.empty():
            queued.append(pool._queue.get_nowait())
        assert len(queued) == 3
    finally:
        reset_thumb_worker_pool()


# ---------------------------------------------------------------------------
# S6: Worker pool not available — skip
# ---------------------------------------------------------------------------

async def test_post_sync_no_pool(db_session):
    """GIVEN no worker pool registered WHEN post-sync THEN returns 0, logs warning."""
    ch = await _seed_channel(db_session, tg_id=1006)

    await _seed_file(db_session, ch.id, message_id=1, file_type="photo", thumb_path=None)
    await _seed_file(db_session, ch.id, message_id=2, file_type="photo", thumb_path=None)

    # Ensure no pool
    reset_thumb_worker_pool()

    created = await _trigger_post_sync_thumbs(db_session, ch.id)
    assert created == 0

    # No ThumbJob records created
    result = await db_session.execute(select(ThumbJob))
    assert len(result.scalars().all()) == 0


# ---------------------------------------------------------------------------
# Full integration: sync API triggers post-sync
# ---------------------------------------------------------------------------

async def test_sync_triggers_post_sync_thumb(db_session, client: AsyncClient, tmp_path: Path):
    """GIVEN a channel with photo files,
    WHEN sync is triggered and completes,
    THEN ThumbJobs are automatically created (post-sync trigger fires)."""
    import asyncio as aio
    from tests.test_sync_engine import _make_msg_photo

    ch = await _seed_channel(db_session, tg_id=2001)

    svc, mock_client = _make_mock_authorized_svc()
    messages = [_make_msg_photo(i) for i in range(1, 4)]
    _setup_mock_sync(svc, mock_client, messages)
    from services.telegram_client import set_telegram_service
    set_telegram_service(svc)

    # Start worker pool (needed for enqueue)
    cache_dir = tmp_path / "cache"
    thumb_dir = tmp_path / "thumbnails"
    pool = ThumbnailWorkerPool(num_workers=1, thumb_dir=str(thumb_dir), cache_dir=str(cache_dir))
    set_thumb_worker_pool(pool)

    try:
        # Trigger sync
        response = await client.post(f"/api/channels/{ch.id}/sync")
        assert response.status_code == 202
        data = response.json()

        # Wait for background sync + post-sync to complete
        from api.sync import _running_syncs
        bg_task = _running_syncs.get(data["id"])
        if bg_task:
            try:
                await aio.wait_for(bg_task, timeout=10.0)
            except aio.TimeoutError:
                bg_task.cancel()
            except Exception:
                pass  # mock env may cause errors, that's OK

        # Verify ThumbJobs were created (sync inserted files → post-sync created jobs)
        result = await db_session.execute(
            select(ThumbJob).join(FileModel, ThumbJob.file_id == FileModel.id)
            .where(FileModel.channel_id == ch.id)
        )
        jobs = result.scalars().all()
        assert len(jobs) > 0, "Post-sync should have created ThumbJobs for sync'd files"
        for j in jobs:
            assert j.status == "pending"
    finally:
        reset_thumb_worker_pool()
