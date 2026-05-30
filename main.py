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
    from config import ensure_initialized

    async with AsyncSessionLocal() as session:
        await ensure_initialized(session)

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
