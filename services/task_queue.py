"""Producer-consumer PriorityQueue thumbnail worker pool.

Generates thumbnails/covers for cached files:
- photo/sticker: Pillow thumbnail
- video: ffmpeg cover frame (1s or 10% position)
"""

import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timezone
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

# Supported file types for thumbnail/cover generation
_SUPPORTED_TYPES = frozenset({"photo", "sticker", "video"})

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


def generate_video_cover(
    file_path: Path,
    cover_path: Path,
    max_width: int = 320,
    max_height: int = 240,
    seek_seconds: float = 1.0,
    fallback_percent: float = 0.1,
) -> bool:
    """Extract a cover frame from a video file using ffmpeg.

    Strategy (S8):
    1. Try ffmpeg -ss {seek_seconds} to grab the frame at 1s (avoids black intro frames).
    2. If that fails, fall back to ffmpeg -ss {duration * fallback_percent} (10% position).

    Returns True on success, False if ffmpeg is unavailable or the video is corrupt.
    """
    cover_path.parent.mkdir(parents=True, exist_ok=True)

    # Check ffmpeg availability
    if not _ffmpeg_available():
        logger.warning("ffmpeg not installed, cannot generate video cover for {}", file_path)
        return False

    scale_filter = f"scale={max_width}:{max_height}:force_original_aspect_ratio=decrease"

    def _try_extract(seek: float) -> bool:
        """Run ffmpeg to extract one frame at a given seek position."""
        cmd = [
            "ffmpeg",
            "-y",                   # overwrite output
            "-ss", str(seek),       # seek to position (seconds)
            "-i", str(file_path),   # input file
            "-vframes", "1",        # extract 1 frame
            "-vf", scale_filter,    # scale while keeping aspect ratio
            "-q:v", "2",            # quality (2 = high)
            str(cover_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,  # 30s timeout for large videos
            )
            if result.returncode == 0 and cover_path.exists() and cover_path.stat().st_size > 0:
                return True
            logger.warning(
                "ffmpeg cover extraction failed at seek={}: returncode={} stderr={}",
                seek, result.returncode, result.stderr[:200],
            )
            return False
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg cover extraction timed out at seek={}", seek)
            return False
        except FileNotFoundError:
            logger.warning("ffmpeg binary not found in PATH")
            return False
        except Exception as e:
            logger.warning("ffmpeg cover extraction error at seek={}: {}", seek, e)
            return False

    # Strategy 1: try at 1s (primary position)
    if _try_extract(seek_seconds):
        return True

    # Strategy 2: fallback — probe duration and try at 10% position
    logger.info("Primary cover extraction failed at {}s, trying fallback", seek_seconds)
    duration = _probe_video_duration(file_path)
    if duration and duration > 0:
        fallback_seek = duration * fallback_percent
        # Only retry if fallback position differs meaningfully from primary
        if abs(fallback_seek - seek_seconds) > 0.5:
            return _try_extract(fallback_seek)

    return False


def _ffmpeg_available() -> bool:
    """Check if ffmpeg is installed and accessible."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _probe_video_duration(file_path: Path) -> float | None:
    """Probe video duration in seconds using ffprobe.

    Returns float seconds or None if probing fails.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.debug("Failed to probe video duration for {}: {}", file_path, e)
    return None


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
        """Process a single thumbnail job: download cache → generate thumb → update DB.

        For videos: downloads Telegram's pre-generated thumbnail (near-instant, few KB).
        For photos/stickers: downloads full file and generates thumbnail via Pillow.

        Checks for cancellation at 3 checkpoints to avoid overwriting user-cancelled state.
        """
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            # Load job and file
            job = await session.get(ThumbJob, job_id)
            if job is None:
                return

            # Checkpoint 0: skip if cancelled before processing
            if job.status == "cancelled":
                logger.debug("Worker {} skipping cancelled job {}", worker_id, job_id)
                return

            file_record = await session.get(FileModel, file_id)
            if file_record is None:
                job.status = "failed"
                job.phase = "failed"
                job.error_msg = "File record not found in database"
                await session.commit()
                return

            # Mark as processing
            job.status = "processing"
            job.phase = "processing"
            job.progress = 10
            job.started_at = datetime.now(timezone.utc)
            job.attempt += 1
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

        # Checkpoint 1: after TG thumb download, check cancellation
        await session.refresh(job)
        if job.status == "cancelled":
            logger.info("Job {} cancelled after TG thumb download", str(job.id))
            return

        if tg_thumb is None:
            # TG thumbnail unavailable → fall back to full download + ffmpeg
            logger.warning(
                "TG thumb unavailable for file_id={}, falling back to ffmpeg", file_id
            )
            await self._process_video_ffmpeg_fallback(session, job, file_record, file_id, worker_id)
            return

        # Success: use TG thumbnail
        thumb_rel = f"{file_record.channel_id}/{file_id}.jpg"
        file_record.thumb_path = thumb_rel
        file_record.thumb_type = "telegram"

        # Checkpoint 2: final confirmation before writing completed
        await session.refresh(job)
        if job.status == "cancelled":
            logger.info("Job {} cancelled before completion", str(job.id))
            return

        job.status = "completed"
        job.phase = "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("Worker {} completed job {} via TG thumb → {}", worker_id, str(job.id), thumb_rel)

    async def _process_video_ffmpeg_fallback(
        self, session, job: ThumbJob, file_record: FileModel, file_id: int, worker_id: int
    ) -> None:
        """Fallback: download full video and extract cover via ffmpeg.

        Used when Telegram's built-in thumbnail is unavailable or download fails.
        """
        job.phase = "downloading"
        job.progress = 30
        await session.commit()

        cache_path = await self._ensure_cached(session, file_record)

        # Checkpoint: after full download, check cancellation
        await session.refresh(job)
        if job.status == "cancelled":
            logger.info("Job {} cancelled after video download", str(job.id))
            return

        if cache_path is None:
            await self._handle_failure(session, job, "Failed to download video from Telegram")
            return

        # Generate cover via ffmpeg
        job.phase = "generating"
        job.progress = 70
        await session.commit()

        thumb_rel = f"{file_record.channel_id}/{file_id}.jpg"
        thumb_full = self.thumb_dir / thumb_rel

        success = generate_video_cover(
            cache_path, thumb_full,
            max_width=self.max_width,
            max_height=self.max_height,
        )

        # Checkpoint: after ffmpeg, check cancellation
        await session.refresh(job)
        if job.status == "cancelled":
            logger.info("Job {} cancelled after ffmpeg cover extraction", str(job.id))
            return

        if not success:
            await self._handle_failure(
                session, job,
                "Video cover generation failed — ffmpeg unavailable or corrupt video"
            )
            return

        # Update file record
        file_record.thumb_path = thumb_rel
        file_record.thumb_type = "auto"

        # Checkpoint: final confirmation
        await session.refresh(job)
        if job.status == "cancelled":
            logger.info("Job {} cancelled before completion", str(job.id))
            return

        job.status = "completed"
        job.phase = "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("Worker {} completed job {} via ffmpeg → {}", worker_id, str(job.id), thumb_rel)

    async def _process_image_thumb(
        self, session, job: ThumbJob, file_record: FileModel, file_id: int, worker_id: int
    ) -> None:
        """Standard path: download full file and generate thumbnail via Pillow."""

        # Step 1: Ensure file is cached
        job.phase = "downloading"
        job.progress = 30
        await session.commit()

        cache_path = await self._ensure_cached(session, file_record)

        # Checkpoint 1: after download, check cancellation
        await session.refresh(job)
        if job.status == "cancelled":
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

        # Checkpoint 2: after generation, check cancellation
        await session.refresh(job)
        if job.status == "cancelled":
            logger.info("Job {} cancelled after generation", str(job.id))
            return

        if not success:
            await self._handle_failure(session, job, "Thumbnail generation failed — unsupported or corrupt image")
            return

        # Step 4: Update file record
        file_record.thumb_path = thumb_rel
        file_record.thumb_type = "auto"

        # Checkpoint 3: final confirmation
        await session.refresh(job)
        if job.status == "cancelled":
            logger.info("Job {} cancelled before completion", str(job.id))
            return

        job.status = "completed"
        job.phase = "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("Worker {} completed job {} → thumb: {}", worker_id, str(job.id), thumb_rel)

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
                message, file=str(thumb_full), thumb=0
            )
        except Exception as e:
            logger.warning("Failed to download TG thumb for file_id={}: {}", file_record.id, e)
            return None

        if path is None or not thumb_full.exists() or thumb_full.stat().st_size == 0:
            return None

        return thumb_full

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
            job.completed_at = datetime.now(timezone.utc)
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
