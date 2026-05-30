"""Playwright session management: interactive login and headless capture."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from playwright.sync_api import (
    BrowserContext,
    Response,
    sync_playwright,
)

from . import DASHBOARD_URL
from .api import save_api_session
from .credentials import Credentials, load_credentials
from .paths import chrome_profile_dir, ensure_parent, session_marker

# The dashboard SPA bounces through Mercedes' identity provider when not
# authenticated. If, after navigation, the URL still matches one of these
# host/path markers, we are not signed in. (Matched against the URL; kept
# specific so the post-login callback `?b2xFlow=LOGIN` is NOT a false positive.)
AUTH_HOST_HINTS = ("id.mercedes-benz.com", "/ciam/", "/auth/", "//sso.")

# A realistic UA keeps the SPA from serving a degraded/blocked experience.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


class SessionError(RuntimeError):
    """Raised when the saved session is missing or no longer valid."""


@dataclass
class CapturedResponse:
    url: str
    status: int
    json: Any


@dataclass
class CaptureResult:
    final_url: str
    responses: list[CapturedResponse] = field(default_factory=list)

    @property
    def authenticated(self) -> bool:
        low = self.final_url.lower()
        return not any(h in low for h in AUTH_HOST_HINTS)


def _terminate_profile_chrome(profile: Path) -> None:
    """Kill any leftover Chrome still bound to our dedicated profile.

    A previous run interrupted with Ctrl-C can orphan the Chrome process, which
    keeps the profile's SingletonLock and makes the next launch hang forever.
    The profile is exclusive to mbcli, so anything using it is ours to clean up.
    """
    marker = f"--user-data-dir={profile}"
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        out = ""
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if marker in line and "Google Chrome" in line:
            with contextlib.suppress(ValueError, IndexError):
                pids.append(int(line.split(None, 1)[0]))
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
    if pids:
        time.sleep(1.5)
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
    # Remove stale singleton lock files so the fresh launch can take the profile.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        with contextlib.suppress(FileNotFoundError, OSError):
            (profile / name).unlink()


@contextlib.contextmanager
def _persistent(headless: bool) -> Iterator[BrowserContext]:
    """Launch the user's real Google Chrome with a dedicated persistent profile.

    The profile keeps the Mercedes login across runs, so you sign in once
    (headed) and later `status` runs reuse it headlessly. Falls back to
    Playwright's bundled Chromium only if real Chrome can't be launched.
    """
    profile = chrome_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    _terminate_profile_chrome(profile)  # clear any orphaned Chrome / stale lock
    args = [
        # Mercedes' login callback can fail in Chromium with
        # ERR_HTTP2_PROTOCOL_ERROR; forcing HTTP/1.1 avoids it.
        "--disable-http2",
        "--disable-quic",
        # Hide the automation fingerprint so the login's reCAPTCHA/bot-detection
        # doesn't silently block sign-in.
        "--disable-blink-features=AutomationControlled",
        "--window-size=1480,1000",
    ]
    kwargs: dict[str, Any] = dict(
        user_data_dir=str(profile),
        headless=headless,
        args=args,
        ignore_default_args=["--enable-automation"],
        user_agent=USER_AGENT,
        locale="en-US",
    )
    if headless:
        kwargs["viewport"] = {"width": 1366, "height": 900}
    else:
        kwargs["no_viewport"] = True  # OS window (via --window-size) drives size
    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception:
            context = p.chromium.launch_persistent_context(**kwargs)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        try:
            yield context
        finally:
            # During Ctrl-C the driver connection may already be torn down;
            # don't let cleanup raise over the original (Keyboard)Interrupt.
            with contextlib.suppress(Exception):
                context.close()


# Heuristic selectors for the Mercedes identity (Keycloak/CIAM) login form.
# Tried in order; first visible match wins. The browser is visible, so a miss
# just means the user types it themselves.
_EMAIL_SELECTORS = (
    'input[type="email"]',
    'input[name="username"]',
    'input#username',
    'input[autocomplete="username"]',
    'input[name="email"]',
)
_PASSWORD_SELECTORS = (
    'input[type="password"]',
    'input[name="password"]',
    'input#password',
    'input[autocomplete="current-password"]',
)
# NOTE: order matters — first match wins. On id.mercedes-benz.com the advance
# button is `#continue` on the email step and `#confirm` on the password step.
# Several `type=submit` elements exist (incl. the legal-texts footer link), so
# match these specific ids/texts and never fall back to a bare button[type=submit].
_SUBMIT_SELECTORS = (
    '#continue',
    '#confirm',
    '#kc-login',
    'button:has-text("Next")',
    'button:has-text("Continue")',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Weiter")',
    'button:has-text("Anmelden")',
)


def _fill_first(page: Any, selectors: tuple[str, ...], value: str, timeout: int) -> bool:
    for sel in selectors:
        try:
            page.wait_for_selector(sel, state="visible", timeout=timeout)
            page.fill(sel, value)
            return True
        except Exception:
            continue
    return False


def _present(page: Any, selectors: tuple[str, ...], timeout: int) -> bool:
    for sel in selectors:
        try:
            page.wait_for_selector(sel, state="visible", timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _click_first(page: Any, selectors: tuple[str, ...], timeout: int = 4_000) -> bool:
    for sel in selectors:
        try:
            page.click(sel, timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _autofill(page: Any, creds: Credentials) -> bool:
    """Best-effort fill of username (then password, if stored). Returns True if
    it filled at least the username field. The Mercedes form is two-step (email
    → Next → password), and OTP is always left to the user."""
    if not _fill_first(page, _EMAIL_SELECTORS, creds.username, timeout=15_000):
        return False
    if not creds.password:
        # Advance to the password step so it's ready, then hand off to the user.
        _click_first(page, _SUBMIT_SELECTORS)
        return True
    # Password stored: reveal the password field (if behind the Next step), then
    # fill and submit.
    if not _present(page, _PASSWORD_SELECTORS, timeout=1_500):
        _click_first(page, _SUBMIT_SELECTORS)
        page.wait_for_timeout(2_000)
    if _fill_first(page, _PASSWORD_SELECTORS, creds.password, timeout=15_000):
        _click_first(page, _SUBMIT_SELECTORS)
    return True


def _wait_for_auth(page: Any, timeout_s: int = 300) -> bool:
    """Poll until the browser returns to the authenticated me.mercedes-benz.com
    area after passing through the identity provider. Returns True on success,
    False on timeout. Lets the user complete OTP without pressing Enter."""
    saw_login = False
    stable = 0
    for _ in range(timeout_s):
        url = ""
        try:
            url = page.url.lower()
        except Exception:
            return False  # page/browser closed
        on_idp = "id.mercedes-benz.com" in url or any(
            h in url for h in ("ciam", "/auth/")
        )
        if on_idp:
            saw_login = True
            stable = 0
        elif saw_login and "me.mercedes-benz.com" in url:
            stable += 1
            if stable >= 2:  # ~2s stable back on the authenticated site
                return True
        try:
            page.wait_for_timeout(1_000)
        except Exception:
            return False
    return False


def _is_signed_out(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in AUTH_HOST_HINTS)


def login(*, autofill: bool = True) -> Path:
    """Open the user's real Chrome (persistent profile) and let them sign in.

    Uses the dedicated persistent profile, so if it already holds a valid
    session this returns immediately. Otherwise the user signs in (email,
    password, OTP) in the window and it continues automatically. Stored
    credentials (via `mbcli creds set`) pre-fill the form as a convenience.
    Returns the profile directory.
    """
    creds = load_credentials() if autofill else None
    profile = chrome_profile_dir()
    with _persistent(headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        # Visit the dashboard: it loads if already signed in, else redirects to
        # the identity provider.
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=120_000)
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=15_000)
        page.wait_for_timeout(1_500)

        if not _is_signed_out(page.url):
            print("Already signed in — this Chrome profile has a valid session.")
            _mark_logged_in()
            return profile

        if creds:
            # Best-effort pre-fill if a login form is on screen; ignore misses.
            with contextlib.suppress(Exception):
                _autofill(page, creds)

        print("\nBrowser opened — sign in to Mercedes me (email, password, OTP).")
        print("This continues automatically once you're signed in...\n")

        if not _wait_for_auth(page):
            # Auto-detection timed out (or the page closed) — fall back to Enter.
            try:
                input("If you're signed in, press Enter to finish... ")
            except EOFError:
                raise SessionError("Login aborted before the session was saved.")
            # KeyboardInterrupt propagates to main() for a clean "Cancelled."
        # The profile persists the session automatically on context close.
    _mark_logged_in()
    return profile


def _mark_logged_in() -> None:
    ensure_parent(session_marker()).touch()


# The dashboard is reached from the profile page via this link.
_MANAGE_VEHICLES_SELECTORS = (
    'a[href*="my-dashboard"]',
    'a:has-text("Manage Vehicles")',
    'button:has-text("Manage Vehicles")',
    'text="Manage Vehicles"',
)


def _follow_manage_vehicles(page: Any, context: BrowserContext) -> Any:
    """Click the 'Manage Vehicles' link and return the resulting page (which may
    be a new tab). Returns the original page if no link was found/clicked."""
    before = set(context.pages)
    clicked = False
    for sel in _MANAGE_VEHICLES_SELECTORS:
        try:
            page.wait_for_selector(sel, state="visible", timeout=4_000)
            page.click(sel, timeout=4_000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        return page
    page.wait_for_timeout(2_000)
    opened = [p for p in context.pages if p not in before]
    return opened[0] if opened else page


def capture(
    url: str = DASHBOARD_URL,
    *,
    headless: bool = True,
    settle_ms: int = 5_000,
    nav_timeout_ms: int = 60_000,
) -> CaptureResult:
    """Load the dashboard with the persistent profile and collect JSON."""
    result = CaptureResult(final_url="")
    with _persistent(headless=headless) as context:

        def on_response(response: Response) -> None:
            try:
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype.lower():
                    return
                body = response.json()
            except Exception:
                return
            result.responses.append(
                CapturedResponse(url=response.url, status=response.status, json=body)
            )

        def on_request(request: Any) -> None:
            # Capture the bearer token + headers so later runs can hit the API
            # directly (fast path) instead of launching a browser.
            if "status-information" in request.url:
                with contextlib.suppress(Exception):
                    save_api_session(dict(request.headers))

        # Listen at the context level so JSON is captured even if "Manage
        # Vehicles" opens the dashboard in a new tab.
        context.on("response", on_response)
        context.on("request", on_request)

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)

        # The dashboard streams telemetry forever, so don't wait for network
        # idle. Instead exit as soon as the vehicle status endpoint responds.
        def _have_status() -> bool:
            return any("status-information" in r.url for r in result.responses)

        def _await_status(max_ticks: int) -> bool:
            for _ in range(max_ticks):
                if _have_status() or _is_signed_out(page.url):
                    break
                page.wait_for_timeout(500)
            return _have_status()

        got = _await_status(60)  # up to ~30s, but typically a few seconds

        # Fallback: if we landed on the profile page, click through once.
        if not got and "my-dashboard" not in page.url.lower() \
                and not _is_signed_out(page.url):
            page = _follow_manage_vehicles(page, context)
            got = _await_status(30)

        page.wait_for_timeout(1_000)  # let the vehicles-list / late JSON settle

        result.final_url = page.url

    if not result.authenticated:
        raise SessionError(
            "Not signed in (redirected to login). Run `mbcli login`."
        )
    return result
