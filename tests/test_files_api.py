"""Tests for file management API (Step 4)."""
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from api.files import (
    list_files,
    get_file,
    download_file,
    cache_file,
    delete_cache,
    _file_to_dict,
    CACHE_DIR,
)
from models import Channel as ChannelModel, File as FileModel
from services.telegram_client import (
    get_telegram_service,
    set_telegram_service,
    reset_telegram_service,
    AuthState,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helper: create test data
# ---------------------------------------------------------------------------
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
    )
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)
    return f


async def _mock_authorized_service(client_mock: AsyncMock = None):
    """Set up an authorized mock Telegram service. Returns the service mock."""
    if client_mock is None:
        client_mock = AsyncMock()
    svc = AsyncMock()
    svc.auth_state = AuthState.AUTHORIZED
    svc.get_client = AsyncMock(return_value=client_mock)
    set_telegram_service(svc)
    return svc


# ---------------------------------------------------------------------------
# S1 — Happy Path: 频道文件列表分页
# ---------------------------------------------------------------------------
async def test_list_files_paginated(db_session):
    """GIVEN channel with 10 files WHEN offset=0,limit=5 THEN 5 items,total=10."""
    ch = await _create_channel(db_session)
    for i in range(10):
        await _create_file(db_session, ch.id, message_id=100 + i, file_name=f"file_{i}.txt")

    result = await list_files(channel_id=ch.id, offset=0, limit=5, db=db_session)
    assert len(result["files"]) == 5
    assert result["total"] == 10
    assert result["offset"] == 0
    assert result["limit"] == 5


# ---------------------------------------------------------------------------
# S2 — Happy Path: 频道文件列表默认分页
# ---------------------------------------------------------------------------
async def test_list_files_default_pagination(db_session):
    """GIVEN channel with 3 files WHEN no offset/limit THEN 3 items,limit=50."""
    ch = await _create_channel(db_session)
    for i in range(3):
        await _create_file(db_session, ch.id, message_id=100 + i)

    result = await list_files(channel_id=ch.id, offset=0, limit=50, db=db_session)
    assert len(result["files"]) == 3
    assert result["total"] == 3
    assert result["offset"] == 0


# ---------------------------------------------------------------------------
# S3 — Edge: 频道不存在
# ---------------------------------------------------------------------------
async def test_list_files_channel_not_found(db_session):
    """GIVEN channel 999 doesn't exist WHEN query files THEN 404."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await list_files(channel_id=999, offset=0, limit=50, db=db_session)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# S4 — Edge: 频道文件列表为空
# ---------------------------------------------------------------------------
async def test_list_files_empty(db_session):
    """GIVEN channel with 0 files THEN empty array,total=0."""
    ch = await _create_channel(db_session)

    result = await list_files(channel_id=ch.id, offset=0, limit=50, db=db_session)
    assert result["files"] == []
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# S5 — Happy Path: 获取文件详情
# ---------------------------------------------------------------------------
async def test_get_file_detail(db_session):
    """GIVEN file exists WHEN get detail THEN all fields present."""
    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id)

    result = await get_file(file_id=f.id, db=db_session)
    assert result["id"] == f.id
    assert result["file_name"] == "test_file.pdf"
    assert result["mime_type"] == "application/pdf"
    assert result["is_cached"] is False


# ---------------------------------------------------------------------------
# S6 — Edge: 获取不存在的文件
# ---------------------------------------------------------------------------
async def test_get_file_not_found(db_session):
    """GIVEN file 999 doesn't exist WHEN get detail THEN 404."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_file(file_id=999, db=db_session)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# S7 — Happy Path: 缓存文件 (integration, mock Telegram download)
# ---------------------------------------------------------------------------
async def test_cache_file(db_session):
    """GIVEN file is_cached=false,Telegram authorized
    WHEN POST /cache THEN is_cached=true, cache_path set."""
    ch = await _create_channel(db_session, tg_id=123456789)
    f = await _create_file(db_session, ch.id, is_cached=False, cache_path=None)

    # We mock at the _download_from_telegram level using patch
    with patch("api.files._download_from_telegram") as mock_dl:
        # Make _download_from_telegram write a placeholder file and return size
        async def _fake_dl(svc, tg_id, message_id, target_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"fake content")
            return 12  # 12 bytes

        mock_dl.side_effect = _fake_dl

        svc = await _mock_authorized_service()
        result = await cache_file(file_id=f.id, db=db_session)

    assert result["is_cached"] is True
    assert result["cache_path"] is not None
    assert "test_file.pdf" in result["cache_path"]

    # Verify DB state
    await db_session.refresh(f)
    assert f.is_cached is True
    assert f.cache_path is not None
    assert f.file_size == 12

    # Cleanup
    if f.cache_path:
        full = CACHE_DIR / f.cache_path
        if full.exists():
            os.remove(str(full))


# ---------------------------------------------------------------------------
# S8 — Edge: 缓存时不授权
# ---------------------------------------------------------------------------
async def test_cache_file_unauthorized(db_session):
    """GIVEN Telegram not authorized WHEN POST /cache THEN 400."""
    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id, is_cached=False)

    reset_telegram_service()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await cache_file(file_id=f.id, db=db_session)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# S9 — Edge: 缓存不存在的文件
# ---------------------------------------------------------------------------
async def test_cache_file_not_found(db_session):
    """GIVEN file 999 doesn't exist WHEN POST /cache THEN 404."""
    await _mock_authorized_service()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await cache_file(file_id=999, db=db_session)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# S10 — Edge: 幂等缓存（已缓存再次缓存）
# ---------------------------------------------------------------------------
async def test_cache_file_already_cached(db_session):
    """GIVEN file already cached on disk WHEN POST /cache THEN 200, no re-download."""
    ch = await _create_channel(db_session)
    # Create a real cached file on disk
    cache_rel = f"{ch.id}/999_test_cached.pdf"
    cache_full = CACHE_DIR / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    cache_full.write_bytes(b"cached data")

    f = await _create_file(
        db_session, ch.id,
        is_cached=True,
        cache_path=cache_rel,
    )

    svc = await _mock_authorized_service()
    # The download mock should NOT be called since file is already cached
    with patch("api.files._download_from_telegram") as mock_dl:
        result = await cache_file(file_id=f.id, db=db_session)
        mock_dl.assert_not_called()

    assert result["is_cached"] is True
    assert result["cache_path"] == cache_rel

    # Cleanup
    if cache_full.exists():
        os.remove(str(cache_full))


# ---------------------------------------------------------------------------
# S11 — Happy Path: 清除缓存
# ---------------------------------------------------------------------------
async def test_delete_cache(db_session):
    """GIVEN file is_cached=true,cache file exists WHEN DELETE /cache
    THEN is_cached=false,cache_path=None,file removed."""
    ch = await _create_channel(db_session)
    cache_rel = f"{ch.id}/888_to_delete.pdf"
    cache_full = CACHE_DIR / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    cache_full.write_bytes(b"to be deleted")

    f = await _create_file(
        db_session, ch.id,
        is_cached=True,
        cache_path=cache_rel,
    )

    result = await delete_cache(file_id=f.id, db=db_session)
    assert result["status"] == "ok"

    await db_session.refresh(f)
    assert f.is_cached is False
    assert f.cache_path is None

    # File should be gone from disk
    assert not cache_full.exists()


# ---------------------------------------------------------------------------
# S12 — Edge: 清除不存在的缓存（幂等）
# ---------------------------------------------------------------------------
async def test_delete_cache_not_cached(db_session):
    """GIVEN file is_cached=false WHEN DELETE /cache THEN 200, no error."""
    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id, is_cached=False, cache_path=None)

    result = await delete_cache(file_id=f.id, db=db_session)
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# S13 — Happy Path: 下载已缓存文件（流式）
# ---------------------------------------------------------------------------
async def test_download_cached_file(db_session):
    """GIVEN file is_cached=true,cache file exists WHEN GET /download
    THEN 200 streaming response with correct headers."""
    ch = await _create_channel(db_session)
    cache_rel = f"{ch.id}/777_download.pdf"
    cache_full = CACHE_DIR / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    content = b"test download content" * 100
    cache_full.write_bytes(content)

    f = await _create_file(
        db_session, ch.id,
        file_name="download.pdf",
        mime_type="application/pdf",
        file_size=len(content),
        is_cached=True,
        cache_path=cache_rel,
    )

    response = await download_file(file_id=f.id, db=db_session)

    assert response.status_code == 200
    assert response.media_type == "application/pdf"

    # Read all chunks and verify
    chunks = b""
    async for chunk in response.body_iterator:
        chunks += chunk if isinstance(chunk, bytes) else chunk.encode()
    assert chunks == content

    # Cleanup
    if cache_full.exists():
        os.remove(str(cache_full))


# ---------------------------------------------------------------------------
# S14 — Edge: 下载未授权（文件未缓存）
# ---------------------------------------------------------------------------
async def test_download_unauthorized(db_session):
    """GIVEN file not cached,Telegram not authorized WHEN GET /download THEN 400."""
    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id, is_cached=False, cache_path=None)

    reset_telegram_service()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await download_file(file_id=f.id, db=db_session)
    assert exc.value.status_code == 400
