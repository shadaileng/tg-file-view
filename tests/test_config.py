"""Tests for config module: Settings, DB-backed config, priority."""
import os
import pytest


@pytest.mark.asyncio
class TestConfig:
    """Configuration management tests."""

    async def test_settings_from_env(self, db_session):
        """🔴 Test Settings loads from environment variables."""
        from config import Settings
        s = Settings()
        assert s.tg_api_id >= 0
        assert hasattr(s, "tg_api_hash")
        assert s.sync_batch_size > 0
        assert s.host == "0.0.0.0"
        assert s.port == 8000

    async def test_settings_field_types(self):
        """🔴 Test Settings field types are correct."""
        from config import Settings
        s = Settings()
        assert isinstance(s.tg_api_id, int)
        assert isinstance(s.tg_api_hash, str)
        assert isinstance(s.sync_batch_size, int)
        assert isinstance(s.sync_delay_seconds, float)
        assert isinstance(s.debug, bool)
        # Default-like assertions on non-TG-specific fields
        assert s.sync_batch_size > 0
        assert s.sync_delay_seconds >= 0

    async def test_settings_env_override(self, monkeypatch):
        """🔴 Test environment variables override defaults."""
        monkeypatch.setenv("TG_SYNC_BATCH_SIZE", "1234")
        monkeypatch.setenv("TG_DEBUG", "true")
        from config import Settings
        s = Settings()
        assert s.sync_batch_size == 1234
        assert s.debug is True

    async def test_get_config_value_from_db(self, db_session):
        """🔴 Test reading config value from database."""
        from config import get_config_value
        from models import AppConfig

        cfg = AppConfig(key="test_key", value="test_value")
        db_session.add(cfg)
        await db_session.commit()

        value = await get_config_value("test_key")
        assert value == "test_value"

    async def test_get_config_value_not_found(self, db_session):
        """🔴 Test reading non-existent config returns None."""
        from config import get_config_value
        value = await get_config_value("nonexistent")
        assert value is None

    async def test_set_config_value(self, db_session):
        """🔴 Test upserting a config value."""
        from config import set_config_value, get_config_value

        await set_config_value("dynamic_key", "dynamic_val")
        value = await get_config_value("dynamic_key")
        assert value == "dynamic_val"

        # Update same key
        await set_config_value("dynamic_key", "updated_val")
        value = await get_config_value("dynamic_key")
        assert value == "updated_val"

    async def test_get_settings_from_db(self, db_session):
        """🔴 Test get_settings() merges DB values into Settings."""
        from config import get_settings, set_config_value

        await set_config_value("sync_batch_size", "999")
        settings = await get_settings(db_session)
        assert settings.sync_batch_size == 999

    async def test_db_overrides_env(self, db_session, monkeypatch):
        """🔴 Test DB value takes priority over env value."""
        monkeypatch.setenv("TG_SYNC_BATCH_SIZE", "1111")
        from config import get_settings, set_config_value

        await set_config_value("sync_batch_size", "2222")
        settings = await get_settings(db_session)
        # DB should win
        assert settings.sync_batch_size == 2222

    async def test_is_admin(self, db_session):
        """🔴 Test admin password validation."""
        from config import is_admin
        # With default empty password, any check should pass
        assert await is_admin("") is True
        assert await is_admin(None) is True

    async def test_proxy_url_from_env(self, monkeypatch):
        """🔴 Test tg_proxy_url loads from environment variable."""
        monkeypatch.setenv("TG_PROXY_URL", "socks5://192.168.1.1:9999")
        from config import Settings
        s = Settings()
        assert s.tg_proxy_url == "socks5://192.168.1.1:9999"

    async def test_proxy_url_default_none(self, monkeypatch):
        """🔴 Test tg_proxy_url defaults to None when env var is empty."""
        monkeypatch.setenv("TG_PROXY_URL", "")
        from config import Settings
        s = Settings()
        assert s.tg_proxy_url is None or s.tg_proxy_url == ""

    async def test_ensure_initialized(self, db_session):
        """🔴 Test ensure_initialized seeds DB from .env."""
        from config import ensure_initialized
        from models import AppConfig
        from sqlalchemy import select

        await ensure_initialized(db_session)

        # Check that seed values were written
        result = await db_session.execute(
            select(AppConfig).where(AppConfig.key == "api_id")
        )
        row = result.scalar_one_or_none()
        assert row is not None, "api_id should be seeded"
        assert row.value is not None and row.value != ""
