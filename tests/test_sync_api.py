"""Tests for the sync API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from models import Channel, SyncTask, File
from config import Settings


# ---------------------------------------------------------------------------
# Helper: seed data
# ---------------------------------------------------------------------------

async def _seed_channel(db_session: AsyncSession, tg_id: int = 111,
                        title: str = "Test Channel") -> Channel:
    ch = Channel(tg_id=tg_id, username="test_ch", title=title)
    db_session.add(ch)
    await db_session.commit()
    await db_session.refresh(ch)
    return ch


async def _seed_sync_task(db_session: AsyncSession, channel_id: int,
                          status: str = "completed") -> SyncTask:
    t = SyncTask(channel_id=channel_id, status=status,
                 total_files=10, synced_files=8, skipped_files=2)
    db_session.add(t)
    await db_session.commit()
    await db_session.refresh(t)
    return t


# ---------------------------------------------------------------------------
# Helper: mock authorized Telegram service
# ---------------------------------------------------------------------------

def _make_mock_authorized_svc() -> MagicMock:
    """Create a mock Telegram service that appears authorized."""
    from services.telegram_client import AuthState
    svc = AsyncMock()
    svc.auth_state = AuthState.AUTHORIZED
    svc.is_authorized = AsyncMock(return_value=True)
    mock_client = AsyncMock()
    svc.get_client = AsyncMock(return_value=mock_client)
    return svc, mock_client


def _setup_mock_sync(svc, mock_client, messages=None):
    """Configure mock for a sync operation (iter_messages)."""
    from tests.test_sync_engine import _AsyncIter
    mock_client.get_entity = AsyncMock(return_value=MagicMock())
    if messages is not None:
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter(messages))
    else:
        mock_client.iter_messages = MagicMock(return_value=_AsyncIter([]))


# ---------------------------------------------------------------------------
# Test client fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    """Async HTTP test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestTriggerSync:
    """POST /api/channels/{channel_id}/sync"""

    async def test_sync_channel_not_found(self, db_session: AsyncSession, client: AsyncClient):
        """S6: Channel not in DB returns 404."""
        svc, _ = _make_mock_authorized_svc()
        from services.telegram_client import set_telegram_service
        set_telegram_service(svc)

        response = await client.post("/api/channels/99999/sync")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_sync_unauthorized(self, db_session: AsyncSession, client: AsyncClient):
        """S7: Unauthorized returns 400."""
        ch = await _seed_channel(db_session, tg_id=222)

        svc = AsyncMock()
        from services.telegram_client import AuthState
        svc.auth_state = AuthState.DISCONNECTED
        from services.telegram_client import set_telegram_service
        set_telegram_service(svc)

        response = await client.post(f"/api/channels/{ch.id}/sync")
        assert response.status_code == 400
        assert "not authorized" in response.json()["detail"].lower()

    async def test_sync_triggered(self, db_session: AsyncSession, client: AsyncClient):
        """S1: Trigger sync returns 202 with task_id."""
        import asyncio
        from tests.test_sync_engine import _make_msg_photo

        ch = await _seed_channel(db_session, tg_id=333)
        svc, mock_client = _make_mock_authorized_svc()
        messages = [_make_msg_photo(i) for i in range(1, 6)]
        _setup_mock_sync(svc, mock_client, messages)

        from services.telegram_client import set_telegram_service
        set_telegram_service(svc)

        response = await client.post(f"/api/channels/{ch.id}/sync")
        assert response.status_code == 202
        data = response.json()
        assert "id" in data
        assert data["channel_id"] == ch.id

        # Wait for background sync to complete (avoids DB lock on teardown)
        from api.sync import _running_syncs
        bg_task = _running_syncs.get(data["id"])
        if bg_task:
            try:
                await asyncio.wait_for(bg_task, timeout=5.0)
            except asyncio.TimeoutError:
                bg_task.cancel()
            except Exception:
                pass  # sync may fail in mock env, that's OK

    async def test_sync_already_running(self, db_session: AsyncSession, client: AsyncClient):
        """S8: Concurrent sync returns 409."""
        ch = await _seed_channel(db_session, tg_id=444)
        await _seed_sync_task(db_session, ch.id, status="running")

        svc, _ = _make_mock_authorized_svc()
        from services.telegram_client import set_telegram_service
        set_telegram_service(svc)

        response = await client.post(f"/api/channels/{ch.id}/sync")
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"].lower()


@pytest.mark.asyncio
class TestListSyncTasks:
    """GET /api/channels/{channel_id}/sync/tasks"""

    async def test_list_empty(self, db_session: AsyncSession, client: AsyncClient):
        """Channel with no sync tasks returns empty list."""
        ch = await _seed_channel(db_session, tg_id=555)
        response = await client.get(f"/api/channels/{ch.id}/sync/tasks")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_with_tasks(self, db_session: AsyncSession, client: AsyncClient):
        """S4: Channel with sync tasks returns them."""
        ch = await _seed_channel(db_session, tg_id=666)
        t1 = await _seed_sync_task(db_session, ch.id, status="completed")
        t2 = await _seed_sync_task(db_session, ch.id, status="failed")

        response = await client.get(f"/api/channels/{ch.id}/sync/tasks")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] in (t1.id, t2.id)
        assert data[1]["id"] in (t1.id, t2.id)
        # Verify each has expected fields
        for item in data:
            assert "status" in item
            assert "synced_files" in item
            assert "total_files" in item

    async def test_list_channel_not_found(self, db_session: AsyncSession, client: AsyncClient):
        """Non-existent channel returns 404."""
        response = await client.get("/api/channels/99999/sync/tasks")
        assert response.status_code == 404


@pytest.mark.asyncio
class TestGetSyncTask:
    """GET /api/sync/tasks/{task_id}"""

    async def test_get_task(self, db_session: AsyncSession, client: AsyncClient):
        """S5: Get specific sync task."""
        ch = await _seed_channel(db_session, tg_id=777)
        t = await _seed_sync_task(db_session, ch.id, status="completed")

        response = await client.get(f"/api/sync/tasks/{t.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == t.id
        assert data["channel_id"] == ch.id
        assert data["status"] == "completed"
        assert data["total_files"] == 10
        assert data["synced_files"] == 8
        assert data["skipped_files"] == 2

    async def test_get_task_not_found(self, db_session: AsyncSession, client: AsyncClient):
        """Non-existent task returns 404."""
        response = await client.get("/api/sync/tasks/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
class TestCancelSyncTask:
    """POST /api/sync/tasks/{task_id}/cancel"""

    async def test_cancel_running_task(self, db_session: AsyncSession, client: AsyncClient):
        """S9: Cancel a running task."""
        ch = await _seed_channel(db_session, tg_id=888)
        t = await _seed_sync_task(db_session, ch.id, status="running")

        response = await client.post(f"/api/sync/tasks/{t.id}/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == t.id
        assert data["status"] == "cancelled"

    async def test_cancel_non_running_task(self, db_session: AsyncSession, client: AsyncClient):
        """S10: Cancel completed/failed task returns 400."""
        ch = await _seed_channel(db_session, tg_id=999)
        t = await _seed_sync_task(db_session, ch.id, status="completed")

        response = await client.post(f"/api/sync/tasks/{t.id}/cancel")
        assert response.status_code == 400
        assert "not running" in response.json()["detail"].lower()

    async def test_cancel_task_not_found(self, db_session: AsyncSession, client: AsyncClient):
        """Non-existent task returns 404."""
        response = await client.post("/api/sync/tasks/nonexistent-id/cancel")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
