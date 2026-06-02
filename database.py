"""SQLAlchemy async engine and session for SQLite."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Determine database path
DB_PATH = os.environ.get("TG_DB_PATH", "./data/db.sqlite")
# Ensure parent directory exists
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# Async SQLite engine
engine = create_async_engine(
    f"sqlite+aiosqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base for all models."""
    pass


async def _migrate_schema():
    """Run additive schema migrations for new columns added to existing models.

    SQLAlchemy's create_all() only creates tables, it never alters existing
    ones.  This function applies ALTER TABLE ADD COLUMN for each new field
    that was added since the last deployment.
    """
    # Migration: 2026-06-02 — add phase/progress to sync_tasks
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE sync_tasks ADD COLUMN phase VARCHAR(20) NOT NULL DEFAULT 'pending'")
            )
            await conn.execute(
                text("ALTER TABLE sync_tasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
            )
        logger.info("Migration: added phase/progress columns to sync_tasks")
    except Exception:
        # Columns already exist (duplicate migration or fresh create_all)
        pass


async def init_db(drop_first: bool = False):
    """Initialize database: create all tables and run schema migrations."""
    logger.info("Initializing database at {} (drop_first={})", DB_PATH, drop_first)
    try:
        async with engine.begin() as conn:
            if drop_first:
                await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        # Run schema migrations (adds columns for model updates)
        await _migrate_schema()
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error("Database initialization failed: {}", e)
        raise


@asynccontextmanager
async def get_session() -> AsyncSession:
    """Return a new async database session as context manager."""
    async with AsyncSessionLocal() as session:
        yield session


# Provide FastAPI dependency
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
