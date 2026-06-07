"""Tests for cache management API (Step — CacheRecord endpoints)."""

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from api.cache import list_cache_records, delete_cache_record
from models import Channel as ChannelModel, File as FileModel, CacheRecord as CacheRecordModel
from services.telegram_client import (
    get_telegram_service,
    set_telegram_service,
    reset_telegram_service,
    AuthState,
)

pytestmark = pytest.mark.asyncio


async def _create_channel(db_session, **kwargs) -> ChannelModel:
    ch = ChannelModel(
        tg_id=kwargs.get("tg_id", 123456789),
        username=kwargs.get("username", "testchan"),
        title=kwargs.get("title", "Test Channel"),
    )
    db_session.add(ch)
    await db_session.commit()
    await db_session.refresh(ch)
    return ch


async def _create_file(db_session, channel_id: int, **kwargs) -> FileModel:
    f = FileModel(
        channel_id=channel_id,
        message_id=kwargs.get("message_id", 100),
        file_name=kwargs.get("file_name", "test_file.pdf"),
        file_size=kwargs.get("file_size", 1024),
        mime_type=kwargs.get("mime_type", "application/pdf"),
        file_type=kwargs.get("file_type", "document"),
        is_cached=kwargs.get("is_cached", False),
        cache_path=kwargs.get("cache_path", None),
        cached_at=kwargs.get("cached_at", None),
        accessed_at=kwargs.get("accessed_at", None),
    )
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)
    return f


async def _create_cache_record(db_session, file_id: int, **kwargs) -> CacheRecordModel:
    now = datetime.now(timezone.utc)
    cr = CacheRecordModel(
        file_id=file_id,
        file_path=kwargs.get("file_path", "test/path.dat"),
        file_size=kwargs.get("file_size", 1024),
        status=kwargs.get("status", "cached"),
        error_msg=kwargs.get("error_msg", None),
        cached_at=now,
        accessed_at=now,
    )
    db_session.add(cr)
    await db_session.commit()
    await db_session.refresh(cr)
    return cr


# ---------------------------------------------------------------------------
# CacheRecord list API tests
# ---------------------------------------------------------------------------

async def test_list_records_happy(db_session):
    """GIVEN 3 cache records WHEN list_records THEN paginated list returned."""
    ch = await _create_channel(db_session, title="Chan1")
    f1 = await _create_file(db_session, ch.id, message_id=101, file_name="a.pdf", is_cached=True)
    f2 = await _create_file(db_session, ch.id, message_id=102, file_name="b.pdf", is_cached=True)
    f3 = await _create_file(db_session, ch.id, message_id=103, file_name="c.pdf", is_cached=True)
    await _create_cache_record(db_session, f1.id, file_path="1/a.pdf")
    await _create_cache_record(db_session, f2.id, file_path="1/b.pdf")
    await _create_cache_record(db_session, f3.id, file_path="1/c.pdf")

    result = await list_cache_records(offset=0, limit=50, db=db_session)

    assert result["total"] == 3
    assert len(result["records"]) == 3
    assert result["records"][0]["file_name"] in ("a.pdf", "b.pdf", "c.pdf")
    assert result["records"][0]["channel_title"] == "Chan1"
    assert result["records"][0]["status"] == "cached"


async def test_list_records_paginated(db_session):
    """GIVEN 5 cache records WHEN limit=2 THEN 2 items returned."""
    ch = await _create_channel(db_session)
    for i in range(5):
        f = await _create_file(db_session, ch.id, message_id=100 + i, is_cached=True)
        await _create_cache_record(db_session, f.id, file_path=f"{ch.id}/{i}.dat")

    result = await list_cache_records(offset=0, limit=2, db=db_session)
    assert len(result["records"]) == 2
    assert result["total"] == 5


async def test_list_records_empty(db_session):
    """GIVEN no cache records WHEN list_records THEN empty list."""
    result = await list_cache_records(offset=0, limit=50, db=db_session)
    assert result["records"] == []
    assert result["total"] == 0


async def test_list_records_with_caching_status(db_session):
    """GIVEN cache records with different statuses WHEN list THEN status preserved."""
    ch = await _create_channel(db_session)
    f1 = await _create_file(db_session, ch.id, message_id=101, is_cached=True)
    f2 = await _create_file(db_session, ch.id, message_id=102, is_cached=False)
    await _create_cache_record(db_session, f1.id, status="cached")
    await _create_cache_record(db_session, f2.id, status="caching")

    result = await list_cache_records(offset=0, limit=50, db=db_session)
    statuses = {r["file_id"]: r["status"] for r in result["records"]}
    assert statuses[f1.id] == "cached"
    assert statuses[f2.id] == "caching"


# ---------------------------------------------------------------------------
# CacheRecord delete API tests
# ---------------------------------------------------------------------------

async def test_delete_record_happy(db_session):
    """GIVEN a cache record WHEN delete_record THEN 200 + record deleted."""
    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id, message_id=101, is_cached=True, cache_path="test/path.dat")
    cr = await _create_cache_record(db_session, f.id, file_path="test/path.dat")

    result = await delete_cache_record(record_id=cr.id, db=db_session)
    assert result["status"] == "ok"

    # Verify DB
    deleted = await db_session.get(CacheRecordModel, cr.id)
    assert deleted is None


async def test_delete_record_not_found(db_session):
    """GIVEN record_id 999 WHEN delete THEN 404."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await delete_cache_record(record_id=999, db=db_session)
    assert exc.value.status_code == 404
