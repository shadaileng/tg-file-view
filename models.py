"""SQLAlchemy ORM models for tg_file_viewer."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    BigInteger,
    String,
    Text,
    Boolean,
    Float,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Channel(Base):
    """Telegram channel metadata."""
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    file_count: Mapped[int] = mapped_column(default=0)
    total_size: Mapped[int] = mapped_column(BigInteger, default=0)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    files = relationship("File", back_populates="channel", cascade="all, delete-orphan")


class File(Base):
    """Telegram file metadata."""
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("channels.id"), nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    file_type: Mapped[str] = mapped_column(
        String(50), default="document"
    )  # photo, video, audio, document, sticker, voice, etc.
    thumb_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumb_type: Mapped[str] = mapped_column(String(20), default="auto")  # auto, manual
    cache_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_cached: Mapped[bool] = mapped_column(default=False)
    cached_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # when file was first cached
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # last access for LRU eviction
    tg_ref: Mapped[str | None] = mapped_column(Text, nullable=True)  # telethon file reference
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    channel = relationship("Channel", back_populates="files")

    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", name="uq_channel_message"),
    )


class SyncTask(Base):
    """Synchronization task tracking."""
    __tablename__ = "sync_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, running, completed, failed
    total_files: Mapped[int] = mapped_column(default=0)
    synced_files: Mapped[int] = mapped_column(default=0)
    skipped_files: Mapped[int] = mapped_column(default=0)
    phase: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending / connecting / scanning / inserting / finalizing / completed / failed / cancelled
    progress: Mapped[int] = mapped_column(
        Integer, default=0
    )  # 0-100
    errors: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ThumbJob(Base):
    """Thumbnail generation job tracking."""
    __tablename__ = "thumb_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, processing, completed, failed, cancelled
    priority: Mapped[int] = mapped_column(default=5)  # 1-10, 1 highest
    strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attempt: Mapped[int] = mapped_column(default=0)
    max_retries: Mapped[int] = mapped_column(default=3)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AppConfig(Base):
    """Dynamic configuration key-value store."""
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
