#!/usr/bin/env python3
"""Compute the next immutable image-refresh version from build history."""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

LOGGER = logging.getLogger(__name__)
STABLE_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REFRESH_VERSION_PATTERN = re.compile(
    r"^([0-9]+\.[0-9]+\.[0-9]+)-([1-9][0-9]*)$",
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Compute the next X.Y.Z-N image refresh version.",
    )
    parser.add_argument(
        "--history",
        type=pathlib.Path,
        required=True,
        help="Path to build-history.json.",
    )
    parser.add_argument(
        "--repository",
        type=pathlib.Path,
        required=True,
        help="Path to the Git repository.",
    )
    parser.add_argument(
        "--github-output",
        type=pathlib.Path,
        help="Append base_version and refresh_version to this GitHub output file.",
    )
    return parser.parse_args()


def load_base_version(history_path: pathlib.Path) -> str:
    """Load and validate the base release version from build history.

    Args:
        history_path: Path to the JSON build-history file.

    Returns:
        Stable release version without a numeric refresh suffix.

    Raises:
        ValueError: If the path or build history content is invalid.
    """
    if not isinstance(history_path, pathlib.Path):
        raise ValueError("history_path must be a pathlib.Path")
    if not history_path.is_file():
        raise ValueError(f"Build history does not exist: {history_path}")

    try:
        raw_history: Any = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Build history is not valid JSON: {history_path}") from exc
    except OSError as exc:
        LOGGER.error("Unable to read build history %s: %s", history_path, exc)
        raise

    if not isinstance(raw_history, list) or not raw_history:
        raise ValueError("Build history must be a non-empty JSON list")

    latest_entry = raw_history[-1]
    if not isinstance(latest_entry, dict):
        raise ValueError("Latest build-history entry must be a JSON object")

    raw_tag = latest_entry.get("tag")
    if not isinstance(raw_tag, str) or not raw_tag:
        raise ValueError("Latest build-history entry must contain a non-empty tag")

    if STABLE_VERSION_PATTERN.fullmatch(raw_tag):
        return raw_tag

    refresh_match = REFRESH_VERSION_PATTERN.fullmatch(raw_tag)
    if refresh_match is not None:
        return refresh_match.group(1)

    raise ValueError(
        f"Latest build tag {raw_tag!r} does not identify a stable X.Y.Z release",
    )


def list_git_tags(repository_path: pathlib.Path) -> list[str]:
    """List Git tags in a repository.

    Args:
        repository_path: Path to the Git repository.

    Returns:
        All tag names in the repository.

    Raises:
        ValueError: If repository_path is invalid.
        subprocess.CalledProcessError: If Git cannot list the tags.
    """
    if not isinstance(repository_path, pathlib.Path):
        raise ValueError("repository_path must be a pathlib.Path")
    if not repository_path.is_dir():
        raise ValueError(f"Repository directory does not exist: {repository_path}")

    try:
        result = subprocess.run(
            ["git", "tag", "--list"],
            cwd=repository_path,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        LOGGER.error("Unable to list Git tags in %s: %s", repository_path, exc.stderr)
        raise

    return [tag for tag in result.stdout.splitlines() if tag]


def compute_next_refresh_version(base_version: str, git_tags: Sequence[str]) -> str:
    """Compute the next numeric refresh version for a stable release.

    Args:
        base_version: Stable release version in X.Y.Z form.
        git_tags: Existing Git tag names.

    Returns:
        Next refresh version in X.Y.Z-N form.

    Raises:
        ValueError: If inputs are invalid or the stable release tag is absent.
    """
    if not isinstance(base_version, str) or not base_version:
        raise ValueError("base_version must be a non-empty string")
    if not STABLE_VERSION_PATTERN.fullmatch(base_version):
        raise ValueError(f"Invalid stable base version: {base_version!r}")
    if isinstance(git_tags, (str, bytes)) or not isinstance(git_tags, Sequence):
        raise ValueError("git_tags must be a sequence of strings")
    if any(not isinstance(tag, str) or not tag for tag in git_tags):
        raise ValueError("git_tags must contain only non-empty strings")

    stable_git_tag = f"v{base_version}"
    if stable_git_tag not in git_tags:
        raise ValueError(f"Required stable Git tag does not exist: {stable_git_tag}")

    refresh_pattern = re.compile(
        rf"^v{re.escape(base_version)}-([1-9][0-9]*)$",
    )
    refresh_numbers = [
        int(match.group(1))
        for tag in git_tags
        if (match := refresh_pattern.fullmatch(tag)) is not None
    ]
    next_number = max(refresh_numbers, default=0) + 1
    refresh_version = f"{base_version}-{next_number}"

    if f"v{refresh_version}" in git_tags:
        raise ValueError(f"Computed refresh Git tag already exists: v{refresh_version}")
    return refresh_version


def write_output(
    base_version: str,
    refresh_version: str,
    github_output_path: pathlib.Path | None,
) -> None:
    """Write computed versions for a caller or GitHub Actions.

    Args:
        base_version: Stable release version.
        refresh_version: Computed immutable refresh version.
        github_output_path: GitHub output file, or None to write to stdout.

    Raises:
        ValueError: If either version is empty or the output path is invalid.
        OSError: If the GitHub output file cannot be written.
    """
    if not base_version or not refresh_version:
        raise ValueError("base_version and refresh_version must be non-empty")

    output = (
        f"base_version={base_version}\n"
        f"refresh_version={refresh_version}\n"
    )
    if github_output_path is None:
        sys.stdout.write(output)
        return
    if not isinstance(github_output_path, pathlib.Path):
        raise ValueError("github_output_path must be a pathlib.Path or None")
    if not github_output_path.parent.is_dir():
        raise ValueError(
            f"GitHub output parent directory does not exist: "
            f"{github_output_path.parent}",
        )

    try:
        with github_output_path.open("a", encoding="utf-8") as output_file:
            output_file.write(output)
    except OSError as exc:
        LOGGER.error("Unable to write GitHub output %s: %s", github_output_path, exc)
        raise


def main() -> int:
    """Run the refresh-version computation command.

    Returns:
        Process exit status: zero on success, one on validation or Git failure.
    """
    args = parse_args()
    try:
        base_version = load_base_version(args.history)
        git_tags = list_git_tags(args.repository)
        refresh_version = compute_next_refresh_version(base_version, git_tags)
        write_output(base_version, refresh_version, args.github_output)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        LOGGER.error("Cannot compute image refresh version: %s", exc)
        return 1

    LOGGER.info(
        "Next image refresh is %s, based on release %s",
        refresh_version,
        base_version,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
