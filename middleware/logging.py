"""Request logging middleware using loguru."""
import time
from loguru import logger


async def request_logging_middleware(request, call_next):
    """Log incoming HTTP requests with method, path, status, and elapsed time."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "{} {} → {} ({:.1f}ms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response
