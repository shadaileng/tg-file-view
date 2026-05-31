"""Cache management API: statistics and manual eviction."""

from fastapi import APIRouter, Depends
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
