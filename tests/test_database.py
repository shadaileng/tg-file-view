"""Tests for database module: engine, session, table creation, CRUD."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_session, engine, Base


@pytest.mark.asyncio
class TestDatabase:
    """Database connection and session tests."""

    async def test_engine_created(self):
        """🔴 Test that SQLAlchemy async engine is created."""
        from database import engine as eng
        assert eng is not None
        assert "aiosqlite" in str(eng.url)

    async def test_session_context_manager(self):
        """🔴 Test that get_session() works as async context manager."""
        s = None
        async with get_session() as session:
            assert isinstance(session, AsyncSession)
            s = session
        # After context exit, session should be closed
        # (aiosqlite sessions don't expose is_active reliably, but no error is good)

    async def test_tables_created(self, db_session):
        """🔴 Test that all tables exist after init_db()."""
        # channels, files, sync_tasks, thumb_jobs, app_config
        result = await db_session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = [row[0] for row in result.fetchall()]
        expected = {"channels", "files", "sync_tasks", "thumb_jobs", "app_config"}
        assert expected.issubset(set(tables)), f"Missing tables: {expected - set(tables)}"


@pytest.mark.asyncio
class TestCRUD:
    """Basic CRUD operations on each table."""

    async def test_insert_channel(self, db_session):
        """🔴 Test inserting a channel record."""
        from models import Channel
        ch = Channel(
            tg_id=-1001234567890,
            username="test_channel",
            title="Test Channel",
            file_count=0,
            total_size=0,
        )
        db_session.add(ch)
        await db_session.commit()

        from sqlalchemy import select
        result = await db_session.execute(
            select(Channel).where(Channel.tg_id == -1001234567890)
        )
        saved = result.scalar_one()
        assert saved.username == "test_channel"
        assert saved.title == "Test Channel"
        assert saved.file_count == 0
        assert saved.total_size == 0
        assert saved.last_sync is None

    async def test_insert_file(self, db_session):
        """🔴 Test inserting a file record."""
        from models import File, Channel
        # Need a channel first
        ch = Channel(tg_id=-1001234567890, username="test", title="Test")
        db_session.add(ch)
        await db_session.flush()

        f = File(
            channel_id=ch.id,
            message_id=42,
            file_name="test.pdf",
            file_size=1024,
            mime_type="application/pdf",
            file_type="document",
        )
        db_session.add(f)
        await db_session.commit()

        from sqlalchemy import select
        result = await db_session.execute(select(File).where(File.message_id == 42))
        saved = result.scalar_one()
        assert saved.file_name == "test.pdf"
        assert saved.file_size == 1024
        assert saved.is_cached is False
        assert saved.thumb_type == "auto"

    async def test_insert_sync_task(self, db_session):
        """🔴 Test inserting a sync task record."""
        from models import SyncTask
        import uuid
        task_id = str(uuid.uuid4())
        task = SyncTask(
            id=task_id,
            channel_id=1,
            status="pending",
            total_files=0,
            synced_files=0,
            errors="[]",
        )
        db_session.add(task)
        await db_session.commit()

        from sqlalchemy import select
        result = await db_session.execute(select(SyncTask).where(SyncTask.id == task_id))
        saved = result.scalar_one()
        assert saved.status == "pending"
        assert saved.total_files == 0

    async def test_insert_thumb_job(self, db_session):
        """🔴 Test inserting a thumbnail job."""
        from models import ThumbJob
        import uuid
        job_id = str(uuid.uuid4())
        job = ThumbJob(
            id=job_id,
            file_id=1,
            file_name="test.jpg",
            mime_type="image/jpeg",
            status="pending",
            priority=5,
        )
        db_session.add(job)
        await db_session.commit()

        from sqlalchemy import select
        result = await db_session.execute(select(ThumbJob).where(ThumbJob.id == job_id))
        saved = result.scalar_one()
        assert saved.status == "pending"
        assert saved.priority == 5
        assert saved.attempt == 0
        assert saved.max_retries == 3

    async def test_insert_config(self, db_session):
        """🔴 Test inserting a dynamic config key-value."""
        from models import AppConfig
        cfg = AppConfig(key="sync_batch_size", value="100")
        db_session.add(cfg)
        await db_session.commit()

        from sqlalchemy import select
        result = await db_session.execute(
            select(AppConfig).where(AppConfig.key == "sync_batch_size")
        )
        saved = result.scalar_one()
        assert saved.value == "100"
