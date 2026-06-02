"""Tests for Config Management API endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
class TestConfigAPI:
    """Config Management API tests (S1-S7 + edge cases)."""

    # ── S1: List all config ──
    async def test_list_all_config(self, db_session):
        """S1 ✅ GET /api/config returns all registered keys."""
        from main import app
        from models import AppConfig

        # Seed directly via db_session to avoid session-creation race in first test
        db_session.add(AppConfig(key="sync_batch_size", value="500"))
        db_session.add(AppConfig(key="thumb_workers", value="2"))
        db_session.add(AppConfig(key="host", value="0.0.0.0"))
        await db_session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/config")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 3  # at least the three we seeded
            keys = [item["key"] for item in data]
            assert "sync_batch_size" in keys
            assert "thumb_workers" in keys
            assert "host" in keys
            # Check structure of each item
            for item in data:
                assert "key" in item
                assert "value" in item
                assert "editable" in item

    # ── S2: Get single config ──
    async def test_get_single_config(self, db_session):
        """S2 ✅ GET /api/config/{key} returns single config."""
        from main import app
        from models import AppConfig

        cfg = AppConfig(key="sync_batch_size", value="777")
        db_session.add(cfg)
        await db_session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/config/sync_batch_size")
            assert resp.status_code == 200
            data = resp.json()
            assert data["key"] == "sync_batch_size"
            assert data["value"] == "777"
            assert data["editable"] is True

    # ── S3: Update config (admin) ──
    async def test_update_config(self, db_session):
        """S3 ✅ PUT /api/config updates value via admin password."""
        from main import app
        from config import set_config_value

        # Seed admin_password so we can test actual auth
        await set_config_value("admin_password", "secret123")
        await set_config_value("thumb_workers", "2")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/thumb_workers",
                json={"value": "4"},
                headers={"X-Admin-Password": "secret123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["key"] == "thumb_workers"
            assert data["value"] == "4"
            assert "updated successfully" in data["message"]

            # Verify it persisted
            from config import get_config_value
            stored = await get_config_value("thumb_workers")
            assert stored == "4"

    # ── S4: Update non-existent key ──
    async def test_update_nonexistent_key(self):
        """S4 ✅ PUT /api/config/nonexistent → 404."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/no_such_key",
                json={"value": "x"},
                headers={"X-Admin-Password": ""},
            )
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()

    # ── S5: No password → 401 ──
    async def test_update_no_password(self, db_session):
        """S5 ✅ PUT without X-Admin-Password → 401."""
        from main import app
        from config import set_config_value

        await set_config_value("admin_password", "secret123")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/thumb_workers",
                json={"value": "4"},
                # No X-Admin-Password header
            )
            assert resp.status_code == 401
            assert "password" in resp.json()["detail"].lower()

    # ── S6: Wrong password → 403 ──
    async def test_update_wrong_password(self, db_session):
        """S6 ✅ PUT with wrong X-Admin-Password → 403."""
        from main import app
        from config import set_config_value

        await set_config_value("admin_password", "secret123")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/thumb_workers",
                json={"value": "4"},
                headers={"X-Admin-Password": "wrong-password"},
            )
            assert resp.status_code == 403
            assert "invalid" in resp.json()["detail"].lower()

    # ── S7: Invalid value type → 400 ──
    async def test_update_invalid_type(self, db_session):
        """S7 ✅ PUT with non-integer for int field → 400."""
        from main import app
        from config import set_config_value

        await set_config_value("admin_password", "secret123")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/sync_batch_size",
                json={"value": "not_a_number"},
                headers={"X-Admin-Password": "secret123"},
            )
            assert resp.status_code == 400
            assert "integer" in resp.json()["detail"].lower()

    # ── Edge: Get non-existent key ──
    async def test_get_nonexistent_key(self):
        """GET /api/config/nonexistent → 404."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/config/no_such_key")
            assert resp.status_code == 404

    # ── Edge: Update read-only key → 403 ──
    async def test_update_readonly_key(self, db_session):
        """PUT /api/config/api_id → 403 (read-only protection)."""
        from main import app
        from config import set_config_value

        await set_config_value("admin_password", "secret123")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/api_id",
                json={"value": "999999"},
                headers={"X-Admin-Password": "secret123"},
            )
            assert resp.status_code == 403
            assert "read-only" in resp.json()["detail"].lower()

    # ── Edge: Value out of range → 400 ──
    async def test_update_value_out_of_range(self, db_session):
        """PUT value out of allowed range → 400."""
        from main import app
        from config import set_config_value

        await set_config_value("admin_password", "secret123")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/thumb_workers",
                json={"value": "999"},
                headers={"X-Admin-Password": "secret123"},
            )
            assert resp.status_code == 400
            detail = resp.json()["detail"].lower()
            assert "must be <=" in detail or "thumb_workers" in detail

    # ── Edge: Attempt to modify readonly key (deployment param) ──
    async def test_update_readonly_key_rejected(self, db_session):
        """PUT readonly key 'debug' → 403 (not 400, readonly check comes first)."""
        from main import app
        from config import set_config_value

        await set_config_value("admin_password", "secret123")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/debug",
                json={"value": "notabool"},
                headers={"X-Admin-Password": "secret123"},
            )
            assert resp.status_code == 403
            assert "read-only" in resp.json()["detail"].lower()

    # ── Edge: Float validation ──
    async def test_update_float_valid(self, db_session):
        """PUT sync_delay_seconds with valid float → 200."""
        from main import app
        from config import set_config_value

        await set_config_value("admin_password", "secret123")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/config/sync_delay_seconds",
                json={"value": "2.5"},
                headers={"X-Admin-Password": "secret123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["value"] == "2.5"
