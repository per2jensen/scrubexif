#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Remove a tag from Docker Hub via the Registry API.

Used as a rollback step in the release workflow when cosign signing or
SBOM attestation fails after the image has already been pushed.

Credentials are read from environment variables to avoid exposing them
in process listings or log files:

    DOCKERHUB_USER   Docker Hub username
    DOCKERHUB_TOKEN  Docker Hub password or access token

Usage:
    DOCKERHUB_USER=myuser DOCKERHUB_TOKEN=mytoken \\
        python3 scripts/remove_dockerhub_tag.py \\
            --repo per2jensen/scrubexif \\
            --tag 1.2.3
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DOCKER_REGISTRY_AUTH_URL = "https://auth.docker.io/token"
DOCKER_REGISTRY_SERVICE = "registry.docker.io"
DOCKER_REGISTRY_MANIFEST_URL_TEMPLATE = (
    "https://registry-1.docker.io/v2/{repo}/manifests/{reference}"
)
MANIFEST_ACCEPT_HEADER = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
REPOSITORY_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*$"
)
TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        Parsed namespace with repo and tag.
    """
    parser = argparse.ArgumentParser(
        description="Remove a Docker Hub tag (cosign failure rollback)."
    )
    parser.add_argument("--repo", required=True, help="Repository, e.g. per2jensen/scrubexif")
    parser.add_argument("--tag", required=True, help="Tag to remove, e.g. 1.2.3")
    return parser.parse_args()


def _validate_repository(repo: str) -> None:
    """Validate a Docker Hub repository name.

    Args:
        repo: Repository in ``namespace/name`` form.

    Returns:
        None.

    Raises:
        ValueError: If the repository name is empty or malformed.
    """
    if not isinstance(repo, str) or not REPOSITORY_PATTERN.fullmatch(repo):
        raise ValueError(
            "repo must be a lowercase Docker Hub repository in namespace/name form"
        )


def _validate_tag(tag: str) -> None:
    """Validate a Docker image tag.

    Args:
        tag: Docker tag to validate.

    Returns:
        None.

    Raises:
        ValueError: If the tag is empty or malformed.
    """
    if not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag):
        raise ValueError("tag must be a valid non-empty Docker tag")


def _validate_nonempty_secret(name: str, value: str) -> None:
    """Validate a non-empty credential value.

    Args:
        name: Credential name used in an error message.
        value: Credential value to validate.

    Returns:
        None.

    Raises:
        ValueError: If the credential name or value is empty.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("credential name must be a non-empty string")
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _manifest_url(repo: str, reference: str) -> str:
    """Build a Docker Registry manifest URL.

    Args:
        repo: Validated Docker Hub repository name.
        reference: Validated image tag or manifest digest.

    Returns:
        Registry manifest URL.

    Raises:
        ValueError: If repo or reference is empty.
    """
    if not isinstance(repo, str) or not repo:
        raise ValueError("repo must be a non-empty string")
    if not isinstance(reference, str) or not reference:
        raise ValueError("reference must be a non-empty string")

    encoded_repo = urllib.parse.quote(repo, safe="/")
    encoded_reference = urllib.parse.quote(reference, safe=":")
    return DOCKER_REGISTRY_MANIFEST_URL_TEMPLATE.format(
        repo=encoded_repo,
        reference=encoded_reference,
    )


def get_registry_token(user: str, token: str, repo: str) -> str:
    """
    Obtain a repository-scoped Docker Registry bearer token.

    Args:
        user: Docker Hub username.
        token: Docker Hub personal access token.
        repo: Docker Hub repository in ``namespace/name`` form.

    Returns:
        Registry bearer token with pull, push, and delete scope.

    Raises:
        ValueError: If an argument is invalid.
        SystemExit: If authentication or response parsing fails.
    """
    _validate_nonempty_secret("user", user)
    _validate_nonempty_secret("token", token)
    _validate_repository(repo)

    query = urllib.parse.urlencode(
        {
            "service": DOCKER_REGISTRY_SERVICE,
            "scope": f"repository:{repo}:pull,push,delete",
        }
    )
    credentials = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"{DOCKER_REGISTRY_AUTH_URL}?{query}",
        headers={"Authorization": f"Basic {credentials}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body: Any = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logger.error("Docker Registry authentication failed: HTTP %s", exc.code)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        logger.error("Docker Registry authentication failed: %s", exc.reason)
        raise SystemExit(1) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("Docker Registry authentication returned invalid JSON: %s", exc)
        raise SystemExit(1) from exc

    if not isinstance(body, dict):
        logger.error("Docker Registry authentication returned a non-object response")
        raise SystemExit(1)
    registry_token = body.get("token") or body.get("access_token")
    if not isinstance(registry_token, str) or not registry_token:
        logger.error("Docker Registry authentication returned no token")
        raise SystemExit(1)
    return registry_token


def get_manifest_digest(repo: str, tag: str, registry_token: str) -> str | None:
    """Resolve a Docker tag to its registry manifest digest.

    Args:
        repo: Docker Hub repository in ``namespace/name`` form.
        tag: Image tag to resolve.
        registry_token: Repository-scoped Registry bearer token.

    Returns:
        Manifest digest, or None if the tag is already absent.

    Raises:
        ValueError: If an argument is invalid.
        SystemExit: If lookup fails or returns an invalid digest.
    """
    _validate_repository(repo)
    _validate_tag(tag)
    _validate_nonempty_secret("registry_token", registry_token)

    req = urllib.request.Request(
        _manifest_url(repo, tag),
        headers={
            "Authorization": f"Bearer {registry_token}",
            "Accept": MANIFEST_ACCEPT_HEADER,
        },
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            digest = resp.headers.get("Docker-Content-Digest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.info("✅ Tag %s is already absent from %s (HTTP 404)", tag, repo)
            return None
        logger.error("❌ Failed to resolve tag %s from %s: HTTP %s", tag, repo, exc.code)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        logger.error("❌ Failed to resolve tag %s from %s: %s", tag, repo, exc.reason)
        raise SystemExit(1) from exc

    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        logger.error(
            "❌ Registry returned an invalid manifest digest for %s:%s: %r",
            repo,
            tag,
            digest,
        )
        raise SystemExit(1)
    return digest


def delete_manifest(repo: str, digest: str, registry_token: str) -> None:
    """Delete a Docker Registry manifest by digest.

    Args:
        repo: Docker Hub repository in ``namespace/name`` form.
        digest: Manifest digest in ``sha256:<hex>`` form.
        registry_token: Repository-scoped Registry bearer token.

    Returns:
        None.

    Raises:
        ValueError: If an argument is invalid.
        SystemExit: If deletion fails for a reason other than absence.
    """
    _validate_repository(repo)
    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("digest must be a sha256 manifest digest")
    _validate_nonempty_secret("registry_token", registry_token)

    req = urllib.request.Request(
        _manifest_url(repo, digest),
        headers={"Authorization": f"Bearer {registry_token}"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if not 200 <= resp.status < 300:
                logger.error(
                    "❌ Registry returned unexpected HTTP %s deleting %s@%s",
                    resp.status,
                    repo,
                    digest,
                )
                raise SystemExit(1)
            logger.info(
                "✅ Removed manifest %s from %s (HTTP %s)",
                digest,
                repo,
                resp.status,
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.info(
                "✅ Manifest %s is already absent from %s (HTTP 404)",
                digest,
                repo,
            )
            return
        logger.error(
            "❌ Failed to remove manifest %s from %s: HTTP %s",
            digest,
            repo,
            exc.code,
        )
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        logger.error(
            "❌ Failed to remove manifest %s from %s: %s",
            digest,
            repo,
            exc.reason,
        )
        raise SystemExit(1) from exc


def remove_tag(repo: str, tag: str, registry_token: str) -> None:
    """
    Delete a Docker Hub tag through its Registry manifest digest.

    Args:
        repo: Repository name, e.g. per2jensen/scrubexif.
        tag: Tag name to remove.
        registry_token: Token obtained from get_registry_token().

    Returns:
        None.

    Raises:
        ValueError: If an argument is invalid.
        SystemExit: If the deletion fails for a reason other than an absent tag.
    """
    digest = get_manifest_digest(repo, tag, registry_token)
    if digest is None:
        return
    delete_manifest(repo, digest, registry_token)


def main() -> None:
    """
    Entry point: read credentials from environment, then remove the tag.

    Raises:
        SystemExit: If credentials are missing or any API call fails.
    """
    args = parse_args()

    user = os.environ.get("DOCKERHUB_USER")
    token = os.environ.get("DOCKERHUB_TOKEN")
    if not user or not token:
        logger.error("DOCKERHUB_USER and DOCKERHUB_TOKEN must be set in the environment")
        raise SystemExit(1)

    logger.info(
        "⚠️  Rollback: removing %s:%s from Docker Hub (cosign failure)",
        args.repo,
        args.tag,
    )
    try:
        registry_token = get_registry_token(user, token, args.repo)
        remove_tag(args.repo, args.tag, registry_token)
    except ValueError as exc:
        logger.error("Invalid Docker Hub rollback input: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
