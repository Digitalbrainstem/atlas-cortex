"""Proactive intelligence — rule engine, throttle, and data providers.

Monitors environmental data (weather, energy, sensors, calendar) and
fires rules when conditions are met, subject to notification throttling.
"""

# Module ownership: Proactive intelligence and rule engine
from __future__ import annotations

from cortex.proactive.engine import RuleEngine
from cortex.proactive.throttle import NotificationThrottle
from cortex.proactive.providers import (
    ProactiveProvider,
    WeatherIntelligence,
    EnergyMonitor,
    AnomalyDetector,
    CalendarAwareness,
)
from cortex.proactive.briefing import DailyBriefing

__all__ = [
    "RuleEngine",
    "NotificationThrottle",
    "ProactiveProvider",
    "WeatherIntelligence",
    "EnergyMonitor",
    "AnomalyDetector",
    "CalendarAwareness",
    "DailyBriefing",
]
