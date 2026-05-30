"""FastAPI application entry point for tg_file_viewer."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    await init_db()
    # Seed config from .env
    from database import AsyncSessionLocal
    from config import ensure_initialized, Settings
    from services.telegram_client import TelegramService, set_telegram_service

    async with AsyncSessionLocal() as session:
        await ensure_initialized(session)

    # Initialize TelegramService with proxy from settings
    settings = Settings()
    tg_service = TelegramService(
        api_id=settings.tg_api_id,
        api_hash=settings.tg_api_hash,
        phone=settings.tg_phone or None,
        bot_token=settings.tg_bot_token or None,
        proxy_url=settings.tg_proxy_url,
    )
    set_telegram_service(tg_service)

    yield
    # Shutdown
    from database import engine
    await engine.dispose()


app = FastAPI(
    title="TG File Viewer",
    description="Monolithic Telegram channel file viewer with thumbnail preview and caching",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for thumbnails
import os
from pathlib import Path

data_dir = Path(os.environ.get("TG_DATA_DIR", "./data"))
data_dir.mkdir(parents=True, exist_ok=True)
thumb_dir = data_dir / "thumbnails"
thumb_dir.mkdir(parents=True, exist_ok=True)
cache_dir = data_dir / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)

app.mount("/thumbnails", StaticFiles(directory=str(thumb_dir)), name="thumbnails")
app.mount("/cache", StaticFiles(directory=str(cache_dir)), name="cache")

# Register API routers
from api.auth import router as auth_router

app.include_router(auth_router)


@app.get("/")
async def root():
    """Health check / root endpoint."""
    return {"status": "ok", "service": "tg_file_viewer", "version": "0.1.0"}


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
