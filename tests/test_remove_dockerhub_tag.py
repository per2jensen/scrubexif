# SPDX-License-Identifier: GPL-3.0-or-later
"""
Unit and integration tests for scripts/remove_dockerhub_tag.py.

Covers:
  - get_jwt: success, HTTP error, missing token in response
  - remove_tag: success (204), HTTP error (404), HTTP error (401)
  - main: full happy path, login failure propagates, delete failure propagates

Design notes
------------
* The module is loaded once via importlib so individual functions can be
  tested without spawning a subprocess.
* All HTTP calls are mocked — no network access in unit tests.
* The single integration test is marked @pytest.mark.integration and also
  skips automatically when DOCKERHUB_USER / DOCKERHUB_TOKEN are absent,
  so it never fails silently in environments without credentials.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load the script as a module (avoids adding scripts/ to sys.path globally)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "remove_dockerhub_tag.py"

if not SCRIPT.exists():
    pytest.skip(
        f"scripts/remove_dockerhub_tag.py not found at {SCRIPT}",
        allow_module_level=True,
    )

_spec = importlib.util.spec_from_file_location("remove_dockerhub_tag", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

get_jwt = _mod.get_jwt
remove_tag = _mod.remove_tag
main = _mod.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resp(body: bytes = b"", status: int = 200) -> MagicMock:
    """Build a mock HTTP response usable as a context manager."""
    r = MagicMock()
    r.read.return_value = body
    r.status = status
    r.__enter__ = lambda s: s
    r.__exit__ = MagicMock(return_value=False)
    return r


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://hub.docker.com/",
        code=code,
        msg=f"HTTP {code}",
        hdrs={},  # type: ignore[arg-type]
        fp=BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# get_jwt
# ---------------------------------------------------------------------------

def test_get_jwt_success_returns_token():
    """Login succeeds and response contains a token → JWT is returned."""
    body = json.dumps({"token": "fake-jwt-abc"}).encode()
    with patch("urllib.request.urlopen", return_value=_resp(body)):
        result = get_jwt("user", "pass")
    assert result == "fake-jwt-abc"


def test_get_jwt_http_error_raises_system_exit():
    """Login returns HTTP 401 → SystemExit(1)."""
    with patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(SystemExit) as exc:
            get_jwt("user", "badpass")
    assert exc.value.code == 1


def test_get_jwt_missing_token_in_response_raises_system_exit():
    """Login returns 200 but body contains no 'token' key → SystemExit(1)."""
    body = json.dumps({"detail": "something else"}).encode()
    with patch("urllib.request.urlopen", return_value=_resp(body)):
        with pytest.raises(SystemExit) as exc:
            get_jwt("user", "pass")
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# remove_tag
# ---------------------------------------------------------------------------

def test_remove_tag_success_on_204():
    """DELETE returns 204 → completes without raising."""
    with patch("urllib.request.urlopen", return_value=_resp(status=204)):
        remove_tag("per2jensen/scrubexif", "1.2.3", "fake-jwt")


def test_remove_tag_http_404_raises_system_exit():
    """DELETE returns 404 (tag not found) → SystemExit(1)."""
    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        with pytest.raises(SystemExit) as exc:
            remove_tag("per2jensen/scrubexif", "1.2.3", "fake-jwt")
    assert exc.value.code == 1


def test_remove_tag_http_401_raises_system_exit():
    """DELETE returns 401 (bad/expired token) → SystemExit(1)."""
    with patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(SystemExit) as exc:
            remove_tag("per2jensen/scrubexif", "1.2.3", "fake-jwt")
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# main (full flow)
# ---------------------------------------------------------------------------

def test_main_happy_path_exits_cleanly(monkeypatch):
    """Login succeeds, DELETE returns 204 → main() returns without raising."""
    login_body = json.dumps({"token": "fake-jwt"}).encode()
    monkeypatch.setattr(sys, "argv", ["remove_dockerhub_tag.py", "--repo", "per2jensen/scrubexif", "--tag", "1.2.3"])
    monkeypatch.setenv("DOCKERHUB_USER", "u")
    monkeypatch.setenv("DOCKERHUB_TOKEN", "t")
    with patch("urllib.request.urlopen",
               side_effect=[_resp(login_body), _resp(status=204)]):
        main()  # must not raise


def test_main_missing_credentials_raises_system_exit(monkeypatch):
    """DOCKERHUB_USER / DOCKERHUB_TOKEN absent → SystemExit(1) before any HTTP call."""
    monkeypatch.setattr(sys, "argv", ["remove_dockerhub_tag.py", "--repo", "per2jensen/scrubexif", "--tag", "1.2.3"])
    monkeypatch.delenv("DOCKERHUB_USER", raising=False)
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_main_login_failure_propagates(monkeypatch):
    """Login returns HTTP 401 → main() raises SystemExit(1)."""
    monkeypatch.setattr(sys, "argv", ["remove_dockerhub_tag.py", "--repo", "per2jensen/scrubexif", "--tag", "1.2.3"])
    monkeypatch.setenv("DOCKERHUB_USER", "u")
    monkeypatch.setenv("DOCKERHUB_TOKEN", "t")
    with patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 1


def test_main_delete_failure_propagates(monkeypatch):
    """Login succeeds but DELETE returns 404 → main() raises SystemExit(1)."""
    login_body = json.dumps({"token": "fake-jwt"}).encode()
    monkeypatch.setattr(sys, "argv", ["remove_dockerhub_tag.py", "--repo", "per2jensen/scrubexif", "--tag", "1.2.3"])
    monkeypatch.setenv("DOCKERHUB_USER", "u")
    monkeypatch.setenv("DOCKERHUB_TOKEN", "t")
    with patch("urllib.request.urlopen",
               side_effect=[_resp(login_body), _http_error(404)]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Integration test — real Docker Hub API, skipped without credentials
# ---------------------------------------------------------------------------

@pytest.mark.dockerhub
def test_integration_login_with_real_credentials():
    """
    Authenticate against the live Docker Hub API and verify a JWT is returned.

    Skipped automatically when DOCKERHUB_USER / DOCKERHUB_TOKEN are not set.
    Does not push or delete anything — read-only proof that credentials work.

    Run locally with:
        DOCKERHUB_USER=per2jensen DOCKERHUB_TOKEN=your_token \\
          python3 -m pytest tests/test_remove_dockerhub_tag.py::test_integration_login_with_real_credentials \\
          -v --override-ini="addopts="

    --override-ini="addopts=" is required because pytest.ini excludes the
    'dockerhub' mark by default via addopts = -m "not ... and not dockerhub".
    """
    user = os.environ.get("DOCKERHUB_USER")
    token = os.environ.get("DOCKERHUB_TOKEN")
    if not user or not token:
        pytest.skip("DOCKERHUB_USER / DOCKERHUB_TOKEN not set")

    jwt = get_jwt(user, token)
    assert jwt, "Expected a non-empty JWT from Docker Hub login"
