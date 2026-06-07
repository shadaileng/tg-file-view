"""Cache management API: statistics, records listing, and manual eviction."""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.cache_manager import CacheManager

router = APIRouter(prefix="/api/cache", tags=["cache"])


@router.get("/stats")
async def get_cache_stats(db: AsyncSession = Depends(get_db)):
    """Get cache statistics: total size, file count, usage percentage."""
    return await CacheManager.get_stats(db)


@router.post("/evict")
async def manual_evict(db: AsyncSession = Depends(get_db)):
    """Manually trigger LRU eviction to meet the configured size limit."""
    return await CacheManager.evict_to_limit(db)


@router.get("/records")
async def list_cache_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List all cache records with file and channel info (paginated)."""
    records, total = await CacheManager.list_records(db, offset=offset, limit=limit)
    return {
        "records": records,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.delete("/records/{record_id}")
async def delete_cache_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific cache record (disk file + DB entry)."""
    deleted = await CacheManager.delete_record(db, record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Cache record id={record_id} not found")
    return {"status": "ok", "detail": f"Cache record id={record_id} deleted"}
