from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class DashboardApiError(RuntimeError):
    """Raised when the dashboard cannot retrieve data from the API."""


@dataclass(frozen=True)
class StoreIntelligenceClient:
    """Small HTTP client for the Store Intelligence FastAPI service."""

    base_url: str
    timeout_seconds: float = 5.0

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def metrics(self, store_id: str) -> dict[str, Any]:
        return self._get(f"/stores/{store_id}/metrics")

    def heatmap(self, store_id: str) -> dict[str, Any]:
        return self._get(f"/stores/{store_id}/heatmap")

    def anomalies(self, store_id: str) -> dict[str, Any]:
        return self._get(f"/stores/{store_id}/anomalies")

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            response = requests.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DashboardApiError(f"API request failed for {url}: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise DashboardApiError(f"API returned a non-object response for {url}.")
        return payload
