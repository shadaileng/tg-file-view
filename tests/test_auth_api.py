"""Tests for auth API endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from main import app


@pytest.fixture
async def client():
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _make_mock_svc():
    """Create a mock TelegramService for testing."""
    svc = AsyncMock()
    svc.auth_state = MagicMock()
    svc.auth_state.value = "DISCONNECTED"
    svc.is_authorized = AsyncMock(return_value=False)
    svc.send_code = AsyncMock()
    svc.verify_code = AsyncMock()
    svc.verify_2fa = AsyncMock()
    svc.logout = AsyncMock(return_value=True)
    return svc


@pytest.mark.asyncio
class TestAuthSendCode:
    """Tests for POST /api/auth/send-code."""

    async def test_send_code_no_service(self, client):
        """🔴 Test send-code returns 400 when no Telegram service."""
        from services.telegram_client import reset_telegram_service
        reset_telegram_service()
        response = await client.post("/api/auth/send-code")
        assert response.status_code == 400
        data = response.json()
        assert "not configured" in data["detail"].lower()

    async def test_send_code_service_not_ready(self, client):
        """🔴 Test send-code returns 400 when service can't send code."""
        from services.telegram_client import set_telegram_service
        svc = _make_mock_svc()
        svc.send_code = AsyncMock(side_effect=ValueError("NO_PHONE"))
        set_telegram_service(svc)

        response = await client.post("/api/auth/send-code")
        assert response.status_code == 400

    async def test_send_code_success(self, client):
        """🔴 Test send-code returns 200 on success."""
        from services.telegram_client import set_telegram_service
        svc = _make_mock_svc()
        svc.send_code = AsyncMock(return_value=True)
        svc.auth_state.value = "code_sent"
        set_telegram_service(svc)

        response = await client.post("/api/auth/send-code")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "code_sent"


@pytest.mark.asyncio
class TestAuthVerifyCode:
    """Tests for POST /api/auth/verify-code."""

    async def test_verify_code_no_service(self, client):
        """🔴 Test verify-code returns 400 when no Telegram service."""
        from services.telegram_client import reset_telegram_service
        reset_telegram_service()
        response = await client.post("/api/auth/verify-code", json={"code": "12345"})
        assert response.status_code == 400

    async def test_verify_code_success(self, client):
        """🔴 Test verify-code returns 200 on success."""
        from services.telegram_client import set_telegram_service
        svc = _make_mock_svc()
        svc.verify_code = AsyncMock(return_value=True)
        svc.auth_state.value = "authorized"
        set_telegram_service(svc)

        response = await client.post("/api/auth/verify-code", json={"code": "12345"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "authorized"

    async def test_verify_code_needs_2fa(self, client):
        """🔴 Test verify-code returns 200 with 2fa_required status."""
        from services.telegram_client import set_telegram_service
        svc = _make_mock_svc()
        svc.verify_code = AsyncMock(return_value="2FA_REQUIRED")
        set_telegram_service(svc)

        response = await client.post("/api/auth/verify-code", json={"code": "12345"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "2fa_required"


@pytest.mark.asyncio
class TestAuthStatus:
    """Tests for GET /api/auth/status."""

    async def test_status_no_service(self, client):
        """🔴 Test status returns not_configured when no service."""
        from services.telegram_client import reset_telegram_service
        reset_telegram_service()
        response = await client.get("/api/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_configured"

    async def test_status_authorized(self, client):
        """🔴 Test status returns authorized."""
        from services.telegram_client import set_telegram_service
        svc = _make_mock_svc()
        svc.is_authorized = AsyncMock(return_value=True)
        svc.auth_state.value = "authorized"
        set_telegram_service(svc)

        response = await client.get("/api/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "authorized"
        assert data["is_authorized"] is True


@pytest.mark.asyncio
class TestAuthLogout:
    """Tests for POST /api/auth/logout."""

    async def test_logout_success(self, client):
        """🔴 Test logout returns success."""
        from services.telegram_client import set_telegram_service
        svc = _make_mock_svc()
        svc.logout = AsyncMock(return_value=True)
        set_telegram_service(svc)

        response = await client.post("/api/auth/logout")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged_out"
