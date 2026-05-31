"""Producer-consumer PriorityQueue thumbnail worker pool.

Generates thumbnails for cached files using Pillow (photo/sticker only).
Video thumbnails require ffmpeg — deferred to a future step.
"""

import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image
from sqlalchemy import select
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

# Supported image types for Pillow thumbnail generation
_SUPPORTED_TYPES = frozenset({"photo", "sticker"})

# Retry backoff in seconds
_RETRY_BACKOFF = [1, 2, 4]


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
    """Manages N async workers consuming a PriorityQueue to generate thumbnails.

    Jobs are persisted in the thumb_jobs DB table.
    On startup, any pending jobs are reloaded into the queue.
    On shutdown, workers finish their current job and exit gracefully.
    """

    def __init__(
        self,
        num_workers: int = 2,
        thumb_dir: str = "./data/thumbnails",
        cache_dir: str = "./data/cache",
        max_width: int = 320,
        max_height: int = 240,
    ):
        self.num_workers = num_workers
        self.thumb_dir = Path(thumb_dir)
        self.cache_dir = Path(cache_dir)
        self.max_width = max_width
        self.max_height = max_height

        # (priority, job_id, file_id) — lower priority first
        self._queue: asyncio.PriorityQueue[tuple[int, str, int]] = asyncio.PriorityQueue()
        self._workers: list[asyncio.Task] = []
        self._shutdown = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start worker pool: load pending jobs from DB, spawn N workers."""
        logger.info("Starting thumbnail worker pool (workers={})", self.num_workers)
        self._shutdown = False

        # Load pending jobs from DB
        await self._load_pending_jobs()

        # Spawn workers
        for i in range(self.num_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self._workers.append(task)
        logger.info("Thumbnail worker pool started with {} workers", self.num_workers)

    async def stop(self) -> None:
        """Gracefully stop all workers (finish current job, then exit)."""
        logger.info("Stopping thumbnail worker pool...")
        self._shutdown = True

        # Drain remaining items to unblock waiting workers
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Wait for workers to finish
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()

        logger.info("Thumbnail worker pool stopped")

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(self, job_id: str, file_id: int, file_type: str) -> None:
        """Enqueue a thumbnail job into the PriorityQueue."""
        priority = _get_priority(file_type)
        self._queue.put_nowait((priority, job_id, file_id))
        logger.debug("Enqueued job {} (file_id={}, priority={})", job_id, file_id, priority)

    async def _load_pending_jobs(self) -> None:
        """Load pending and processing ThumbJobs from DB into the queue."""
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ThumbJob).where(ThumbJob.status.in_(["pending", "processing"]))
            )
            jobs = result.scalars().all()

            for job in jobs:
                # Reset processing jobs back to pending (previous run crashed mid-job)
                if job.status == "processing":
                    job.status = "pending"
                self.enqueue(str(job.id), job.file_id, job.file_name)  # file_type not stored; use default
            await session.commit()

        if jobs:
            logger.info("Loaded {} pending/processing jobs from DB on startup", len(jobs))

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Main worker loop: dequeue and process jobs until shutdown."""
        logger.info("Thumbnail worker {} started", worker_id)

        while not self._shutdown:
            try:
                # Wait for next job with a timeout (allows shutdown check)
                try:
                    priority, job_id, file_id = await asyncio.wait_for(
                        self._queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    continue

                await self._process_job(job_id, file_id, worker_id)
                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Worker {} unexpected error: {}", worker_id, e)

        logger.info("Thumbnail worker {} stopped", worker_id)

    # ------------------------------------------------------------------
    # Process a single job
    # ------------------------------------------------------------------

    async def _process_job(self, job_id: str, file_id: int, worker_id: int) -> None:
        """Process a single thumbnail job: download cache → generate thumb → update DB."""
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            # Load job and file
            job = await session.get(ThumbJob, job_id)
            if job is None:
                return

            # Skip if cancelled
            if job.status == "cancelled":
                logger.debug("Worker {} skipping cancelled job {}", worker_id, job_id)
                return

            file_record = await session.get(FileModel, file_id)
            if file_record is None:
                job.status = "failed"
                job.error_msg = "File record not found in database"
                await session.commit()
                return

            # Mark as processing
            job.status = "processing"
            job.started_at = datetime.utcnow()
            job.attempt += 1
            await session.commit()

            logger.debug("Worker {} processing job {} (file_id={}, attempt={})",
                        worker_id, job_id, file_id, job.attempt)

            # Step 1: Ensure file is cached
            cache_path = await self._ensure_cached(session, file_record)
            if cache_path is None:
                await self._handle_failure(session, job, "Failed to download file from Telegram")
                return

            # Step 2: Check supported format
            if file_record.file_type not in _SUPPORTED_TYPES:
                await self._handle_failure(session, job, f"Unsupported format: {file_record.file_type}")
                return

            # Step 3: Generate thumbnail
            channel_id = file_record.channel_id
            thumb_rel = f"{channel_id}/{file_id}.jpg"
            thumb_full = self.thumb_dir / thumb_rel

            success = generate_thumbnail(
                cache_path, thumb_full,
                max_width=self.max_width,
                max_height=self.max_height,
            )

            if not success:
                await self._handle_failure(session, job, "Thumbnail generation failed — unsupported or corrupt image")
                return

            # Step 4: Update file record
            file_record.thumb_path = thumb_rel
            file_record.thumb_type = "auto"

            # Step 5: Mark job complete
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            await session.commit()

            logger.info("Worker {} completed job {} → thumb: {}", worker_id, job_id, thumb_rel)

    async def _ensure_cached(
        self, session: AsyncSession, file_record: FileModel
    ) -> Path | None:
        """Ensure the file exists in the cache directory.

        Returns the full cache path, or None on failure.
        """
        # Already cached and exists on disk
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

        # Build cache path
        safe_name = file_record.file_name.replace("/", "_").replace("\\", "_")
        cache_rel = f"{file_record.channel_id}/{file_record.id}_{safe_name}"
        cache_full = self.cache_dir / cache_rel

        try:
            size = await dl_from_tg(svc, channel.tg_id, file_record.message_id, cache_full)
            # Update DB
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
            job.completed_at = datetime.utcnow()
            await session.commit()
            logger.warning("Job {} permanently failed ({} attempts): {}",
                         job.id, job.attempt, error_msg)
        else:
            # Retry: reset to pending, the job is already queued
            # Exponential backoff
            backoff_index = min(job.attempt - 1, len(_RETRY_BACKOFF) - 1)
            delay = _RETRY_BACKOFF[backoff_index]
            job.status = "pending"
            job.error_msg = error_msg
            await session.commit()
            logger.info("Job {} retry (attempt {}/{}) in {}s: {}",
                       job.id, job.attempt, job.max_retries, delay, error_msg)
            await asyncio.sleep(delay)
            # Re-enqueue for retry
            self.enqueue(str(job.id), job.file_id, "document")  # default priority for retry


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
