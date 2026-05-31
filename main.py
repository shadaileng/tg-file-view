"""FastAPI application entry point for tg_file_viewer."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from config import Settings
from logging_config import setup_logging

# Initialize settings and logging as early as possible
settings = Settings()

from database import init_db
setup_logging(
    log_level=settings.tg_log_level,
    log_file=settings.tg_log_file,
    rotation=settings.tg_log_rotation,
    retention=settings.tg_log_retention,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("tg_file_viewer v0.1.0 starting...")

    # Startup
    import models  # noqa: F401 - register all ORM models before table creation
    await init_db()
    db_path = settings.tg_db_path
    logger.info("Database initialized at {}", db_path)

    # Seed config from .env
    from database import AsyncSessionLocal
    from config import ensure_initialized
    from services.telegram_client import TelegramService, set_telegram_service

    async with AsyncSessionLocal() as session:
        await ensure_initialized(session)

    # Initialize TelegramService with proxy from settings
    tg_service = TelegramService(
        api_id=settings.tg_api_id,
        api_hash=settings.tg_api_hash,
        phone=settings.tg_phone or None,
        bot_token=settings.tg_bot_token or None,
        proxy_url=settings.tg_proxy_url,
    )
    set_telegram_service(tg_service)
    logger.info("TelegramService initialized (api_id={}, proxy={})",
                settings.tg_api_id,
                "yes" if settings.tg_proxy_url else "no")

    # Initialize cache manager directories
    cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Cache directory: {}", cache_dir)

    # Initialize thumbnail worker pool
    from services.task_queue import ThumbnailWorkerPool, set_thumb_worker_pool

    thumb_pool = ThumbnailWorkerPool(
        num_workers=settings.thumb_workers,
        thumb_dir=str(thumb_dir),
        cache_dir=str(cache_dir),
        max_width=settings.thumb_max_width,
        max_height=settings.thumb_max_height,
    )
    set_thumb_worker_pool(thumb_pool)
    await thumb_pool.start()

    yield

    # Shutdown: stop worker pool first, then DB
    logger.info("Shutting down...")
    await thumb_pool.stop()
    from database import engine
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="TG File Viewer",
    description="Monolithic Telegram channel file viewer with thumbnail preview and caching",
    version="0.1.0",
    lifespan=lifespan,
)

# Request logging middleware (must be added before CORS)
from middleware.logging import request_logging_middleware

app.middleware("http")(request_logging_middleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for thumbnails
data_dir = Path(settings.tg_data_dir)
data_dir.mkdir(parents=True, exist_ok=True)
thumb_dir = data_dir / "thumbnails"
thumb_dir.mkdir(parents=True, exist_ok=True)
cache_dir = data_dir / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)

app.mount("/thumbnails", StaticFiles(directory=str(thumb_dir)), name="thumbnails")
app.mount("/cache", StaticFiles(directory=str(cache_dir)), name="cache")

# Register API routers
from api.auth import router as auth_router
from api.cache import router as cache_router
from api.channels import router as channels_router
from api.files import router as files_router
from api.sync import router as sync_router
from api.thumbnails import router as thumb_router

app.include_router(auth_router)
app.include_router(cache_router)
app.include_router(channels_router)
app.include_router(files_router)
app.include_router(sync_router)
app.include_router(thumb_router)


@app.get("/")
async def root():
    """Health check / root endpoint."""
    return {"status": "ok", "service": "tg_file_viewer", "version": "0.1.0"}


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
