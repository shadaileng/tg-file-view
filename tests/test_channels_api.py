"""Tests for channels CRUD API endpoints.

Coverage mapping:
  S1 (Happy: create by username) → test_create_channel_by_username
  S2 (Happy: list channels)      → test_list_channels
  S3 (Happy: get single channel) → test_get_channel
  S4 (Happy: delete cascade)     → test_delete_channel_cascade
  S5 (Edge: non-existent)        → test_create_channel_not_found
  S6 (Edge: duplicate)           → test_create_channel_duplicate
  S7 (Edge: unauthorized)        → test_create_channel_unauthorized
  S8 (Edge: get non-existent)    → test_get_channel_not_found

Discover:
  S9  (Happy: discover channels)           → test_discover_channels
  S10 (Edge: unauthorized discover)         → test_discover_channels_unauthorized
  S11 (Edge: empty dialogs)                → test_discover_channels_empty
  S12 (Edge: all already tracked)          → test_discover_channels_all_tracked
  S13 (Edge: get_dialogs exception)         → test_discover_channels_dialogs_error

Additional:
  E9 (Edge: delete non-existent) → test_delete_channel_not_found
  E10 (Edge: no service)         → test_create_channel_no_service
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from main import app


@pytest.fixture
async def client():
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _make_mock_authorized_svc():
    """Create a mock TelegramService in AUTHORIZED state."""
    from services.telegram_client import AuthState
    svc = AsyncMock()
    svc.auth_state = AuthState.AUTHORIZED  # real enum value for != comparisons
    svc.is_authorized = AsyncMock(return_value=True)
    # get_client() is synchronous — use MagicMock (not AsyncMock attribute)
    mock_client = AsyncMock()
    svc.get_client = MagicMock(return_value=mock_client)
    return svc, mock_client


def _make_mock_entity(tg_id: int, username: str, title: str):
    """Create a mock Telethon entity with id, username, title."""
    entity = MagicMock()
    entity.id = tg_id
    entity.username = username
    entity.title = title
    return entity


async def _seed_channel(db_session, tg_id: int, username: str, title: str) -> int:
    """Insert a channel and return its id."""
    from models import Channel
    channel = Channel(tg_id=tg_id, username=username, title=title)
    db_session.add(channel)
    await db_session.commit()
    await db_session.refresh(channel)
    return channel.id


async def _seed_file(db_session, channel_id: int, message_id: int, file_name: str):
    """Insert a file linked to a channel."""
    from models import File
    f = File(
        channel_id=channel_id,
        message_id=message_id,
        file_name=file_name,
        file_size=1024,
    )
    db_session.add(f)
    await db_session.commit()


def _make_mock_dialog_entity(tg_id: int, username: str, title: str, *, broadcast: bool = True):
    """Create a mock Telethon dialog entity that passes the channel filter.

    Sets broadcast=True and provides id/username/title attributes to mimic
    a Telegram Channel/Supergroup entity.
    """
    entity = MagicMock()
    entity.id = tg_id
    entity.username = username
    entity.title = title
    entity.broadcast = broadcast  # True → recognized as channel
    entity.megagroup = False
    return entity


def _make_mock_dialog(entity: MagicMock):
    """Wrap an entity in a mock dialog object (has .entity attribute)."""
    dialog = MagicMock()
    dialog.entity = entity
    return dialog


def _setup_discover_dialogs(mock_client, entities: list):
    """Configure mock_client.iter_dialogs to return the given entities."""
    dialogs = [_make_mock_dialog(e) for e in entities]

    class _AsyncIter:
        def __init__(self, items):
            self._items = items
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx >= len(self._items):
                raise StopAsyncIteration
            item = self._items[self._idx]
            self._idx += 1
            return item

    mock_client.iter_dialogs = MagicMock(return_value=_AsyncIter(dialogs))


# ──────────────── S1: Happy Path — create by username ────────────────

@pytest.mark.asyncio
class TestCreateChannel:
    """Tests for POST /api/channels."""

    async def test_create_channel_by_username(self, client, db_session):
        """S1: create a channel by username with authorized Telegram client."""
        from services.telegram_client import set_telegram_service
        svc, mock_client = _make_mock_authorized_svc()
        mock_client.get_entity.return_value = _make_mock_entity(
            tg_id=123456789, username="test_channel", title="Test Channel"
        )
        set_telegram_service(svc)

        response = await client.post("/api/channels", json={"username": "test_channel"})
        assert response.status_code == 201
        data = response.json()
        assert data["tg_id"] == 123456789
        assert data["username"] == "test_channel"
        assert data["title"] == "Test Channel"
        assert data["file_count"] == 0
        assert data["total_size"] == 0
        assert data["last_sync"] is None
        assert "id" in data

    async def test_create_channel_by_tg_id(self, client, db_session):
        """Create a channel by tg_id (complementary to S1)."""
        from services.telegram_client import set_telegram_service
        svc, mock_client = _make_mock_authorized_svc()
        mock_client.get_entity.return_value = _make_mock_entity(
            tg_id=987654321, username="another_ch", title="Another Channel"
        )
        set_telegram_service(svc)

        response = await client.post("/api/channels", json={"tg_id": 987654321})
        assert response.status_code == 201
        data = response.json()
        assert data["tg_id"] == 987654321
        assert data["username"] == "another_ch"

    # ──────────────── S5: Edge — non-existent channel ────────────────

    async def test_create_channel_not_found(self, client, db_session):
        """S5: creating a non-existent channel returns 404."""
        from services.telegram_client import set_telegram_service
        svc, mock_client = _make_mock_authorized_svc()
        mock_client.get_entity.side_effect = ValueError("Cannot find any entity")
        set_telegram_service(svc)

        response = await client.post(
            "/api/channels", json={"username": "no_such_channel_xyz"}
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    # ──────────────── S6: Edge — duplicate channel ────────────────

    async def test_create_channel_duplicate(self, client, db_session):
        """S6: adding a duplicate channel returns 409."""
        from services.telegram_client import set_telegram_service
        await _seed_channel(db_session, tg_id=111111, username="dup_ch", title="Dup")

        svc, mock_client = _make_mock_authorized_svc()
        mock_client.get_entity.return_value = _make_mock_entity(
            tg_id=111111, username="dup_ch", title="Dup"
        )
        set_telegram_service(svc)

        response = await client.post("/api/channels", json={"username": "dup_ch"})
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    # ──────────────── S7: Edge — unauthorized client ────────────────

    async def test_create_channel_unauthorized(self, client, db_session):
        """S7: creating channel with non-authorized client returns 400."""
        from services.telegram_client import set_telegram_service, AuthState
        svc = AsyncMock()
        svc.get_client = AsyncMock()
        svc.auth_state = AuthState.DISCONNECTED
        set_telegram_service(svc)

        response = await client.post("/api/channels", json={"username": "any"})
        assert response.status_code == 400
        assert "not authorized" in response.json()["detail"].lower()

    # ──────────────── Additional Edge: no service ────────────────

    async def test_create_channel_no_service(self, client, db_session):
        """E10: creating channel with no Telegram service returns 400."""
        from services.telegram_client import reset_telegram_service
        reset_telegram_service()

        response = await client.post("/api/channels", json={"username": "any"})
        assert response.status_code == 400
        assert "not configured" in response.json()["detail"].lower()

    # ──────────────── Validation: both fields ────────────────

    async def test_create_channel_both_fields(self, client, db_session):
        """Providing both username and tg_id returns 422."""
        from services.telegram_client import set_telegram_service
        svc, _ = _make_mock_authorized_svc()
        set_telegram_service(svc)

        response = await client.post(
            "/api/channels", json={"username": "a", "tg_id": 123}
        )
        assert response.status_code == 422

    # ──────────────── Validation: neither field ────────────────

    async def test_create_channel_empty_body(self, client, db_session):
        """Providing neither username nor tg_id returns 422."""
        from services.telegram_client import set_telegram_service
        svc, _ = _make_mock_authorized_svc()
        set_telegram_service(svc)

        response = await client.post("/api/channels", json={})
        assert response.status_code == 422


# ──────────────── S2: Happy Path — list channels ────────────────

@pytest.mark.asyncio
class TestListChannels:
    """Tests for GET /api/channels."""

    async def test_list_channels(self, client, db_session):
        """S2: listing channels returns all records."""
        await _seed_channel(db_session, 1, "ch_a", "Channel A")
        await _seed_channel(db_session, 2, "ch_b", "Channel B")

        response = await client.get("/api/channels")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["title"] == "Channel A"
        assert data[1]["title"] == "Channel B"

    async def test_list_channels_empty(self, client, db_session):
        """Listing channels with empty DB returns empty array."""
        response = await client.get("/api/channels")
        assert response.status_code == 200
        assert response.json() == []


# ──────────────── S3: Happy Path — get single channel ────────────────

@pytest.mark.asyncio
class TestGetChannel:
    """Tests for GET /api/channels/{id}."""

    async def test_get_channel(self, client, db_session):
        """S3: getting a single channel by id returns its data."""
        cid = await _seed_channel(db_session, 100, "target", "Target Channel")

        response = await client.get(f"/api/channels/{cid}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == cid
        assert data["tg_id"] == 100
        assert data["title"] == "Target Channel"

    # ──────────────── S8: Edge — non-existent channel ────────────────

    async def test_get_channel_not_found(self, client, db_session):
        """S8: getting a non-existent channel returns 404."""
        response = await client.get("/api/channels/99999")
        assert response.status_code == 404


# ──────────────── S4: Happy Path — delete channel cascade ────────────────

@pytest.mark.asyncio
class TestDeleteChannel:
    """Tests for DELETE /api/channels/{id}."""

    async def test_delete_channel_cascade(self, client, db_session):
        """S4: deleting a channel also deletes its files (cascade)."""
        from sqlalchemy import select, func
        from models import File

        cid = await _seed_channel(db_session, 555, "to_delete", "To Delete")
        await _seed_file(db_session, cid, 1, "file1.txt")
        await _seed_file(db_session, cid, 2, "file2.txt")
        await _seed_file(db_session, cid, 3, "file3.txt")

        # Verify files exist
        file_count = await db_session.scalar(
            select(func.count()).select_from(File).where(File.channel_id == cid)
        )
        assert file_count == 3

        response = await client.delete(f"/api/channels/{cid}")
        assert response.status_code == 204

        # Verify files are deleted
        file_count = await db_session.scalar(
            select(func.count()).select_from(File).where(File.channel_id == cid)
        )
        assert file_count == 0

        # Verify channel is deleted
        response = await client.get(f"/api/channels/{cid}")
        assert response.status_code == 404

    async def test_delete_channel_not_found(self, client, db_session):
        """E9: deleting a non-existent channel returns 404."""
        response = await client.delete("/api/channels/99999")
        assert response.status_code == 404


# ──────────────── Discover: GET /api/channels/discover ────────────────

@pytest.mark.asyncio
class TestDiscoverChannels:
    """Tests for GET /api/channels/discover."""

    async def test_discover_channels(self, client, db_session):
        """S9: discover channels — some tracked, some not."""
        from services.telegram_client import set_telegram_service

        # Pre-seed one channel to test already_tracked
        await _seed_channel(db_session, 100, "tracked_ch", "Tracked Channel")

        svc, mock_client = _make_mock_authorized_svc()
        _setup_discover_dialogs(mock_client, [
            _make_mock_dialog_entity(100, "tracked_ch", "Tracked Channel"),
            _make_mock_dialog_entity(200, "new_ch", "New Channel"),
            _make_mock_dialog_entity(300, "another_ch", "Another Channel"),
        ])
        set_telegram_service(svc)

        response = await client.get("/api/channels/discover")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 3

        tracked = [d for d in data if d["already_tracked"]]
        untracked = [d for d in data if not d["already_tracked"]]
        assert len(tracked) == 1
        assert tracked[0]["tg_id"] == 100
        assert len(untracked) == 2

    async def test_discover_channels_unauthorized(self, client, db_session):
        """S10: discovering channels with unauthorized client returns 400."""
        from services.telegram_client import set_telegram_service, AuthState
        svc = AsyncMock()
        svc.get_client = AsyncMock()
        svc.auth_state = AuthState.DISCONNECTED
        set_telegram_service(svc)

        response = await client.get("/api/channels/discover")
        assert response.status_code == 400
        assert "not authorized" in response.json()["detail"].lower()

    async def test_discover_channels_empty(self, client, db_session):
        """S11: discovering channels returns empty list when user has no channels."""
        from services.telegram_client import set_telegram_service

        svc, mock_client = _make_mock_authorized_svc()
        _setup_discover_dialogs(mock_client, [])  # no channels
        set_telegram_service(svc)

        response = await client.get("/api/channels/discover")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_discover_channels_all_tracked(self, client, db_session):
        """S12: all discovered channels are already in the database."""
        from services.telegram_client import set_telegram_service

        await _seed_channel(db_session, 111, "ch_a", "Channel A")
        await _seed_channel(db_session, 222, "ch_b", "Channel B")

        svc, mock_client = _make_mock_authorized_svc()
        _setup_discover_dialogs(mock_client, [
            _make_mock_dialog_entity(111, "ch_a", "Channel A"),
            _make_mock_dialog_entity(222, "ch_b", "Channel B"),
        ])
        set_telegram_service(svc)

        response = await client.get("/api/channels/discover")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert all(d["already_tracked"] for d in data)

    async def test_discover_channels_dialogs_error(self, client, db_session):
        """S13: get_dialogs exception returns 500."""
        from services.telegram_client import set_telegram_service

        svc, mock_client = _make_mock_authorized_svc()
        # iter_dialogs raises on call
        mock_client.iter_dialogs = MagicMock(side_effect=RuntimeError("FloodWait"))
        set_telegram_service(svc)

        response = await client.get("/api/channels/discover")
        assert response.status_code == 500
        assert "Failed to fetch channels" in response.json()["detail"]
