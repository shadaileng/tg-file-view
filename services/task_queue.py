"""Producer-Consumer thumbnail worker pool.

Generates thumbnails/covers for cached files:
- photo/sticker: Pillow thumbnail from temporary download
- video: Telegram pre-generated thumbnail (fast path, few KB)

Architecture:
- 1 Producer: atomically claims pending jobs from DB, enqueues to asyncio.Queue
- N Workers: block on queue.get(), process jobs without DB contention
- asyncio.Event: wakes producer instantly when new jobs are created
- Memory cancel set: O(1) cancellation detection, no DB round-trip needed
- Job-level timeout: asyncio.wait_for guards against stuck downloads

Eliminates: multi-worker CAS contention, polling waste, and double attempt-count bug.
"""

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import File as FileModel, ThumbJob

# Priority values (lower = higher priority)
_PRIORITY_MAP: dict[str, int] = {
    "photo": 3,
    "sticker": 4,
    "video": 4,
    "document": 5,
}
_DEFAULT_PRIORITY = 5

# Supported file types for thumbnail/cover generation
_SUPPORTED_TYPES = frozenset({"photo", "sticker", "video"})

# Retry backoff in seconds
_RETRY_BACKOFF = [1, 2, 4]

# Default job timeout in seconds (0 = no timeout)
_DEFAULT_JOB_TIMEOUT = 600

# Heartbeat interval in seconds
_HEARTBEAT_INTERVAL = 30


def _get_priority(file_type: str) -> int:
    """Map file_type to priority; lower = higher priority."""
    return _PRIORITY_MAP.get(file_type, _DEFAULT_PRIORITY)


def generate_thumbnail(
    file_path: Path,
    thumb_path: Path,
    max_width: int = 320,
    max_height: int = 240,
) -> bool:
    """Generate a thumbnail for an image file using Pillow.

    Returns True on success, False if the format is unsupported.
    """
    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(file_path) as img:
            # Convert to RGB if needed (for RGBA, P, etc.)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            # Create thumbnail (maintains aspect ratio, in-place resize)
            img.thumbnail((max_width, max_height), Image.LANCZOS)
            img.save(thumb_path, "JPEG", quality=85)
            return True
    except Exception as e:
        logger.warning("Failed to generate thumbnail from {}: {}", file_path, e)
        return False


class ThumbnailWorkerPool:
    """Producer-Consumer thumbnail worker pool.

    Architecture:
    - 1 Producer uniquely claims pending jobs from DB → asyncio bounded Queue
    - N Workers block on queue.get() and process, no DB contention
    - asyncio.Event wakes producer instantly when new jobs are created
    - Memory cancel set enables O(1) cancel detection at worker checkpoints

    On startup, stale processing jobs are recovered. On shutdown, producer
    is cancelled first, queue drained, then workers exit gracefully.
    """

    def __init__(
        self,
        num_workers: int = 2,
        thumb_dir: str = "./data/thumbnails",
        cache_dir: str = "./data/cache",
        max_width: int = 320,
        max_height: int = 240,
        job_timeout: float = _DEFAULT_JOB_TIMEOUT,
    ):
        self.num_workers = num_workers
        self.thumb_dir = Path(thumb_dir)
        self.cache_dir = Path(cache_dir)
        self.max_width = max_width
        self.max_height = max_height
        self.job_timeout = job_timeout  # seconds, 0 = no timeout

        # Producer-Consumer primitives
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=50)  # bounded → backpressure
        self._wake: asyncio.Event = asyncio.Event()              # signals producer: new jobs available
        self._cancelled: set[str] = set()                        # O(1) in-memory cancel tracking

        # Stats tracking
        self._stats: dict[str, int] = {"claimed": 0, "completed": 0, "failed": 0, "timed_out": 0}

        self._workers: list[asyncio.Task] = []
        self._producer: asyncio.Task | None = None
        self._shutdown = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start pool: recover stale jobs, spawn 1 producer + N workers."""
        logger.info("Starting thumbnail worker pool (workers={})", self.num_workers)
        self._shutdown = False

        # Reset any stale 'processing' jobs left from a previous crash
        await self._recover_stale_jobs()

        # Spawn 1 producer (sole DB claimer) + N workers (queue consumers)
        self._producer = asyncio.create_task(self._producer_loop())
        for i in range(self.num_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self._workers.append(task)
        logger.info("Thumbnail pool started: 1 producer + {} workers", self.num_workers)

    async def stop(self) -> None:
        """Graceful shutdown: cancel producer → drain queue → cancel workers."""
        logger.info(
            "Stopping thumbnail worker pool. Final stats: claimed={}, completed={}, failed={}, timed_out={}",
            self._stats["claimed"], self._stats["completed"],
            self._stats["failed"], self._stats["timed_out"],
        )
        self._shutdown = True

        # Step 1: wake + cancel producer so no new jobs enter the queue
        self._wake.set()
        if self._producer:
            self._producer.cancel()
            try:
                await self._producer
            except asyncio.CancelledError:
                pass

        # Step 2: drain remaining queued jobs (will be re-claimed on restart)
        drained = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.info("Drained {} queued jobs during shutdown", drained)

        # Step 3: cancel all workers
        for t in self._workers:
            t.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()

        logger.info("Thumbnail worker pool stopped")

    # ------------------------------------------------------------------
    # Public signals (called by API layer)
    # ------------------------------------------------------------------

    def signal_new_jobs(self) -> None:
        """Wake the producer to immediately claim newly created pending jobs.

        Call after inserting new ThumbJob(s) with status='pending'.
        Idempotent — multiple calls before producer wakes are harmless.
        """
        self._wake.set()

    def cancel_job(self, job_id: str) -> None:
        """Mark job as cancelled in the in-memory set (O(1) worker detection).

        Workers check this set at key checkpoints instead of doing a DB round-trip.
        The DB status is still the source of truth; this is a fast-path optimization.
        """
        self._cancelled.add(job_id)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _recover_stale_jobs(self) -> None:
        """On startup, reset any stale 'processing' jobs back to 'pending'.

        These are jobs that were being processed when the server crashed.
        Without this, they'd be stuck in 'processing' forever.
        """
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    update(ThumbJob)
                    .where(ThumbJob.status == "processing")
                    .values(
                        status="pending",
                        phase="pending",
                        progress=0,
                        started_at=None,
                    )
                )
                count = result.rowcount
            if count > 0:
                logger.info("Recovered {} stale processing jobs → pending", count)

    # ------------------------------------------------------------------
    # Producer loop (sole DB claimer)
    # ------------------------------------------------------------------

    async def _producer_loop(self) -> None:
        """Single producer: batch-claim pending jobs from DB and enqueue them.

        Only one producer exists → no multi-worker CAS contention.
        Uses Event-driven wake instead of polling → zero CPU waste when idle.
        """
        logger.info("Thumbnail producer started")
        _last_heartbeat = 0

        while not self._shutdown:
            try:
                # Phase 1: batch-claim all pending jobs
                enqueued = 0
                while not self._shutdown:
                    claimed = await self._claim_next()
                    if claimed is None:
                        break  # no more pending jobs

                    job_id, file_id = claimed
                    # Skip if cancelled before even entering the queue
                    if job_id in self._cancelled:
                        self._cancelled.discard(job_id)
                        continue

                    # Enqueue (blocks if queue is full → natural backpressure)
                    await self._queue.put(claimed)
                    enqueued += 1

                if enqueued > 0:
                    self._stats["claimed"] += enqueued
                    logger.info("[producer] claimed {} jobs → queue (qsize={})", enqueued, self._queue.qsize())

                # Phase 2: idle — wait for wake signal or periodic re-poll
                if not self._shutdown:
                    self._wake.clear()
                    # Heartbeat log while idle
                    now = asyncio.get_event_loop().time()
                    if now - _last_heartbeat > _HEARTBEAT_INTERVAL:
                        logger.info("[heartbeat] Producer idle (queue={})", self._queue.qsize())
                        _last_heartbeat = now
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass  # periodic re-poll catches any missed signals

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Producer error: {}", e)
                await asyncio.sleep(1)

        logger.info(
            "Thumbnail producer stopped. Stats: claimed={}, queue={}",
            self._stats["claimed"], self._queue.qsize(),
        )

    # ------------------------------------------------------------------
    # DB claim (called only by producer)
    # ------------------------------------------------------------------

    async def _claim_next(self, caller: str = "producer") -> tuple[str, int] | None:
        """Atomically claim the next pending job from DB.

        Uses SELECT + conditional UPDATE (CAS) to guarantee exclusive claim.
        Only called by the single producer — no multi-worker contention.

        Returns (job_id, file_id) or None if no pending jobs exist.
        """
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Step 1: find highest-priority pending job
                result = await session.execute(
                    select(ThumbJob.id, ThumbJob.file_id)
                    .where(ThumbJob.status == "pending")
                    .order_by(ThumbJob.priority.asc(), ThumbJob.created_at.asc())
                    .limit(1)
                )
                row = result.one_or_none()
                if row is None:
                    return None

                job_id, file_id = row

                # Step 2: atomically claim it (CAS: only if still pending)
                update_result = await session.execute(
                    update(ThumbJob)
                    .where(
                        ThumbJob.id == job_id,
                        ThumbJob.status == "pending",  # CAS — only claim if unclaimed
                    )
                    .values(
                        status="processing",
                        phase="processing",
                        progress=10,
                        started_at=datetime.now(timezone.utc),
                        attempt=ThumbJob.attempt + 1,
                    )
                )

            if update_result.rowcount == 0:
                # Cancelled or claimed by a pre-refactor worker — skip
                logger.debug("{} CAS miss on job {}", caller, job_id)
                return None

            return str(job_id), file_id

    # ------------------------------------------------------------------
    # Worker loop (queue consumer)
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker: block on queue.get() for jobs, process them.

        No DB polling — wakes instantly when producer enqueues a job.
        Checks in-memory cancel set before processing to skip cancelled jobs.
        Wraps _process_job with asyncio.wait_for for job-level timeout.
        Emits heartbeat log every _HEARTBEAT_INTERVAL seconds when idle.
        """
        logger.info("Thumbnail worker {} started", worker_id)
        _last_idle_log = 0.0

        while not self._shutdown:
            try:
                # Block on queue (with timeout to check _shutdown periodically)
                claimed = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                # Heartbeat: log idle status every _HEARTBEAT_INTERVAL
                now = asyncio.get_event_loop().time()
                if now - _last_idle_log > _HEARTBEAT_INTERVAL:
                    logger.info("[heartbeat] Worker {} idle (queue={})", worker_id, self._queue.qsize())
                    _last_idle_log = now
                continue

            job_id, file_id = claimed
            t0 = asyncio.get_event_loop().time()

            try:
                # Fast cancel check — O(1) memory lookup, no DB round-trip
                if job_id in self._cancelled:
                    self._cancelled.discard(job_id)
                    logger.debug("Worker {} skipping cancelled job {}", worker_id, job_id)
                    self._queue.task_done()
                    continue

                logger.info("[worker {}] processing job {} (file_id={})...", worker_id, job_id, file_id)

                # ── Task-level timeout guard ──
                if self.job_timeout > 0:
                    await asyncio.wait_for(
                        self._process_job(job_id, file_id, worker_id),
                        timeout=self.job_timeout,
                    )
                else:
                    await self._process_job(job_id, file_id, worker_id)

                self._stats["completed"] += 1
                elapsed = asyncio.get_event_loop().time() - t0
                logger.info("[worker {}] completed job {} (elapsed {:.1f}s)", worker_id, job_id, elapsed)

            except asyncio.TimeoutError:
                self._stats["timed_out"] += 1
                elapsed = asyncio.get_event_loop().time() - t0
                logger.warning(
                    "[worker {}] job {} timed out after {:.0f}s (limit: {:.0f}s)",
                    worker_id, job_id, elapsed, self.job_timeout,
                )
                await self._mark_job_timeout(job_id)

            except asyncio.CancelledError:
                self._queue.task_done()
                break

            except Exception as e:
                self._stats["failed"] += 1
                elapsed = asyncio.get_event_loop().time() - t0
                logger.exception("[worker {}] job {} failed after {:.1f}s: {}", worker_id, job_id, elapsed, e)

            finally:
                self._queue.task_done()

        logger.info("Thumbnail worker {} stopped", worker_id)

    # ------------------------------------------------------------------
    # Process a single job
    # ------------------------------------------------------------------

    async def _process_job(self, job_id: str, file_id: int, worker_id: int) -> None:
        """Process a single thumbnail job: download cache → generate thumb → update DB.

        For videos: downloads Telegram's pre-generated thumbnail (near-instant, few KB).
        For photos/stickers: downloads full file and generates thumbnail via Pillow.

        Checks in-memory cancel set + DB status at checkpoints to avoid
        overwriting user-cancelled state.
        """
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            # Load job and file
            job = await session.get(ThumbJob, job_id)
            if job is None:
                return

            # Checkpoint 0: skip if cancelled (memory set + DB)
            if job_id in self._cancelled or job.status == "cancelled":
                self._cancelled.discard(job_id)
                logger.debug("Worker {} skipping cancelled job {}", worker_id, job_id)
                return

            file_record = await session.get(FileModel, file_id)
            if file_record is None:
                job.status = "failed"
                job.phase = "failed"
                job.error_msg = "File record not found in database"
                await session.commit()
                return

            # Mark as processing (attempt already incremented by _claim_next via CAS)
            # Only increment attempt if this job was NOT pre-claimed by the producer
            # (i.e., direct _process_job call for testing / recovery paths).
            if job.status == "pending":
                job.attempt += 1
            job.status = "processing"
            job.phase = "processing"
            job.progress = 10
            job.started_at = datetime.now(timezone.utc)
            # NOTE: attempt is NOT incremented here — _claim_next already did
            #       ThumbJob.attempt + 1 atomically via SQL CAS. Double-increment
            #       would cause retries to exhaust prematurely (attempt 2→4→dead).
            await session.commit()

            logger.debug("Worker {} processing job {} (file_id={}, attempt={})",
                        worker_id, job_id, file_id, job.attempt)

            # ── Video: use Telegram's pre-generated thumbnail (fast path) ──
            if file_record.file_type == "video":
                await self._process_video_via_tg_thumb(session, job, file_record, file_id, worker_id)
                return

            # ── Photo/sticker: download full file → Pillow thumbnail ──
            await self._process_image_thumb(session, job, file_record, file_id, worker_id)

    async def _process_video_via_tg_thumb(
        self, session, job: ThumbJob, file_record: FileModel, file_id: int, worker_id: int
    ) -> None:
        """Fast path: download Telegram's pre-generated video thumbnail (few KB, sub-second).

        Falls back to full download + ffmpeg if TG thumb is unavailable.
        """
        job.phase = "downloading"
        job.progress = 30
        await session.commit()

        tg_thumb = await self._download_telegram_thumb(file_record)

        # Checkpoint 1: after TG thumb download — fast cancel check
        if str(job.id) in self._cancelled:
            self._cancelled.discard(str(job.id))
            logger.info("Job {} cancelled after TG thumb download", str(job.id))
            return

        if tg_thumb is None:
            await self._handle_failure(
                session, job,
                "Telegram video thumbnail unavailable — TG thumb is the only supported method"
            )
            return

        # Success: use TG thumbnail
        thumb_rel = f"{file_record.channel_id}/{file_id}.jpg"
        file_record.thumb_path = thumb_rel
        file_record.thumb_type = "telegram"

        # Checkpoint 2: final confirmation before writing completed
        if str(job.id) in self._cancelled:
            self._cancelled.discard(str(job.id))
            logger.info("Job {} cancelled before completion", str(job.id))
            return

        job.status = "completed"
        job.phase = "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("Worker {} completed job {} via TG thumb → {}", worker_id, str(job.id), thumb_rel)

    async def _process_image_thumb(
        self, session, job: ThumbJob, file_record: FileModel, file_id: int, worker_id: int
    ) -> None:
        """Standard path: download file temporarily and generate thumbnail via Pillow.

        Uses permanent=False for _ensure_cached to avoid polluting the cache directory.
        The temporary file is cleaned up in finally block to cover all exit paths.

        Checks in-memory cancel set at 3 checkpoints instead of DB refresh,
        enabling O(1) cancellation detection even during long I/O operations.
        """

        # Step 1: Ensure file is available (download temporarily if not cached)
        job.phase = "downloading"
        job.progress = 30
        await session.commit()

        was_already_cached = file_record.is_cached
        cache_path = await self._ensure_cached(session, file_record, permanent=False)

        try:
            # Checkpoint 1: after download, O(1) cancel check
            if str(job.id) in self._cancelled:
                self._cancelled.discard(str(job.id))
                logger.info("Job {} cancelled after download", str(job.id))
                return

            if cache_path is None:
                await self._handle_failure(session, job, "Failed to download file from Telegram")
                return

            # Step 2: Check supported format
            if file_record.file_type not in _SUPPORTED_TYPES:
                await self._handle_failure(session, job, f"Unsupported format: {file_record.file_type}")
                return

            # Step 3: Generate thumbnail
            job.phase = "generating"
            job.progress = 70
            await session.commit()

            thumb_rel = f"{file_record.channel_id}/{file_id}.jpg"
            thumb_full = self.thumb_dir / thumb_rel

            success = generate_thumbnail(
                cache_path, thumb_full,
                max_width=self.max_width,
                max_height=self.max_height,
            )

            # Checkpoint 2: after generation, O(1) cancel check
            if str(job.id) in self._cancelled:
                self._cancelled.discard(str(job.id))
                logger.info("Job {} cancelled after generation", str(job.id))
                return

            if not success:
                await self._handle_failure(session, job, "Thumbnail generation failed — unsupported or corrupt image")
                return

            # Step 4: Update file record
            file_record.thumb_path = thumb_rel
            file_record.thumb_type = "auto"

            # Checkpoint 3: final confirmation, O(1) cancel check
            if str(job.id) in self._cancelled:
                self._cancelled.discard(str(job.id))
                logger.info("Job {} cancelled before completion", str(job.id))
                return

            job.status = "completed"
            job.phase = "completed"
            job.progress = 100
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info("Worker {} completed job {} → thumb: {}", worker_id, str(job.id), thumb_rel)
        finally:
            # Always clean up temp file if it was downloaded just for this job
            if not was_already_cached and cache_path is not None:
                try:
                    cache_path.unlink(missing_ok=True)
                    # Also remove the temp directory if empty
                    cache_path.parent.rmdir()
                except (OSError, Exception) as e:
                    logger.debug("Could not fully clean up temp file {}: {}", cache_path, e)

    async def _download_telegram_thumb(
        self, file_record: FileModel,
    ) -> Path | None:
        """Download Telegram's pre-generated video thumbnail (few KB, near-instant).

        Only for video files. Returns the thumb file path on success, None on failure.
        """
        from services.telegram_client import get_telegram_service, AuthState
        from models import Channel as ChannelModel
        from database import AsyncSessionLocal

        svc = get_telegram_service()
        if svc is None or svc.auth_state != AuthState.AUTHORIZED:
            logger.warning("Cannot download TG thumb: Telegram not authorized")
            return None

        # Fetch channel entity
        async with AsyncSessionLocal() as session:
            channel = await session.get(ChannelModel, file_record.channel_id)
            if channel is None:
                return None

        try:
            client = await svc.get_client()
            entity = await client.get_entity(channel.tg_id)
            message = await client.get_messages(entity, ids=file_record.message_id)
        except Exception as e:
            logger.warning("Cannot fetch message for TG thumb file_id={}: {}", file_record.id, e)
            return None

        if message is None:
            logger.warning("Message not found for file_id={} (msg_id={})", file_record.id, file_record.message_id)
            return None

        thumb_rel = f"{file_record.channel_id}/{file_record.id}.jpg"
        thumb_full = self.thumb_dir / thumb_rel
        thumb_full.parent.mkdir(parents=True, exist_ok=True)

        try:
            path = await client.download_media(
                message, file=str(thumb_full), thumb=-1
            )
        except Exception as e:
            logger.warning("Failed to download TG thumb for file_id={}: {}", file_record.id, e)
            return None

        if path is None or not thumb_full.exists() or thumb_full.stat().st_size == 0:
            return None

        return thumb_full

    async def _ensure_cached(
        self, session: AsyncSession, file_record: FileModel, permanent: bool = True,
    ) -> Path | None:
        """Ensure the file exists on disk for processing.

        When permanent=True (default): download to self.cache_dir and update DB
        (is_cached, cache_path, file_size). File persists on disk for future reuse.

        When permanent=False: if already cached, reuse cached file; otherwise
        download to a temp location — caller should delete the file after use.
        The DB is NOT updated in this mode.

        Returns the file path, or None on failure.
        """
        # Already cached and exists on disk — reuse regardless of mode
        if file_record.cache_path and file_record.is_cached:
            full = self.cache_dir / file_record.cache_path
            if full.exists():
                return full

        # Need to download from Telegram
        from api.files import _download_from_telegram as dl_from_tg
        from services.telegram_client import get_telegram_service, AuthState
        from models import Channel as ChannelModel

        svc = get_telegram_service()
        if svc is None or svc.auth_state != AuthState.AUTHORIZED:
            logger.warning("Cannot download file {}: Telegram not authorized", file_record.id)
            return None

        channel = await session.get(ChannelModel, file_record.channel_id)
        if channel is None:
            logger.warning("Channel not found for file {}", file_record.id)
            return None

        if permanent:
            # Download to persistent cache directory
            safe_name = file_record.file_name.replace("/", "_").replace("\\", "_")
            cache_rel = f"{file_record.channel_id}/{file_record.id}_{safe_name}"
            cache_full = self.cache_dir / cache_rel
            update_db = True
        else:
            # Download to a temp directory — will be cleaned up by caller
            cache_full = Path(tempfile.mkdtemp(prefix="thumb_")) / file_record.file_name
            cache_full.parent.mkdir(parents=True, exist_ok=True)
            update_db = False

        try:
            size = await dl_from_tg(svc, channel.tg_id, file_record.message_id, cache_full)
            if update_db:
                # Only persist cache info to DB when permanent=True
                safe_name = file_record.file_name.replace("/", "_").replace("\\", "_")
                cache_rel = f"{file_record.channel_id}/{file_record.id}_{safe_name}"
                file_record.cache_path = cache_rel
                file_record.is_cached = True
                file_record.file_size = size
                await session.commit()
            return cache_full
        except Exception as e:
            logger.error("Failed to download file {} from Telegram: {}", file_record.id, e)
            return None

    async def _handle_failure(
        self, session: AsyncSession, job: ThumbJob, error_msg: str
    ) -> None:
        """Mark a job as failed, or retry if attempts remain."""
        if job.attempt >= job.max_retries:
            job.status = "failed"
            job.error_msg = error_msg
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.warning("Job {} permanently failed ({} attempts): {}",
                         job.id, job.attempt, error_msg)
        else:
            # Retry: reset to pending, worker will pick it up via DB polling
            backoff_index = min(job.attempt - 1, len(_RETRY_BACKOFF) - 1)
            delay = _RETRY_BACKOFF[backoff_index]
            job.status = "pending"
            job.error_msg = error_msg
            await session.commit()
            logger.info("Job {} retry (attempt {}/{}) in {}s: {}",
                       job.id, job.attempt, job.max_retries, delay, error_msg)
            await asyncio.sleep(delay)
            # Producer will pick up this 'pending' job via batch-claim

    async def _mark_job_timeout(self, job_id: str) -> None:
        """Mark a job as failed due to timeout, or retry if attempts remain.

        Called by _worker_loop when _process_job() exceeds self.job_timeout seconds.
        Uses a fresh DB session to avoid conflicts with the timed-out job's session.
        """
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            job = await session.get(ThumbJob, job_id)
            if job is None:
                logger.warning("_mark_job_timeout: job {} not found in DB", job_id)
                return

            error_msg = f"Job timed out after {self.job_timeout:.0f}s"

            if job.attempt >= job.max_retries:
                job.status = "failed"
                job.phase = "failed"
                job.error_msg = error_msg
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.warning(
                    "Job {} permanently failed after timeout ({} attempts): {}",
                    job_id, job.attempt, error_msg,
                )
            else:
                backoff_index = min(job.attempt - 1, len(_RETRY_BACKOFF) - 1)
                delay = _RETRY_BACKOFF[backoff_index]
                job.status = "pending"
                job.phase = "pending"
                job.error_msg = error_msg
                await session.commit()
                logger.info(
                    "Job {} timed out, retry (attempt {}/{}) in {}s",
                    job_id, job.attempt, job.max_retries, delay,
                )
                await asyncio.sleep(delay)


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_thumb_worker_pool: Optional[ThumbnailWorkerPool] = None


def get_thumb_worker_pool() -> Optional[ThumbnailWorkerPool]:
    """Get the global thumbnail worker pool instance."""
    return _thumb_worker_pool


def set_thumb_worker_pool(pool: ThumbnailWorkerPool) -> None:
    """Set the global thumbnail worker pool instance."""
    global _thumb_worker_pool
    _thumb_worker_pool = pool


def reset_thumb_worker_pool() -> None:
    """Reset the global thumbnail worker pool."""
    global _thumb_worker_pool
    _thumb_worker_pool = None
