"""Thumbnail job management API routes.

Endpoints:
- POST /api/files/{file_id}/thumbnail       → trigger single file
- POST /api/thumbnails/generate-batch       → batch submit
- GET  /api/thumbnails/jobs                 → list jobs (with ?status= filter)
- GET  /api/thumbnails/jobs/{job_id}        → job detail
- GET  /api/thumbnails/stats                → aggregate statistics
- POST /api/thumbnails/jobs/{job_id}/cancel → cancel a job
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc as sa_desc

from api.utils import utc_iso
from database import get_db
from models import File as FileModel, ThumbJob

router = APIRouter(tags=["thumbnails"])

# Valid job statuses for filtering and cancelling
_VALID_STATUSES = frozenset({"pending", "processing", "completed", "failed", "cancelled"})
_CANCELLABLE_STATUSES = frozenset({"pending", "processing"})


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class BatchGenerateRequest(BaseModel):
    file_ids: list[int] = Field(..., min_length=1, max_length=100)


class ThumbJobOut(BaseModel):
    id: str
    file_id: int
    file_name: str
    mime_type: str
    status: str
    phase: str
    progress: int
    priority: int
    strategy: Optional[str] = None
    attempt: int
    max_retries: int
    error_msg: Optional[str] = None
    thumb_url: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_to_dict(job: ThumbJob, thumb_path: str | None = None) -> ThumbJobOut:
    """Serialize a ThumbJob ORM object to a pydantic model."""
    thumb_url = None
    if thumb_path:
        thumb_url = f"/thumbnails/{thumb_path}"

    return ThumbJobOut(
        id=str(job.id),
        file_id=job.file_id,
        file_name=job.file_name,
        mime_type=job.mime_type,
        status=job.status,
        phase=getattr(job, "phase", "pending"),
        progress=getattr(job, "progress", 0),
        priority=job.priority,
        strategy=job.strategy,
        attempt=job.attempt,
        max_retries=job.max_retries,
        error_msg=job.error_msg,
        thumb_url=thumb_url,
        created_at=utc_iso(job.created_at) or "",
        started_at=utc_iso(job.started_at),
        completed_at=utc_iso(job.completed_at),
    )


def _require_worker_pool():
    """Get the thumbnail worker pool or raise HTTPException."""
    from services.task_queue import get_thumb_worker_pool
    pool = get_thumb_worker_pool()
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Thumbnail worker pool is not running. Service may be starting up.",
        )
    return pool


# ---------------------------------------------------------------------------
# POST /api/files/{file_id}/thumbnail
# ---------------------------------------------------------------------------

@router.post("/api/files/{file_id}/thumbnail", status_code=202)
async def trigger_single_thumbnail(
    file_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Trigger thumbnail generation for a single file.

    Returns 202 with the created job ID on success.
    Returns 404 if the file doesn't exist.
    Returns 409 if a pending/processing job already exists for this file.
    """
    pool = _require_worker_pool()

    # Validate file exists
    file_record = await db.get(FileModel, file_id)
    if file_record is None:
        raise HTTPException(status_code=404, detail=f"File with id={file_id} not found")

    # Check for existing pending/processing job
    existing = await db.execute(
        select(ThumbJob).where(
            ThumbJob.file_id == file_id,
            ThumbJob.status.in_(["pending", "processing"]),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"File {file_id} already has a pending or processing thumbnail job",
        )

    # Create ThumbJob
    import uuid
    from services.task_queue import _get_priority

    job = ThumbJob(
        id=str(uuid.uuid4()),
        file_id=file_id,
        file_name=file_record.file_name,
        mime_type=file_record.mime_type,
        status="pending",
        priority=_get_priority(file_record.file_type),
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue
    pool.enqueue(str(job.id), file_id, file_record.file_type)
    logger.info("Triggered single thumbnail: file_id={} job_id={}", file_id, job.id)

    return {"job_id": str(job.id), "file_id": file_id, "status": "pending"}


# ---------------------------------------------------------------------------
# POST /api/thumbnails/generate-batch
# ---------------------------------------------------------------------------

@router.post("/api/thumbnails/generate-batch", status_code=202)
async def generate_batch(
    body: BatchGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Submit multiple files for thumbnail generation.

    Skips files that already have a pending/processing job (no error).
    Returns the list of created job IDs.
    """
    pool = _require_worker_pool()
    import uuid
    from services.task_queue import _get_priority

    # Resolve all files
    file_ids = body.file_ids
    files_result = await db.execute(
        select(FileModel).where(FileModel.id.in_(file_ids))
    )
    files = {f.id: f for f in files_result.scalars().all()}

    # Find files that already have pending/processing jobs
    existing_result = await db.execute(
        select(ThumbJob.file_id).where(
            ThumbJob.file_id.in_(file_ids),
            ThumbJob.status.in_(["pending", "processing"]),
        )
    )
    blocked_ids = set(existing_result.scalars().all())

    # Create jobs for eligible files
    created_ids: list[str] = []
    skipped_ids: list[int] = []
    not_found_ids: list[int] = []

    for fid in file_ids:
        file_record = files.get(fid)
        if file_record is None:
            not_found_ids.append(fid)
            continue

        if fid in blocked_ids:
            skipped_ids.append(fid)
            continue

        job = ThumbJob(
            id=str(uuid.uuid4()),
            file_id=fid,
            file_name=file_record.file_name,
            mime_type=file_record.mime_type,
            status="pending",
            priority=_get_priority(file_record.file_type),
            created_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.flush()  # get the id before commit
        created_ids.append(str(job.id))
        pool.enqueue(str(job.id), fid, file_record.file_type)

    await db.commit()

    logger.info(
        "Batch thumbnail: requested={} created={} skipped={} not_found={}",
        len(file_ids), len(created_ids), len(skipped_ids), len(not_found_ids),
    )

    return {
        "job_ids": created_ids,
        "skipped_file_ids": skipped_ids,
        "not_found_file_ids": not_found_ids,
        "total_requested": len(file_ids),
        "total_created": len(created_ids),
    }


# ---------------------------------------------------------------------------
# GET /api/thumbnails/jobs
# ---------------------------------------------------------------------------

@router.get("/api/thumbnails/jobs")
async def list_thumb_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List thumbnail jobs, optionally filtered by status."""
    # Validate status filter
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Valid: {sorted(_VALID_STATUSES)}",
        )

    # Build query
    q = select(ThumbJob)
    count_q = select(func.count(ThumbJob.id))

    if status:
        q = q.where(ThumbJob.status == status)
        count_q = count_q.where(ThumbJob.status == status)

    total = (await db.execute(count_q)).scalar()

    q = q.order_by(sa_desc(ThumbJob.created_at)).offset(offset).limit(limit)
    jobs = (await db.execute(q)).scalars().all()

    # Enrich with thumb URL
    file_id_set = {j.file_id for j in jobs}
    files_result = await db.execute(
        select(FileModel.id, FileModel.thumb_path).where(FileModel.id.in_(file_id_set))
    )
    thumb_map = {fid: tp for fid, tp in files_result.all()}

    return {
        "jobs": [_job_to_dict(j, thumb_map.get(j.file_id)) for j in jobs],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# GET /api/thumbnails/jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get("/api/thumbnails/jobs/{job_id}")
async def get_thumb_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single thumbnail job's detail."""
    job = await db.get(ThumbJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Thumbnail job '{job_id}' not found")

    # Look up thumb path for URL
    file_record = await db.get(FileModel, job.file_id)
    thumb_path = file_record.thumb_path if file_record else None

    return _job_to_dict(job, thumb_path)


# ---------------------------------------------------------------------------
# GET /api/thumbnails/stats
# ---------------------------------------------------------------------------

@router.get("/api/thumbnails/stats")
async def thumb_stats(db: AsyncSession = Depends(get_db)):
    """Get aggregate thumbnail job statistics."""
    stats: dict[str, int] = {}
    for st in _VALID_STATUSES:
        count_q = select(func.count(ThumbJob.id)).where(ThumbJob.status == st)
        count = (await db.execute(count_q)).scalar()
        stats[st] = count

    # Total
    total = (await db.execute(select(func.count(ThumbJob.id)))).scalar()
    stats["total"] = total

    return stats


# ---------------------------------------------------------------------------
# POST /api/thumbnails/jobs/{job_id}/cancel
# ---------------------------------------------------------------------------

@router.post("/api/thumbnails/jobs/{job_id}/cancel")
async def cancel_thumb_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending or processing thumbnail job."""
    job = await db.get(ThumbJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Thumbnail job '{job_id}' not found")

    if job.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job '{job_id}' with status '{job.status}'. "
                   f"Only pending or processing jobs can be cancelled.",
        )

    job.status = "cancelled"
    job.phase = "cancelled"
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Cancelled thumbnail job {}", job_id)
    return {"job_id": job_id, "status": "cancelled"}
