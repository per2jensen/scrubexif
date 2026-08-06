# Basic container sanity tests

import hashlib
from pathlib import Path

import pytest

from tests._docker import run_container
from scrubexif.__about__ import __license__, __version__


EXPECTED_LICENSE_SHA256 = (
    "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
)
REPOSITORY_LICENSE = Path(__file__).resolve().parents[1] / "LICENSE"


def _calculate_sha256(path: Path) -> str:
    """Calculate the SHA-256 digest of a regular file.

    Args:
        path: File whose bytes will be hashed.

    Returns:
        Lowercase hexadecimal SHA-256 digest.

    Raises:
        ValueError: If path is not a pathlib.Path or a regular file.
        OSError: If the file cannot be read.
    """
    if not isinstance(path, Path):
        raise ValueError("path must be a pathlib.Path")
    if not path.is_file():
        raise ValueError(f"path must identify a regular file: {path}")

    with path.open("rb") as source_file:
        return hashlib.file_digest(source_file, "sha256").hexdigest()


def test_repository_license_matches_pinned_sha256() -> None:
    """The repository LICENSE matches the independently verified digest."""
    assert _calculate_sha256(REPOSITORY_LICENSE) == EXPECTED_LICENSE_SHA256


def test_modified_license_does_not_match_pinned_sha256(tmp_path: Path) -> None:
    """A modified LICENSE copy fails the pinned digest comparison."""
    modified_license = tmp_path / "LICENSE"
    modified_license.write_bytes(REPOSITORY_LICENSE.read_bytes() + b"\n")

    assert _calculate_sha256(modified_license) != EXPECTED_LICENSE_SHA256


def test_tmp_writable_in_container():
    cp = run_container(entrypoint="bash", args=["-c", "echo ok > /tmp/x && cat /tmp/x"])
    assert "ok" in cp.stdout.lower()


def test_exiftool_available():
    cp = run_container(entrypoint="exiftool", args=["-ver"])
    assert cp.returncode == 0


def test_jpegtran_available():
    cp = run_container(entrypoint="jpegtran", args=["-version"])
    assert cp.returncode == 0


def test_scrubexif_invokable():
    """The packaged CLI prints the exact version and license metadata."""
    cp = run_container(args=["--version"])
    assert cp.returncode == 0
    assert cp.stdout == f"scrubexif {__version__}\n{__license__}\n"
    assert cp.stderr == ""


def test_complete_license_is_packaged_in_distribution_metadata() -> None:
    """The final image declares GPL and contains the exact LICENSE file."""
    script = (
        "from importlib.metadata import distribution; "
        'package = distribution("scrubexif"); '
        'assert package.metadata["License-Expression"] == "GPL-3.0-or-later"; '
        'assert package.metadata.get_all("License-File") == ["LICENSE"]; '
        'license_text = package.read_text("licenses/LICENSE"); '
        'assert license_text is not None, "packaged LICENSE not found"; '
        'print(license_text, end="")'
    )

    cp = run_container(entrypoint="python3", args=["-c", script])

    expected_license = REPOSITORY_LICENSE.read_text(encoding="utf-8")
    assert cp.returncode == 0
    assert cp.stdout == expected_license
    assert hashlib.sha256(cp.stdout.encode("utf-8")).hexdigest() == (
        EXPECTED_LICENSE_SHA256
    )
    assert cp.stderr == ""


def test_complete_license_is_not_copied_beside_scrub_module() -> None:
    """The final image does not duplicate LICENSE in the import package."""
    script = (
        "from pathlib import Path; "
        "import scrubexif.scrub; "
        'license_path = Path(scrubexif.scrub.__file__).with_name("LICENSE"); '
        'assert not license_path.exists(), f"unexpected LICENSE: {license_path}"'
    )

    cp = run_container(entrypoint="python3", args=["-c", script])

    assert cp.returncode == 0
    assert cp.stdout == ""
    assert cp.stderr == ""


def test_stability_env_override_prints():
    cp = run_container(envs={"SCRUBEXIF_STABLE_SECONDS": "0"}, args=["--version"])
    joined = (cp.stdout + cp.stderr).lower()
    assert "scrubexif" in joined
