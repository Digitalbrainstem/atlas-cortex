"""Home Assistant REST API client (Phase I2).

Uses httpx for async HTTP. No live HA required to import — raises
HAClientError at call time if HA is unreachable.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class HAClientError(Exception):
    """Base error for HA client failures."""


class HAConnectionError(HAClientError):
    """Raised when HA is network-unreachable."""


class HAAuthError(HAClientError):
    """Raised when HA returns 401 or 403."""


class HAClient:
    """Thin async wrapper around the Home Assistant REST API.

    A single :class:`httpx.AsyncClient` is reused across calls for connection
    pooling and keep-alive.  Call :meth:`aclose` (or use as an async context
    manager) when done.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=self.timeout,
            headers=self._headers(),
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client and free resources."""
        await self._client.aclose()

    async def __aenter__(self) -> "HAClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def health(self) -> bool:
        """Return True if HA API is reachable (GET /api/ → 200)."""
        try:
            await self._get("/api/")
            return True
        except HAClientError:
            return False

    async def get_states(self) -> list[dict]:
        """Return all entity states (GET /api/states)."""
        result = await self._get("/api/states")
        return result if isinstance(result, list) else []

    async def get_areas(self) -> list[dict]:
        """Return all area registry entries (POST /api/config/area_registry/list)."""
        result = await self._post("/api/config/area_registry/list", {})
        return result if isinstance(result, list) else []

    async def call_service(self, domain: str, service: str, data: dict) -> dict:
        """Call a HA service (POST /api/services/{domain}/{service})."""
        result = await self._post(f"/api/services/{domain}/{service}", data)
        return result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    async def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = await self._client.get(url)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise HAConnectionError(f"Cannot reach HA at {url}: {exc}") from exc
        self._raise_for_auth(response)
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, data: dict) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = await self._client.post(url, json=data)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise HAConnectionError(f"Cannot reach HA at {url}: {exc}") from exc
        self._raise_for_auth(response)
        response.raise_for_status()
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_auth(response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise HAAuthError(f"HA returned {response.status_code} — check your token")
