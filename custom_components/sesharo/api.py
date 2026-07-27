"""Thin async client for the Sesharo Home Assistant ingest endpoint."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class SesharoAuthError(Exception):
    """Raised when the token/user are rejected (401/403/404)."""


class SesharoApiError(Exception):
    """Raised on any other non-2xx / transport failure."""


class SesharoClient:
    """Posts batches of readings + events to POST /users/{id}/home-assistant."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, user_id: str, token: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._user_id = user_id
        self._token = token

    @property
    def _url(self) -> str:
        return f"{self._base}/users/{self._user_id}/home-assistant"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def async_validate(self) -> None:
        """Send an empty batch to confirm the base URL, user id and token all work. Raises on failure."""
        await self.async_push({"readings": [], "events": []})

    async def async_push(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.post(
                self._url, json=payload, headers=self._headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status in (401, 403, 404):
                    raise SesharoAuthError(f"Sesharo rejected the request ({resp.status})")
                if resp.status >= 400:
                    body = await resp.text()
                    raise SesharoApiError(f"Sesharo ingest failed ({resp.status}): {body[:300]}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise SesharoApiError(f"Could not reach Sesharo: {exc}") from exc
