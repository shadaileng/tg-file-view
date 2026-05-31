"""Configuration management: env vars + DB-backed dynamic config."""
import os
from pathlib import Path
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Load .env first
load_dotenv()


class Settings(BaseSettings):
    """Application settings with env var support.

    Priority: DB value > env var > default (when get_settings is used).
    """

    # Telegram
    tg_api_id: int = Field(0, validation_alias="TG_API_ID")
    tg_api_hash: str = Field("", validation_alias="TG_API_HASH")
    tg_phone: str = Field("", validation_alias="TG_PHONE")
    tg_bot_token: str = Field("", validation_alias="TG_BOT_TOKEN")
    tg_proxy_url: Optional[str] = Field(None, validation_alias="TG_PROXY_URL")

    # Data
    tg_data_dir: str = "./data"
    tg_db_path: str = "./data/db.sqlite"

    # Sync
    sync_batch_size: int = 500
    sync_bulk_api_limit: int = 10000
    sync_delay_seconds: float = 1.0

    # Thumbnails
    thumb_max_width: int = 320
    thumb_max_height: int = 240
    thumb_video_chunk_preview_mb: int = 20
    thumb_workers: int = 2

    # Cache
    cache_max_size_mb: int = 0  # 0 = no limit

    # Logging
    tg_log_level: str = Field("INFO", validation_alias="TG_LOG_LEVEL")
    tg_log_file: str = Field("./data/app.log", validation_alias="TG_LOG_FILE")
    tg_log_rotation: str = Field("10 MB", validation_alias="TG_LOG_ROTATION")
    tg_log_retention: int = Field(5, validation_alias="TG_LOG_RETENTION")

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    admin_password: str = ""
    debug: bool = False

    model_config = {
        "env_prefix": "TG_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


# DB-backed config helpers

async def get_config_value(key: str, default: str | None = None) -> Optional[str]:
    """Read a config value from the app_config table."""
    from database import AsyncSessionLocal
    from models import AppConfig

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AppConfig).where(AppConfig.key == key))
        row = result.scalar_one_or_none()
        if row:
            return row.value
        return default


async def set_config_value(key: str, value: str) -> None:
    """Upsert a config value into the app_config table."""
    from database import AsyncSessionLocal
    from models import AppConfig

    async with AsyncSessionLocal() as session:
        existing = await session.get(AppConfig, key)
        if existing:
            existing.value = value
        else:
            session.add(AppConfig(key=key, value=value))
        await session.commit()


async def get_settings(db_session: AsyncSession | None = None) -> Settings:
    """Get settings with DB values merged in (DB > env > default).

    If db_session is provided, use it; otherwise create a new one.
    """
    settings = Settings()

    if db_session is not None:
        from models import AppConfig

        result = await db_session.execute(select(AppConfig))
        rows = result.scalars().all()
    else:
        from database import AsyncSessionLocal
        from models import AppConfig

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AppConfig))
            rows = result.scalars().all()

    # Map config keys to Settings fields
    key_to_field = {
        "api_id": "tg_api_id",
        "api_hash": "tg_api_hash",
        "phone": "tg_phone",
        "bot_token": "tg_bot_token",
        "proxy_url": "tg_proxy_url",
        "sync_batch_size": "sync_batch_size",
        "sync_bulk_api_limit": "sync_bulk_api_limit",
        "sync_delay_seconds": "sync_delay_seconds",
        "thumb_max_width": "thumb_max_width",
        "thumb_max_height": "thumb_max_height",
        "thumb_video_chunk_preview_mb": "thumb_video_chunk_preview_mb",
        "thumb_workers": "thumb_workers",
        "cache_max_size_mb": "cache_max_size_mb",
        "host": "host",
        "port": "port",
        "admin_password": "admin_password",
        "debug": "debug",
    }

    for row in rows:
        field_name = key_to_field.get(row.key)
        if field_name:
            field_type = type(getattr(settings, field_name, ""))
            try:
                if field_type == bool:
                    setattr(settings, field_name, row.value.lower() in ("true", "1", "yes"))
                elif field_type == float:
                    setattr(settings, field_name, float(row.value))
                elif field_type == int:
                    setattr(settings, field_name, int(row.value))
                else:
                    setattr(settings, field_name, row.value)
            except (ValueError, TypeError):
                pass  # Keep default if conversion fails

    return settings


async def ensure_initialized(db_session: AsyncSession) -> None:
    """Seed database with values from .env (only on first run)."""
    from models import AppConfig

    settings = Settings()
    seed_values = {
        "api_id": str(settings.tg_api_id),
        "api_hash": settings.tg_api_hash,
        "phone": settings.tg_phone,
        "bot_token": settings.tg_bot_token,
        "proxy_url": settings.tg_proxy_url or "",
        "sync_batch_size": str(settings.sync_batch_size),
        "sync_bulk_api_limit": str(settings.sync_bulk_api_limit),
        "sync_delay_seconds": str(settings.sync_delay_seconds),
        "thumb_max_width": str(settings.thumb_max_width),
        "thumb_max_height": str(settings.thumb_max_height),
        "thumb_video_chunk_preview_mb": str(settings.thumb_video_chunk_preview_mb),
        "thumb_workers": str(settings.thumb_workers),
        "cache_max_size_mb": str(settings.cache_max_size_mb),
        "host": settings.host,
        "port": str(settings.port),
        "admin_password": settings.admin_password,
        "debug": str(settings.debug).lower(),
    }

    seeded = 0
    for key, value in seed_values.items():
        existing = await db_session.get(AppConfig, key)
        if existing is None:
            db_session.add(AppConfig(key=key, value=value))
            seeded += 1

    await db_session.commit()
    if seeded:
        logger.info("Seeded %d new config entries from .env", seeded)


async def is_admin(password: str | None) -> bool:
    """Check if the given password matches admin password from config."""
    stored = await get_config_value("admin_password")
    if not stored:
        return True  # No password set = open access
    if password is None:
        return False
    return password == stored
