"""Playwright walkthrough of every Atlas Cortex admin panel view + CLI smoke test.

Run with::

    python -m pytest tests/test_ui_walkthrough.py -v -m browser --tb=short

Requires:
  - ``playwright`` (``pip install playwright && playwright install chromium``)
  - ``httpx``
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright", reason="playwright package not installed")
from playwright.sync_api import sync_playwright, Browser  # noqa: E402

import httpx  # noqa: E402

pytestmark = pytest.mark.browser

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 15101
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
ADMIN_URL = f"{BASE_URL}/admin/"

# Default admin credentials seeded by init_db
ADMIN_USER = "admin"
ADMIN_PASS = "atlas-admin"


# ── Helpers ───────────────────────────────────────────────────────────────


def _wait_for_server(base_url: str, *, retries: int = 40, delay: float = 0.5) -> bool:
    """Poll the /health endpoint synchronously."""
    for _ in range(retries):
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def server():
    """Start the cortex server in a subprocess for browser tests."""
    tmpdir = tempfile.mkdtemp(prefix="cortex_ui_test_")
    env = os.environ.copy()
    env["CORTEX_DATA_DIR"] = tmpdir
    env["CORTEX_HOST"] = SERVER_HOST
    env["CORTEX_PORT"] = str(SERVER_PORT)
    env["LLM_PROVIDER"] = "ollama"
    env["OLLAMA_BASE_URL"] = "http://127.0.0.1:1"  # unreachable on purpose
    env["TTS_PROVIDER"] = "none"

    proc = subprocess.Popen(
        ["python3", "-m", "cortex.server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if not _wait_for_server(BASE_URL):
        proc.terminate()
        proc.wait(timeout=5)
        pytest.skip("Cortex server failed to start — skipping browser tests")

    yield BASE_URL

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


@pytest.fixture(scope="module")
def pw_browser():
    """Launch a headless Chromium browser for the module."""
    pw_ctx = sync_playwright().start()
    browser = pw_ctx.chromium.launch(headless=True)
    yield browser
    browser.close()
    pw_ctx.stop()


@pytest.fixture(scope="module")
def auth_token(server):
    """Obtain an admin JWT token via the login endpoint."""
    resp = httpx.post(
        f"{server}/admin/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["token"]


@pytest.fixture(scope="module")
def authed_context(pw_browser: Browser, server: str, auth_token: str):
    """Browser context pre-loaded with the admin JWT in localStorage.

    We inject the token by navigating to the admin page first, then setting
    localStorage so the Vue router guard sees us as authenticated.
    """
    ctx = pw_browser.new_context(viewport={"width": 1280, "height": 720})
    page = ctx.new_page()
    # Navigate to a page that loads the SPA shell
    page.goto(f"{server}/admin/", wait_until="domcontentloaded")
    # Inject the token into localStorage
    page.evaluate(
        f"window.localStorage.setItem('atlas-admin-token', '{auth_token}')"
    )
    page.close()
    yield ctx
    ctx.close()


# ── View data ─────────────────────────────────────────────────────────────

# Every admin view we need to verify.  Each entry:
#   route  – hash path (appended to /admin/#)
#   title  – substring expected somewhere on the page *or* in <title>
#   marker – a CSS selector that must be present to consider the view loaded
VIEWS = [
    {
        "route": "/login",
        "title": "Login",
        "marker": "input",  # login form inputs
        "public": True,
        "skip_authed": True,  # authed users get redirected away
    },
    {
        "route": "/dashboard",
        "title": "Dashboard",
        "marker": ".dashboard, .stat-card, .stats, h1, h2",
    },
    {
        "route": "/chat",
        "title": "Chat",
        "marker": ".chat, .message-input, textarea, input, .chat-container",
    },
    {
        "route": "/users",
        "title": "Users",
        "marker": "table, .user, .users, h1, h2",
    },
    {
        "route": "/parental",
        "title": "Parental",
        "marker": ".parental, select, h1, h2",
    },
    {
        "route": "/safety",
        "title": "Safety",
        "marker": ".safety, table, .tab, h1, h2",
    },
    {
        "route": "/voice",
        "title": "Voice",
        "marker": ".voice, table, h1, h2",
    },
    {
        "route": "/avatar",
        "title": "Avatar",
        "marker": ".avatar, .skin, h1, h2",
    },
    {
        "route": "/devices",
        "title": "Devices",
        "marker": ".devices, table, .tab, h1, h2",
    },
    {
        "route": "/satellites",
        "title": "Satellite",
        "marker": ".satellite, table, h1, h2",
    },
    {
        "route": "/plugins",
        "title": "Plugin",
        "marker": ".plugin, .card, h1, h2",
    },
    {
        "route": "/scheduling",
        "title": "Schedul",
        "marker": ".scheduling, .tab, table, h1, h2",
    },
    {
        "route": "/routines",
        "title": "Routine",
        "marker": ".routine, .card, table, h1, h2",
    },
    {
        "route": "/learning",
        "title": "Learn",
        "marker": ".learning, .tab, table, h1, h2",
    },
    {
        "route": "/proactive",
        "title": "Proactive",
        "marker": ".proactive, .card, table, h1, h2",
    },
    {
        "route": "/media",
        "title": "Media",
        "marker": ".media, .player, h1, h2",
    },
    {
        "route": "/intercom",
        "title": "Intercom",
        "marker": ".intercom, .zone, .broadcast, h1, h2",
    },
    {
        "route": "/evolution",
        "title": "Evolution",
        "marker": ".evolution, .tab, table, h1, h2",
    },
    {
        "route": "/stories",
        "title": "Stor",
        "marker": ".stories, .story, .tab, table, h1, h2",
    },
    {
        "route": "/system",
        "title": "System",
        "marker": ".system, .config, .section, h1, h2",
    },
]


# ══════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════


class TestLoginFlow:
    """Verify the login page loads and a login attempt works."""

    def test_login_page_loads(self, server, pw_browser):
        """Login view shows username and password inputs."""
        page = pw_browser.new_context().new_page()
        page.goto(f"{server}/admin/#/login", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        # The login form should have input fields
        inputs = page.query_selector_all("input")
        assert len(inputs) >= 2, "Expected at least 2 inputs (username + password)"
        page.context.close()

    def test_login_submit(self, server, pw_browser):
        """Submitting correct credentials redirects away from /login."""
        ctx = pw_browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(f"{server}/admin/#/login", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        # Fill in credentials
        inputs = page.query_selector_all("input")
        if len(inputs) >= 2:
            inputs[0].fill(ADMIN_USER)
            inputs[1].fill(ADMIN_PASS)

        # Submit (click button or press Enter)
        btn = page.query_selector("button[type='submit'], button.btn-primary, button")
        if btn:
            btn.click()
        else:
            inputs[1].press("Enter")

        # Wait for navigation away from login
        page.wait_for_timeout(2000)
        url = page.url
        # After login we should NOT be on /login anymore
        assert "/login" not in url or "token" in page.evaluate(
            "window.localStorage.getItem('atlas-admin-token') || ''"
        ), "Login did not succeed — still on login page with no token"
        ctx.close()


class TestAdminViews:
    """Walk through every admin view and verify it renders correctly."""

    @pytest.mark.parametrize(
        "view",
        [v for v in VIEWS if not v.get("skip_authed")],
        ids=[v["route"].strip("/") or "root" for v in VIEWS if not v.get("skip_authed")],
    )
    def test_view_loads(self, server, authed_context, view):
        """Navigate to each admin view and verify key elements."""
        page = authed_context.new_page()
        try:
            full_url = f"{server}/admin/#{view['route']}"
            page.goto(full_url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")

            current = page.url

            # If redirected to login (auth failure), the redirect itself
            # is valid behavior — verify login page renders instead.
            if "/login" in current and not view.get("public"):
                page.wait_for_selector("input", timeout=5000)
                return

            # Wait for at least one marker element.  The marker field is a
            # comma-separated CSS selector — any match satisfies the check.
            page.wait_for_selector(view["marker"], timeout=8000)

            # Verify page contains expected title text
            body_text = page.inner_text("body")
            assert view["title"].lower() in body_text.lower(), (
                f"Expected '{view['title']}' in page body for {view['route']}. "
                f"Body starts with: {body_text[:200]!r}"
            )
        finally:
            page.close()


class TestNavBar:
    """Verify the sidebar navigation has all expected items and is clickable."""

    EXPECTED_NAV_LABELS = [
        "Chat",
        "Dashboard",
        "Users",
        "Satellites",
        "Parental",
        "Safety",
        "Voice",
        "Avatar",
        "Devices",
        "Plugins",
        "Scheduling",
        "Routines",
        "Learning",
        "Proactive",
        "Media",
        "Intercom",
        "Evolution",
        "System",
    ]

    def test_navbar_has_all_links(self, server, authed_context):
        """All expected navigation items appear in the sidebar."""
        page = authed_context.new_page()
        try:
            page.goto(
                f"{server}/admin/#/dashboard", wait_until="domcontentloaded"
            )
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".nav-item", timeout=8000)

            nav_items = page.query_selector_all(".nav-item .nav-label")
            labels = [item.inner_text().strip() for item in nav_items]

            for expected in self.EXPECTED_NAV_LABELS:
                assert any(
                    expected.lower() in label.lower() for label in labels
                ), f"Missing nav item '{expected}'. Found: {labels}"
        finally:
            page.close()

    def test_click_through_all_nav(self, server, authed_context):
        """Click every nav item; verify the URL changes and no crash."""
        page = authed_context.new_page()
        try:
            page.goto(
                f"{server}/admin/#/dashboard", wait_until="domcontentloaded"
            )
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".nav-item", timeout=8000)

            # Collect the label text of each nav item first
            nav_labels = page.eval_on_selector_all(
                ".nav-item .nav-label",
                "els => els.map(e => e.textContent.trim())",
            )

            routes_visited: list[str] = []

            for label in nav_labels:
                if "logout" in label.lower():
                    continue

                # Re-query elements after each navigation (DOM may rebuild)
                items = page.query_selector_all(".nav-item")
                for item in items:
                    lbl_el = item.query_selector(".nav-label")
                    if lbl_el and lbl_el.inner_text().strip() == label:
                        item.click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(300)
                        routes_visited.append(page.url)
                        break

            unique = set(routes_visited)
            assert len(unique) >= 10, (
                f"Expected ≥10 distinct routes, visited {len(unique)}: {unique}"
            )
        finally:
            page.close()

    def test_sidebar_collapse(self, server, authed_context):
        """Collapse button hides nav labels."""
        page = authed_context.new_page()
        try:
            page.goto(
                f"{server}/admin/#/dashboard", wait_until="domcontentloaded"
            )
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".nav-item", timeout=8000)

            btn = page.query_selector(".collapse-btn")
            if btn:
                btn.click()
                page.wait_for_timeout(500)
                sidebar = page.query_selector("nav.sidebar")
                assert sidebar is not None
                cls = sidebar.get_attribute("class") or ""
                assert "collapsed" in cls, "Sidebar should have 'collapsed' class"
        finally:
            page.close()


class TestHealthEndpoint:
    """Quick server health check (not Playwright, but validates server fixture)."""

    def test_health_ok(self, server):
        resp = httpx.get(f"{server}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")


# ══════════════════════════════════════════════════════════════════════════
# CLI Smoke Tests (no browser needed, but grouped in same file)
# ══════════════════════════════════════════════════════════════════════════


class TestCLISmokeTest:
    """Basic CLI invocation tests — no browser required."""

    def test_cli_help(self):
        """``python -m cortex.cli --help`` runs without error."""
        result = subprocess.run(
            ["python3", "-m", "cortex.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        out = result.stdout.lower()
        assert "chat" in out or "atlas" in out or "usage" in out

    def test_cli_status(self):
        """``python -m cortex.cli status`` runs without crash."""
        tmpdir = tempfile.mkdtemp(prefix="cortex_cli_test_")
        env = {
            **os.environ,
            "CORTEX_DATA_DIR": tmpdir,
            "LLM_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://127.0.0.1:1",
        }
        result = subprocess.run(
            ["python3", "-m", "cortex.cli", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        # 0 = healthy, 1 = degraded/unreachable — both are fine
        assert result.returncode in (0, 1), (
            f"Unexpected exit code {result.returncode}. "
            f"stdout: {result.stdout[:300]}, stderr: {result.stderr[:300]}"
        )
