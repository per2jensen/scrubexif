#!/usr/bin/env python3
"""Load pinned security-tool versions and detect available updates."""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)
VERSION_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
EXPECTED_TOOLS = frozenset(("syft", "grype"))


@dataclass(frozen=True)
class SecurityToolVersions:
    """Pinned Syft and Grype release tags.

    Args:
        syft: Exact Syft release tag.
        grype: Exact Grype release tag.
    """

    syft: str
    grype: str

    def __post_init__(self) -> None:
        """Validate both release tags after initialization.

        Returns:
            None.

        Raises:
            ValueError: If either release tag is malformed.
        """
        validate_version("syft", self.syft)
        validate_version("grype", self.grype)


def validate_version(tool: str, version: str) -> str:
    """Validate a pinned security-tool release tag.

    Args:
        tool: Tool name used in validation errors.
        version: Release tag to validate.

    Returns:
        The validated release tag.

    Raises:
        ValueError: If the tool name or version is invalid.
    """
    if not isinstance(tool, str) or not tool:
        raise ValueError("tool must be a non-empty string")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(
            f"{tool} version must be an exact release tag such as v1.2.3",
        )
    return version


def load_versions(config_path: pathlib.Path) -> SecurityToolVersions:
    """Load and validate pinned versions from JSON.

    Args:
        config_path: Path to the security-tool version configuration.

    Returns:
        Validated Syft and Grype versions.

    Raises:
        ValueError: If the path or JSON structure is invalid.
        OSError: If the configuration cannot be read.
    """
    if not isinstance(config_path, pathlib.Path):
        raise ValueError("config_path must be a pathlib.Path")
    if not config_path.is_file():
        raise ValueError(f"Security-tool configuration does not exist: {config_path}")

    try:
        raw_config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Security-tool configuration is not valid JSON: {config_path}",
        ) from exc
    except OSError:
        LOGGER.error("Unable to read security-tool configuration: %s", config_path)
        raise

    if not isinstance(raw_config, dict):
        raise ValueError("Security-tool configuration must be a JSON object")
    if set(raw_config) != EXPECTED_TOOLS:
        raise ValueError(
            "Security-tool configuration must contain exactly 'syft' and 'grype'",
        )

    return SecurityToolVersions(
        syft=validate_version("syft", raw_config["syft"]),
        grype=validate_version("grype", raw_config["grype"]),
    )


def write_github_environment(
    versions: SecurityToolVersions,
    environment_path: pathlib.Path,
) -> None:
    """Append pinned versions to a GitHub Actions environment file.

    Args:
        versions: Validated security-tool versions.
        environment_path: GitHub Actions environment file.

    Returns:
        None.

    Raises:
        ValueError: If an argument is invalid.
        OSError: If the environment file cannot be written.
    """
    if not isinstance(versions, SecurityToolVersions):
        raise ValueError("versions must be SecurityToolVersions")
    if not isinstance(environment_path, pathlib.Path):
        raise ValueError("environment_path must be a pathlib.Path")

    try:
        with environment_path.open("a", encoding="utf-8") as environment_file:
            environment_file.write(f"SYFT_VERSION={versions.syft}\n")
            environment_file.write(f"GRYPE_VERSION={versions.grype}\n")
    except OSError:
        LOGGER.error(
            "Unable to write GitHub Actions environment file: %s",
            environment_path,
        )
        raise


def find_outdated_versions(
    pinned: SecurityToolVersions,
    latest: SecurityToolVersions,
) -> dict[str, tuple[str, str]]:
    """Find pinned versions that differ from the latest releases.

    Args:
        pinned: Versions currently pinned by the repository.
        latest: Latest release versions reported by GitHub.

    Returns:
        Mapping of outdated tool names to ``(pinned, latest)`` tuples.

    Raises:
        ValueError: If either argument has the wrong type.
    """
    if not isinstance(pinned, SecurityToolVersions):
        raise ValueError("pinned must be SecurityToolVersions")
    if not isinstance(latest, SecurityToolVersions):
        raise ValueError("latest must be SecurityToolVersions")

    outdated: dict[str, tuple[str, str]] = {}
    if pinned.syft != latest.syft:
        outdated["syft"] = (pinned.syft, latest.syft)
    if pinned.grype != latest.grype:
        outdated["grype"] = (pinned.grype, latest.grype)
    return outdated


def write_github_summary(
    pinned: SecurityToolVersions,
    latest: SecurityToolVersions,
    summary_path: pathlib.Path,
) -> None:
    """Append the security-tool version comparison to a step summary.

    Args:
        pinned: Versions currently pinned by the repository.
        latest: Latest release versions reported by GitHub.
        summary_path: GitHub Actions step-summary file.

    Returns:
        None.

    Raises:
        ValueError: If an argument is invalid.
        OSError: If the summary cannot be written.
    """
    if not isinstance(pinned, SecurityToolVersions):
        raise ValueError("pinned must be SecurityToolVersions")
    if not isinstance(latest, SecurityToolVersions):
        raise ValueError("latest must be SecurityToolVersions")
    if not isinstance(summary_path, pathlib.Path):
        raise ValueError("summary_path must be a pathlib.Path")

    rows = (
        ("Syft", pinned.syft, latest.syft),
        ("Grype", pinned.grype, latest.grype),
    )
    lines = [
        "## Security tool version check",
        "",
        "| Tool | Pinned | Latest | Status |",
        "|---|---|---|---|",
    ]
    for tool, pinned_version, latest_version in rows:
        status = "Current" if pinned_version == latest_version else "Update available"
        lines.append(
            f"| {tool} | `{pinned_version}` | `{latest_version}` | {status} |",
        )

    try:
        with summary_path.open("a", encoding="utf-8") as summary_file:
            summary_file.write("\n".join(lines) + "\n")
    except OSError:
        LOGGER.error("Unable to write GitHub Actions summary: %s", summary_path)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional command-line arguments excluding the executable name.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Manage pinned Syft and Grype versions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export",
        help="Export pinned versions to GitHub Actions.",
    )
    export_parser.add_argument("--config", type=pathlib.Path, required=True)
    export_parser.add_argument("--github-env", type=pathlib.Path, required=True)

    check_parser = subparsers.add_parser(
        "check",
        help="Compare pinned versions with latest GitHub releases.",
    )
    check_parser.add_argument("--config", type=pathlib.Path, required=True)
    check_parser.add_argument("--latest-syft", required=True)
    check_parser.add_argument("--latest-grype", required=True)
    check_parser.add_argument("--github-summary", type=pathlib.Path, required=True)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested security-tool version operation.

    Args:
        argv: Optional command-line arguments excluding the executable name.

    Returns:
        Zero when successful or one when pinned versions are outdated.

    Raises:
        ValueError: If configuration or version input is invalid.
        OSError: If a requested output file cannot be written.
    """
    args = parse_args(argv)
    pinned = load_versions(args.config)

    if args.command == "export":
        write_github_environment(pinned, args.github_env)
        LOGGER.info(
            "Loaded pinned security tools: Syft %s, Grype %s",
            pinned.syft,
            pinned.grype,
        )
        return 0

    latest = SecurityToolVersions(
        syft=validate_version("latest syft", args.latest_syft),
        grype=validate_version("latest grype", args.latest_grype),
    )
    write_github_summary(pinned, latest, args.github_summary)
    outdated = find_outdated_versions(pinned, latest)
    if not outdated:
        LOGGER.info("Pinned Syft and Grype versions are current")
        return 0

    for tool, (pinned_version, latest_version) in outdated.items():
        LOGGER.error(
            "%s update available: pinned %s, latest %s",
            tool,
            pinned_version,
            latest_version,
        )
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
