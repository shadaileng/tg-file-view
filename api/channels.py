"""Channel management CRUD API routes."""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Channel
from services.telegram_client import get_telegram_service, AuthState

router = APIRouter(prefix="/api/channels", tags=["channels"])


class CreateChannelRequest(BaseModel):
    """Request to add a channel by username or tg_id."""
    username: str | None = None
    tg_id: int | None = None

    @model_validator(mode="after")
    def validate_one_field(self):
        """Ensure exactly one of username or tg_id is provided."""
        if self.username is not None and self.tg_id is not None:
            raise ValueError("Provide exactly one of 'username' or 'tg_id', not both")
        if self.username is None and self.tg_id is None:
            raise ValueError("Either 'username' or 'tg_id' is required")
        return self


async def _require_authorized_client():
    """Get an authorized Telegram client or raise HTTP error."""
    svc = get_telegram_service()
    if svc is None:
        raise HTTPException(
            status_code=400,
            detail="Telegram service not configured. Please configure TG_API_ID and TG_API_HASH.",
        )
    if svc.auth_state != AuthState.AUTHORIZED:
        raise HTTPException(
            status_code=400,
            detail="Telegram client not authorized. Please complete login via /api/auth first.",
        )
    return await svc.get_client()


def _channel_to_dict(channel: Channel) -> dict:
    """Serialize a Channel ORM object to a JSON-safe dict."""
    return {
        "id": channel.id,
        "tg_id": channel.tg_id,
        "username": channel.username,
        "title": channel.title,
        "file_count": channel.file_count,
        "total_size": channel.total_size,
        "last_sync": channel.last_sync.isoformat() if channel.last_sync else None,
    }


@router.get("/discover")
async def discover_channels(db: AsyncSession = Depends(get_db)):
    """Discover channels that the authenticated user follows on Telegram.

    Uses Telethon's iter_dialogs() to fetch conversation list (metadata only,
    no message history), then filters for Channel type entities.

    Returns a list of channels with:
    - tg_id, username, title from Telegram
    - already_tracked: whether this channel already exists in the database
    """
    client = await _require_authorized_client()

    # Fetch existing tg_ids for already_tracked check (single query, not N+1)
    result = await db.execute(select(Channel.tg_id))
    tracked_tg_ids = {row[0] for row in result.all()}

    discovered = []
    try:
        async for dialog in client.iter_dialogs(limit=200):
            entity = dialog.entity
            # Only include Channel (broadcast) and Megagroup (supergroup) types.
            # Duck-typing check: both have .broadcast or .megagroup attributes set to True.
            is_channel = getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)
            if not is_channel:
                continue

            tg_id = int(entity.id)
            username = getattr(entity, "username", None) or ""
            title = getattr(entity, "title", "") or ""

            if not title:
                continue

            discovered.append({
                "tg_id": tg_id,
                "username": username,
                "title": title,
                "already_tracked": tg_id in tracked_tg_ids,
            })
    except Exception as e:
        logger.error("Failed to discover channels from dialogs: {}", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch channels from Telegram: {e}",
        )

    logger.info("Discovered {} channels from dialogs ({} already tracked)",
                len(discovered), sum(1 for d in discovered if d["already_tracked"]))
    return discovered


@router.post("", status_code=201)
async def create_channel(req: CreateChannelRequest, db: AsyncSession = Depends(get_db)):
    """Add a new channel by resolving its Telegram entity.

    Accepts either a username (e.g. "test_channel") or a numeric tg_id.
    Returns 404 if the entity is not found on Telegram.
    Returns 409 if the channel already exists in the database.
    """
    client = await _require_authorized_client()

    # Resolve the Telegram entity to get tg_id, username, title
    try:
        if req.tg_id is not None:
            entity = await client.get_entity(req.tg_id)
        else:
            entity = await client.get_entity(req.username)
    except ValueError as e:
        logger.warning("Channel not found on Telegram: {}", e)
        raise HTTPException(
            status_code=404,
            detail=f"Channel not found on Telegram: {req.username or req.tg_id}",
        )
    except Exception as e:
        logger.error("Failed to resolve channel entity: {}", e)
        raise HTTPException(status_code=500, detail=f"Failed to resolve channel: {e}")

    tg_id = int(entity.id)
    username = getattr(entity, "username", None) or ""
    title = getattr(entity, "title", "") or ""

    if not title:
        raise HTTPException(
            status_code=400,
            detail="Resolved entity has no title — received a user or chat instead of a channel?",
        )

    # Check for duplicate
    existing = await db.execute(select(Channel).where(Channel.tg_id == tg_id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Channel '{title}' (tg_id={tg_id}) already exists",
        )

    channel = Channel(tg_id=tg_id, username=username, title=title)
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    logger.info("Channel created: id={} tg_id={} title={}", channel.id, channel.tg_id, channel.title)
    return _channel_to_dict(channel)


@router.get("")
async def list_channels(db: AsyncSession = Depends(get_db)):
    """List all channels, ordered by id."""
    result = await db.execute(select(Channel).order_by(Channel.id))
    channels = result.scalars().all()
    return [_channel_to_dict(c) for c in channels]


@router.get("/{channel_id}")
async def get_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single channel by its database id."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Channel with id={channel_id} not found")
    return _channel_to_dict(channel)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a channel and all its associated files (cascade)."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Channel with id={channel_id} not found")

    await db.delete(channel)
    await db.commit()

    logger.info("Channel deleted: id={} tg_id={} title={}", channel.id, channel.tg_id, channel.title)
    return None
