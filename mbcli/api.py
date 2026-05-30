"""Fast path: call the Mercedes JSON API directly with a captured bearer token.

The dashboard SPA authenticates its API calls with a simple
``Authorization: Bearer <token>`` header plus ``x-me-finorvin`` (which selects
the vehicle). We capture that header set during a browser run (see
``session.capture``) and replay it here over plain HTTP — ~1.5s vs ~9s for a
full browser launch. When the token expires the call fails and the caller
falls back to the (token-refreshing) browser path.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .paths import config_dir, ensure_parent

API_BASE = "https://api.oneweb.mercedes-benz.com"
# Fast path only needs the live status; the vehicle name/VIN is cached from a
# prior browser run (it rarely changes), saving a second round-trip.
_STATUS_ENDPOINT = "me/vsc/v1/user/vehicle/status-information?&locale=en-US"
# The only headers the API actually requires (telemetry headers dropped).
_KEEP_HEADERS = (
    "authorization", "x-application-name", "x-me-finorvin", "content-type",
    "user-agent", "referer", "accept-language",
)


@dataclass
class ApiResponse:
    """Same shape (.url/.status/.json) as session.CapturedResponse so the parser
    works for both the API and browser paths."""
    url: str
    status: int
    json: Any


class ApiError(Exception):
    """Raised when the direct API call fails (e.g. expired/invalid token)."""


def api_session_path():
    return config_dir() / "api.json"


def save_api_session(headers: dict) -> None:
    """Persist the curated request headers (incl. bearer token) at 0600."""
    curated = {k: v for k, v in headers.items() if k.lower() in _KEEP_HEADERS}
    if "authorization" not in {k.lower() for k in curated}:
        return
    path = ensure_parent(api_session_path())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"headers": curated}, fh)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_api_session() -> dict | None:
    path = api_session_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    headers = data.get("headers")
    if isinstance(headers, dict) and any(k.lower() == "authorization" for k in headers):
        return headers
    return None


def clear_api_session() -> None:
    with contextlib.suppress(FileNotFoundError, OSError):
        api_session_path().unlink()


def vehicle_cache_path():
    return config_dir() / "vehicles.json"


def save_vehicles(vehicles: list[dict]) -> None:
    """Cache the (non-secret) vehicle list so the fast path can iterate cars."""
    if not vehicles:
        return
    path = ensure_parent(vehicle_cache_path())
    path.write_text(json.dumps(vehicles))


def load_vehicles() -> list[dict] | None:
    path = vehicle_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) and data else None
    except (OSError, json.JSONDecodeError):
        return None


def fetch_status(headers: dict, finorvin: str | None = None,
                 *, timeout: float = 15.0) -> ApiResponse:
    """GET the live status for one vehicle (selected via `x-me-finorvin`).

    Raises ApiError on any HTTP/network failure so the caller can fall back to
    the browser path (which refreshes the token).
    """
    sent = dict(headers)
    if finorvin:
        for k in [k for k in sent if k.lower() == "x-me-finorvin"]:
            del sent[k]
        sent["x-me-finorvin"] = finorvin
    url = f"{API_BASE}/{_STATUS_ENDPOINT}"
    request = urllib.request.Request(url, headers=sent, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read())
        return ApiResponse(url=f"{url}#{finorvin or ''}", status=resp.status, json=body)
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code} from status-information") from e
    except Exception as e:  # network error, timeout, bad JSON
        raise ApiError(str(e)) from e
