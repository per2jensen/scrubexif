#!/usr/bin/env python3
"""Inspect Docker Hub tags through the authenticated Registry API."""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
from collections.abc import Mapping, Sequence

if __package__:
    from .remove_dockerhub_tag import (
        DIGEST_PATTERN,
        _validate_repository,
        _validate_tag,
        get_manifest_digest,
        get_registry_token,
    )
else:
    from remove_dockerhub_tag import (
        DIGEST_PATTERN,
        _validate_repository,
        _validate_tag,
        get_manifest_digest,
        get_registry_token,
    )

LOGGER = logging.getLogger(__name__)


class ExistingTagError(RuntimeError):
    """Raised when publication would overwrite an existing Docker Hub tag.

    Args:
        message: Human-readable overwrite error.
    """


def load_credentials(environment: Mapping[str, str]) -> tuple[str, str]:
    """Load Docker Hub credentials from an environment mapping.

    Args:
        environment: Environment variable mapping.

    Returns:
        Docker Hub username and token.

    Raises:
        ValueError: If the mapping or either credential is invalid.
    """
    if not isinstance(environment, Mapping):
        raise ValueError("environment must be a mapping")

    user = environment.get("DOCKERHUB_USER", "")
    token = environment.get("DOCKERHUB_TOKEN", "")
    if not isinstance(user, str) or not user:
        raise ValueError("DOCKERHUB_USER must be a non-empty string")
    if not isinstance(token, str) or not token:
        raise ValueError("DOCKERHUB_TOKEN must be a non-empty string")
    return user, token


def require_tag_absent(repo: str, tag: str, digest: str | None) -> None:
    """Require a registry lookup to confirm that a tag is absent.

    Args:
        repo: Docker Hub repository in ``namespace/name`` form.
        tag: Docker tag checked for immutability.
        digest: Registry digest, or None only for a confirmed HTTP 404.

    Returns:
        None.

    Raises:
        ValueError: If repository or tag context is invalid.
        ExistingTagError: If the tag already resolves to a digest.
    """
    _validate_repository(repo)
    _validate_tag(tag)
    if digest is None:
        return
    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("digest must be None or a sha256 manifest digest")
    raise ExistingTagError(
        f"Docker Hub tag already exists: {repo}:{tag} ({digest})",
    )


def format_image_digest(repo: str, digest: str) -> str:
    """Format and validate an immutable repository digest.

    Args:
        repo: Docker Hub repository in ``namespace/name`` form.
        digest: Registry manifest digest.

    Returns:
        Immutable ``repository@sha256:...`` reference.

    Raises:
        ValueError: If repository or digest is invalid.
    """
    _validate_repository(repo)
    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("digest must be a sha256 manifest digest")
    return f"{repo}@{digest}"


def write_github_output(output_path: pathlib.Path, image_digest: str) -> None:
    """Append an immutable digest to a GitHub Actions output file.

    Args:
        output_path: GitHub Actions output file.
        image_digest: Validated immutable image reference.

    Returns:
        None.

    Raises:
        ValueError: If an argument is invalid.
        OSError: If the output file cannot be written.
    """
    if not isinstance(output_path, pathlib.Path):
        raise ValueError("output_path must be a pathlib.Path")
    if not isinstance(image_digest, str) or image_digest.count("@") != 1:
        raise ValueError("image_digest must be an immutable image reference")
    repo, digest = image_digest.split("@", maxsplit=1)
    _validate_repository(repo)
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("image_digest must be an immutable image reference")

    try:
        with output_path.open("a", encoding="utf-8") as output_file:
            output_file.write(f"digest={image_digest}\n")
    except OSError:
        LOGGER.error("Unable to write GitHub Actions output: %s", output_path)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional command-line arguments excluding the executable name.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Inspect Docker Hub tags without treating registry errors as absence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    absent_parser = subparsers.add_parser(
        "assert-absent",
        help="Fail if a Docker Hub tag already exists.",
    )
    absent_parser.add_argument("--repo", required=True)
    absent_parser.add_argument("--tag", required=True)

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Resolve a Docker Hub tag to its immutable digest.",
    )
    resolve_parser.add_argument("--repo", required=True)
    resolve_parser.add_argument("--tag", required=True)
    resolve_parser.add_argument(
        "--github-output",
        type=pathlib.Path,
        required=True,
    )

    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Run an authenticated Docker Hub manifest inspection.

    Args:
        argv: Optional command-line arguments excluding the executable name.
        environment: Optional environment mapping for tests or embedding.

    Returns:
        Zero when the requested registry condition is satisfied, otherwise one.

    Raises:
        OSError: If a requested output file cannot be written.
        SystemExit: If Docker Hub authentication or lookup fails.
    """
    args = parse_args(argv)
    active_environment = os.environ if environment is None else environment

    try:
        user, token = load_credentials(active_environment)
        registry_token = get_registry_token(user, token, args.repo)
        digest = get_manifest_digest(args.repo, args.tag, registry_token)

        if args.command == "assert-absent":
            require_tag_absent(args.repo, args.tag, digest)
            LOGGER.info("Docker Hub tag is available: %s:%s", args.repo, args.tag)
            return 0

        if digest is None:
            LOGGER.error("Docker Hub tag does not exist: %s:%s", args.repo, args.tag)
            return 1

        image_digest = format_image_digest(args.repo, digest)
        write_github_output(args.github_output, image_digest)
        LOGGER.info("Resolved Docker Hub digest: %s", image_digest)
        return 0
    except (ExistingTagError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
