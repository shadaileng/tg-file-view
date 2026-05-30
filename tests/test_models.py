"""Tests for SQLAlchemy models: field validation and constraints."""
import pytest
import uuid
from models import Channel, File, SyncTask, ThumbJob, AppConfig


@pytest.mark.asyncio
class TestChannelModel:
    """Channel model tests."""

    async def test_channel_creation(self, db_session):
        """🔴 Test Channel model creates with valid fields."""
        ch = Channel(
            tg_id=-1001234567890,
            username="my_channel",
            title="My Channel",
            file_count=5,
            total_size=1024000,
        )
        db_session.add(ch)
        await db_session.commit()
        assert ch.id is not None
        assert ch.tg_id == -1001234567890
        assert ch.username == "my_channel"
        assert ch.file_count == 5

    async def test_channel_tg_id_unique(self, db_session):
        """🔴 Test duplicate tg_id raises integrity error."""
        from sqlalchemy.exc import IntegrityError

        ch1 = Channel(tg_id=-1001234567890, username="ch1", title="Channel 1")
        ch2 = Channel(tg_id=-1001234567890, username="ch2", title="Channel 2")
        db_session.add(ch1)
        await db_session.commit()
        db_session.add(ch2)
        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_channel_defaults(self, db_session):
        """🔴 Test Channel default values."""
        ch = Channel(tg_id=-1009876543210, username="def_ch", title="Default")
        db_session.add(ch)
        await db_session.commit()
        assert ch.file_count == 0
        assert ch.total_size == 0
        assert ch.last_sync is None


@pytest.mark.asyncio
class TestFileModel:
    """File model tests."""

    async def test_file_creation(self, db_session):
        """🔴 Test File model basic creation."""
        ch = Channel(tg_id=-1001234567890, username="ch", title="CH")
        db_session.add(ch)
        await db_session.flush()

        f = File(
            channel_id=ch.id,
            message_id=100,
            file_name="video.mp4",
            file_size=5000000,
            mime_type="video/mp4",
            file_type="video",
        )
        db_session.add(f)
        await db_session.commit()
        assert f.id is not None
        assert f.file_type == "video"
        assert f.is_cached is False

    async def test_file_defaults(self, db_session):
        """🔴 Test File default values."""
        ch = Channel(tg_id=-1001234567890, username="ch", title="CH")
        db_session.add(ch)
        await db_session.flush()

        f = File(channel_id=ch.id, message_id=200, file_name="doc.pdf", file_size=1000)
        db_session.add(f)
        await db_session.commit()
        assert f.mime_type == "application/octet-stream"
        assert f.file_type == "document"
        assert f.thumb_type == "auto"
        assert f.is_cached is False

    async def test_file_tg_ref_default(self, db_session):
        """🔴 Test tg_ref is None by default (set during sync)."""
        ch = Channel(tg_id=-1001234567890, username="ch", title="CH")
        db_session.add(ch)
        await db_session.flush()

        f = File(channel_id=ch.id, message_id=42, file_name="f.txt", file_size=10)
        db_session.add(f)
        await db_session.commit()
        # tg_ref is set by sync engine, default is None
        assert f.tg_ref is None

        # But we can set it manually
        f.tg_ref = "telethon:-1001234567890:42"
        await db_session.commit()
        from sqlalchemy import select
        result = await db_session.execute(select(File).where(File.id == f.id))
        reloaded = result.scalar_one()
        assert reloaded.tg_ref == "telethon:-1001234567890:42"


@pytest.mark.asyncio
class TestSyncTaskModel:
    """SyncTask model tests."""

    async def test_sync_task_creation(self, db_session):
        """🔴 Test SyncTask model creation."""
        task_id = str(uuid.uuid4())
        t = SyncTask(
            id=task_id,
            channel_id=1,
            status="pending",
            total_files=100,
            synced_files=0,
            errors="[]",
        )
        db_session.add(t)
        await db_session.commit()
        assert t.id == task_id
        assert t.status == "pending"

    async def test_sync_task_defaults(self, db_session):
        """🔴 Test SyncTask defaults."""
        task_id = str(uuid.uuid4())
        t = SyncTask(id=task_id, channel_id=1, status="running")
        db_session.add(t)
        await db_session.commit()
        assert t.total_files == 0
        assert t.synced_files == 0
        assert t.skipped_files == 0
        assert t.errors == "[]"


@pytest.mark.asyncio
class TestThumbJobModel:
    """ThumbJob model tests."""

    async def test_thumb_job_creation(self, db_session):
        """🔴 Test ThumbJob creation."""
        job_id = str(uuid.uuid4())
        j = ThumbJob(
            id=job_id,
            file_id=1,
            file_name="img.jpg",
            mime_type="image/jpeg",
            status="pending",
            priority=3,
        )
        db_session.add(j)
        await db_session.commit()
        assert j.id == job_id
        assert j.priority == 3
        assert j.status == "pending"

    async def test_thumb_job_defaults(self, db_session):
        """🔴 Test ThumbJob defaults."""
        job_id = str(uuid.uuid4())
        j = ThumbJob(
            id=job_id, file_id=1, file_name="v.mp4", mime_type="video/mp4", status="pending"
        )
        db_session.add(j)
        await db_session.commit()
        assert j.priority == 5
        assert j.attempt == 0
        assert j.max_retries == 3


@pytest.mark.asyncio
class TestAppConfigModel:
    """AppConfig model tests."""

    async def test_app_config_creation(self, db_session):
        """🔴 Test AppConfig creation."""
        c = AppConfig(key="my_key", value="my_value")
        db_session.add(c)
        await db_session.commit()
        assert c.key == "my_key"
        assert c.value == "my_value"

    async def test_app_config_key_unique(self, db_session):
        """🔴 Test duplicate key raises integrity error."""
        from sqlalchemy.exc import IntegrityError

        c1 = AppConfig(key="unique_key", value="v1")
        c2 = AppConfig(key="unique_key", value="v2")
        db_session.add(c1)
        await db_session.commit()
        db_session.add(c2)
        with pytest.raises(IntegrityError):
            await db_session.commit()
