# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for fail-closed Docker Hub manifest inspection."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.dockerhub_manifest import (
    ExistingTagError,
    format_image_digest,
    load_credentials,
    require_tag_absent,
    write_github_output,
)

DIGEST = "sha256:" + ("a" * 64)


def test_load_credentials_complete_environment_returns_credentials() -> None:
    """Complete credentials are returned without modification."""
    credentials = load_credentials(
        {
            "DOCKERHUB_USER": "release-user",
            "DOCKERHUB_TOKEN": "release-token",
        },
    )

    assert credentials == ("release-user", "release-token")


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"DOCKERHUB_USER": "release-user"},
        {"DOCKERHUB_TOKEN": "release-token"},
    ],
)
def test_load_credentials_missing_value_raises_value_error(
    environment: dict[str, str],
) -> None:
    """Missing credentials fail before any registry request is attempted."""
    with pytest.raises(ValueError):
        load_credentials(environment)


def test_require_tag_absent_confirmed_absence_returns_successfully() -> None:
    """A confirmed Registry API 404 permits immutable publication."""
    require_tag_absent("per2jensen/scrubexif", "1.2.3", None)


def test_require_tag_absent_existing_digest_raises_existing_tag_error() -> None:
    """An existing digest blocks an immutable tag overwrite."""
    with pytest.raises(ExistingTagError, match="already exists"):
        require_tag_absent("per2jensen/scrubexif", "1.2.3", DIGEST)


def test_format_image_digest_valid_digest_returns_immutable_reference() -> None:
    """A registry digest is combined with its repository name."""
    assert format_image_digest("per2jensen/scrubexif", DIGEST) == (
        f"per2jensen/scrubexif@{DIGEST}"
    )


def test_format_image_digest_invalid_digest_raises_value_error() -> None:
    """Malformed registry output cannot enter signing or build history."""
    with pytest.raises(ValueError, match="sha256"):
        format_image_digest("per2jensen/scrubexif", "not-a-digest")


def test_format_image_digest_invalid_repository_raises_value_error() -> None:
    """Malformed repository names cannot enter immutable image references."""
    with pytest.raises(ValueError, match="repository"):
        format_image_digest("UPPERCASE/invalid", DIGEST)


def test_write_github_output_valid_digest_appends_output(tmp_path: Path) -> None:
    """The resolved digest is exported using GitHub output syntax."""
    output_path = tmp_path / "github-output"
    output_path.write_text("existing=value\n", encoding="utf-8")

    write_github_output(
        output_path,
        f"per2jensen/scrubexif@{DIGEST}",
    )

    assert output_path.read_text(encoding="utf-8") == (
        "existing=value\n"
        f"digest=per2jensen/scrubexif@{DIGEST}\n"
    )


def test_write_github_output_malformed_reference_raises_value_error(
    tmp_path: Path,
) -> None:
    """Only a fully validated repository digest can reach later steps."""
    output_path = tmp_path / "github-output"

    with pytest.raises(ValueError, match="immutable image reference"):
        write_github_output(output_path, "per2jensen/scrubexif@sha256:short")

    assert not output_path.exists()
