"""Data providers — weather, energy, anomaly detection, calendar awareness."""

# Module ownership: Proactive data provider backends
from __future__ import annotations

import abc
import logging
import os
import statistics
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ProactiveProvider(abc.ABC):
    """Abstract base for proactive data providers."""

    provider_id: str = ""

    @abc.abstractmethod
    async def fetch_data(self) -> dict[str, Any]:
        """Fetch current data from the provider backend."""
        ...

    @abc.abstractmethod
    async def health(self) -> bool:
        """Return True if the provider backend is reachable."""
        ...


class WeatherIntelligence(ProactiveProvider):
    """Fetch forecast data from OpenWeatherMap (or compatible API)."""

    provider_id = "weather"

    def __init__(
        self,
        api_key: str = "",
        location: str = "",
        base_url: str = "",
    ) -> None:
        self.api_key = api_key or os.getenv("WEATHER_API_KEY", "")
        self.location = location or os.getenv("WEATHER_LOCATION", "")
        self.base_url = (
            base_url
            or os.getenv("WEATHER_API_URL", "https://api.openweathermap.org/data/2.5")
        )

    async def fetch_data(self) -> dict[str, Any]:
        """Return current weather and any active alerts."""
        if not self.api_key or not self.location:
            return self._empty()
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/weather",
                    params={
                        "q": self.location,
                        "appid": self.api_key,
                        "units": "imperial",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                main = data.get("main", {})
                weather = data.get("weather", [{}])[0]
                return {
                    "temperature": main.get("temp"),
                    "feels_like": main.get("feels_like"),
                    "humidity": main.get("humidity"),
                    "condition": weather.get("main", ""),
                    "description": weather.get("description", ""),
                    "alerts": [],
                }
        except Exception:
            logger.exception("Weather fetch failed")
            return self._empty()

    async def health(self) -> bool:
        if not self.api_key or not self.location:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/weather",
                    params={
                        "q": self.location,
                        "appid": self.api_key,
                        "units": "imperial",
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "temperature": None,
            "feels_like": None,
            "humidity": None,
            "condition": "",
            "description": "",
            "alerts": [],
        }


class EnergyMonitor(ProactiveProvider):
    """Query Home Assistant energy sensors for consumption data."""

    provider_id = "energy"

    def __init__(self, ha_url: str = "", ha_token: str = "") -> None:
        self.ha_url = (ha_url or os.getenv("HA_URL", "")).rstrip("/")
        self.ha_token = ha_token or os.getenv("HA_TOKEN", "")
        self._history: list[float] = []

    async def fetch_data(self) -> dict[str, Any]:
        if not self.ha_url or not self.ha_token:
            return self._empty()
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.ha_token}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Try common energy sensor entity IDs
                for entity in [
                    "sensor.energy_power",
                    "sensor.current_power_use",
                    "sensor.power_consumption",
                ]:
                    resp = await client.get(
                        f"{self.ha_url}/api/states/{entity}",
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        watts = float(data.get("state", 0))
                        self._history.append(watts)
                        # Keep last 288 readings (~24h at 5-min intervals)
                        self._history = self._history[-288:]
                        anomaly = self._detect_anomaly(watts)
                        return {
                            "current_watts": watts,
                            "daily_kwh": self._estimate_daily_kwh(),
                            "anomaly": anomaly,
                        }
            return self._empty()
        except Exception:
            logger.exception("Energy fetch failed")
            return self._empty()

    async def health(self) -> bool:
        if not self.ha_url or not self.ha_token:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.ha_url}/api/",
                    headers={"Authorization": f"Bearer {self.ha_token}"},
                )
                return resp.status_code == 200
        except Exception:
            return False

    def _detect_anomaly(self, current: float) -> bool:
        """Simple statistical anomaly: current > mean + 2*stddev."""
        if len(self._history) < 10:
            return False
        mean = statistics.mean(self._history)
        stdev = statistics.stdev(self._history)
        if stdev == 0:
            return False
        return current > mean + 2 * stdev

    def _estimate_daily_kwh(self) -> float:
        """Rough daily estimate from recent readings."""
        if not self._history:
            return 0.0
        avg_watts = statistics.mean(self._history)
        return round(avg_watts * 24 / 1000, 2)

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"current_watts": 0, "daily_kwh": 0.0, "anomaly": False}


class AnomalyDetector(ProactiveProvider):
    """Track sensor patterns and flag unusual activity."""

    provider_id = "anomaly"

    def __init__(self, ha_url: str = "", ha_token: str = "") -> None:
        self.ha_url = (ha_url or os.getenv("HA_URL", "")).rstrip("/")
        self.ha_token = ha_token or os.getenv("HA_TOKEN", "")
        self._baselines: dict[str, list[float]] = {}

    async def fetch_data(self) -> dict[str, Any]:
        """Return anomaly status for tracked sensors."""
        if not self.ha_url or not self.ha_token:
            return self._empty()
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.ha_token}",
                "Content-Type": "application/json",
            }
            anomalies: list[dict[str, Any]] = []
            now = datetime.now(timezone.utc)

            async with httpx.AsyncClient(timeout=15.0) as client:
                # Check binary sensors (doors, motion, presence)
                resp = await client.get(
                    f"{self.ha_url}/api/states",
                    headers=headers,
                )
                if resp.status_code != 200:
                    return self._empty()

                states = resp.json()
                for state in states:
                    entity_id: str = state.get("entity_id", "")
                    if not entity_id.startswith(("binary_sensor.door", "binary_sensor.motion")):
                        continue
                    if state.get("state") == "on":
                        hour = now.hour
                        # Flag activity during unusual hours (midnight–5am)
                        if 0 <= hour < 5:
                            anomalies.append({
                                "entity": entity_id,
                                "state": "on",
                                "hour": hour,
                                "reason": "activity_during_unusual_hours",
                            })

            return {
                "anomalies": anomalies,
                "anomaly_count": len(anomalies),
                "checked_at": now.isoformat(),
            }
        except Exception:
            logger.exception("Anomaly detection failed")
            return self._empty()

    def record_baseline(self, sensor_id: str, value: float) -> None:
        """Add a value to the rolling baseline for a sensor."""
        if sensor_id not in self._baselines:
            self._baselines[sensor_id] = []
        self._baselines[sensor_id].append(value)
        # Keep last 1000 readings
        self._baselines[sensor_id] = self._baselines[sensor_id][-1000:]

    def is_anomalous(self, sensor_id: str, value: float) -> bool:
        """Check if value deviates from baseline (mean ± 2*stddev)."""
        history = self._baselines.get(sensor_id, [])
        if len(history) < 10:
            return False
        mean = statistics.mean(history)
        stdev = statistics.stdev(history)
        if stdev == 0:
            return False
        return abs(value - mean) > 2 * stdev

    async def health(self) -> bool:
        if not self.ha_url or not self.ha_token:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.ha_url}/api/",
                    headers={"Authorization": f"Bearer {self.ha_token}"},
                )
                return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"anomalies": [], "anomaly_count": 0, "checked_at": ""}


class CalendarAwareness(ProactiveProvider):
    """Pull upcoming events from CalDAV for proactive reminders."""

    provider_id = "calendar"

    def __init__(
        self,
        caldav_url: str = "",
        username: str = "",
        password: str = "",
    ) -> None:
        self.caldav_url = caldav_url or os.getenv("CALDAV_URL", "")
        self.username = username or os.getenv("CALDAV_USERNAME", "")
        self.password = password or os.getenv("CALDAV_PASSWORD", "")

    async def fetch_data(self) -> dict[str, Any]:
        """Return upcoming calendar events within the next 24 hours."""
        if not self.caldav_url or not self.username:
            return self._empty()
        try:
            from cortex.integrations.knowledge.caldav import CalDAVConnector

            connector = CalDAVConnector(
                url=self.caldav_url,
                username=self.username,
                password=self.password,
            )
            try:
                calendars = await connector.discover_calendars()
                now = datetime.now(timezone.utc)
                upcoming: list[dict[str, Any]] = []

                for cal in calendars:
                    events = await connector.fetch_events(
                        calendar_url=cal.get("href", ""),
                        days_back=0,
                        days_forward=1,
                    )
                    for ev in events:
                        start_str = ev.get("start", "")
                        if start_str:
                            try:
                                start = datetime.fromisoformat(start_str)
                                if start.tzinfo is None:
                                    start = start.replace(tzinfo=timezone.utc)
                                delta = (start - now).total_seconds() / 60
                                if delta > 0:
                                    upcoming.append({
                                        "title": ev.get("summary", ""),
                                        "start": start_str,
                                        "minutes_until": round(delta),
                                    })
                            except (ValueError, TypeError):
                                continue

                upcoming.sort(key=lambda e: e.get("minutes_until", 0))
                return {"upcoming": upcoming}
            finally:
                await connector.aclose()
        except Exception:
            logger.exception("Calendar fetch failed")
            return self._empty()

    async def health(self) -> bool:
        if not self.caldav_url or not self.username:
            return False
        try:
            from cortex.integrations.knowledge.caldav import CalDAVConnector

            connector = CalDAVConnector(
                url=self.caldav_url,
                username=self.username,
                password=self.password,
            )
            try:
                await connector.discover_calendars()
                return True
            except Exception:
                return False
            finally:
                await connector.aclose()
        except Exception:
            return False

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"upcoming": []}
