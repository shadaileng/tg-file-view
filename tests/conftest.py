"""pytest fixtures for tg_file_viewer tests."""
import os
import sys
import pytest
import pytest_asyncio
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Override data dir for tests
TEST_DATA_DIR = Path(__file__).parent / "test_data"
os.environ["TG_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["TG_DB_PATH"] = str(TEST_DATA_DIR / "test.db")

# Create test data directories upfront
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
(TEST_DATA_DIR / "thumbnails").mkdir(parents=True, exist_ok=True)
(TEST_DATA_DIR / "cache").mkdir(parents=True, exist_ok=True)

# Import database module (engine created once)
from database import init_db, get_session, engine, AsyncSessionLocal
import models  # noqa: F401 - register all ORM models before table creation


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for async fixtures."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def _engine_cleanup():
    """Run once: init db, then cleanup after all tests."""
    await init_db(drop_first=True)
    yield
    await engine.dispose()
    # Remove test data dir
    if TEST_DATA_DIR.exists():
        for f in TEST_DATA_DIR.rglob("*"):
            if f.is_file():
                f.unlink()
        for d in sorted(TEST_DATA_DIR.rglob("*"), reverse=True):
            if d.is_dir():
                d.rmdir()
        TEST_DATA_DIR.rmdir()


@pytest_asyncio.fixture(autouse=True)
async def _reset_tables(_engine_cleanup):
    """Drop and recreate all tables before each test."""
    from database import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def db_session():
    """Provide an async database session for tests."""
    async with get_session() as session:
        yield session
