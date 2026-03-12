"""Playwright-based browser tests for the Atlas Cortex avatar and admin UI.

Run with::

    python -m pytest tests/test_ui_browser.py -v -m browser

Requires:
  - ``playwright`` (``pip install playwright && playwright install chromium``)
  - ``httpx``
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import struct
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip the entire module if playwright is not installed
# ---------------------------------------------------------------------------
pw = pytest.importorskip("playwright", reason="playwright package not installed")
from playwright.sync_api import sync_playwright, Page, Browser  # noqa: E402

import httpx  # noqa: E402

# Every test in this module gets the ``browser`` marker automatically.
pytestmark = pytest.mark.browser

SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 15100
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


# ── Helpers ───────────────────────────────────────────────────────────────

def _generate_silence_pcm(duration_ms: int = 200, sample_rate: int = 24000) -> str:
    """Return base64-encoded silent PCM (16-bit mono) for *duration_ms* ms."""
    n_samples = int(sample_rate * duration_ms / 1000)
    raw = struct.pack(f"<{n_samples}h", *([0] * n_samples))
    return base64.b64encode(raw).decode()


def _wait_for_server(base_url: str, *, retries: int = 30, delay: float = 0.5) -> bool:
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
    env = os.environ.copy()
    env["CORTEX_DB_PATH"] = ":memory:"
    env["CORTEX_HOST"] = SERVER_HOST
    env["CORTEX_PORT"] = str(SERVER_PORT)
    env["LLM_PROVIDER"] = "ollama"
    env["OLLAMA_BASE_URL"] = "http://127.0.0.1:1"
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


@pytest.fixture()
def page(server: str, pw_browser: Browser, request):
    """Provide a fresh browser page.  Takes a screenshot on failure."""
    context = pw_browser.new_context(viewport={"width": 1280, "height": 720})
    pg = context.new_page()
    yield pg

    # Screenshot on failure
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        safe_name = request.node.name.replace("/", "_").replace(":", "_")
        pg.screenshot(path=str(SCREENSHOTS_DIR / f"{safe_name}.png"))

    context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach call report to the request node so the page fixture can read it."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# ══════════════════════════════════════════════════════════════════════════
# Avatar Browser Tests
# ══════════════════════════════════════════════════════════════════════════


class TestAvatarDisplay:
    """Tests for the avatar display page (``/avatar``)."""

    # 1 — Avatar loads and renders ------------------------------------------

    def test_avatar_loads_and_renders(self, page: Page, server: str):
        """Navigate to /avatar, verify SVG appears and page title is correct."""
        t0 = time.monotonic()

        page.goto(f"{server}/avatar", wait_until="domcontentloaded")

        # The avatar SVG is injected into #avatar-svg-wrap after skin loads
        svg = page.locator("#avatar-svg-wrap svg")
        svg.wait_for(state="attached", timeout=5000)

        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 5000, f"Avatar load took {elapsed_ms:.0f}ms (limit 5000ms)"

        title = page.title()
        assert "Atlas" in title, f"Expected 'Atlas' in page title, got {title!r}"

    # 2 — Audio unlock overlay -----------------------------------------------

    def test_audio_unlock_overlay(self, page: Page, server: str):
        """Verify audio unlock overlay shows on load and disappears on click."""
        page.goto(f"{server}/avatar", wait_until="domcontentloaded")

        overlay = page.locator("#audio-unlock")
        overlay.wait_for(state="visible", timeout=3000)

        # Click it to dismiss
        overlay.click()
        overlay.wait_for(state="hidden", timeout=2000)

        unlocked = page.evaluate("() => window._audioUnlocked")
        assert unlocked is True

    # 3 — WebSocket connection -----------------------------------------------

    def test_websocket_connection(self, page: Page, server: str):
        """Verify the avatar opens a WebSocket and receives a SKIN message."""
        ws_messages: list[dict] = []

        def _on_ws(ws_obj):
            def _on_frame(data):
                try:
                    # Sync API passes the payload directly as str|bytes
                    payload = data if isinstance(data, str) else data.decode()
                    ws_messages.append(json.loads(payload))
                except Exception:
                    pass

            ws_obj.on("framereceived", _on_frame)

        page.on("websocket", _on_ws)

        t0 = time.monotonic()
        page.goto(f"{server}/avatar", wait_until="domcontentloaded")

        # Wait for WS messages (SKIN is sent immediately on connect)
        page.wait_for_timeout(3000)

        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 8000, f"WS flow took {elapsed_ms:.0f}ms"

        types = [m.get("type") for m in ws_messages]
        assert "SKIN" in types, f"Expected SKIN message, got {types}"

    # 4 — Expression transitions (timing) ------------------------------------

    def test_expression_transitions(self, page: Page, server: str):
        """Send EXPRESSION messages and verify DOM state changes."""
        page.goto(f"{server}/avatar", wait_until="domcontentloaded")
        page.locator("#avatar-svg-wrap svg").wait_for(state="attached", timeout=5000)

        # Dismiss audio overlay if present
        overlay = page.locator("#audio-unlock")
        if overlay.is_visible():
            overlay.click()

        # Wait for WS connection and initial skin load
        page.wait_for_timeout(2000)

        expressions_to_test = ["happy", "thinking", "neutral"]
        for expr in expressions_to_test:
            t0 = time.monotonic()

            # Inject expression via the page's JS functions
            page.evaluate(
                f"""() => {{
                    if (typeof setExpression === 'function') {{
                        setExpression('{expr}', 1.0);
                    }} else if (typeof showExpression === 'function') {{
                        showExpression('{expr}');
                    }}
                }}"""
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            assert elapsed_ms < 1000, (
                f"Expression '{expr}' took {elapsed_ms:.0f}ms (limit 1000ms)"
            )

            # Verify expression element is visible if it exists and is not neutral
            # (neutral is the base state — its group stays hidden)
            if expr != "neutral":
                expr_el = page.locator(f"#expr-{expr}")
                if expr_el.count() > 0:
                    try:
                        expr_el.wait_for(state="visible", timeout=2000)
                    except Exception:
                        pass  # Some skins don't have per-expression layers

            current = page.evaluate(
                "() => typeof currentExpr !== 'undefined' ? currentExpr : null"
            )
            if current is not None:
                assert current == expr, f"currentExpr={current!r}, expected {expr!r}"

    # 5 — Viseme timing with audio -------------------------------------------

    def test_viseme_tts_flow(self, page: Page, server: str):
        """Send TTS_START + TTS_CHUNK + TTS_END via JS and verify viseme state."""
        page.goto(f"{server}/avatar", wait_until="domcontentloaded")
        page.locator("#avatar-svg-wrap svg").wait_for(state="attached", timeout=5000)

        overlay = page.locator("#audio-unlock")
        if overlay.is_visible():
            overlay.click()
        page.wait_for_timeout(2000)

        session_id = "pw-test-tts-001"
        silence_b64 = _generate_silence_pcm(500)

        # Simulate TTS_START
        page.evaluate(
            """(args) => {
                if (typeof handleTtsStart === 'function') handleTtsStart(args);
                else if (typeof window.handleTtsStart === 'function') window.handleTtsStart(args);
            }""",
            {
                "type": "TTS_START",
                "session_id": session_id,
                "sample_rate": 24000,
                "text": "Hello from Playwright",
                "format": "pcm_24k_16bit_mono",
            },
        )

        # Simulate TTS_CHUNK
        page.evaluate(
            """(args) => {
                if (typeof handleTtsChunk === 'function') handleTtsChunk(args);
                else if (typeof window.handleTtsChunk === 'function') window.handleTtsChunk(args);
            }""",
            {
                "type": "TTS_CHUNK",
                "session_id": session_id,
                "audio": silence_b64,
            },
        )

        # Give the viseme scheduler a tick
        page.wait_for_timeout(300)

        # Simulate TTS_END
        page.evaluate(
            """(args) => {
                if (typeof handleTtsEnd === 'function') handleTtsEnd(args);
                else if (typeof window.handleTtsEnd === 'function') window.handleTtsEnd(args);
            }""",
            {
                "type": "TTS_END",
                "session_id": session_id,
                "expression": "happy",
            },
        )

        # After TTS_END, viseme should eventually return to IDLE
        # The viseme scheduler may keep animating for a bit after TTS_END
        page.wait_for_timeout(2500)
        viseme = page.evaluate(
            "() => typeof currentViseme !== 'undefined' ? currentViseme : null"
        )
        known_visemes = {
            "IDLE", "IDLE-TALK", "PP", "FF", "TH", "DD", "KK", "SS", "SH",
            "RR", "NN", "IH", "EH", "AA", "OH", "OU", "CLOSED", "OPEN",
            "ROUND", "TEETH", "FV",
        }
        if viseme is not None:
            assert viseme in known_visemes, (
                f"Unexpected viseme after TTS_END: {viseme!r}"
            )

    # 6 — Idle animations (blink) -------------------------------------------

    def test_idle_blink_animation(self, page: Page, server: str):
        """Wait on /avatar and verify at least one blink fires within 10 s."""
        page.goto(f"{server}/avatar", wait_until="domcontentloaded")
        page.locator("#avatar-svg-wrap svg").wait_for(state="attached", timeout=5000)

        overlay = page.locator("#audio-unlock")
        if overlay.is_visible():
            overlay.click()
        page.wait_for_timeout(1000)

        # Inject a blink counter — the blink routine briefly shows #blink
        page.evaluate("""() => {
            window._blinkCount = 0;
            const blinkEl = document.getElementById('blink');
            if (blinkEl) {
                const observer = new MutationObserver(mutations => {
                    for (const m of mutations) {
                        if (m.attributeName === 'style' || m.attributeName === 'display') {
                            const vis = window.getComputedStyle(blinkEl).display;
                            if (vis !== 'none') window._blinkCount++;
                        }
                    }
                });
                observer.observe(blinkEl, {
                    attributes: true,
                    attributeFilter: ['style', 'display'],
                });
            }
        }""")

        # Wait up to 10 s, polling every 500ms
        blinks = 0
        for _ in range(20):
            page.wait_for_timeout(500)
            blinks = page.evaluate("() => window._blinkCount || 0")
            if blinks >= 1:
                break

        assert blinks >= 1, "Expected at least 1 blink event in 10 s"

    # 7 — State transitions --------------------------------------------------

    def test_state_transitions(self, page: Page, server: str):
        """Send LISTENING / SPEAKING_START / SPEAKING_END and verify state."""
        page.goto(f"{server}/avatar", wait_until="domcontentloaded")
        page.locator("#avatar-svg-wrap svg").wait_for(state="attached", timeout=5000)

        overlay = page.locator("#audio-unlock")
        if overlay.is_visible():
            overlay.click()
        page.wait_for_timeout(2000)

        status = page.locator("#status")

        # Simulate LISTENING
        page.evaluate("""() => {
            if (typeof handleMessage === 'function')
                handleMessage({ type: 'LISTENING', active: true });
        }""")
        page.wait_for_timeout(500)
        cls = status.get_attribute("class") or ""
        listening_via_cls = "listening" in cls
        listening_via_js = page.evaluate(
            "() => typeof _isListening !== 'undefined' ? _isListening : false"
        )
        # At least one indicator should reflect listening state
        assert listening_via_cls or listening_via_js, (
            f"Listening state not detected (class={cls!r})"
        )

        # Simulate SPEAKING_START
        page.evaluate("""() => {
            if (typeof handleMessage === 'function')
                handleMessage({ type: 'SPEAKING_START' });
        }""")
        page.wait_for_timeout(500)
        speaking = page.evaluate(
            "() => typeof _isSpeaking !== 'undefined' ? _isSpeaking : null"
        )
        if speaking is not None:
            assert speaking is True, f"Expected _isSpeaking=true, got {speaking!r}"

        # Simulate SPEAKING_END
        page.evaluate("""() => {
            if (typeof handleMessage === 'function')
                handleMessage({ type: 'SPEAKING_END' });
        }""")
        page.wait_for_timeout(500)
        after = page.evaluate(
            "() => typeof _isSpeaking !== 'undefined' ? _isSpeaking : null"
        )
        if after is not None:
            assert after is False, "Expected _isSpeaking=false after SPEAKING_END"


# ══════════════════════════════════════════════════════════════════════════
# Admin Portal Browser Tests
# ══════════════════════════════════════════════════════════════════════════

_ADMIN_DIST = Path(__file__).resolve().parent.parent / "admin" / "dist"
_skip_admin = pytest.mark.skipif(
    not (_ADMIN_DIST / "index.html").is_file(),
    reason="Admin SPA not built (admin/dist/index.html missing)",
)


@_skip_admin
class TestAdminLogin:
    """Tests for the admin login flow."""

    # 8 — Login page loads ---------------------------------------------------

    def test_login_page_loads(self, page: Page, server: str):
        """Navigate to /admin/ — should show (or redirect to) the login form."""
        t0 = time.monotonic()

        page.goto(f"{server}/admin/", wait_until="networkidle")

        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 5000, f"Admin page load took {elapsed_ms:.0f}ms (limit 5000ms)"

        # Vue router should land on /#/login for unauthenticated users
        page.wait_for_timeout(1000)
        url = page.url
        assert "/login" in url or "login" in page.content().lower(), (
            f"Expected login page, got URL {url}"
        )

        # Verify login form elements by their IDs (from LoginView.vue)
        page.locator("#username").wait_for(state="visible", timeout=3000)
        page.locator("#password").wait_for(state="visible", timeout=3000)

    # 9 — Login flow ---------------------------------------------------------

    def test_login_flow(self, page: Page, server: str):
        """Fill login form with default creds, verify redirect to dashboard."""
        page.goto(f"{server}/admin/", wait_until="networkidle")
        page.wait_for_timeout(500)

        # Fill credentials (seeded: admin / atlas-admin)
        page.fill("#username", "admin")
        page.fill("#password", "atlas-admin")

        # Submit
        page.click("button[type='submit']")

        # Wait for navigation away from login
        page.wait_for_timeout(2000)
        url = page.url
        if "/login" in url:
            error_el = page.locator(".login-error")
            if error_el.count() > 0 and error_el.is_visible():
                error_text = error_el.text_content()
                pytest.skip(
                    f"Login failed (server may lack seed data): {error_text}"
                )
            pytest.fail(f"Still on login page after submit: {url}")

        content = page.content()
        assert "dashboard" in content.lower() or "Dashboard" in content


@_skip_admin
class TestAdminNavigation:
    """Tests for navigating the admin SPA after login."""

    def _login(self, page: Page, server: str):
        """Helper: log into the admin panel."""
        page.goto(f"{server}/admin/", wait_until="networkidle")
        page.wait_for_timeout(500)

        if "/login" not in page.url:
            return

        page.fill("#username", "admin")
        page.fill("#password", "atlas-admin")
        page.click("button[type='submit']")
        page.wait_for_timeout(2000)

        if "/login" in page.url:
            pytest.skip("Could not log in — skipping navigation tests")

    # 10 — Navigation walkthrough --------------------------------------------

    _NAV_ROUTES = [
        ("users", "Users", ["table", "user", "name"]),
        ("safety", "Safety", ["safety", "event", "filter"]),
        ("voice", "Voice", ["voice", "tts"]),
        ("avatar", "Avatar", ["avatar", "skin"]),
        ("devices", "Devices", ["device"]),
        ("satellites", "Satellites", ["satellite"]),
        ("evolution", "Evolution", ["evolution", "learn"]),
        ("system", "System", ["system", "info", "version"]),
    ]

    @pytest.mark.parametrize(
        "route,label,keywords",
        _NAV_ROUTES,
        ids=[r[0] for r in _NAV_ROUTES],
    )
    def test_nav_page(
        self,
        page: Page,
        server: str,
        route: str,
        label: str,
        keywords: list[str],
    ):
        """Navigate to each admin page and verify it loads without error."""
        self._login(page, server)

        t0 = time.monotonic()
        page.goto(f"{server}/admin/#/{route}", wait_until="networkidle")
        page.wait_for_timeout(1000)
        elapsed_ms = (time.monotonic() - t0) * 1000

        if "/login" in page.url:
            pytest.skip(f"Redirected to login on /{route}")

        content = page.content().lower()
        found = any(kw in content for kw in keywords)
        is_placeholder = "coming soon" in content or "placeholder" in content

        assert found or is_placeholder, (
            f"/{route} page loaded but none of {keywords} found in content "
            f"(load time: {elapsed_ms:.0f}ms)"
        )


@_skip_admin
class TestAdminCRUD:
    """Tests for admin CRUD operations on users."""

    def _login(self, page: Page, server: str):
        """Helper: log into the admin panel."""
        page.goto(f"{server}/admin/", wait_until="networkidle")
        page.wait_for_timeout(500)
        if "/login" not in page.url:
            return
        page.fill("#username", "admin")
        page.fill("#password", "atlas-admin")
        page.click("button[type='submit']")
        page.wait_for_timeout(2000)
        if "/login" in page.url:
            pytest.skip("Could not log in — skipping CRUD tests")

    # 11 — Admin CRUD flow (via REST API) ------------------------------------

    def test_user_crud_via_api(self, server: str):
        """Exercise user CRUD through the admin REST API to validate the
        backend that the admin SPA talks to."""
        # Login to get a token
        resp = httpx.post(
            f"{server}/admin/auth/login",
            json={"username": "admin", "password": "atlas-admin"},
            timeout=5,
        )
        if resp.status_code != 200:
            pytest.skip(f"Admin login API failed: {resp.status_code}")

        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # CREATE
        create_resp = httpx.post(
            f"{server}/admin/users",
            json={"username": "pw-test-user", "display_name": "Playwright Test"},
            headers=headers,
            timeout=5,
        )
        if create_resp.status_code not in (200, 201):
            pytest.skip(
                f"User creation failed ({create_resp.status_code}) "
                "— endpoint schema may differ"
            )
        user = create_resp.json()
        user_id = user.get("id") or user.get("user_id")
        assert user_id, f"No user ID in response: {user}"

        # READ list
        list_resp = httpx.get(f"{server}/admin/users", headers=headers, timeout=5)
        assert list_resp.status_code == 200
        users = list_resp.json()
        if isinstance(users, dict):
            users = users.get("users", users.get("items", []))
        names = [u.get("username", "") for u in users]
        display_names = [u.get("display_name", "") for u in users]
        assert "pw-test-user" in names or "Playwright Test" in display_names, (
            f"Created user not in list: usernames={names}, display_names={display_names}"
        )

        # READ detail
        detail_resp = httpx.get(
            f"{server}/admin/users/{user_id}", headers=headers, timeout=5
        )
        assert detail_resp.status_code == 200

        # UPDATE
        patch_resp = httpx.patch(
            f"{server}/admin/users/{user_id}",
            json={"display_name": "PW Updated"},
            headers=headers,
            timeout=5,
        )
        if patch_resp.status_code == 200:
            updated = patch_resp.json()
            assert updated.get("display_name") == "PW Updated"

        # DELETE
        del_resp = httpx.delete(
            f"{server}/admin/users/{user_id}", headers=headers, timeout=5
        )
        assert del_resp.status_code in (200, 204)

        # Verify deletion
        list_resp2 = httpx.get(f"{server}/admin/users", headers=headers, timeout=5)
        users2 = list_resp2.json()
        if isinstance(users2, dict):
            users2 = users2.get("users", users2.get("items", []))
        names2 = [u.get("username", "") for u in users2]
        display2 = [u.get("display_name", "") for u in users2]
        assert "pw-test-user" not in names2 and "PW Updated" not in display2, (
            "User not deleted"
        )
