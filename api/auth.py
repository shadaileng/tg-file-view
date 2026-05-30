"""Authentication API routes for Telegram login."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.telegram_client import (
    get_telegram_service,
    reset_telegram_service,
    AuthState,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class VerifyCodeRequest(BaseModel):
    code: str


class Verify2FARequest(BaseModel):
    password: str


def _require_service():
    """Get the Telegram service or raise 400."""
    svc = get_telegram_service()
    if svc is None:
        raise HTTPException(status_code=400, detail="Telegram service not configured")
    return svc


@router.post("/send-code")
async def send_code():
    """Send Telegram verification code to phone."""
    svc = _require_service()
    try:
        result = await svc.send_code()
        if result:
            return {"status": "code_sent", "auth_state": svc.auth_state.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("send_code failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-code")
async def verify_code(req: VerifyCodeRequest):
    """Verify the Telegram login code."""
    svc = _require_service()
    try:
        result = await svc.verify_code(req.code)

        if result is True:
            return {"status": "authorized", "auth_state": svc.auth_state.value}
        elif result == "2FA_REQUIRED":
            return {"status": "2fa_required", "auth_state": "2fa_required"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("verify_code failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-2fa")
async def verify_2fa(req: Verify2FARequest):
    """Complete 2FA login with password."""
    svc = _require_service()
    try:
        await svc.verify_2fa(req.password)
        return {"status": "authorized", "auth_state": svc.auth_state.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("verify_2fa failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def auth_status():
    """Get current authentication status."""
    svc = get_telegram_service()
    if svc is None:
        return {"status": "not_configured"}
    return {
        "status": svc.auth_state.value,
        "is_authorized": await svc.is_authorized(),
    }


@router.post("/logout")
async def logout():
    """Log out and disconnect."""
    svc = get_telegram_service()
    if svc:
        await svc.logout()
    reset_telegram_service()
    return {"status": "logged_out"}
