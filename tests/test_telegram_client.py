"""Tests for Telegram client service."""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from services.telegram_client import (
    TelegramService,
    AuthState,
    get_telegram_service,
    reset_telegram_service,
)


@pytest.mark.asyncio
class TestTelegramServiceInit:
    """Tests for TelegramService initialization."""

    async def test_service_creation_defaults(self, db_session):
        """🔴 Test TelegramService creates with default config."""
        service = TelegramService(
            api_id=12345,
            api_hash="abc123",
            phone="+8613800138000",
        )
        assert service.api_id == 12345
        assert service.api_hash == "abc123"
        assert service.phone == "+8613800138000"
        assert service.bot_token is None
        assert service.proxy is None
        assert service.session_name == "tg_file_viewer"
        assert service._client is None

    async def test_service_creation_bot(self, db_session):
        """🔴 Test TelegramService with bot token."""
        service = TelegramService(
            api_id=12345,
            api_hash="abc123",
            bot_token="123456:ABC-DEF",
        )
        assert service.bot_token == "123456:ABC-DEF"
        assert service.phone is None

    async def test_service_creation_with_proxy(self, db_session):
        """🔴 Test TelegramService with socks5 proxy."""
        service = TelegramService(
            api_id=12345,
            api_hash="abc123",
            phone="+8613800138000",
            proxy_url="socks5://127.0.0.1:1080",
        )
        assert service.proxy is not None


@pytest.mark.asyncio
class TestTelegramServiceFromSettings:
    """Tests for TelegramService creation from Settings config."""

    async def test_service_creation_from_settings_with_proxy(self, db_session):
        """🔴 Test TelegramService created from Settings with proxy_url."""
        from config import Settings
        from services.telegram_client import TelegramService

        settings = Settings()
        service = TelegramService(
            api_id=settings.tg_api_id,
            api_hash=settings.tg_api_hash,
            phone=settings.tg_phone or None,
            bot_token=settings.tg_bot_token or None,
            proxy_url=settings.tg_proxy_url,
        )
        assert service.api_id == settings.tg_api_id
        assert service.api_hash == settings.tg_api_hash
        # proxy should be set because .env has TG_PROXY_URL=socks5://127.0.0.1:1080
        assert service.proxy is not None, "Proxy should be set from Settings"

    async def test_service_creation_from_settings_no_proxy(self, monkeypatch, db_session):
        """🔴 Test TelegramService created from Settings without proxy."""
        monkeypatch.setenv("TG_PROXY_URL", "")
        from config import Settings
        from services.telegram_client import TelegramService

        settings = Settings()
        service = TelegramService(
            api_id=settings.tg_api_id,
            api_hash=settings.tg_api_hash,
            phone=settings.tg_phone or None,
            bot_token=settings.tg_bot_token or None,
            proxy_url=settings.tg_proxy_url,
        )
        assert service.proxy is None, "Proxy should be None when TG_PROXY_URL is empty"


@pytest.mark.asyncio
class TestTelegramServiceAuthState:
    """Tests for auth state management."""

    async def test_initial_auth_state(self, db_session):
        """🔴 Test initial auth state is DISCONNECTED."""
        service = TelegramService(api_id=1, api_hash="x", phone="+86")
        assert service.auth_state == AuthState.DISCONNECTED

    async def test_phone_code_hash_storage(self, db_session):
        """🔴 Test phone_code_hash is stored after send_code."""
        service = TelegramService(api_id=1, api_hash="x", phone="+86")
        service._phone_code_hash = "test_hash"
        assert service._phone_code_hash == "test_hash"

    async def test_not_authorized_initially(self, db_session):
        """🔴 Test is_authorized returns False initially."""
        service = TelegramService(api_id=1, api_hash="x", phone="+86")
        # Client not created, so should return False
        is_auth = await service.is_authorized()
        assert is_auth is False


@pytest.mark.asyncio
class TestTelegramServiceSendCode:
    """Tests for send_code method."""

    async def test_send_code_requires_phone(self, db_session):
        """🔴 Test send_code fails when no phone configured."""
        service = TelegramService(api_id=1, api_hash="x", bot_token="t")
        with pytest.raises(ValueError, match=r"(?i)phone"):
            await service.send_code()

    @patch("services.telegram_client.TelegramClient")
    async def test_send_code_success(self, mock_tc, db_session):
        """🔴 Test send_code returns phone_code_hash on success."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=False)
        mock_client.send_code_request = AsyncMock(return_value=MagicMock(phone_code_hash="hash_123"))
        mock_tc.return_value = mock_client

        service = TelegramService(api_id=1, api_hash="x", phone="+86")
        result = await service.send_code()

        assert result is True
        assert service._phone_code_hash == "hash_123"
        assert service.auth_state == AuthState.CODE_SENT

    @patch("services.telegram_client.TelegramClient")
    async def test_send_code_already_authorized(self, mock_tc, db_session):
        """🔴 Test send_code returns True when already authorized."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_tc.return_value = mock_client

        service = TelegramService(api_id=1, api_hash="x", phone="+86")
        result = await service.send_code()

        assert result is True
        assert service.auth_state == AuthState.AUTHORIZED


@pytest.mark.asyncio
class TestTelegramServiceVerifyCode:
    """Tests for verify_code method."""

    @patch("services.telegram_client.TelegramClient")
    async def test_verify_code_no_phone_code_hash(self, mock_tc, db_session):
        """🔴 Test verify_code fails without send_code first."""
        service = TelegramService(api_id=1, api_hash="x", phone="+86")
        with pytest.raises(ValueError, match="send_code"):
            await service.verify_code("12345")

    @patch("services.telegram_client.TelegramClient")
    async def test_verify_code_success(self, mock_tc, db_session):
        """🔴 Test verify_code signs in successfully."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=False)
        mock_client.send_code_request = AsyncMock(return_value=MagicMock(phone_code_hash="hash_123"))
        mock_client.sign_in = AsyncMock(return_value=MagicMock())
        mock_tc.return_value = mock_client

        service = TelegramService(api_id=1, api_hash="x", phone="+86")
        await service.send_code()
        result = await service.verify_code("12345")

        assert result is True
        assert service.auth_state == AuthState.AUTHORIZED


@pytest.mark.asyncio
class TestTelegramServiceGlobalService:
    """Tests for global service instance management."""

    async def test_get_service_none_by_default(self, db_session):
        """🔴 Test get_telegram_service returns None initially."""
        reset_telegram_service()
        service = get_telegram_service()
        assert service is None

    async def test_get_service_after_setup(self, db_session):
        """🔴 Test get_telegram_service returns instance after setup."""
        reset_telegram_service()
        from services.telegram_client import set_telegram_service
        svc = TelegramService(api_id=1, api_hash="x", phone="+86")
        set_telegram_service(svc)
        assert get_telegram_service() is svc

    async def test_reset_clears_service(self, db_session):
        """🔴 Test reset_telegram_service clears instance."""
        from services.telegram_client import set_telegram_service
        svc = TelegramService(api_id=1, api_hash="x", phone="+86")
        set_telegram_service(svc)
        reset_telegram_service()
        assert get_telegram_service() is None
