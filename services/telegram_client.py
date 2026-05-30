"""Telegram client service using Telethon."""

import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
)

logger = logging.getLogger(__name__)


class AuthState(str, Enum):
    """Authentication state of the Telegram service."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CODE_SENT = "code_sent"
    CODE_VERIFIED = "code_verified"
    TWO_FA_REQUIRED = "2fa_required"
    AUTHORIZED = "authorized"
    LOGGED_OUT = "logged_out"


class TelegramService:
    """Manages a Telethon TelegramClient instance.

    Supports both user phone auth and bot token auth.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        phone: str | None = None,
        bot_token: str | None = None,
        proxy_url: str | None = None,
        session_name: str = "tg_file_viewer",
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.bot_token = bot_token
        self.session_name = session_name
        self.auth_state = AuthState.DISCONNECTED
        self._phone_code_hash: str = ""
        self._client: Optional[TelegramClient] = None
        self._lock = asyncio.Lock()

        # Parse proxy
        self.proxy = None
        if proxy_url:
            self.proxy = self._parse_proxy(proxy_url)

    @staticmethod
    def _parse_proxy(proxy_url: str) -> tuple | None:
        """Parse proxy URL into Telethon proxy tuple.

        Supports socks5://host:port format.
        """
        if not proxy_url:
            return None
        try:
            from urllib.parse import urlparse
            from python_socks import ProxyType
            parsed = urlparse(proxy_url)
            scheme = parsed.scheme.lower()
            if scheme in ("socks5", "socks5h"):
                hostname = parsed.hostname or "127.0.0.1"
                port = parsed.port or 1080
                return (ProxyType.SOCKS5, hostname, port)
            elif scheme == "socks4":
                hostname = parsed.hostname or "127.0.0.1"
                port = parsed.port or 1080
                return (ProxyType.SOCKS4, hostname, port)
        except Exception as e:
            logger.warning("Failed to parse proxy URL: %s", e)
        return None

    async def _ensure_client(self) -> TelegramClient:
        """Create or return the existing client."""
        if self._client is not None:
            return self._client

        async with self._lock:
            if self._client is not None:
                return self._client

            client = TelegramClient(
                self.session_name,
                self.api_id,
                self.api_hash,
                proxy=self.proxy,
            )
            self._client = client
            return client

    async def is_authorized(self) -> bool:
        """Check if client exists and is authorized."""
        if self._client is None:
            return False
        try:
            return await self._client.is_user_authorized()
        except Exception:
            return False

    async def send_code(self) -> bool:
        """Send verification code to phone number.

        Returns True if code sent or already authorized.
        Raises ValueError if no phone configured.
        """
        if not self.phone:
            raise ValueError("Phone number not configured, cannot send code")

        client = await self._ensure_client()
        self.auth_state = AuthState.CONNECTING

        await client.connect()

        if await client.is_user_authorized():
            self.auth_state = AuthState.AUTHORIZED
            return True

        result = await client.send_code_request(self.phone)
        self._phone_code_hash = result.phone_code_hash
        self.auth_state = AuthState.CODE_SENT
        logger.info("Verification code sent to %s", self.phone)
        return True

    async def verify_code(self, code: str) -> bool | str:
        """Verify the received code.

        Returns:
            True if authorized
            "2FA_REQUIRED" if two-factor password needed
        Raises ValueError if send_code wasn't called first.
        """
        if not self._phone_code_hash:
            raise ValueError("No phone_code_hash — call send_code() first")

        client = await self._ensure_client()

        try:
            await client.sign_in(
                phone=self.phone,
                code=code,
                phone_code_hash=self._phone_code_hash,
            )
            self.auth_state = AuthState.AUTHORIZED
            logger.info("Successfully signed in")
            return True

        except SessionPasswordNeededError:
            self.auth_state = AuthState.TWO_FA_REQUIRED
            logger.info("2FA password required")
            return "2FA_REQUIRED"

        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
            logger.warning("Code verification failed: %s", e)
            raise ValueError(str(e))

    async def verify_2fa(self, password: str) -> bool:
        """Complete 2FA login with password.

        Returns True if authorized.
        """
        if not self._client:
            raise ValueError("Client not initialized")

        await self._client.sign_in(password=password)
        self.auth_state = AuthState.AUTHORIZED
        logger.info("2FA sign-in successful")
        return True

    async def logout(self) -> bool:
        """Log out and disconnect."""
        if self._client:
            try:
                await self._client.log_out()
            except Exception:
                pass
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.auth_state = AuthState.LOGGED_OUT
        self._phone_code_hash = ""
        return True

    async def get_client(self) -> TelegramClient:
        """Get the underlying Telethon client (for sync engine etc.)."""
        client = await self._ensure_client()
        await client.connect()
        return client


# Global service instance
_telegram_service: Optional[TelegramService] = None


def get_telegram_service() -> Optional[TelegramService]:
    """Get the global Telegram service instance."""
    return _telegram_service


def set_telegram_service(service: TelegramService) -> None:
    """Set the global Telegram service instance."""
    global _telegram_service
    _telegram_service = service


def reset_telegram_service() -> None:
    """Reset (clear) the global Telegram service instance."""
    global _telegram_service
    _telegram_service = None
