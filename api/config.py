"""Configuration management API: read/list all config, update (admin-only)."""

from fastapi import APIRouter, Depends, Header, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from config import (
    ALL_CONFIG_KEYS,
    READONLY_CONFIG_KEYS,
    is_admin,
    list_all_configs,
    validate_config_value,
    set_config_value,
    get_config_value,
)

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdateRequest(BaseModel):
    value: str


async def _check_admin(x_admin_password: str | None = Header(None)) -> None:
    """Verify admin password or raise 401/403."""
    if not await is_admin(x_admin_password):
        if x_admin_password is None:
            raise HTTPException(status_code=401, detail="admin password required")
        raise HTTPException(status_code=403, detail="invalid admin password")


@router.get("")
async def get_all_config(db: AsyncSession = Depends(get_db)):
    """List all configuration entries (read-only for non-admins)."""
    return await list_all_configs(db)


@router.get("/{key}")
async def get_config(key: str, db: AsyncSession = Depends(get_db)):
    """Get a single configuration value by key."""
    if key not in ALL_CONFIG_KEYS:
        raise HTTPException(status_code=404, detail=f"config key '{key}' not found")

    value = await get_config_value(key)
    return {
        "key": key,
        "value": value or "",
        "editable": key not in READONLY_CONFIG_KEYS,
    }


@router.put("/{key}")
async def update_config(
    key: str,
    body: ConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    x_admin_password: str | None = Header(None),
):
    """Update a configuration value (admin-only).

    Hot-reload: the new value takes effect immediately on next get_settings() call.
    """
    # Auth check
    await _check_admin(x_admin_password)

    # Key existence
    if key not in ALL_CONFIG_KEYS:
        raise HTTPException(status_code=404, detail=f"config key '{key}' not found")

    # Read-only protection
    if key in READONLY_CONFIG_KEYS:
        raise HTTPException(
            status_code=403,
            detail=f"config key '{key}' is read-only and cannot be modified via API",
        )

    # Value validation
    is_valid, error_msg = validate_config_value(key, body.value)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # Persist
    await set_config_value(key, body.value)
    logger.info("Config updated via API: %s = %s", key, body.value)

    return {
        "key": key,
        "value": body.value,
        "message": f"'{key}' updated successfully",
    }
