"""Filesystem locations for session state and captured data."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    """Base directory for mbcli's state, honoring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "mbcli"


def state_path() -> Path:
    """Path to the saved Playwright storage state (cookies + localStorage)."""
    override = os.environ.get("MBCLI_STATE")
    if override:
        return Path(override).expanduser()
    return config_dir() / "state.json"


def captures_dir() -> Path:
    """Directory where raw JSON capture bundles are written."""
    return config_dir() / "captures"


def chrome_profile_dir() -> Path:
    """Dedicated persistent Chrome profile (keeps the Mercedes login)."""
    override = os.environ.get("MBCLI_CHROME_PROFILE")
    if override:
        return Path(override).expanduser()
    return config_dir() / "chrome-profile"


def session_marker() -> Path:
    """Touched after a successful login; its presence means 'we've signed in'."""
    return config_dir() / ".logged-in"


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
