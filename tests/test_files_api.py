"""Tests for file management API (Step 4)."""
import os
import tempfile
from datetime import datetime, timezone
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
    _safe_filename,
    CACHE_DIR,
)
from models import Channel as ChannelModel, File as FileModel, CacheRecord as CacheRecordModel
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
    WHEN POST /cache THEN returns is_caching=true, CacheRecord created."""
    ch = await _create_channel(db_session, tg_id=123456789)
    f = await _create_file(db_session, ch.id, is_cached=False, cache_path=None)

    svc = await _mock_authorized_service()
    result = await cache_file(file_id=f.id, db=db_session)

    # New async behavior: creates CacheRecord(status='caching'), returns immediately
    assert result["is_caching"] is True
    assert result["is_cached"] is False
    assert result["cache_error"] is None

    # Verify CacheRecord was created
    db_q = select(CacheRecordModel).where(CacheRecordModel.file_id == f.id)
    cr_result = await db_session.execute(db_q)
    cr = cr_result.scalar_one_or_none()
    assert cr is not None
    assert cr.status == "caching"


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
    """GIVEN file already cached on disk with CacheRecord
    WHEN POST /cache THEN 200, no re-download."""
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

    # Create CacheRecord (required for "already cached" detection)
    now = datetime.now(timezone.utc)
    cr = CacheRecordModel(
        file_id=f.id,
        file_path=cache_rel,
        file_size=12,
        status="cached",
        cached_at=now,
        accessed_at=now,
    )
    db_session.add(cr)
    await db_session.commit()

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


# ===================================================================
# View endpoint tests (S15–S19)
# ===================================================================

from api.files import view_file, _stream_from_telegram


# ---------------------------------------------------------------------------
# S15 — Happy Path: 查看已缓存图片（inline 流式）
# ---------------------------------------------------------------------------
async def test_view_cached_image(db_session):
    """GIVEN cached image file WHEN GET /view THEN 200 inline with correct mime."""
    ch = await _create_channel(db_session)
    cache_rel = f"{ch.id}/555_view_image.jpg"
    cache_full = CACHE_DIR / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    content = b"\xff\xd8\xff\xe0" + b"x" * 500  # fake JPEG header + body
    cache_full.write_bytes(content)

    f = await _create_file(
        db_session, ch.id,
        file_name="view_image.jpg",
        mime_type="image/jpeg",
        file_size=len(content),
        is_cached=True,
        cache_path=cache_rel,
    )

    response = await view_file(file_id=f.id, db=db_session)

    assert response.status_code == 200
    assert response.media_type == "image/jpeg"
    # Verify inline disposition
    cd = response.headers.get("content-disposition", "")
    assert "inline" in cd

    # Read chunks and verify
    chunks = b""
    async for chunk in response.body_iterator:
        chunks += chunk if isinstance(chunk, bytes) else chunk.encode()
    assert chunks == content

    if cache_full.exists():
        os.remove(str(cache_full))


# ---------------------------------------------------------------------------
# S16 — Happy Path: 查看已缓存视频（inline 流式）
# ---------------------------------------------------------------------------
async def test_view_cached_video(db_session):
    """GIVEN cached video file WHEN GET /view THEN 200 inline video/mp4."""
    ch = await _create_channel(db_session)
    cache_rel = f"{ch.id}/666_view_video.mp4"
    cache_full = CACHE_DIR / cache_rel
    cache_full.parent.mkdir(parents=True, exist_ok=True)
    content = b"\x00\x00\x00\x18ftypmp42" + b"x" * 1000  # fake MP4
    cache_full.write_bytes(content)

    f = await _create_file(
        db_session, ch.id,
        file_name="view_video.mp4",
        mime_type="video/mp4",
        file_size=len(content),
        is_cached=True,
        cache_path=cache_rel,
    )

    response = await view_file(file_id=f.id, db=db_session)

    assert response.status_code == 200
    assert response.media_type == "video/mp4"
    assert "inline" in response.headers.get("content-disposition", "")

    if cache_full.exists():
        os.remove(str(cache_full))


# ---------------------------------------------------------------------------
# S17 — Edge: 未缓存文件从 TG 流式代理
# ---------------------------------------------------------------------------
async def test_view_uncached_streams_from_telegram(db_session):
    """GIVEN file not cached,Telegram authorized
    WHEN GET /view THEN streams via iter_download from TG."""
    ch = await _create_channel(db_session, tg_id=123456789)
    f = await _create_file(
        db_session, ch.id,
        file_name="remote.txt",
        mime_type="text/plain",
        is_cached=False,
        cache_path=None,
    )

    # Mock client with iter_download
    client_mock = AsyncMock()
    client_mock.get_entity = AsyncMock(return_value="fake_entity")
    client_mock.get_messages = AsyncMock()
    client_mock.get_messages.return_value.media = "fake_media"

    # async generator for iter_download
    async def _fake_iter(m, request_size=64 * 1024):
        for chunk in [b"chunk1", b"chunk2", b"chunk3"]:
            yield chunk

    client_mock.iter_download = _fake_iter

    svc = await _mock_authorized_service(client_mock)

    response = await view_file(file_id=f.id, db=db_session)

    assert response.status_code == 200
    assert response.media_type == "text/plain"
    assert "inline" in response.headers.get("content-disposition", "")

    chunks = b""
    async for chunk in response.body_iterator:
        chunks += chunk if isinstance(chunk, bytes) else chunk.encode()
    assert chunks == b"chunk1chunk2chunk3"


# ---------------------------------------------------------------------------
# S18 — Edge: 查看不存在的文件
# ---------------------------------------------------------------------------
async def test_view_file_not_found(db_session):
    """GIVEN file 999 doesn't exist WHEN GET /view THEN 404."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await view_file(file_id=999, db=db_session)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# S19 — Edge: 未缓存且未授权时查看
# ---------------------------------------------------------------------------
async def test_view_uncached_unauthorized(db_session):
    """GIVEN file not cached,Telegram not authorized WHEN GET /view THEN 400."""
    ch = await _create_channel(db_session)
    f = await _create_file(db_session, ch.id, is_cached=False, cache_path=None)

    reset_telegram_service()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await view_file(file_id=f.id, db=db_session)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# S20 — Unit: _safe_filename preserves Chinese chars
# ---------------------------------------------------------------------------
async def test_safe_filename_preserves_chinese():
    """GIVEN filename with Chinese chars WHEN sanitized THEN chars preserved."""
    result = _safe_filename("2024_年终总结.pdf")
    assert result == "2024_年终总结.pdf"


async def test_safe_filename_replaces_windows_illegal():
    """GIVEN filename with Windows-illegal chars WHEN sanitized THEN replaced."""
    result = _safe_filename('a<b>c:"d|e/f\\g*h?')
    assert result == "a_b_c__d_e_f_g_h_"


async def test_safe_filename_preserves_normal():
    """GIVEN normal ASCII filename WHEN sanitized THEN unchanged."""
    result = _safe_filename("photo_2024-01.jpg")
    assert result == "photo_2024-01.jpg"


async def test_safe_filename_handles_spaces():
    """GIVEN filename with spaces WHEN sanitized THEN spaces preserved."""
    result = _safe_filename("my report (1).txt")
    assert result == "my report (1).txt"
