"""Cache manager: LRU eviction with dynamic size limit.

The CacheManager handles:
- Pre-download space check & LRU eviction
- Post-download overflow cleanup
- Real-time limit reading from DB settings (hot-reloadable)
- Disk file cleanup with missing-file resilience
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import File as FileModel, CacheRecord as CacheRecordModel

CACHE_DIR_STR = os.environ.get("TG_DATA_DIR", "./data")
CACHE_DIR = Path(CACHE_DIR_STR) / "cache"

BYTES_PER_MB = 1024 * 1024


class CacheManager:
    """Manages local file cache with LRU eviction and dynamic size limits.

    All methods are static — no instance state needed since configuration
    is read from DB in real-time on every operation.
    """

    @staticmethod
    async def _get_max_bytes(db: AsyncSession) -> int:
        """Read cache_max_size_mb from DB settings in real-time.

        Returns 0 if unlimited (no eviction).
        """
        from config import get_settings

        settings = await get_settings(db)
        return settings.cache_max_size_mb * BYTES_PER_MB

    @staticmethod
    async def _get_cached_total(db: AsyncSession) -> int:
        """Sum file_size for all is_cached=True files."""
        result = await db.execute(
            select(func.sum(FileModel.file_size)).where(FileModel.is_cached == True)
        )
        return result.scalar() or 0

    @staticmethod
    async def _get_evictable_sum(
        db: AsyncSession, exclude_file_id: Optional[int] = None
    ) -> int:
        """Sum file_size for cached files, optionally excluding one by id."""
        q = select(func.sum(FileModel.file_size)).where(FileModel.is_cached == True)
        if exclude_file_id is not None:
            q = q.where(FileModel.id != exclude_file_id)
        result = await db.execute(q)
        return result.scalar() or 0

    @staticmethod
    async def _get_lru_files(
        db: AsyncSession, exclude_file_id: Optional[int] = None
    ) -> list[FileModel]:
        """Get cached files ordered by accessed_at ASC (oldest = first to evict).

        Files with NULL accessed_at are treated as ancient (epoch date).
        """
        q = select(FileModel).where(FileModel.is_cached == True)
        if exclude_file_id is not None:
            q = q.where(FileModel.id != exclude_file_id)
        # COALESCE treats NULL as epoch — these get evicted first
        q = q.order_by(
            func.coalesce(FileModel.accessed_at, datetime(1970, 1, 1)).asc()
        )
        result = await db.execute(q)
        return list(result.scalars().all())

    @staticmethod
    async def _delete_disk_file(cache_path: str) -> bool:
        """Delete a cache file from disk.

        Returns True if deleted successfully or file already missing.
        Returns False only on OS error (permission denied, etc.).
        """
        full_path = CACHE_DIR / cache_path
        if not full_path.exists():
            logger.warning("Cached file missing on disk: {}", full_path)
            return True  # Treat as success — nothing to delete
        try:
            os.remove(str(full_path))
            logger.debug("Deleted cache file: {}", full_path)
            return True
        except OSError as e:
            logger.error("Failed to delete cache file {}: {}", full_path, e)
            return False

    @staticmethod
    async def _evict_one(db: AsyncSession, file_: FileModel) -> int:
        """Evict a single cached file: delete disk file + clear DB fields.

        Returns bytes freed (0 if disk delete failed).
        DB is always updated regardless of disk deletion success.
        """
        freed = 0
        if file_.cache_path:
            if await CacheManager._delete_disk_file(file_.cache_path):
                freed = file_.file_size

        file_.cache_path = None
        file_.is_cached = False
        file_.cached_at = None

        # Also delete CacheRecord if exists (direct query to avoid lazy-load)
        cr_q = select(CacheRecordModel).where(CacheRecordModel.file_id == file_.id)
        cr_result = await db.execute(cr_q)
        cr = cr_result.scalar_one_or_none()
        if cr:
            await db.delete(cr)

        await db.commit()

        return freed

    @staticmethod
    async def _evict_until(
        db: AsyncSession,
        bytes_needed: int,
        new_file_id: Optional[int] = None,
    ) -> int:
        """Evict LRU files until bytes_needed is freed.

        Args:
            bytes_needed: Target bytes to free.
            new_file_id: The file being cached now (will NOT be evicted).

        Returns:
            Total bytes freed. May be less than bytes_needed if evictable
            files are exhausted.
        """
        freed_total = 0
        files = await CacheManager._get_lru_files(db, exclude_file_id=new_file_id)

        for file_ in files:
            if freed_total >= bytes_needed:
                break
            freed = await CacheManager._evict_one(db, file_)
            freed_total += freed
            if freed > 0:
                logger.info(
                    "LRU evicted: file_id={} name={} size={} bytes",
                    file_.id,
                    file_.file_name,
                    freed,
                )

        return freed_total

    @staticmethod
    async def check_and_evict(
        db: AsyncSession,
        new_file_size: int = 0,
        new_file_id: Optional[int] = None,
    ) -> bool:
        """Ensure cache has room for new_file_size bytes. Evict LRU files if needed.

        Called BEFORE downloading a new file to avoid unnecessary download
        when space is insufficient. Also called post-download (with
        new_file_size=0) to clean up overflow.

        Args:
            new_file_size: Bytes to make room for (0 = just check current state).
            new_file_id: The file being cached (protected from eviction during
                pre-eviction; None for post-check).

        Returns:
            True if sufficient space (or unlimited mode).

        Raises:
            HTTPException(507): Insufficient space even after full eviction.
        """
        max_bytes = await CacheManager._get_max_bytes(db)

        # Unlimited mode — skip all checks
        if max_bytes <= 0:
            return True

        current_total = await CacheManager._get_cached_total(db)
        projected = current_total + new_file_size

        if projected <= max_bytes:
            return True

        # Need to free space
        need_to_free = projected - max_bytes

        # Check if enough evictable space exists (excluding the new file)
        evictable = await CacheManager._get_evictable_sum(
            db, exclude_file_id=new_file_id
        )

        if evictable < need_to_free:
            msg = (
                f"Insufficient cache space. "
                f"Need {need_to_free / BYTES_PER_MB:.1f} MB, "
                f"but only {evictable / BYTES_PER_MB:.1f} MB evictable. "
                f"Current: {current_total / BYTES_PER_MB:.1f} MB, "
                f"Limit: {max_bytes / BYTES_PER_MB:.1f} MB"
            )
            logger.warning("Cache space insufficient: {}", msg)
            raise HTTPException(status_code=507, detail=msg)

        logger.info(
            "Cache eviction: current={:.1f}MB + new={:.1f}MB, "
            "limit={:.1f}MB, need={:.1f}MB",
            current_total / BYTES_PER_MB,
            new_file_size / BYTES_PER_MB,
            max_bytes / BYTES_PER_MB,
            need_to_free / BYTES_PER_MB,
        )

        freed = await CacheManager._evict_until(
            db, need_to_free, new_file_id=new_file_id
        )

        logger.info("Cache eviction freed {:.1f} MB", freed / BYTES_PER_MB)

        if freed < need_to_free:
            raise HTTPException(
                status_code=507,
                detail=(
                    f"Insufficient cache space after eviction. "
                    f"Freed {freed / BYTES_PER_MB:.1f} MB, "
                    f"needed {need_to_free / BYTES_PER_MB:.1f} MB"
                ),
            )

        return True

    @staticmethod
    async def post_download_check(db: AsyncSession) -> None:
        """After downloading, ensure cache is under limit.

        Called after a file has been downloaded and cached. If the newly
        downloaded file (or concurrent downloads) pushed cache over the
        limit, evict LRU files until under threshold.
        """
        return await CacheManager.check_and_evict(db, new_file_size=0)

    @staticmethod
    async def mark_accessed(db: AsyncSession, file_: FileModel) -> None:
        """Update accessed_at timestamp for a cached file (LRU refresh)."""
        file_.accessed_at = datetime.now(timezone.utc)
        await db.commit()

    @staticmethod
    async def create_record(db: AsyncSession, file_: FileModel) -> CacheRecordModel:
        """Create a CacheRecord for a cached file.
        If a record already exists, update it.
        """
        now = datetime.now(timezone.utc)

        # Direct query to avoid lazy-load relationship issues
        cr_q = select(CacheRecordModel).where(CacheRecordModel.file_id == file_.id)
        cr_result = await db.execute(cr_q)
        existing = cr_result.scalar_one_or_none()

        if existing:
            existing.file_path = file_.cache_path
            existing.file_size = file_.file_size
            existing.status = "cached"
            existing.error_msg = None
            existing.accessed_at = now
            existing.cached_at = now
            rec = existing
        else:
            rec = CacheRecordModel(
                file_id=file_.id,
                file_path=file_.cache_path,
                file_size=file_.file_size,
                status="cached",
                cached_at=now,
                accessed_at=now,
            )
            db.add(rec)
        await db.commit()
        await db.refresh(rec)
        return rec

    @staticmethod
    async def list_records(
        db: AsyncSession, offset: int = 0, limit: int = 50
    ) -> tuple[list[dict], int]:
        """List all cache records with file and channel info.

        Returns (records_list, total_count).
        """
        from sqlalchemy.orm import joinedload

        count_q = select(func.count(CacheRecordModel.id))
        total = (await db.execute(count_q)).scalar() or 0

        q = (
            select(CacheRecordModel)
            .options(joinedload(CacheRecordModel.file).joinedload(FileModel.channel))
            .order_by(CacheRecordModel.cached_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await db.execute(q)).scalars().unique().all()

        records = []
        for cr in rows:
            f = cr.file
            records.append({
                "id": cr.id,
                "file_id": cr.file_id,
                "file_name": f.file_name if f else "?",
                "file_type": f.file_type if f else "?",
                "channel_title": f.channel.title if f and f.channel else "?",
                "file_size": cr.file_size,
                "file_path": cr.file_path,
                "status": cr.status,
                "error_msg": cr.error_msg,
                "cached_at": cr.cached_at.isoformat() if cr.cached_at else None,
                "accessed_at": cr.accessed_at.isoformat() if cr.accessed_at else None,
                "created_at": cr.created_at.isoformat() if cr.created_at else None,
            })

        return records, total

    @staticmethod
    async def delete_record(db: AsyncSession, record_id: int) -> bool:
        """Delete a cache record: remove disk file + delete DB record.

        Returns True if record existed and was deleted.
        Returns False if record not found (idempotent).
        """
        from sqlalchemy.orm import joinedload

        stmt = (
            select(CacheRecordModel)
            .where(CacheRecordModel.id == record_id)
            .options(joinedload(CacheRecordModel.file))
        )
        result = await db.execute(stmt)
        rec = result.unique().scalar_one_or_none()
        if rec is None:
            return False

        # Delete disk file
        if rec.file_path:
            await CacheManager._delete_disk_file(rec.file_path)

        # Also update File model fields
        if rec.file:
            rec.file.cache_path = None
            rec.file.is_cached = False
            rec.file.cached_at = None

        await db.delete(rec)
        await db.commit()
        return True

    @staticmethod
    async def get_stats(db: AsyncSession) -> dict:
        """Get cache statistics.

        Returns:
            Dict with total_size_mb, file_count, max_size_mb, usage_percent, etc.
        """
        max_bytes = await CacheManager._get_max_bytes(db)
        total = await CacheManager._get_cached_total(db)

        count_result = await db.execute(
            select(func.count(FileModel.id)).where(FileModel.is_cached == True)
        )
        count = count_result.scalar() or 0

        max_mb = max_bytes / BYTES_PER_MB if max_bytes > 0 else 0
        total_mb = total / BYTES_PER_MB
        usage = (total_mb / max_mb * 100) if max_mb > 0 else 0

        return {
            "total_size_mb": round(total_mb, 2),
            "total_size_bytes": total,
            "file_count": count,
            "max_size_mb": round(max_mb, 2) if max_bytes > 0 else None,
            "max_size_bytes": max_bytes if max_bytes > 0 else None,
            "unlimited": max_bytes <= 0,
            "usage_percent": round(usage, 1),
        }

    @staticmethod
    async def evict_to_limit(db: AsyncSession) -> dict:
        """Manually evict files until cache is under the configured limit.

        Returns:
            Dict with evicted_count, freed_mb, total_size_mb.
        """
        max_bytes = await CacheManager._get_max_bytes(db)

        if max_bytes <= 0:
            return {
                "evicted_count": 0,
                "freed_mb": 0,
                "detail": "Cache is unlimited, no eviction performed.",
            }

        current_total = await CacheManager._get_cached_total(db)

        if current_total <= max_bytes:
            return {
                "evicted_count": 0,
                "freed_mb": 0,
                "total_size_mb": round(current_total / BYTES_PER_MB, 2),
                "detail": "Cache is already under limit.",
            }

        # Count before
        count_before_q = await db.execute(
            select(func.count(FileModel.id)).where(FileModel.is_cached == True)
        )
        count_before = count_before_q.scalar() or 0

        need_to_free = current_total - max_bytes
        freed = await CacheManager._evict_until(db, need_to_free)

        # Count after
        count_after_q = await db.execute(
            select(func.count(FileModel.id)).where(FileModel.is_cached == True)
        )
        count_after = count_after_q.scalar() or 0

        new_total = await CacheManager._get_cached_total(db)

        return {
            "evicted_count": count_before - count_after,
            "freed_mb": round(freed / BYTES_PER_MB, 2),
            "total_size_mb": round(new_total / BYTES_PER_MB, 2),
        }
