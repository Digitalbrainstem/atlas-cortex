"""Discovery wizard — walks user through service configuration."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from cortex.discovery.registry import ServiceRegistry
from cortex.discovery.scanner import DiscoveredService, ServiceScanner

logger = logging.getLogger(__name__)

# ─── CLI helpers ────────────────────────────────────────────────────────────


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _input(prompt: str) -> str:
    return input(prompt).strip()


def _yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = _input(prompt + suffix)
    if not raw:
        return default
    return raw.lower().startswith("y")


# ─── Seed loader ────────────────────────────────────────────────────────────

# Matches a single VALUES row: ('...', '...', ...) with optional trailing comma
_ROW_RE = re.compile(
    r"\(\s*'((?:[^'\\]|\\.|'')*)'\s*,\s*"   # pattern
    r"'((?:[^'\\]|\\.|'')*)'\s*,\s*"         # intent
    r"(?:'((?:[^'\\]|\\.)*)'|NULL)\s*,\s*"   # entity_domain
    r"(?:(\d+)|NULL)\s*,\s*"                  # entity_match_group
    r"(?:(\d+)|NULL)\s*,\s*"                  # value_match_group
    r"(?:'((?:[^'\\]|\\.)*)'|NULL)\s*,\s*"   # response_template
    r"'((?:[^'\\]|\\.)*)'\s*,\s*"            # source
    r"([\d.]+)\s*\)",                         # confidence
    re.DOTALL,
)


def load_seed_patterns(conn: sqlite3.Connection) -> int:
    """Load ``seeds/command_patterns.sql`` into the ``command_patterns`` table.

    Parses INSERT … VALUES rows with a regex — no subprocess involved.

    Returns:
        Number of rows inserted (duplicates are silently skipped).
    """
    seeds_path = Path(__file__).parents[2] / "seeds" / "command_patterns.sql"
    if not seeds_path.exists():
        logger.warning("Seed file not found: %s", seeds_path)
        return 0

    text = seeds_path.read_text(encoding="utf-8")
    rows = _ROW_RE.findall(text)

    inserted = 0
    for row in rows:
        (pattern, intent, entity_domain, entity_match_group,
         value_match_group, response_template, source, confidence) = row
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO command_patterns
                    (pattern, intent, entity_domain, entity_match_group,
                     value_match_group, response_template, source, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pattern,
                    intent,
                    entity_domain or None,
                    int(entity_match_group) if entity_match_group else None,
                    int(value_match_group) if value_match_group else None,
                    response_template or None,
                    source,
                    float(confidence),
                ),
            )
            # changes() returns 1 for a new insert, 0 for a skipped duplicate
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as exc:
            logger.warning("Skipping seed row (parse error): %s", exc)
    conn.commit()
    logger.info("load_seed_patterns: inserted %d row(s)", inserted)
    return inserted


# ─── Wizard ─────────────────────────────────────────────────────────────────


def run_discovery_wizard(
    conn: sqlite3.Connection,
    non_interactive: bool = False,
) -> list[dict[str, Any]]:
    """Walk the user through service discovery and initial configuration.

    Steps:
      1. Scan the local network for known services.
      2. Present each discovered service to the user.
      3. Optionally prompt for credentials / base URL.
      4. Persist config via :class:`~cortex.discovery.registry.ServiceRegistry`.

    Args:
        conn:            Open SQLite connection.
        non_interactive: Skip all prompts and store only auto-discovered info.

    Returns:
        List of configured service dicts (``discovered_services`` rows).
    """
    _print("\n╔══════════════════════════════════════════════╗")
    _print("║    Atlas Cortex — Service Discovery          ║")
    _print("╚══════════════════════════════════════════════╝\n")

    _print("Scanning local network for services…")
    services: list[DiscoveredService] = asyncio.run(ServiceScanner().scan())

    registry = ServiceRegistry(conn)

    if not services:
        _print("  No services found — you can re-run discovery at any time.")
        _print("  Run: python -m cortex.discover\n")
        return []

    configured: list[dict[str, Any]] = []

    for svc in services:
        _print(f"\n  ✓ {svc.name} found at {svc.url}")
        service_id = registry.upsert_service(svc)

        if non_interactive:
            configured.append({"id": service_id, **svc.__dict__})
            continue

        configure = _yes_no(f"    Configure {svc.name}?", default=True)
        if not configure:
            continue

        if svc.service_type == "home_assistant":
            _configure_home_assistant(registry, service_id, svc)
        elif svc.service_type == "mqtt":
            _configure_mqtt(registry, service_id, svc)
        elif svc.service_type in ("nextcloud", "caldav"):
            _configure_basic_auth(registry, service_id, svc)

        registry.mark_active(service_id, active=True)
        rows = registry.list_services(service_type=svc.service_type)
        match = next((r for r in rows if r["id"] == service_id), None)
        if match:
            configured.append(match)

    _print("\n  Discovery complete.")
    return configured


# ─── Per-service configuration prompts ──────────────────────────────────────


def _configure_home_assistant(
    registry: ServiceRegistry,
    service_id: int,
    svc: DiscoveredService,
) -> None:
    """Prompt for Home Assistant long-lived access token and base URL."""
    default_url = svc.url
    raw_url = _input(f"    Base URL [{default_url}]: ")
    base_url = raw_url if raw_url else default_url

    token = _input("    Long-lived access token: ")
    if token:
        registry.set_config(service_id, "base_url", base_url, sensitive=False)
        registry.set_config(service_id, "token", token, sensitive=True)
        _print("    ✓ Home Assistant configured.")
    else:
        _print("    Skipped (no token provided).")


def _configure_mqtt(
    registry: ServiceRegistry,
    service_id: int,
    svc: DiscoveredService,
) -> None:
    """Prompt for optional MQTT credentials."""
    username = _input("    MQTT username (leave blank if none): ")
    if username:
        password = _input("    MQTT password: ")
        registry.set_config(service_id, "username", username, sensitive=False)
        registry.set_config(service_id, "password", password, sensitive=True)
    registry.set_config(service_id, "url", svc.url, sensitive=False)
    _print("    ✓ MQTT configured.")


def _configure_basic_auth(
    registry: ServiceRegistry,
    service_id: int,
    svc: DiscoveredService,
) -> None:
    """Prompt for HTTP basic-auth credentials (Nextcloud / CalDAV)."""
    default_url = svc.url
    raw_url = _input(f"    Base URL [{default_url}]: ")
    base_url = raw_url if raw_url else default_url

    username = _input(f"    {svc.name} username: ")
    password = _input(f"    {svc.name} password/app-password: ")
    if username and password:
        registry.set_config(service_id, "base_url", base_url, sensitive=False)
        registry.set_config(service_id, "username", username, sensitive=False)
        registry.set_config(service_id, "password", password, sensitive=True)
        _print(f"    ✓ {svc.name} configured.")
    else:
        _print("    Skipped (incomplete credentials).")
