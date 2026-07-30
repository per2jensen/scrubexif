# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for pinned security-tool version management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.security_tool_versions import (
    SecurityToolVersions,
    find_outdated_versions,
    load_versions,
    main,
    write_github_environment,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / ".github" / "security-tools.json"


def test_load_versions_repository_config_returns_exact_release_tags() -> None:
    """The repository configuration contains valid exact release tags."""
    versions = load_versions(CONFIG)

    assert versions == SecurityToolVersions(
        syft="v1.50.0",
        grype="v0.116.1",
    )


def test_load_versions_missing_tool_raises_value_error(tmp_path: Path) -> None:
    """A configuration missing either required tool is rejected."""
    config = tmp_path / "security-tools.json"
    config.write_text(json.dumps({"syft": "v1.50.0"}), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly 'syft' and 'grype'"):
        load_versions(config)


def test_write_github_environment_valid_versions_appends_both_pins(
    tmp_path: Path,
) -> None:
    """Validated pins are exported using GitHub environment-file syntax."""
    environment_path = tmp_path / "github-env"
    environment_path.write_text("EXISTING=value\n", encoding="utf-8")

    write_github_environment(
        SecurityToolVersions(syft="v1.50.0", grype="v0.116.1"),
        environment_path,
    )

    assert environment_path.read_text(encoding="utf-8") == (
        "EXISTING=value\n"
        "SYFT_VERSION=v1.50.0\n"
        "GRYPE_VERSION=v0.116.1\n"
    )


def test_find_outdated_versions_matching_versions_returns_empty_mapping() -> None:
    """Matching pinned and latest releases require no update."""
    versions = SecurityToolVersions(syft="v1.50.0", grype="v0.116.1")

    assert find_outdated_versions(versions, versions) == {}


def test_find_outdated_versions_new_syft_reports_version_difference() -> None:
    """A newer release is reported with both pinned and latest tags."""
    pinned = SecurityToolVersions(syft="v1.50.0", grype="v0.116.1")
    latest = SecurityToolVersions(syft="v1.51.0", grype="v0.116.1")

    assert find_outdated_versions(pinned, latest) == {
        "syft": ("v1.50.0", "v1.51.0"),
    }


def test_main_current_versions_returns_success_and_writes_summary(
    tmp_path: Path,
) -> None:
    """The monthly check succeeds when both pins are current."""
    summary = tmp_path / "summary.md"

    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--latest-syft",
            "v1.50.0",
            "--latest-grype",
            "v0.116.1",
            "--github-summary",
            str(summary),
        ],
    )

    assert result == 0
    assert "Current" in summary.read_text(encoding="utf-8")


def test_main_outdated_version_returns_failure_and_writes_summary(
    tmp_path: Path,
) -> None:
    """The monthly check fails visibly when a pinned tool is outdated."""
    summary = tmp_path / "summary.md"

    result = main(
        [
            "check",
            "--config",
            str(CONFIG),
            "--latest-syft",
            "v1.51.0",
            "--latest-grype",
            "v0.116.1",
            "--github-summary",
            str(summary),
        ],
    )

    assert result == 1
    assert "Update available" in summary.read_text(encoding="utf-8")
