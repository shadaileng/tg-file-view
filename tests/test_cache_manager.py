"""Tests for cache manager (Step 7) — LRU eviction, dynamic limits, stats."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from models import Channel as ChannelModel, File as FileModel, AppConfig, CacheRecord as CacheRecordModel
from services.cache_manager import CacheManager
from services.telegram_client import (
    get_telegram_service,
    set_telegram_service,
    reset_telegram_service,
    AuthState,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_tg_service():
    """Ensure Telegram service is reset before each test."""
    reset_telegram_service()
    yield
    reset_telegram_service()


def _auth_service():
    """Set up an authorized mock Telegram service."""
    svc = AsyncMock()
    svc.auth_state = AuthState.AUTHORIZED
    svc.get_client = AsyncMock(return_value=AsyncMock())
    set_telegram_service(svc)
    return svc


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
    """Create a File record. Supports cached_at/accessed_at for LRU testing."""
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


async def _set_config(db_session, key: str, value: str) -> None:
    """Upsert a config value in app_config."""
    existing = await db_session.get(AppConfig, key)
    if existing:
        existing.value = value
    else:
        db_session.add(AppConfig(key=key, value=value))
    await db_session.commit()


async def _touch_cache_file(cache_path: str, size: int = 0) -> Path:
    """Create a dummy cache file on disk."""
    from api.files import CACHE_DIR
    full_path = CACHE_DIR / cache_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(b"x" * size)
    return full_path


# ---------------------------------------------------------------------------
# S2 — Happy Path: 查看缓存统计
# ---------------------------------------------------------------------------
async def test_cache_stats(db_session):
    """GIVEN 3 cached files (total 3KB) WHEN get_stats THEN correct stats."""
    ch = await _create_channel(db_session)

    for i in range(3):
        cache_path = f"{ch.id}/{i + 1}_file_{i}.ext"
        await _touch_cache_file(cache_path, 1024)
        await _create_file(
            db_session,
            ch.id,
            message_id=100 + i,
            file_size=1024,
            is_cached=True,
            cache_path=cache_path,
        )

    # Set limit to 1 MB
    await _set_config(db_session, "cache_max_size_mb", "1")

    stats = await CacheManager.get_stats(db_session)

    assert stats["file_count"] == 3
    assert stats["total_size_bytes"] == 3072
    assert stats["total_size_mb"] == pytest.approx(0.0, abs=0.01)  # 3072 bytes ≈ 0.0 MB
    assert stats["max_size_mb"] == 1.0
    assert stats["max_size_bytes"] == 1048576
    assert stats["unlimited"] is False
    assert 0 <= stats["usage_percent"] <= 1  # ~0.3%


async def test_cache_stats_empty(db_session):
    """GIVEN no cached files WHEN get_stats THEN all zeros."""
    await _set_config(db_session, "cache_max_size_mb", "100")

    stats = await CacheManager.get_stats(db_session)

    assert stats["file_count"] == 0
    assert stats["total_size_bytes"] == 0
    assert stats["total_size_mb"] == 0
    assert stats["usage_percent"] == 0


# ---------------------------------------------------------------------------
# S4 — Happy Path: 无限缓存模式
# ---------------------------------------------------------------------------
async def test_unlimited_cache(db_session):
    """GIVEN cache_max_size_mb=0 WHEN check_and_evict THEN always True, no eviction."""
    await _set_config(db_session, "cache_max_size_mb", "0")
    ch = await _create_channel(db_session)

    # Create a cached file
    cache_path = f"{ch.id}/1_big_file.dat"
    await _touch_cache_file(cache_path, 1024 * 1024 * 500)  # 500 MB
    await _create_file(
        db_session,
        ch.id,
        file_size=1024 * 1024 * 500,
        is_cached=True,
        cache_path=cache_path,
    )

    # Even with enormous files, unlimited mode skips all checks
    result = await CacheManager.check_and_evict(
        db_session, new_file_size=1024 * 1024 * 1000
    )
    assert result is True


async def test_unlimited_cache_evict_manual(db_session):
    """GIVEN unlimited cache WHEN manual evict THEN returns detail message."""
    await _set_config(db_session, "cache_max_size_mb", "0")

    result = await CacheManager.evict_to_limit(db_session)

    assert result["evicted_count"] == 0
    assert result["freed_mb"] == 0
    assert "unlimited" in result["detail"]


# ---------------------------------------------------------------------------
# S3 — Happy Path: 手动淘汰
# ---------------------------------------------------------------------------
async def test_manual_evict(db_session):
    """GIVEN 5 cached files (5KB), limit=3KB WHEN manual evict THEN evicts 2."""
    await _set_config(db_session, "cache_max_size_mb", "0")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)
    for i in range(5):
        cache_path = f"{ch.id}/{i + 1}_file_{i}.ext"
        await _touch_cache_file(cache_path, 1024)
        await _create_file(
            db_session,
            ch.id,
            message_id=100 + i,
            file_size=1024,
            is_cached=True,
            cache_path=cache_path,
            cached_at=now - timedelta(hours=5 - i),  # oldest = file 1
            accessed_at=now - timedelta(hours=5 - i),
        )

    # Change limit to ~3KB
    await _set_config(db_session, "cache_max_size_mb", "0")
    # Directly set it to a limit smaller than total
    # We'll set to 3 * 1024 = 3072 bytes ≈ 0.003 MB
    # But cache_max_size_mb is int in settings, so minimum is 1 MB.
    # Let's use 1 MB limit with 5 MB total.
    for i in range(5):
        result = await db_session.execute(
            select(FileModel).where(FileModel.id == i + 1)
        )
        f = result.scalar_one()
        f.file_size = 1024 * 1024  # 1 MB each
        f.cache_path = f"{ch.id}/{i + 1}_file_{i}.ext"
    await db_session.commit()

    # Each file is now 1MB, 5 files = 5MB, limit = 1MB → need to evict 4
    await _set_config(db_session, "cache_max_size_mb", "1")

    result = await CacheManager.evict_to_limit(db_session)

    assert result["evicted_count"] == 4
    assert result["freed_mb"] == pytest.approx(4.0, abs=0.01)
    assert result["total_size_mb"] <= 1.0


async def test_manual_evict_already_under(db_session):
    """GIVEN cache is under limit WHEN manual evict THEN evicts 0."""
    await _set_config(db_session, "cache_max_size_mb", "100")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)
    cache_path = f"{ch.id}/1_small_file.dat"
    await _touch_cache_file(cache_path, 1024)
    await _create_file(
        db_session,
        ch.id,
        file_size=1024,
        is_cached=True,
        cache_path=cache_path,
        cached_at=now,
        accessed_at=now,
    )

    result = await CacheManager.evict_to_limit(db_session)

    assert result["evicted_count"] == 0
    assert result["freed_mb"] == 0
    assert "already under limit" in result["detail"]


# ---------------------------------------------------------------------------
# S1 — Happy Path: 下载触发 LRU 淘汰 (via check_and_evict)
# ---------------------------------------------------------------------------
async def test_evict_on_pre_check(db_session):
    """GIVEN cache has 2MB used, limit=2MB, new file=1MB WHEN check THEN evicts oldest."""
    await _set_config(db_session, "cache_max_size_mb", "2")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)

    # File 1: 1MB, accessed 2 hours ago (LRU)
    cache_path_1 = f"{ch.id}/1_old.dat"
    await _touch_cache_file(cache_path_1, 1024 * 1024)
    await _create_file(
        db_session,
        ch.id,
        message_id=101,
        file_size=1024 * 1024,
        is_cached=True,
        cache_path=cache_path_1,
        cached_at=now - timedelta(hours=2),
        accessed_at=now - timedelta(hours=2),
    )

    # File 2: 1MB, accessed 1 hour ago (newer)
    cache_path_2 = f"{ch.id}/2_new.dat"
    await _touch_cache_file(cache_path_2, 1024 * 1024)
    await _create_file(
        db_session,
        ch.id,
        message_id=102,
        file_size=1024 * 1024,
        is_cached=True,
        cache_path=cache_path_2,
        cached_at=now - timedelta(hours=1),
        accessed_at=now - timedelta(hours=1),
    )

    # Current: 2MB, new: 1MB, total: 3MB > 2MB → need to free 1MB
    result = await CacheManager.check_and_evict(
        db_session, new_file_size=1024 * 1024, new_file_id=999
    )

    assert result is True

    # File 1 (older) should be evicted, File 2 (newer) should remain
    updated_f1 = await db_session.get(FileModel, 1)
    assert updated_f1.is_cached is False
    assert updated_f1.cache_path is None
    assert updated_f1.cached_at is None

    updated_f2 = await db_session.get(FileModel, 2)
    assert updated_f2.is_cached is True
    assert updated_f2.cache_path is not None


async def test_evict_respects_lru_order(db_session):
    """GIVEN 3 files with different accessed_at WHEN evict THEN oldest goes first."""
    await _set_config(db_session, "cache_max_size_mb", "1")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)

    # File 1: accessed 3 days ago (OLDEST)
    cache_path_1 = f"{ch.id}/1_oldest.dat"
    await _touch_cache_file(cache_path_1, 1024 * 512)
    await _create_file(
        db_session,
        ch.id,
        message_id=101,
        file_size=1024 * 512,
        is_cached=True,
        cache_path=cache_path_1,
        cached_at=now - timedelta(days=3),
        accessed_at=now - timedelta(days=3),
    )

    # File 2: accessed 1 day ago
    cache_path_2 = f"{ch.id}/2_mid.dat"
    await _touch_cache_file(cache_path_2, 1024 * 512)
    await _create_file(
        db_session,
        ch.id,
        message_id=102,
        file_size=1024 * 512,
        is_cached=True,
        cache_path=cache_path_2,
        cached_at=now - timedelta(days=1),
        accessed_at=now - timedelta(days=1),
    )

    # File 3: accessed recently (NEWEST)
    cache_path_3 = f"{ch.id}/3_newest.dat"
    await _touch_cache_file(cache_path_3, 1024 * 512)
    await _create_file(
        db_session,
        ch.id,
        message_id=103,
        file_size=1024 * 512,
        is_cached=True,
        cache_path=cache_path_3,
        cached_at=now,
        accessed_at=now,
    )

    # Total ~1.5MB, limit 1MB, new file 0.5MB → projected 2MB > 1MB → need ~1MB
    await CacheManager.check_and_evict(
        db_session, new_file_size=1024 * 512, new_file_id=999
    )

    # File 1 (oldest) should be evicted first
    f1 = await db_session.get(FileModel, 1)
    assert f1.is_cached is False

    # File 2 should also be evicted (need ~1MB total)
    f2 = await db_session.get(FileModel, 2)
    assert f2.is_cached is False

    # File 3 (newest) should survive
    f3 = await db_session.get(FileModel, 3)
    assert f3.is_cached is True


# ---------------------------------------------------------------------------
# S6 — Edge: 空间完全不够
# ---------------------------------------------------------------------------
async def test_insufficient_space(db_session):
    """GIVEN cache limit=1MB, current=1MB, new file=5MB WHEN check THEN 507."""
    await _set_config(db_session, "cache_max_size_mb", "1")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)
    cache_path = f"{ch.id}/1_existing.dat"
    await _touch_cache_file(cache_path, 1024 * 1024)

    # Existing file: 1MB (exactly at limit)
    await _create_file(
        db_session,
        ch.id,
        message_id=101,
        file_size=1024 * 1024,
        is_cached=True,
        cache_path=cache_path,
        cached_at=now,
        accessed_at=now,
    )

    # New file: 5MB → projected 6MB, but only 1MB evictable → can't fit
    with pytest.raises(HTTPException) as exc:
        await CacheManager.check_and_evict(
            db_session, new_file_size=1024 * 1024 * 5, new_file_id=999
        )

    assert exc.value.status_code == 507
    assert "Insufficient cache space" in exc.value.detail


async def test_insufficient_space_no_evictable(db_session):
    """GIVEN limit=1MB, no cached files, new file=5MB WHEN check THEN 507."""
    await _set_config(db_session, "cache_max_size_mb", "1")

    # No cached files, just trying to cache a 5MB file
    with pytest.raises(HTTPException) as exc:
        await CacheManager.check_and_evict(
            db_session, new_file_size=1024 * 1024 * 5, new_file_id=1
        )

    assert exc.value.status_code == 507


# ---------------------------------------------------------------------------
# S5 — Edge: 单文件超过上限（但无其他文件可淘汰）
# ---------------------------------------------------------------------------
async def test_single_file_exceeds_limit(db_session):
    """GIVEN limit=1MB, new file=5MB, no other files WHEN check THEN 507.

    The single file itself is too large — can't evict it before caching.
    """
    await _set_config(db_session, "cache_max_size_mb", "1")

    # No other cached files — evictable = 0
    with pytest.raises(HTTPException) as exc:
        await CacheManager.check_and_evict(
            db_session, new_file_size=1024 * 1024 * 5, new_file_id=1
        )

    assert exc.value.status_code == 507


async def test_single_file_exceeds_limit_with_other_files(db_session):
    """GIVEN limit=1MB, other file=0.5MB, new file=5MB WHEN check THEN 507.

    Even with 0.5MB evictable, 5MB need → can't fit.
    """
    await _set_config(db_session, "cache_max_size_mb", "1")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)
    cache_path = f"{ch.id}/1_existing.dat"
    await _touch_cache_file(cache_path, 1024 * 512)

    await _create_file(
        db_session,
        ch.id,
        file_size=1024 * 512,
        is_cached=True,
        cache_path=cache_path,
        cached_at=now,
        accessed_at=now,
    )

    with pytest.raises(HTTPException) as exc:
        await CacheManager.check_and_evict(
            db_session, new_file_size=1024 * 1024 * 5, new_file_id=999
        )

    assert exc.value.status_code == 507


# ---------------------------------------------------------------------------
# S7 — Edge: 淘汰时磁盘文件缺失
# ---------------------------------------------------------------------------
async def test_evict_missing_file(db_session):
    """GIVEN file in DB marked cached but disk file missing WHEN evict THEN skip gracefully."""
    await _set_config(db_session, "cache_max_size_mb", "1")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)

    # File 1: cached in DB, file exists on disk
    cache_path_1 = f"{ch.id}/1_exists.dat"
    await _touch_cache_file(cache_path_1, 1024 * 512)
    await _create_file(
        db_session,
        ch.id,
        message_id=101,
        file_size=1024 * 512,
        is_cached=True,
        cache_path=cache_path_1,
        cached_at=now - timedelta(days=3),
        accessed_at=now - timedelta(days=3),
    )

    # File 2: cached in DB, but NO disk file (simulates manual deletion)
    await _create_file(
        db_session,
        ch.id,
        message_id=102,
        file_size=1024 * 512,
        is_cached=True,
        cache_path=f"{ch.id}/2_missing.dat",  # No disk file created
        cached_at=now - timedelta(days=1),   # Newer than file 1
        accessed_at=now - timedelta(days=1),
    )

    # Total 1MB, limit 1MB, new file 1MB → projected 2MB > 1MB → need 1MB
    # Both File 1 (oldest) and File 2 (missing) must be evicted
    # File 2 is missing on disk → skip disk deletion gracefully, still clear DB
    result = await CacheManager.check_and_evict(
        db_session, new_file_size=1024 * 1024, new_file_id=999
    )

    assert result is True

    # File 1 should be evicted (disk deleted + DB cleared)
    f1 = await db_session.get(FileModel, 1)
    assert f1.is_cached is False

    # File 2: DB should also be cleared even though disk file was missing
    f2 = await db_session.get(FileModel, 2)
    assert f2.is_cached is False
    assert f2.cache_path is None


# ---------------------------------------------------------------------------
# S8 — Edge: 动态修改上限
# ---------------------------------------------------------------------------
async def test_dynamic_limit(db_session):
    """GIVEN limit=10MB, cache=2MB WHEN change limit to 1MB THEN next evict triggers."""
    await _set_config(db_session, "cache_max_size_mb", "10")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)

    # Two 1MB files cached
    for i in range(2):
        cache_path = f"{ch.id}/{i + 1}_file_{i}.dat"
        await _touch_cache_file(cache_path, 1024 * 1024)
        await _create_file(
            db_session,
            ch.id,
            message_id=101 + i,
            file_size=1024 * 1024,
            is_cached=True,
            cache_path=cache_path,
            cached_at=now - timedelta(hours=2 - i),
            accessed_at=now - timedelta(hours=2 - i),
        )

    # Current: 2MB, limit 10MB → no eviction needed
    result = await CacheManager.check_and_evict(db_session, new_file_size=0)
    assert result is True

    # Dynamically change limit to 1MB
    await _set_config(db_session, "cache_max_size_mb", "1")

    # Now: 2MB > 1MB → need to free 1MB (post_download_check mode)
    # new_file_size=0 means "just check current vs limit"
    await CacheManager.check_and_evict(db_session, new_file_size=0)

    # One of the files should be evicted
    count_result = await db_session.execute(
        select(func.count(FileModel.id)).where(FileModel.is_cached == True)
    )
    remaining = count_result.scalar() or 0
    assert remaining == 1


# ---------------------------------------------------------------------------
# Mark accessed helper test
# ---------------------------------------------------------------------------
async def test_mark_accessed(db_session):
    """GIVEN a cached file WHEN mark_accessed THEN accessed_at is updated."""
    ch = await _create_channel(db_session)

    old_time = datetime.now(timezone.utc) - timedelta(hours=5)
    cache_path = f"{ch.id}/1_file.dat"
    await _touch_cache_file(cache_path, 1024)
    f = await _create_file(
        db_session,
        ch.id,
        message_id=101,
        file_size=1024,
        is_cached=True,
        cache_path=cache_path,
        cached_at=old_time,
        accessed_at=old_time,
    )

    await CacheManager.mark_accessed(db_session, f)

    await db_session.refresh(f)
    # SQLite + DateTime(timezone=True) still returns naive datetimes on
    # read-back; the stored value IS UTC, so restore tzinfo for comparison.
    assert f.accessed_at is not None
    assert f.accessed_at.replace(tzinfo=timezone.utc) > old_time


# ---------------------------------------------------------------------------
# post_download_check test
# ---------------------------------------------------------------------------
async def test_post_download_check_evicts_if_over(db_session):
    """GIVEN cache is over limit WHEN post_download_check THEN evicts."""
    await _set_config(db_session, "cache_max_size_mb", "2")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)

    # Three 1MB files = 3MB > 2MB limit
    for i in range(3):
        cache_path = f"{ch.id}/{i + 1}_file_{i}.dat"
        await _touch_cache_file(cache_path, 1024 * 1024)
        await _create_file(
            db_session,
            ch.id,
            message_id=101 + i,
            file_size=1024 * 1024,
            is_cached=True,
            cache_path=cache_path,
            cached_at=now - timedelta(hours=3 - i),
            accessed_at=now - timedelta(hours=3 - i),
        )

    # Should evict at least 1 file (need ≥ 1MB freed)
    await CacheManager.post_download_check(db_session)

    count_result = await db_session.execute(
        select(func.count(FileModel.id)).where(FileModel.is_cached == True)
    )
    remaining = count_result.scalar() or 0
    assert remaining <= 2


# ---------------------------------------------------------------------------
# edge: no eviction needed (under limit)
# ---------------------------------------------------------------------------
async def test_no_eviction_when_under_limit(db_session):
    """GIVEN cache is under limit WHEN check_and_evict THEN no eviction, returns True."""
    await _set_config(db_session, "cache_max_size_mb", "100")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)
    cache_path = f"{ch.id}/1_small.dat"
    await _touch_cache_file(cache_path, 1024)
    await _create_file(
        db_session,
        ch.id,
        file_size=1024,
        is_cached=True,
        cache_path=cache_path,
        cached_at=now,
        accessed_at=now,
    )

    result = await CacheManager.check_and_evict(db_session, new_file_size=1024)
    assert result is True

    # File should still be cached
    f = await db_session.get(FileModel, 1)
    assert f.is_cached is True


# ---------------------------------------------------------------------------
# CacheRecord CRUD tests (Step — CacheRecord table)
# ---------------------------------------------------------------------------

async def test_create_cache_record(db_session):
    """GIVEN a cached file WHEN create_record THEN CacheRecord created with status='cached'."""
    ch = await _create_channel(db_session)
    cache_path = f"{ch.id}/1_test.dat"
    await _touch_cache_file(cache_path, 1024)
    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_path, file_size=1024)

    rec = await CacheManager.create_record(db_session, f)

    assert rec.file_id == f.id
    assert rec.file_path == cache_path
    assert rec.file_size == 1024
    assert rec.status == "cached"
    assert rec.error_msg is None
    assert rec.cached_at is not None
    assert rec.accessed_at is not None


async def test_create_cache_record_idempotent(db_session):
    """GIVEN an existing CacheRecord WHEN create_record again THEN updates."""
    ch = await _create_channel(db_session)
    cache_path = f"{ch.id}/1_test.dat"
    await _touch_cache_file(cache_path, 1024)
    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_path, file_size=1024)

    rec1 = await CacheManager.create_record(db_session, f)
    rec1_id = rec1.id

    rec2 = await CacheManager.create_record(db_session, f)
    assert rec2.id == rec1_id  # Same record updated
    assert rec2.status == "cached"


async def test_list_cache_records_paginated(db_session):
    """GIVEN 5 cached files WHEN list_records THEN returns paginated results."""
    ch = await _create_channel(db_session)
    for i in range(5):
        cache_path = f"{ch.id}/{i + 1}_test.dat"
        await _touch_cache_file(cache_path, 1024)
        f = await _create_file(db_session, ch.id, message_id=100 + i, is_cached=True, cache_path=cache_path, file_size=1024)
        await CacheManager.create_record(db_session, f)

    # Full list
    records, total = await CacheManager.list_records(db_session, offset=0, limit=50)
    assert total == 5
    assert len(records) == 5

    # Paginated
    records2, total2 = await CacheManager.list_records(db_session, offset=0, limit=2)
    assert total2 == 5
    assert len(records2) == 2


async def test_list_cache_records_empty(db_session):
    """GIVEN no cache records WHEN list_records THEN empty list."""
    records, total = await CacheManager.list_records(db_session)
    assert records == []
    assert total == 0


async def test_delete_cache_record(db_session):
    """GIVEN a cache record WHEN delete_record THEN disk file removed + DB entry deleted."""
    ch = await _create_channel(db_session)
    cache_path = f"{ch.id}/1_to_delete.dat"
    full_path = await _touch_cache_file(cache_path, 1024)
    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_path, file_size=1024)

    rec = await CacheManager.create_record(db_session, f)
    rec_id = rec.id

    result = await CacheManager.delete_record(db_session, rec_id)
    assert result is True

    # Verify DB
    deleted = await db_session.get(CacheRecordModel, rec_id)
    assert deleted is None

    # Verify File fields reset
    await db_session.refresh(f)
    assert f.is_cached is False
    assert f.cache_path is None

    # Verify disk file removed
    assert not full_path.exists()


async def test_delete_cache_record_not_found(db_session):
    """GIVEN record_id doesn't exist WHEN delete_record THEN returns False."""
    result = await CacheManager.delete_record(db_session, 999)
    assert result is False


async def test_delete_cache_record_missing_disk_file(db_session):
    """GIVEN CacheRecord exists but disk file missing WHEN delete_record THEN succeeds."""
    ch = await _create_channel(db_session)
    cache_path = f"{ch.id}/1_missing.dat"
    # Don't create disk file
    f = await _create_file(db_session, ch.id, is_cached=True, cache_path=cache_path, file_size=1024)

    rec = await CacheManager.create_record(db_session, f)
    rec_id = rec.id

    result = await CacheManager.delete_record(db_session, rec_id)
    assert result is True

    deleted = await db_session.get(CacheRecordModel, rec_id)
    assert deleted is None


async def test_evict_one_deletes_cache_record(db_session):
    """GIVEN cached file with CacheRecord WHEN LRU evicted THEN CacheRecord deleted."""
    await _set_config(db_session, "cache_max_size_mb", "10")
    ch = await _create_channel(db_session)

    now = datetime.now(timezone.utc)
    cache_path = f"{ch.id}/1_evict_me.dat"
    await _touch_cache_file(cache_path, 1024 * 1024)
    f = await _create_file(
        db_session, ch.id, message_id=101,
        is_cached=True, cache_path=cache_path, file_size=1024 * 1024,
        cached_at=now, accessed_at=now,
    )
    await CacheManager.create_record(db_session, f)

    # Manually evict the file via check_and_evict with a file larger than limit
    # Set limit to 0.5 MB so the 1MB file gets evicted
    await _set_config(db_session, "cache_max_size_mb", "0")
    # Directly call _evict_one to test CacheRecord cleanup
    freed = await CacheManager._evict_one(db_session, f)

    assert freed == 1024 * 1024

    # CacheRecord should be gone
    cr_q = select(CacheRecordModel).where(CacheRecordModel.file_id == f.id)
    cr_result = await db_session.execute(cr_q)
    cr = cr_result.scalar_one_or_none()
    assert cr is None

    # File.is_cached should be False
    await db_session.refresh(f)
    assert f.is_cached is False
