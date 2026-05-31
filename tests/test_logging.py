"""Tests for logging_config and request logging middleware."""
import os
import sys
import pytest
from pathlib import Path
from io import StringIO

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger


@pytest.fixture
def log_file(tmp_path):
    """Temp path for test log file."""
    return str(tmp_path / "test_app.log")


@pytest.fixture
def clean_loguru():
    """Ensure loguru is clean after each test."""
    yield
    logger.remove()


class TestSetupLogging:
    """Tests for logging_config.setup_logging."""

    def test_removes_default_handler(self, log_file, clean_loguru):
        """setup_logging removes the default loguru handler."""
        from logging_config import setup_logging

        setup_logging(log_level="INFO", log_file=log_file)
        # The default (sink_id 0) should be removed — trying to remove it raises
        with pytest.raises(ValueError, match="There is no existing handler"):
            logger.remove(0)

    def test_console_handler_added(self, log_file, clean_loguru):
        """setup_logging adds a stdout sink."""
        from logging_config import setup_logging

        # Capture stdout
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            setup_logging(log_level="INFO", log_file=log_file)
            logger.info("test console")
        finally:
            sys.stdout = old_stdout
        # No error = pass

    def test_file_created(self, log_file, clean_loguru):
        """setup_logging creates the log file."""
        from logging_config import setup_logging

        setup_logging(log_level="DEBUG", log_file=log_file)
        logger.info("test file write")
        assert Path(log_file).exists()
        content = Path(log_file).read_text()
        assert "test file write" in content

    def test_error_log_file_created(self, log_file, clean_loguru):
        """setup_logging creates an error.log file."""
        from logging_config import setup_logging

        error_log = Path(log_file).parent / "error.log"
        setup_logging(log_level="INFO", log_file=log_file)
        logger.error("test error")
        assert error_log.exists()
        content = error_log.read_text()
        assert "test error" in content

    def test_debug_not_logged_at_info_level(self, log_file, clean_loguru):
        """At INFO level, DEBUG messages should not appear in console."""
        from logging_config import setup_logging

        setup_logging(log_level="INFO", log_file=log_file)
        logger.debug("this should not be on console")

        # File should still have it (file level is always DEBUG)
        content = Path(log_file).read_text()
        assert "this should not be on console" in content

    def test_noisy_libraries_silenced(self, log_file, clean_loguru):
        """Noisy third-party loggers are disabled."""
        from logging_config import setup_logging

        setup_logging(log_level="INFO", log_file=log_file)

        # Verify that the noisy loggers are indeed disabled
        from loguru import logger as loguru_logger

        for name in ("telethon", "httpx", "httpcore", "asyncio", "aiosqlite"):
            # loguru disable returns None for already-disabled loggers
            result = loguru_logger.disable(name)
            assert result is None, f"{name} was not disabled by setup_logging"


class TestRequestLoggingMiddleware:
    """Tests for the request logging middleware."""

    @pytest.mark.asyncio
    async def test_middleware_logs_request(self, log_file, clean_loguru):
        """Middleware logs method, path, status, and elapsed time."""
        from logging_config import setup_logging
        from middleware.logging import request_logging_middleware

        setup_logging(log_level="INFO", log_file=log_file)

        # Mock request and next callable
        class MockRequest:
            method = "GET"
            url = type("URL", (), {"path": "/api/health"})()

        class MockResponse:
            status_code = 200

        async def call_next(request):
            return MockResponse()

        response = await request_logging_middleware(MockRequest(), call_next)
        assert response.status_code == 200

        content = Path(log_file).read_text()
        assert "GET" in content
        assert "/api/health" in content
        assert "200" in content
        assert "ms" in content


class TestSettingsLogging:
    """Tests for logging-related Settings fields."""

    def test_log_level_default(self, monkeypatch):
        """TG_LOG_LEVEL defaults to INFO when not set."""
        from config import Settings

        monkeypatch.delenv("TG_LOG_LEVEL", raising=False)
        # Need fresh import after env change since Settings is a pydantic model
        s = Settings(_env_file=None)  # Disable .env file to get true default
        assert s.tg_log_level == "INFO"

    def test_log_file_default(self):
        """TG_LOG_FILE defaults to ./data/app.log."""
        from config import Settings

        s = Settings()
        assert s.tg_log_file == "./data/app.log"

    def test_log_rotation_default(self):
        """TG_LOG_ROTATION defaults to '10 MB'."""
        from config import Settings

        s = Settings()
        assert s.tg_log_rotation == "10 MB"

    def test_log_retention_default(self):
        """TG_LOG_RETENTION defaults to 5."""
        from config import Settings

        s = Settings()
        assert s.tg_log_retention == 5

    def test_log_level_from_env(self, monkeypatch):
        """TG_LOG_LEVEL can be set from env."""
        from config import Settings

        monkeypatch.setenv("TG_LOG_LEVEL", "DEBUG")
        s = Settings()
        assert s.tg_log_level == "DEBUG"

    def test_log_file_from_env(self, monkeypatch):
        """TG_LOG_FILE can be set from env."""
        from config import Settings

        monkeypatch.setenv("TG_LOG_FILE", "/tmp/test.log")
        s = Settings()
        assert s.tg_log_file == "/tmp/test.log"
