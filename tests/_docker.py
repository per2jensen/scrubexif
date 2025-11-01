# tests/_docker.py
# Minimal helper for consistent docker flags in tests.

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional


# Defaults for test runs
DEFAULT_IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")
DEFAULT_ENVS = {
    "SCRUBEXIF_STABLE_SECONDS": os.getenv("SCRUBEXIF_STABLE_SECONDS", "0"),
    "SCRUBEXIF_STATE": os.getenv("SCRUBEXIF_STATE", "/tmp/.scrubexif_state.test.json"),
}
DEFAULT_TMPFS = "/tmp:rw,exec,nosuid,size=64m"


def _user_flag() -> List[str]:
    uid = os.getuid()
    return ["--user", str(uid)] if uid != 0 else []


def docker_flags(
    read_only: bool = True,
    no_new_privileges: bool = True,
    tmpfs: str = DEFAULT_TMPFS,
    envs: Optional[dict] = None,
) -> List[str]:
    """
    Compose common docker flags:
      - read-only root
      - no-new-privileges
      - tmpfs for /tmp
      - exported envs for scrubexif tests
    """
    flags: List[str] = ["docker", "run", "--rm"]
    if read_only:
        flags += ["--read-only"]
    if no_new_privileges:
        flags += ["--security-opt", "no-new-privileges"]
    if tmpfs:
        # Important: no leading space in the mount path.
        flags += ["--tmpfs", tmpfs]

    combined_envs = {**DEFAULT_ENVS, **(envs or {})}
    for k, v in combined_envs.items():
        flags += ["-e", f"{k}={v}"]

    flags += _user_flag()
    return flags


def mk_mounts(
    input_dir: Path,
    output_dir: Path,
    processed_dir: Path,
    errors_dir: Optional[Path] = None,
) -> List[str]:
    """
    Return -v bind mounts for the standard /photos/* paths.
    Directories must exist before calling.
    """
    mounts = [
        "-v", f"{str(input_dir)}:/photos/input",
        "-v", f"{str(output_dir)}:/photos/output",
        "-v", f"{str(processed_dir)}:/photos/processed",
    ]
    if errors_dir is not None:
        mounts += ["-v", f"{str(errors_dir)}:/photos/errors"]
    return mounts


def run_container(
    image: str = DEFAULT_IMAGE,
    mounts: Optional[Iterable[str]] = None,
    args: Optional[Iterable[str]] = None,
    entrypoint: Optional[str] = None,
    envs: Optional[dict] = None,
    capture_output: bool = True,
    check: bool = False,
    extra_flags: Optional[Iterable[str]] = None,
) -> subprocess.CompletedProcess:
    """
    Execute docker run with consistent flags.
    Returns CompletedProcess. If check=True, raises on non-zero rc.
    """
    cmd: List[str] = docker_flags(envs=envs)
    if extra_flags:
        cmd += list(extra_flags)
    if entrypoint:
        cmd += ["--entrypoint", entrypoint]
    if mounts:
        cmd += list(mounts)
    cmd.append(image)
    if args:
        cmd += list(args)

    cp = subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
    )

    # Always echo logs on failure to aid debugging
    if cp.returncode != 0:
        print("=== docker cmd ===")
        print(" ".join(shlex.quote(x) for x in cmd))
        print("=== stdout ===")
        print(cp.stdout)
        print("=== stderr ===")
        print(cp.stderr)

    if check:
        cp.check_returncode()
    return cp


def assert_test_env_visible(image: str = DEFAULT_IMAGE) -> None:
    """
    Sanity check: container sees the expected envs.
    Raises AssertionError if not.
    """
    cp = run_container(
        image=image,
        entrypoint="env",
        args=[],
        capture_output=True,
    )
    stdout = cp.stdout or ""
    for k, v in DEFAULT_ENVS.items():
        needle = f"{k}={v}"
        assert needle in stdout, f"Missing env in container: {needle}\n{stdout}"
