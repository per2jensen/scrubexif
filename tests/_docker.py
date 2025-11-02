# tests/_docker.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Shared Docker helpers for scrubexif tests.

- mk_mounts: build standard -v mounts
- run_container: run the container with stable defaults and envs
- ensure_image: builds SCRUBEXIF_IMAGE if missing, with streamed logs + timeout
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

DEFAULT_IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")
BUILD_TIMEOUT = int(os.getenv("SCRUBEXIF_BUILD_TIMEOUT", "900"))  # 15 min
AUTOBUILD = os.getenv("SCRUBEXIF_AUTOBUILD", "1")
REPO_ROOT = Path(__file__).resolve().parents[1]

def _cmd_ok(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except subprocess.CalledProcessError:
        return False

def image_exists(image: str) -> bool:
    return _cmd_ok(["docker", "image", "inspect", image])

def build_dev_image(image: str) -> None:
    print(f"🛠️  Building image '{image}'… (timeout {BUILD_TIMEOUT}s)")
    cmd = [
        "docker", "build",
        "-f", "Dockerfile",
        "--build-arg", "VERSION=dev",
        "--progress=plain",
        "-t", image,
        str(REPO_ROOT),
    ]
    print("=== docker build ===")
    print(" ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, timeout=BUILD_TIMEOUT)

def ensure_image(image: str = DEFAULT_IMAGE) -> None:
    if image_exists(image):
        return
    if AUTOBUILD.lower() in {"1", "true", "yes"} and image == "scrubexif:dev":
        print("🔧 dev image missing → building scrubexif:dev")
        build_dev_image(image)
        return
    raise RuntimeError(
        f"Image '{image}' not found locally. Build it (`make dev`) or set SCRUBEXIF_IMAGE to a local tag. "
        f"To enable auto-build in tests, set SCRUBEXIF_AUTOBUILD=1."
    )

def mk_mounts(input_dir: Path, output_dir: Path, processed_dir: Path) -> list[str]:
    return [
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
    ]

def _base_flags() -> list[str]:
    return [
        "--rm",
        "--read-only",
        "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=64m",
        "--user", str(os.getuid()),
    ]

# tests/_docker.py  (only the run_container function shown changed)

def run_container(
    mounts: Iterable[str] | None = None,
    args: Iterable[str] | None = None,
    image: Optional[str] = None,
    envs: Optional[Mapping[str, str]] = None,
    capture_output: bool = True,
    entrypoint: Optional[str] = None,
):
    img = image or DEFAULT_IMAGE
    ensure_image(img)

    # Defaults for fast tests; individual tests can override via `envs`
    effective_envs: dict[str, str] = {}
    if envs:
        effective_envs.update(envs)
    effective_envs.setdefault("SCRUBEXIF_STABLE_SECONDS", "0")
    # Give the container a writable state file by default; tests can override or disable
    effective_envs.setdefault("SCRUBEXIF_STATE", "/tmp/.scrubexif_state.test.json")

    cmd: List[str] = ["docker", "run"] + _base_flags()

    # envs
    for k, v in effective_envs.items():
        cmd += ["-e", f"{k}={v}"]

    # mounts
    if mounts:
        cmd += list(mounts)

    # entrypoint
    if entrypoint:
        cmd += ["--entrypoint", entrypoint]

    # image + args
    cmd += [img]
    if args:
        cmd += list(args)

    print("=== docker cmd ===")
    print(" ".join(shlex.quote(c) for c in cmd))

    return subprocess.run(cmd, text=True, capture_output=capture_output, check=False)

