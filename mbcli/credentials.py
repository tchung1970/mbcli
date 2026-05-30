"""Optional stored credentials for auto-filling the Mercedes me login form.

Credentials live in a 0600 file under the config dir (or env vars). They are
only ever used to type into the *visible* login browser during `mbcli login`;
they are never sent anywhere by mbcli itself. OTP is always completed by you.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .paths import config_dir, ensure_parent


def credentials_path() -> Path:
    override = os.environ.get("MBCLI_CREDENTIALS")
    if override:
        return Path(override).expanduser()
    return config_dir() / "credentials.json"


@dataclass
class Credentials:
    username: str
    password: str


def load_credentials() -> Credentials | None:
    """Return credentials from env vars, else the credentials file, else None."""
    env_user = os.environ.get("MBCLI_USERNAME")
    env_pass = os.environ.get("MBCLI_PASSWORD")
    if env_user and env_pass:
        return Credentials(env_user, env_pass)

    path = credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    username = data.get("username", "")
    password = data.get("password", "")
    if not username:
        return None
    return Credentials(username, password)


def save_credentials(username: str, password: str = "") -> Path:
    """Write credentials to a freshly-created 0600 file."""
    path = credentials_path()
    ensure_parent(path)
    # Open with O_CREAT|O_TRUNC and mode 0600 so the secret never briefly
    # exists with broader permissions.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"username": username, "password": password}, fh)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def clear_credentials() -> bool:
    """Delete the credentials file. Returns True if a file was removed."""
    path = credentials_path()
    if path.exists():
        path.unlink()
        return True
    return False
