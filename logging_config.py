"""Centralized logging config using loguru."""
import sys
from pathlib import Path
from loguru import logger


def setup_logging(
    log_level: str = "INFO",
    log_file: str = "./data/app.log",
    rotation: str = "10 MB",
    retention: int = 5,
):
    """Configure loguru with console + file sinks.

    Args:
        log_level: Minimum level for console output (default INFO).
        log_file: Path to the main log file.
        rotation: When to rotate (e.g. "10 MB", "1 day").
        retention: Number of rotated files to keep.
    """
    logger.remove()

    # Console: colored, level-filtered
    logger.add(
        sys.stdout,
        level=log_level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File: all logs with rotation
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_file,
        level="DEBUG",
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} | {message}",
    )

    # Error-only file
    logger.add(
        log_path.parent / "error.log",
        level="ERROR",
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} | {message}",
    )

    # Silence noisy third-party loggers
    for name in ("telethon", "httpx", "httpcore", "asyncio", "aiosqlite"):
        logger.disable(name)

    logger.info("Logging initialized (level=%s, file=%s)", log_level, log_file)
