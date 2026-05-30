"""Credentials storage tests using a temp file via MBCLI_CREDENTIALS."""

import os
import stat

import pytest

from mbcli import credentials as creds


@pytest.fixture
def cred_file(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    monkeypatch.setenv("MBCLI_CREDENTIALS", str(path))
    # Ensure env-var creds don't leak in from the real environment.
    monkeypatch.delenv("MBCLI_USERNAME", raising=False)
    monkeypatch.delenv("MBCLI_PASSWORD", raising=False)
    return path


def test_save_load_roundtrip(cred_file):
    creds.save_credentials("driver@example.com", "s3cret")
    loaded = creds.load_credentials()
    assert loaded is not None
    assert loaded.username == "driver@example.com"
    assert loaded.password == "s3cret"


def test_saved_file_is_0600(cred_file):
    creds.save_credentials("a", "b")
    mode = stat.S_IMODE(os.stat(cred_file).st_mode)
    assert mode == 0o600


def test_env_vars_take_precedence(cred_file, monkeypatch):
    creds.save_credentials("file-user", "file-pass")
    monkeypatch.setenv("MBCLI_USERNAME", "env-user")
    monkeypatch.setenv("MBCLI_PASSWORD", "env-pass")
    loaded = creds.load_credentials()
    assert loaded.username == "env-user"
    assert loaded.password == "env-pass"


def test_username_only_loads(cred_file):
    creds.save_credentials("driver@example.com")  # password omitted
    loaded = creds.load_credentials()
    assert loaded is not None
    assert loaded.username == "driver@example.com"
    assert loaded.password == ""


def test_missing_returns_none(cred_file):
    assert creds.load_credentials() is None


def test_clear(cred_file):
    creds.save_credentials("a", "b")
    assert creds.clear_credentials() is True
    assert creds.load_credentials() is None
    assert creds.clear_credentials() is False
