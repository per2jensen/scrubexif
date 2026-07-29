# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Docker Registry rollback cleanup."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "remove_dockerhub_tag.py"
DUMMY_DIGEST = f"sha256:{'a' * 64}"

if not SCRIPT.exists():
    pytest.skip(
        f"scripts/remove_dockerhub_tag.py not found at {SCRIPT}",
        allow_module_level=True,
    )

_spec = importlib.util.spec_from_file_location("remove_dockerhub_tag", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

get_registry_token = _mod.get_registry_token
get_manifest_digest = _mod.get_manifest_digest
delete_manifest = _mod.delete_manifest
remove_tag = _mod.remove_tag
main = _mod.main


def _resp(
    body: bytes = b"",
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build an HTTP response usable as a context manager.

    Args:
        body: Response body returned by read().
        status: HTTP response status.
        headers: Response headers.

    Returns:
        Configured mock HTTP response.
    """
    response = MagicMock()
    response.read.return_value = body
    response.status = status
    response.headers = headers or {}
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _http_error(code: int) -> urllib.error.HTTPError:
    """Build an HTTPError with the requested status code.

    Args:
        code: HTTP status code.

    Returns:
        Configured urllib HTTPError.
    """
    return urllib.error.HTTPError(
        url="https://registry-1.docker.io/",
        code=code,
        msg=f"HTTP {code}",
        hdrs={},  # type: ignore[arg-type]
        fp=BytesIO(b""),
    )


def test_get_registry_token_success_returns_token() -> None:
    """Registry authentication returns a scoped bearer token."""
    body = json.dumps({"token": "fake-registry-token"}).encode()

    with patch("urllib.request.urlopen", return_value=_resp(body)) as urlopen:
        result = get_registry_token(
            "user",
            "personal-access-token",
            "per2jensen/scrubexif",
        )

    request = urlopen.call_args.args[0]
    assert result == "fake-registry-token"
    assert request.get_method() == "GET"
    assert request.full_url.startswith("https://auth.docker.io/token?")
    assert "repository%3Aper2jensen%2Fscrubexif%3Apull%2Cpush%2Cdelete" in (
        request.full_url
    )
    assert request.headers["Authorization"].startswith("Basic ")


def test_get_registry_token_http_error_raises_system_exit() -> None:
    """Registry authentication failure remains fatal."""
    with patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(SystemExit) as exc:
            get_registry_token("user", "bad-token", "per2jensen/scrubexif")

    assert exc.value.code == 1


def test_get_registry_token_missing_token_raises_system_exit() -> None:
    """Authentication response without a token is rejected."""
    body = json.dumps({"detail": "missing token"}).encode()

    with patch("urllib.request.urlopen", return_value=_resp(body)):
        with pytest.raises(SystemExit) as exc:
            get_registry_token("user", "token", "per2jensen/scrubexif")

    assert exc.value.code == 1


def test_get_registry_token_invalid_repository_raises_value_error() -> None:
    """Malformed repository input is rejected before network access."""
    with pytest.raises(ValueError, match="namespace/name"):
        get_registry_token("user", "token", "invalid-repository")


def test_get_manifest_digest_success_returns_digest() -> None:
    """Registry HEAD resolves an existing tag to its manifest digest."""
    response = _resp(headers={"Docker-Content-Digest": DUMMY_DIGEST})

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        result = get_manifest_digest(
            "per2jensen/scrubexif",
            "0.7.24-2",
            "registry-token",
        )

    request = urlopen.call_args.args[0]
    assert result == DUMMY_DIGEST
    assert request.get_method() == "HEAD"
    assert request.full_url.endswith(
        "/v2/per2jensen/scrubexif/manifests/0.7.24-2"
    )
    assert request.headers["Authorization"] == "Bearer registry-token"


def test_get_manifest_digest_http_404_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Registry HEAD 404 means the tag is already absent."""
    caplog.set_level(logging.INFO, logger=_mod.logger.name)

    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        result = get_manifest_digest(
            "per2jensen/scrubexif",
            "0.7.24-2",
            "registry-token",
        )

    assert result is None
    assert "already absent" in caplog.text


def test_get_manifest_digest_invalid_digest_raises_system_exit() -> None:
    """A malformed Registry digest is rejected instead of deleted."""
    response = _resp(headers={"Docker-Content-Digest": "not-a-digest"})

    with patch("urllib.request.urlopen", return_value=response):
        with pytest.raises(SystemExit) as exc:
            get_manifest_digest(
                "per2jensen/scrubexif",
                "0.7.24-2",
                "registry-token",
            )

    assert exc.value.code == 1


def test_delete_manifest_http_202_returns_success() -> None:
    """Registry DELETE 202 completes successfully."""
    with patch(
        "urllib.request.urlopen",
        return_value=_resp(status=202),
    ) as urlopen:
        delete_manifest(
            "per2jensen/scrubexif",
            DUMMY_DIGEST,
            "registry-token",
        )

    request = urlopen.call_args.args[0]
    assert request.get_method() == "DELETE"
    assert request.full_url.endswith(
        f"/v2/per2jensen/scrubexif/manifests/{DUMMY_DIGEST}"
    )


def test_delete_manifest_http_404_returns_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Registry DELETE 404 means the manifest is already absent."""
    caplog.set_level(logging.INFO, logger=_mod.logger.name)

    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        delete_manifest(
            "per2jensen/scrubexif",
            DUMMY_DIGEST,
            "registry-token",
        )

    assert "already absent" in caplog.text


def test_delete_manifest_http_401_raises_system_exit() -> None:
    """Registry authorization failure remains fatal."""
    with patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(SystemExit) as exc:
            delete_manifest(
                "per2jensen/scrubexif",
                DUMMY_DIGEST,
                "registry-token",
            )

    assert exc.value.code == 1


def test_remove_tag_existing_manifest_resolves_then_deletes() -> None:
    """Existing tag cleanup performs HEAD followed by digest DELETE."""
    responses = [
        _resp(headers={"Docker-Content-Digest": DUMMY_DIGEST}),
        _resp(status=202),
    ]

    with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
        remove_tag(
            "per2jensen/scrubexif",
            "0.7.24-2",
            "registry-token",
        )

    assert [call.args[0].get_method() for call in urlopen.call_args_list] == [
        "HEAD",
        "DELETE",
    ]


def test_remove_tag_absent_manifest_does_not_delete() -> None:
    """Absent tag cleanup stops after the idempotent HEAD 404."""
    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error(404),
    ) as urlopen:
        remove_tag(
            "per2jensen/scrubexif",
            "0.7.24-2",
            "registry-token",
        )

    assert urlopen.call_count == 1


def test_main_happy_path_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Main authenticates, resolves the tag, and deletes its manifest."""
    token_body = json.dumps({"token": "registry-token"}).encode()
    responses = [
        _resp(token_body),
        _resp(headers={"Docker-Content-Digest": DUMMY_DIGEST}),
        _resp(status=202),
    ]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "remove_dockerhub_tag.py",
            "--repo",
            "per2jensen/scrubexif",
            "--tag",
            "0.7.24-2",
        ],
    )
    monkeypatch.setenv("DOCKERHUB_USER", "user")
    monkeypatch.setenv("DOCKERHUB_TOKEN", "personal-access-token")

    with patch("urllib.request.urlopen", side_effect=responses):
        main()


def test_main_missing_credentials_raises_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Docker Hub credentials fail before network access."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "remove_dockerhub_tag.py",
            "--repo",
            "per2jensen/scrubexif",
            "--tag",
            "0.7.24-2",
        ],
    )
    monkeypatch.delenv("DOCKERHUB_USER", raising=False)
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1


def test_main_delete_server_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry DELETE server failure remains fatal."""
    token_body = json.dumps({"token": "registry-token"}).encode()
    responses = [
        _resp(token_body),
        _resp(headers={"Docker-Content-Digest": DUMMY_DIGEST}),
        _http_error(500),
    ]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "remove_dockerhub_tag.py",
            "--repo",
            "per2jensen/scrubexif",
            "--tag",
            "0.7.24-2",
        ],
    )
    monkeypatch.setenv("DOCKERHUB_USER", "user")
    monkeypatch.setenv("DOCKERHUB_TOKEN", "personal-access-token")

    with patch("urllib.request.urlopen", side_effect=responses):
        with pytest.raises(SystemExit) as exc:
            main()

    assert exc.value.code == 1


@pytest.mark.dockerhub
def test_integration_authenticate_with_real_credentials() -> None:
    """Authenticate read-only when real Docker Hub credentials are available."""
    user = os.environ.get("DOCKERHUB_USER")
    token = os.environ.get("DOCKERHUB_TOKEN")
    if not user or not token:
        pytest.skip("DOCKERHUB_USER / DOCKERHUB_TOKEN not set")

    registry_token = get_registry_token(
        user,
        token,
        "per2jensen/scrubexif",
    )
    assert registry_token
