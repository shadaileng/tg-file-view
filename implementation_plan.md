# 实施方案计划

> 参考 tdesktop 源码，对 tg_file_viewer 的三项核心改进
> 日期: 2026-06-07

---

## 目录

- [方案一：分片流式传输](#方案一分片流式传输)
- [方案二：统一下载队列](#方案二统一下载队列)
- [方案三：PhotoSize 缩略图](#方案三-photosize-缩略图)
- [实施顺序与依赖关系](#实施顺序与依赖关系)

---

## 方案一：分片流式传输

### 目标

支持 HTTP Range 请求（206 Partial Content），使浏览器视频播放器可拖拽进度条 seek 到已缓存文件的任意位置。

### 现状

`GET /api/files/{file_id}/view` (`api/files.py:525-607`)：

- 已缓存的：全文件顺序流式传输，无 Range 头处理
- 未缓存的：Telegram `iter_download` 完整流式传输，不支持 offset

当前项目已部分支持流式缓存（局部下载）。分片流式传输利用这个路径，让视频 seek 从本地缓存读取。

### 实现步骤

#### Step 1: 新增 `_file_range_stream` 生成器

**文件**：`api/files.py`（新增，在 `_file_stream` 之后，约第 154 行）

```python
def _file_range_stream(
    file_path: Path,
    start: int,
    end: int,
    chunk_size: int = 64 * 1024
):
    """流式传输文件的指定字节范围 [start, end]（含两端）。"""
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            to_read = min(chunk_size, remaining)
            chunk = f.read(to_read)
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)
```

#### Step 2: 修改 `view_file`，解析 Range 头

**文件**：`api/files.py`，修改 `view_file` 函数（第 525 行）

```python
@router.get("/api/files/{file_id}/view")
async def view_file(
    file_id: int,
    request: Request,  # 新增：注入 FastAPI Request
    db: AsyncSession = Depends(get_db),
):
    # ...（现有查询逻辑不变）...

    # 获取文件总大小
    if file_.is_cached and file_.cache_path:
        full_path = CACHE_DIR / file_.cache_path
        total_size = full_path.stat().st_size if full_path.exists() else 0
    else:
        # 未缓存时从 media 对象获取 size
        total_size = file_.file_size

    # 解析 Range 头
    range_header = request.headers.get("range")
    if range_header and total_size > 0:
        start, end = _parse_range(range_header, total_size)
        if start is not None:
            return await _handle_range_request(
                file_, full_path, start, end, total_size, svc, channel, db
            )

    # 原有全文件流式逻辑（无 Range 头时的 fallback）
    # ...
```

#### Step 3: 实现 Range 头解析和响应

**文件**：`api/files.py`（新增辅助函数）

```python
import re

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")

def _parse_range(range_header: str, total_size: int) -> tuple:
    """解析 HTTP Range 头，返回 (start, end) 或 (None, None)。"""
    m = _RANGE_RE.match(range_header)
    if not m:
        return None, None
    start = int(m.group(1))
    end_str = m.group(2)
    end = int(end_str) if end_str else min(start + 1024 * 1024 - 1, total_size - 1)
    return start, min(end, total_size - 1)
```

#### Step 4: 实现 Range 请求处理

**文件**：`api/files.py`（新增函数）

```python
async def _handle_range_request(
    file_, full_path, start: int, end: int, total_size: int,
    svc, channel, db,
) -> StreamingResponse:
    """处理 Range 请求，返回 206 Partial Content。"""

    # 优先走本地缓存
    if file_.is_cached and full_path and full_path.exists():
        content_length = end - start + 1
        return StreamingResponse(
            _file_range_stream(full_path, start, end),
            media_type=file_.mime_type,
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
            },
        )

    # 未缓存：触发后台缓存，同时 fallback 到全文件流（不支持真正 seek）
    # 当文件正在缓存时，首次播放只能从头看
    # 但第二次播放时文件可能已部分缓存
    if file_.file_size > 0:
        asyncio.create_task(_background_cache(file_.id))

    # Fallback: 返回全文件（浏览器会从头播放）
    # 仍然返回 Accept-Ranges 让浏览器知道支持 Range
    return StreamingResponse(
        _stream_from_telegram(svc, (await _get_message_media(svc, channel.tg_id, file_.message_id))),
        media_type=file_.mime_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Disposition": _make_content_disposition(file_.file_name, "inline"),
        },
    )
```

#### Step 5: 新增 `Accept-Ranges` 到已有完整响应

**文件**：`api/files.py` 第 549-559 行、第 599-607 行

在两个现有 `StreamingResponse` 都加上 `"Accept-Ranges": "bytes"` 头，通知浏览器该端点支持分片请求。

#### Step 6: 验证

- `curl -H "Range: bytes=0-1023" http://localhost:8000/api/files/1/view` → 206, `Content-Range`
- `curl http://localhost:8000/api/files/1/view` → 200, `Accept-Ranges: bytes`
- 浏览器 `<video src="/api/files/1/view" controls>` → 可拖拽进度条

### 涉及文件清单

| 文件 | 改动 |
|------|------|
| `api/files.py` | 新增 `_file_range_stream`、`_parse_range`、`_handle_range_request`；修改 `view_file` 解析 Range 头 |
| `api/utils.py` | 可选：提取 `_parse_range` 为公共函数 |

---

## 方案二：统一下载队列

### 目标

将现有 3 条独立下载路径合并为统一的优先级下载队列，消除重复下载和资源竞争。

### 现状

| 路径 | 位置 | 触发方式 | 用途 |
|------|------|---------|------|
| `_background_cache` | `api/files.py:385` | `asyncio.create_task(...)` | POST /cache |
| `_ensure_cached` | `api/files.py:157` | 同步阻塞 | GET /download |
| `task_queue._ensure_cached` | `services/task_queue.py:664` | Worker 内调用 | 缩略图生成 |

### 实现步骤

#### Step 1: 新增 DownloadJob 模型

**文件**：`models.py`，在 `CacheRecord` 类之后、`SyncTask` 类之前添加

```python
class DownloadJob(Base):
    """统一下载任务队列。"""
    __tablename__ = "download_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)  # 1=最高, 10=最低
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # view, cache, thumb
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, downloading, completed, failed, cancelled
    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(default=0)
    max_retries: Mapped[int] = mapped_column(default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

同时 `File` 模型需要新增一个字段来标记是否有待处理的下载：

```python
# File 模型新增
download_jobs = relationship("DownloadJob", back_populates="file", cascade="all, delete-orphan")
```

#### Step 2: 创建 DownloadQueue 服务

**文件**：新建 `services/download_queue.py`

```python
"""Unified download queue with priority-based scheduling.

Architecture (same pattern as ThumbnailWorkerPool):
- Producer: atomically claims pending DownloadJobs from DB, enqueues to asyncio.PriorityQueue
- Workers: block on queue, download files, update DB

Priority:
  1 = user viewing (view_file triggered)
  3 = user clicked cache
  5 = thumbnail generation
  7 = background pre-cache (future)
"""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from models import File as FileModel, DownloadJob as DownloadJobModel
from database import AsyncSessionLocal

_PURPOSE_PRIORITY = {
    "view": 1,
    "cache": 3,
    "thumb": 5,
}


@dataclass(order=True)
class QueuedJob:
    priority: int
    file_id: int = field(compare=False)
    job_id: str = field(compare=False)


class DownloadQueue:
    """Producer-Consumer download queue with priority scheduling.

    Key behaviors:
    - Same file_id cannot be queued twice (dedup by file_id)
    - Higher priority preempts lower priority (front of queue)
    - Workers check in-memory cancel set at checkpoints (no DB round-trip)
    """

    def __init__(self, num_workers: int = 2, cache_dir: str = "./data/cache"):
        self.num_workers = num_workers
        self.cache_dir = Path(cache_dir)

        self._queue: asyncio.PriorityQueue[QueuedJob] = asyncio.PriorityQueue(maxsize=100)
        self._wake: asyncio.Event = asyncio.Event()
        self._cancelled: set[str] = set()
        self._active_file_ids: dict[int, str] = {}  # file_id → job_id (dedup)

        self._workers: list[asyncio.Task] = []
        self._producer: asyncio.Task | None = None
        self._shutdown = False

    # ── Public API ─────────────────────────────────────────────

    async def enqueue(self, file_id: int, purpose: str) -> str | None:
        """Add a download job to the queue.

        Returns:
            job_id string on success.
            None if a job for this file_id already exists (dedup).
        """
        priority = _PURPOSE_PRIORITY.get(purpose, 5)

        if file_id in self._active_file_ids:
            # Already queued — optionally upgrade priority if new purpose is higher
            existing_job_id = self._active_file_ids[file_id]
            logger.debug("File {} already queued (job {}), skipping", file_id, existing_job_id)
            return None

        job_id = str(uuid.uuid4())
        self._active_file_ids[file_id] = job_id

        # Insert to DB
        async with AsyncSessionLocal() as session:
            job = DownloadJobModel(
                id=job_id,
                file_id=file_id,
                priority=priority,
                purpose=purpose,
                status="pending",
            )
            session.add(job)
            await session.commit()

        # Wake producer
        self._wake.set()
        logger.info("Enqueued download job {} for file_id={} (purpose={}, priority={})",
                    job_id, file_id, purpose, priority)
        return job_id

    async def cancel(self, job_id: str) -> None:
        """Cancel a job (O(1) in-memory + DB update)."""
        self._cancelled.add(job_id)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(DownloadJobModel)
                .where(DownloadJobModel.id == job_id)
                .values(status="cancelled")
            )
            await session.commit()

    def signal(self) -> None:
        """Wake producer to check for pending jobs."""
        self._wake.set()

    # ── Lifecycle ───────────────────────────────────────────
    # Same pattern as ThumbnailWorkerPool:
    #   start() → _recover_stale_jobs() + spawn producer + workers
    #   stop()  → cancel producer → drain queue → cancel workers
    # ── Producer Loop ───────────────────────────────────────
    #   Batch-claim pending DownloadJobs from DB,
    #   CAS update status="downloading",
    #   push to PriorityQueue
    # ── Worker Loop ─────────────────────────────────────────
    #   Get job from queue,
    #   Call api.files._download_from_telegram(...),
    #   Update File.is_cached + CacheRecord,
    #   If purpose == "thumb": signal ThumbnailWorkerPool
```

#### Step 3: 将现有下载入口改为入队

**文件**：`api/files.py` 修改 3 处

**`cache_file`（第 311-382 行）**：将 `asyncio.create_task(_background_cache(file_id))` 替换为 `await download_queue.enqueue(file_id, purpose="cache")`

```python
# 替换第 361 行
from services.download_queue import get_download_queue
dq = get_download_queue()
await dq.enqueue(file_.id, purpose="cache")
```

**`view_file`（第 525 行）**：当文件未缓存时，入队一个 `view` 优先级下载：

```python
# 在 view_file 原有逻辑中，当文件未缓存时
if not (file_.is_cached and file_.cache_path and ...):
    dq = get_download_queue()
    await dq.enqueue(file_.id, purpose="view")
```

**`task_queue.py:_ensure_cached`（第 664 行）**：将直接下载替换为通过 download_queue 入队：

```python
# 修改 _ensure_cached 中的下载逻辑
async def _ensure_cached(self, session, file_record, permanent=True):
    # ...
    if not file_record.is_cached:
        dq = get_download_queue()
        job_id = await dq.enqueue(file_record.id, purpose="thumb")
        # 等待 job 完成（polling 或 event）
        # ...
```

#### Step 4: 数据库迁移

**文件**：新建 `data/migrations/002_add_download_jobs.sql`

```sql
CREATE TABLE IF NOT EXISTS download_jobs (
    id TEXT PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 5,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    bytes_downloaded BIGINT NOT NULL DEFAULT 0,
    total_bytes BIGINT NOT NULL DEFAULT 0,
    error_msg TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_download_jobs_status ON download_jobs(status);
CREATE INDEX IF NOT EXISTS idx_download_jobs_file_id ON download_jobs(file_id);
```

### 涉及文件清单

| 文件 | 改动 |
|------|------|
| `models.py` | 新增 `DownloadJob` 表 |
| `services/download_queue.py` | **新建**：生产者-消费者下载队列 |
| `api/files.py` | `cache_file` 改为入队；`view_file` 加入 view 优先级下载 |
| `services/task_queue.py` | `_ensure_cached` 改为通过 download_queue 下载 |
| `data/migrations/002_add_download_jobs.sql` | **新建**：DDL |
| `services/cache_manager.py` | 可选：检查 queue 占用，避免 LRU 与下载冲突 |

---

## 方案三：PhotoSize 缩略图

### 目标

利用 Telegram MessageMediaPhoto 的 PhotoSize 嵌入缩略图（几 KB），替代当前"下载完整原图→Pillow 缩放"的路径。

### 现状

`sync_engine.py:_extract_file_info()`（第 148-158 行）已拿到 `media.photo.sizes`，但只取最大尺寸：

```python
sizes = getattr(media.photo, "sizes", []) or []
if sizes:
    largest = max(sizes, key=lambda s: getattr(s, "size", 0) or 0)
    info["file_size"] = getattr(largest, "size", 0)
```

**Telegram PhotoSize 层级**（从小到大）：

| type | 典型尺寸 | bytes 是否嵌入 | 典型字节数 |
|------|---------|---------------|-----------|
| s | ~90x90 | ✅ 嵌入 | ~1-3 KB |
| m | ~320x320 | ✅ 有时嵌入 | ~5-30 KB |
| x | ~800x800 | ❌ 需下载 | ~50-200 KB |
| y | ~1280x1280 | ❌ 需下载 | ~200-500 KB |
| w | ~2560x2560 | ❌ 需下载 | ~500+ KB |

`PhotoSize.bytes` 字段对于 type "s"（有时 "m"）已经是 JPEG 二进制数据，**可以直接写入 thumb_path**。

### 实现步骤

#### Step 1: 同步阶段提取缩略图元信息

**文件**：`services/sync_engine.py`，修改 `_extract_file_info`

```python
def _extract_file_info(message) -> Optional[dict]:
    # ...
    if hasattr(media, "photo") and media.photo is not None:
        info["file_type"] = "photo"
        info["mime_type"] = "image/jpeg"
        info["file_name"] = f"photo_{message.id}.jpg"

        sizes = getattr(media.photo, "sizes", []) or []
        if sizes:
            # 取最大尺寸算 file_size
            largest = max(sizes, key=lambda s: getattr(s, "size", 0) or 0)
            info["file_size"] = getattr(largest, "size", 0)

            # 新增：提取最小可用缩略图
            thumb_info = _extract_photo_thumb_info(sizes)
            if thumb_info:
                info["thumb_data"] = thumb_info["data"]   # bytes or None
                info["thumb_size"] = thumb_info["size"]    # "s", "m"
                info["thumb_type"] = "embedded" if thumb_info["data"] else "downloadable"

        return info
```

新增 `_extract_photo_thumb_info`：

```python
def _extract_photo_thumb_info(sizes: list) -> Optional[dict]:
    """从 PhotoSize 列表中提取最小的可用缩略图。

    优先使用 type='s'（嵌入 bytes，无需额外下载）。
    若无 s 则返回最小的可下载尺寸的 location 信息。
    """
    for sz in sizes:
        type_name = getattr(sz, "type", "")
        # 's' = small, 通常嵌入 bytes
        if type_name == "s":
            data = getattr(sz, "bytes", None)
            return {
                "data": data,       # JPEG bytes or None
                "type": type_name,
                "size": getattr(sz, "size", 0),
                "w": getattr(sz, "w", 0),
                "h": getattr(sz, "h", 0),
            }
    # 无 s 尺寸，返回最小尺寸用于下载
    if sizes:
        smallest = min(sizes, key=lambda s: getattr(s, "size", 0) or 0)
        data = getattr(smallest, "bytes", None)
        return {
            "data": data,
            "type": getattr(smallest, "type", "?"),
            "size": getattr(smallest, "size", 0),
        }
    return None
```

#### Step 2: 模型新增字段

**文件**：`models.py`，`File` 模型新增

```python
# 在 file_type 字段之后
thumb_sizes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 可用缩略图尺寸元信息
thumb_data: Mapped[str | None] = mapped_column(Text, nullable=True)   # base64: 嵌入的 JPEG bytes（type='s'）
```

注意：`thumb_data` 存的是 base64 编码的 JPEG 数据，最长可能 3-4KB，用 TEXT 存储没问题。

#### Step 3: 修改同步引擎写入逻辑

**文件**：`services/sync_engine.py`，`_batch_insert_files` 函数中处理新字段

```python
# 在 _batch_insert_files 的 File(...) 创建处（第 195-203 行）
f = File(
    channel_id=channel_id,
    message_id=info["message_id"],
    file_name=info["file_name"],
    file_size=info["file_size"],
    mime_type=info["mime_type"],
    file_type=info["file_type"],
    tg_ref=info.get("tg_ref"),
    # 新增
    thumb_sizes=json.dumps(info.get("thumb_sizes", {})) if info.get("thumb_sizes") else None,
    thumb_data=base64.b64encode(info["thumb_data"]).decode("ascii") if info.get("thumb_data") else None,
)
```

#### Step 4: 修改缩略图生成逻辑

**文件**：`services/task_queue.py`，修改 `_process_job`

在 `_process_job`（第 427 行）中，判断 `file_record.file_type == "photo"` 时走新路径：

```python
async def _process_job(self, job_id, file_id, worker_id):
    # ... 现有逻辑 ...

    # Photo 缩略图：优先使用嵌入的 PhotoSize
    if file_record.file_type == "photo":
        if file_record.thumb_data:
            # Phase A: 嵌入的 JPEG bytes → 直接写入磁盘
            await self._process_photo_embedded_thumb(session, job, file_record, file_id, worker_id)
            return
        elif file_record.thumb_sizes:
            # Phase B: 有尺寸信息可下载 → 下载小尺寸缩略图
            await self._process_photo_downloadable_thumb(session, job, file_record, file_id, worker_id)
            return
        # Phase C: fallback 到现有 Pillow 逻辑
        await self._process_image_thumb(session, job, file_record, file_id, worker_id)
        return

    # Video 和现有逻辑保持不变
    if file_record.file_type == "video":
        await self._process_video_via_tg_thumb(...)
        return

    await self._process_image_thumb(...)
```

新增 `_process_photo_embedded_thumb`：

```python
async def _process_photo_embedded_thumb(
    self, session, job, file_record, file_id, worker_id
) -> None:
    """Phase A: 直接将嵌入的 PhotoSize bytes 写为缩略图文件。零网络请求，零 I/O 延迟。"""
    if not file_record.thumb_data:
        await self._handle_failure(session, job, "No embedded thumb data")
        return

    job.phase = "generating"
    job.progress = 70
    await session.commit()

    thumb_rel = f"{file_record.channel_id}/{file_id}.jpg"
    thumb_full = self.thumb_dir / thumb_rel
    thumb_full.parent.mkdir(parents=True, exist_ok=True)

    try:
        import base64
        data = base64.b64decode(file_record.thumb_data)
        thumb_full.write_bytes(data)
    except Exception as e:
        await self._handle_failure(session, job, f"Failed to write embedded thumb: {e}")
        return

    file_record.thumb_path = thumb_rel
    file_record.thumb_type = "telegram"  # 标记来源

    job.status = "completed"
    job.phase = "completed"
    job.progress = 100
    job.completed_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info(
        "Worker {} completed job {} via embedded PhotoSize ({} bytes) → {}",
        worker_id, str(job.id), len(data), thumb_rel,
    )
```

新增 `_process_photo_downloadable_thumb`：

```python
async def _process_photo_downloadable_thumb(
    self, session, job, file_record, file_id, worker_id
) -> None:
    """Phase B: 下载 Telegram 的小尺寸 PhotoSize（而非完整原图）。

    利用 telethon 的 thumb 参数，传入 PhotoSize.type
    （如 "m" 或 "x"）只下载几百 KB 的小图，而非完整原图。
    """
    job.phase = "downloading"
    job.progress = 30
    await session.commit()

    # 解析 thumb_sizes 获取最小可下载尺寸
    import json
    sizes = json.loads(file_record.thumb_sizes or "{}")

    thumb_rel = f"{file_record.channel_id}/{file_id}.jpg"
    thumb_full = self.thumb_dir / thumb_rel
    thumb_full.parent.mkdir(parents=True, exist_ok=True)

    try:
        from services.telegram_client import get_telegram_service, AuthState
        from models import Channel as ChannelModel

        svc = get_telegram_service()
        if svc is None or svc.auth_state != AuthState.AUTHORIZED:
            await self._handle_failure(session, job, "Telegram not authorized")
            return

        channel = await session.get(ChannelModel, file_record.channel_id)
        if channel is None:
            await self._handle_failure(session, job, "Channel not found")
            return

        client = await svc.get_client()
        entity = await client.get_entity(channel.tg_id)
        message = await client.get_messages(entity, ids=file_record.message_id)

        if message and message.media:
            # 使用 PhotoSize.type 作为 thumb 参数
            thumb_type = sizes.get("type", "m")
            path = await client.download_media(
                message, file=str(thumb_full), thumb=thumb_type
            )
            if path and thumb_full.exists() and thumb_full.stat().st_size > 0:
                file_record.thumb_path = thumb_rel
                file_record.thumb_type = "telegram"

                job.status = "completed"
                job.phase = "completed"
                job.progress = 100
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(
                    "Worker {} completed job {} via PhotoSize download (type={}) → {}",
                    worker_id, str(job.id), thumb_type, thumb_rel,
                )
                return

    except Exception as e:
        logger.warning("PhotoSize download failed for file_id={}: {}", file_id, e)

    # Fallback: 走完整文件下载 + Pillow
    await self._process_image_thumb(session, job, file_record, file_id, worker_id)
```

#### Step 5: 数据库迁移

**文件**：新建 `data/migrations/003_add_thumb_fields.sql`

```sql
ALTER TABLE files ADD COLUMN thumb_sizes TEXT;
ALTER TABLE files ADD COLUMN thumb_data TEXT;
```

#### Step 6: 后端 API 暴露 thumb_sizes

**文件**：`api/files.py` 的 `_file_to_dict`（第 53 行）

```python
return {
    # ... 现有字段 ...
    "thumb_path": file_.thumb_path,
    "thumb_type": file_.thumb_type,
    "thumb_sizes": json.loads(file_.thumb_sizes) if file_.thumb_sizes else None,
    # 注意：thumb_data 不暴露给前端（只用于后端缩略图生成）
}
```

#### Step 7: 测试

**文件**：`tests/test_sync_engine.py` 新增测试

```python
async def test_extract_photo_thumb_info_embedded():
    """验证 PhotoSize type='s' 的嵌入 bytes 被正确提取。"""
    # ...

async def test_extract_photo_thumb_info_no_s():
    """验证无 's' 尺寸时返回最小可下载尺寸。"""
    # ...

async def test_photo_embedded_thumb_job():
    """验证嵌入 bytes 的缩略图任务直接写盘、不经下载。"""
    # ...
```

### 涉及文件清单

| 文件 | 改动 |
|------|------|
| `services/sync_engine.py` | `_extract_file_info` 新增 thumb 提取逻辑；新增 `_extract_photo_thumb_info` |
| `models.py` | `File` 新增 `thumb_sizes`、`thumb_data` 字段 |
| `services/task_queue.py` | `_process_job` 新增 photo 判断路径；新增 `_process_photo_embedded_thumb`、`_process_photo_downloadable_thumb` |
| `api/files.py` | `_file_to_dict` 暴露 `thumb_sizes` |
| `data/migrations/003_add_thumb_fields.sql` | **新建**：DDL |
| `tests/test_sync_engine.py` | 新增 PhotoSize 提取测试 |
| `tests/test_post_sync_thumb.py` | 新增嵌入缩略图任务测试 |

---

## 实施顺序与依赖关系

```
                         ┌─────────────────┐
                         │ PhotoSize 缩略图  │ ← 独立，零依赖，优先实施
                         │  (~0.5 天)       │
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │ 分片流式传输     │ ← 轻度依赖缓存路径
                         │  (~1 天)        │   改动集中在 api/files.py
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │ 统一下载队列     │ ← 依赖前面两个的重构基础
                         │  (~2 天)        │   改动范围最大
                         └─────────────────┘
```

**建议迭代节奏**：每个方案独立开发、测试、合并，不要一次改三个。每个方案完成后跑全量测试。
