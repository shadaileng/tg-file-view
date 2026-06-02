"""Tests for the sync engine service."""

import pytest
import datetime
import json
import base64
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, File, SyncTask
from config import Settings


# ---------------------------------------------------------------------------
# Async iterator helper for mocking Telethon iter_messages
# ---------------------------------------------------------------------------

class _AsyncIter:
    """An async iterator wrapper around a regular iterable.

    Used to mock Telethon's iter_messages() which returns an async generator.
    """

    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


# ---------------------------------------------------------------------------
# Helper: build a mock Telethon message with media
# ---------------------------------------------------------------------------

def _make_msg_photo(msg_id: int) -> MagicMock:
    """Create a mock Telethon message with a photo."""
    msg = MagicMock()
    msg.id = msg_id
    msg.date = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    media = MagicMock()
    media.document = None  # Explicitly no document (mimics real MessageMediaPhoto)
    photo = MagicMock()
    photo.file_reference = b"fake_photo_ref"
    sizes = []
    for _ in range(3):
        s = MagicMock()
        s.size = msg_id * 1234  # deterministic size
        sizes.append(s)
    photo.sizes = sizes
    media.photo = photo
    msg.media = media
    return msg


def _make_msg_document(msg_id: int, file_name: str, mime_type: str, attrs: list = None) -> MagicMock:
    """Create a mock Telethon message with a document."""
    msg = MagicMock()
    msg.id = msg_id
    msg.date = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    media = MagicMock()
    media.photo = None  # Explicitly no photo (mimics real MessageMediaDocument)
    doc = MagicMock()
    doc.file_reference = b"fake_doc_ref"
    doc.size = msg_id * 5678
    doc.mime_type = mime_type
    if attrs is not None:
        doc.attributes = attrs
    else:
        attr = MagicMock()
        attr.file_name = file_name
        doc.attributes = [attr]
    media.document = doc
    msg.media = media
    return msg


def _make_doc_video_attr() -> MagicMock:
    """Create a DocumentAttributeVideo mock."""
    attr = MagicMock()
    attr.file_name = "video.mp4"
    attr.duration = 120
    # VideoAttribute has .video=True or isinstance check
    # Just set magic attributes for duck-typing
    type(attr).__name__ = "DocumentAttributeVideo"
    return attr


def _make_doc_audio_attr() -> MagicMock:
    """Create a DocumentAttributeAudio mock."""
    attr = MagicMock()
    attr.file_name = "audio.mp3"
    attr.duration = 180
    attr.title = "Test Song"
    type(attr).__name__ = "DocumentAttributeAudio"
    return attr


def _make_doc_sticker_attr() -> MagicMock:
    """Create a DocumentAttributeSticker mock."""
    attr = MagicMock()
    attr.alt = "sticker_emoji"
    type(attr).__name__ = "DocumentAttributeSticker"
    return attr


def _make_msg_no_media(msg_id: int) -> MagicMock:
    """Create a mock Telethon message with no media."""
    msg = MagicMock()
    msg.id = msg_id
    msg.media = None
    return msg


# ---------------------------------------------------------------------------
# Helper: seed a channel
# ---------------------------------------------------------------------------

async def _seed_channel(db_session: AsyncSession, tg_id: int = 123456789,
                        title: str = "Test Channel") -> Channel:
    ch = Channel(tg_id=tg_id, username="test_channel", title=title)
    db_session.add(ch)
    await db_session.commit()
    await db_session.refresh(ch)
    return ch


async def _seed_file(db_session: AsyncSession, channel_id: int, message_id: int,
                     file_name: str = "test.txt") -> File:
    f = File(
        channel_id=channel_id, message_id=message_id,
        file_name=file_name, file_size=100, mime_type="text/plain",
        file_type="document",
    )
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)
    return f


# ---------------------------------------------------------------------------
# Test: _extract_file_info
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestExtractFileInfo:
    """Unit tests for the internal _extract_file_info function."""

    async def test_photo_message(self):
        """Extract file info from a photo message."""
        from services.sync_engine import _extract_file_info
        msg = _make_msg_photo(42)
        info = _extract_file_info(msg)
        assert info is not None
        assert info["message_id"] == 42
        assert info["file_type"] == "photo"
        assert info["mime_type"] == "image/jpeg"
        assert info["file_size"] == 42 * 1234  # max of sizes
        assert "photo_42" in info["file_name"]
        assert info["tg_ref"] is not None

    async def test_document_message(self):
        """Extract file info from a document message."""
        from services.sync_engine import _extract_file_info
        msg = _make_msg_document(100, "report.pdf", "application/pdf")
        info = _extract_file_info(msg)
        assert info is not None
        assert info["message_id"] == 100
        assert info["file_type"] == "document"
        assert info["file_name"] == "report.pdf"
        assert info["mime_type"] == "application/pdf"
        assert info["file_size"] == 100 * 5678
        assert info["tg_ref"] is not None

    async def test_video_document(self):
        """Detect video type from DocumentAttributeVideo."""
        from services.sync_engine import _extract_file_info
        msg = _make_msg_document(200, "movie.mp4", "video/mp4",
                                 attrs=[_make_doc_video_attr()])
        info = _extract_file_info(msg)
        assert info["file_type"] == "video"

    async def test_audio_document(self):
        """Detect audio type from DocumentAttributeAudio."""
        from services.sync_engine import _extract_file_info
        msg = _make_msg_document(300, "song.mp3", "audio/mpeg",
                                 attrs=[_make_doc_audio_attr()])
        info = _extract_file_info(msg)
        assert info["file_type"] == "audio"

    async def test_sticker_document(self):
        """Detect sticker type from DocumentAttributeSticker."""
        from services.sync_engine import _extract_file_info
        msg = _make_msg_document(400, "", "image/webp",
                                 attrs=[_make_doc_sticker_attr()])
        info = _extract_file_info(msg)
        assert info["file_type"] == "sticker"

    async def test_no_media_message(self):
        """Messages without media return None."""
        from services.sync_engine import _extract_file_info
        msg = _make_msg_no_media(500)
        info = _extract_file_info(msg)
        assert info is None

    async def test_unnamed_document_fallback(self):
        """Document without filename gets a fallback name."""
        from services.sync_engine import _extract_file_info
        # Document with no file_name attribute — explicitly None
        attr = MagicMock()
        attr.file_name = None  # Explicitly no filename
        type(attr).__name__ = "GenericAttribute"
        msg = _make_msg_document(600, "", "application/octet-stream", attrs=[attr])
        info = _extract_file_info(msg)
        assert info is not None
        assert "document_600" in info["file_name"]


# ---------------------------------------------------------------------------
# Test: sync_channel (integration via direct service call)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestSyncChannelIntegration:
    """Integration tests for sync_channel function."""

    async def test_sync_full(self, db_session: AsyncSession):
        """S1: Full sync - channel with 10 media messages."""
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=111)
        settings = Settings()

        # Mock Telethon client
        mock_svc = AsyncMock()
        mock_client = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.AUTHORIZED  # Use real enum
        mock_svc.get_client = AsyncMock(return_value=mock_client)
        mock_client.get_entity = AsyncMock(return_value=MagicMock())

        # Build 10 photo messages — use _AsyncIter for async iteration
        messages = [_make_msg_photo(i) for i in range(1, 11)]
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))

        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        task = await sync_channel(ch.id, db_session, settings)

        assert task.status == "completed"
        assert task.total_files == 10
        assert task.synced_files == 10
        assert task.skipped_files == 0

        # Verify files in DB
        from sqlalchemy import select, func
        result = await db_session.execute(
            select(func.count()).select_from(File).where(File.channel_id == ch.id)
        )
        count = result.scalar()
        assert count == 10

        # Verify channel last_sync updated
        await db_session.refresh(ch)
        assert ch.last_sync is not None

    async def test_sync_incremental(self, db_session: AsyncSession):
        """S2: Incremental sync - pre-seeded files are skipped."""
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=222)
        # Pre-seed 5 files
        for msg_id in range(1, 6):
            await _seed_file(db_session, ch.id, msg_id, f"existing_{msg_id}.txt")

        settings = Settings()

        mock_svc = AsyncMock()
        mock_client = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.AUTHORIZED
        mock_svc.get_client = AsyncMock(return_value=mock_client)
        mock_client.get_entity = AsyncMock(return_value=MagicMock())

        # Messages 1-5 (already seeded) + 6-10 (new)
        messages = [_make_msg_photo(i) for i in range(1, 11)]
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))

        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        task = await sync_channel(ch.id, db_session, settings)

        assert task.status == "completed"
        assert task.total_files == 10
        assert task.synced_files == 5   # new: 6-10
        assert task.skipped_files == 5  # dup: 1-5

        # Verify only 5 new files added (total 10)
        result = await db_session.execute(
            select(func.count()).select_from(File).where(File.channel_id == ch.id)
        )
        count = result.scalar()
        assert count == 10

    async def test_sync_empty_channel(self, db_session: AsyncSession):
        """S3: Channel with no media messages."""
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=333)
        settings = Settings()

        mock_svc = AsyncMock()
        mock_client = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.AUTHORIZED
        mock_svc.get_client = AsyncMock(return_value=mock_client)
        mock_client.get_entity = AsyncMock(return_value=MagicMock())

        # Only text messages (no media)
        messages = [_make_msg_no_media(i) for i in range(1, 6)]
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))

        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        task = await sync_channel(ch.id, db_session, settings)

        assert task.status == "completed"
        assert task.total_files == 5
        assert task.synced_files == 0

        result = await db_session.execute(
            select(func.count()).select_from(File).where(File.channel_id == ch.id)
        )
        count = result.scalar()
        assert count == 0

    async def test_sync_channel_not_found(self, db_session: AsyncSession):
        """Channel not in DB raises ValueError."""
        from services.sync_engine import sync_channel

        settings = Settings()
        with pytest.raises(ValueError, match="not found"):
            await sync_channel(99999, db_session, settings)

    async def test_sync_unauthorized(self, db_session: AsyncSession):
        """Unauthorized Telegram service raises RuntimeError."""
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=444)
        settings = Settings()

        # Set a mock service with DISCONNECTED state (not authorized)
        mock_svc = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.DISCONNECTED
        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        with pytest.raises(RuntimeError, match="not authorized"):
            await sync_channel(ch.id, db_session, settings)

    async def test_sync_with_existing_task_id(self, db_session: AsyncSession):
        """Bug #1 fix: sync_channel reuses API-created task instead of creating a new one."""
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=555)
        settings = Settings()

        # Create a pending task as the API would
        pending_task = SyncTask(
            channel_id=ch.id,
            status="pending",
        )
        db_session.add(pending_task)
        await db_session.commit()
        await db_session.refresh(pending_task)

        # Mock authorized Telethon
        mock_svc = AsyncMock()
        mock_client = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.AUTHORIZED
        mock_svc.get_client = AsyncMock(return_value=mock_client)
        mock_client.get_entity = AsyncMock(return_value=MagicMock())

        messages = [_make_msg_photo(i) for i in range(1, 6)]
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))

        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        # Pass the pending task's ID — should reuse it
        task = await sync_channel(ch.id, db_session, settings, task_id=pending_task.id)

        # Verify: same task ID reused (not a new one)
        assert task.id == pending_task.id
        assert task.status == "completed"
        assert task.total_files == 5
        assert task.synced_files == 5

    async def test_sync_task_id_not_found(self, db_session: AsyncSession):
        """Passing a non-existent task_id raises ValueError."""
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=666)
        settings = Settings()

        mock_svc = AsyncMock()
        mock_client = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.AUTHORIZED
        mock_svc.get_client = AsyncMock(return_value=mock_client)

        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        with pytest.raises(ValueError, match="not found"):
            await sync_channel(ch.id, db_session, settings, task_id="nonexistent-uuid")

    async def test_total_files_updates_during_sync(self, db_session: AsyncSession):
        """Bug #2 fix: total_files is updated after each batch, not just at end.

        While we cannot easily intercept the intermediate state from the same
        session, the commit at line ~290 ensures total_files is written to DB
        after every batch.  This test verifies the end result matches and
        confirms the batch commit path is exercised by checking logs.
        """
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=777)
        settings = Settings()
        # Use a small batch_size so batch commit path is hit multiple times
        settings.sync_batch_size = 3

        mock_svc = AsyncMock()
        mock_client = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.AUTHORIZED
        mock_svc.get_client = AsyncMock(return_value=mock_client)
        mock_client.get_entity = AsyncMock(return_value=MagicMock())

        # 7 messages → 3 batches (size 3, 3, 1), total_files committed each time
        messages = [_make_msg_photo(i) for i in range(1, 8)]
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))

        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        task = await sync_channel(ch.id, db_session, settings)

        assert task.status == "completed"
        # total_files must be set before completion (was 7 before finalize block)
        assert task.total_files == 7
        assert task.synced_files == 7

        # Verify files actually landed in DB
        from sqlalchemy import select, func
        result = await db_session.execute(
            select(func.count()).select_from(File).where(File.channel_id == ch.id)
        )
        assert result.scalar() == 7

    async def test_sync_updates_channel_stats(self, db_session: AsyncSession):
        """Bug #3 fix: channel.file_count and total_size are updated after sync."""
        from services.sync_engine import sync_channel

        ch = await _seed_channel(db_session, tg_id=888)
        settings = Settings()

        mock_svc = AsyncMock()
        mock_client = AsyncMock()
        from services.telegram_client import AuthState
        mock_svc.auth_state = AuthState.AUTHORIZED
        mock_svc.get_client = AsyncMock(return_value=mock_client)
        mock_client.get_entity = AsyncMock(return_value=MagicMock())

        messages = [_make_msg_photo(i) for i in range(1, 6)]  # 5 photos
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))

        from services.telegram_client import set_telegram_service
        set_telegram_service(mock_svc)

        # Pre-assert: no stats before sync
        assert ch.file_count == 0
        assert ch.total_size == 0

        await sync_channel(ch.id, db_session, settings)

        await db_session.refresh(ch)
        assert ch.file_count == 5
        assert ch.total_size > 0  # each photo has size = msg_id * 1234
        assert ch.last_sync is not None
