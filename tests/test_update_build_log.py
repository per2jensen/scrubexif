# SPDX-License-Identifier: GPL-3.0-or-later
"""
Unit tests for scripts/update_build_log.py.

Covers:
  - core fields always written
  - grype_scan embedded when SARIF file present
  - sbom block written when --sbom-* flags provided
  - cosign block written only when --cosign-signed true
  - build provenance block written when CI env vars provided
  - combinations: all fields together / none of the optional fields
  - idempotency: appending to an existing history list
  - error handling: invalid JSON, non-list JSON, missing required arg

Design notes
------------
* Uses ``runpy.run_path`` so the script is executed in-process without
  needing it on $PATH or installed as a package.
* ``grype_sarif_summary.summarize`` is always patched — we test the
  script's logic, not the summariser itself.
* No Docker, no network, no git — safe in every CI environment.
* The script path is resolved relative to this file so the tests work
  regardless of the working directory pytest is invoked from.
"""

from __future__ import annotations

import json
import runpy
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Script path — resolved once at import time
# ---------------------------------------------------------------------------

# Layout: tests/test_update_build_log.py  →  scripts/update_build_log.py
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update_build_log.py"

if not SCRIPT.exists():
    pytest.skip(
        f"scripts/update_build_log.py not found at {SCRIPT}",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# grype_sarif_summary stub
# ---------------------------------------------------------------------------
# runpy.run_path executes the script in a fresh namespace, so
# ``from grype_sarif_summary import summarize`` triggers a real import.
# unittest.mock.patch only works on an already-imported module, so it
# cannot intercept the import itself.
#
# The fix: inject a fake module into sys.modules *before* runpy runs the
# script.  The script then finds our stub instead of the real package.
# We restore sys.modules to its original state afterwards.

@contextmanager
def _stub_grype(return_value: Any):
    """
    Temporarily install a fake ``grype_sarif_summary`` module whose
    ``summarize`` function returns *return_value*.

    Works regardless of whether the real package is installed.
    """
    stub = types.ModuleType("grype_sarif_summary")
    stub.summarize = lambda _path: return_value  # type: ignore[attr-defined]

    previous = sys.modules.get("grype_sarif_summary")
    sys.modules["grype_sarif_summary"] = stub
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("grype_sarif_summary", None)
        else:
            sys.modules["grype_sarif_summary"] = previous


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_GRYPE_SUMMARY = {
    "file": "grype-results-1.2.3.sarif",
    "total": 5,
    "counts": {
        "critical": 0, "high": 0, "medium": 1,
        "low": 2, "negligible": 2,
        "warning": 0, "note": 0, "info": 0, "unknown": 0,
    },
}


def _run(argv: list[str], tmp_path: Path) -> Dict[str, Any]:
    """
    Execute update_build_log.main() with *argv* and return the last entry
    written to the log file.

    The grype stub returns None by default so grype_scan is never written
    unless the test explicitly uses _run_with_grype().
    """
    log = tmp_path / "build-history.json"
    full_argv = ["update_build_log.py", "--log", str(log)] + argv

    with _stub_grype(return_value=None), \
         patch.object(sys, "argv", full_argv):
        runpy.run_path(str(SCRIPT), run_name="__main__")

    history = json.loads(log.read_text())
    assert isinstance(history, list) and history, "Log must be a non-empty list"
    return history[-1]


def _run_with_grype(argv: list[str], tmp_path: Path,
                    grype_return: Any) -> Dict[str, Any]:
    """Like _run but with a custom grype summarise return value."""
    log = tmp_path / "build-history.json"
    full_argv = ["update_build_log.py", "--log", str(log)] + argv

    with _stub_grype(return_value=grype_return), \
         patch.object(sys, "argv", full_argv):
        runpy.run_path(str(SCRIPT), run_name="__main__")

    history = json.loads(log.read_text())
    return history[-1]


def _base_argv(version: str = "1.2.3", build_number: int = 0) -> list[str]:
    """Minimal set of required arguments."""
    return [
        "--build-number", str(build_number),
        "--version", version,
        "--base", f"scrubexif-base:24.04-{version}",
        "--git-rev", "abc1234",
        "--created", "2026-04-01T10:00:00Z",
        "--url", f"https://hub.docker.com/r/per2jensen/scrubexif/tags/{version}",
        "--digest", "sha256:deadbeef",
        "--image-id", "sha256:cafebabe",
    ]


# ---------------------------------------------------------------------------
# Core fields
# ---------------------------------------------------------------------------

class TestCoreFields:
    def test_all_required_fields_present(self, tmp_path):
        entry = _run(_base_argv(), tmp_path)

        assert entry["build_number"] == 0
        assert entry["tag"] == "1.2.3"
        assert entry["base_image"] == "scrubexif-base:24.04-1.2.3"
        assert entry["git_revision"] == "abc1234"
        assert entry["created"] == "2026-04-01T10:00:00Z"
        assert entry["dockerhub_tag_url"] == (
            "https://hub.docker.com/r/per2jensen/scrubexif/tags/1.2.3"
        )
        assert entry["digest"] == "sha256:deadbeef"
        assert entry["image_id"] == "sha256:cafebabe"

    def test_optional_blocks_absent_by_default(self, tmp_path):
        entry = _run(_base_argv(), tmp_path)

        assert "grype_scan" not in entry
        assert "sbom" not in entry
        assert "cosign" not in entry
        assert "build" not in entry

    def test_appends_to_existing_history(self, tmp_path):
        """Calling the script twice must append, not overwrite."""
        _run(_base_argv(version="1.0.0", build_number=0), tmp_path)
        _run(_base_argv(version="1.0.1", build_number=1), tmp_path)

        log = tmp_path / "build-history.json"
        history = json.loads(log.read_text())
        assert len(history) == 2
        assert history[0]["tag"] == "1.0.0"
        assert history[1]["tag"] == "1.0.1"

    def test_creates_missing_log_file(self, tmp_path):
        """No pre-existing log file → script creates it as a single-entry list."""
        log = tmp_path / "brand-new.json"
        assert not log.exists()
        argv = ["update_build_log.py", "--log", str(log)] + _base_argv()
        with _stub_grype(return_value=None), \
             patch.object(sys, "argv", argv):
            runpy.run_path(str(SCRIPT), run_name="__main__")
        history = json.loads(log.read_text())
        assert len(history) == 1

    def test_creates_parent_directory(self, tmp_path):
        """Log path whose parent does not yet exist should be created."""
        log = tmp_path / "nested" / "deep" / "build-history.json"
        argv = ["update_build_log.py", "--log", str(log)] + _base_argv()
        with _stub_grype(return_value=None), \
             patch.object(sys, "argv", argv):
            runpy.run_path(str(SCRIPT), run_name="__main__")
        history = json.loads(log.read_text())
        assert len(history) == 1
        assert history[0]["tag"] == "1.2.3"


# ---------------------------------------------------------------------------
# Grype scan
# ---------------------------------------------------------------------------

class TestGrypeScan:
    def test_grype_scan_embedded_when_sarif_present(self, tmp_path):
        sarif = tmp_path / "grype-results-1.2.3.sarif"
        sarif.write_text("{}")  # content irrelevant; summariser is patched

        entry = _run_with_grype(
            _base_argv() + ["--grype-sarif", str(sarif)],
            tmp_path,
            FAKE_GRYPE_SUMMARY,
        )
        assert entry["grype_scan"] == FAKE_GRYPE_SUMMARY

    def test_grype_scan_absent_when_sarif_path_empty(self, tmp_path):
        """Passing an empty string for --grype-sarif must not add grype_scan."""
        entry = _run(_base_argv() + ["--grype-sarif", ""], tmp_path)
        assert "grype_scan" not in entry

    def test_grype_scan_absent_when_summariser_returns_none(self, tmp_path):
        sarif = tmp_path / "grype-results-1.2.3.sarif"
        sarif.write_text("{}")

        entry = _run_with_grype(
            _base_argv() + ["--grype-sarif", str(sarif)],
            tmp_path,
            None,  # summariser returns None → no block
        )
        assert "grype_scan" not in entry


# ---------------------------------------------------------------------------
# SBOM
# ---------------------------------------------------------------------------

class TestSbom:
    def test_sbom_file_only(self, tmp_path):
        entry = _run(
            _base_argv() + ["--sbom-file", "sbom-1.2.3.spdx.json"],
            tmp_path,
        )
        assert entry["sbom"] == {"file": "sbom-1.2.3.spdx.json"}

    def test_sbom_url_only(self, tmp_path):
        url = (
            "https://github.com/per2jensen/scrubexif/releases/"
            "download/v1.2.3/sbom-1.2.3.spdx.json"
        )
        entry = _run(
            _base_argv() + ["--sbom-release-asset-url", url],
            tmp_path,
        )
        assert entry["sbom"] == {"release_asset_url": url}

    def test_sbom_file_and_url(self, tmp_path):
        url = (
            "https://github.com/per2jensen/scrubexif/releases/"
            "download/v1.2.3/sbom-1.2.3.spdx.json"
        )
        entry = _run(
            _base_argv() + [
                "--sbom-file", "sbom-1.2.3.spdx.json",
                "--sbom-release-asset-url", url,
            ],
            tmp_path,
        )
        assert entry["sbom"]["file"] == "sbom-1.2.3.spdx.json"
        assert entry["sbom"]["release_asset_url"] == url

    def test_sbom_absent_when_no_flags(self, tmp_path):
        entry = _run(_base_argv(), tmp_path)
        assert "sbom" not in entry


# ---------------------------------------------------------------------------
# Cosign
# ---------------------------------------------------------------------------

class TestCosign:
    def test_cosign_block_present_when_signed_true(self, tmp_path):
        rekor = "https://search.sigstore.dev/?logIndex=12345678"
        digest = "per2jensen/scrubexif@sha256:deadbeef"
        entry = _run(
            _base_argv() + [
                "--cosign-signed", "true",
                "--cosign-rekor-url", rekor,
                "--cosign-image-digest", digest,
            ],
            tmp_path,
        )
        assert entry["cosign"]["signed"] is True
        assert entry["cosign"]["rekor_log_entry"] == rekor
        assert entry["cosign"]["image_digest"] == digest

    @pytest.mark.parametrize("flag_value", ["false", "False", "FALSE", "0", "no", ""])
    def test_cosign_block_absent_when_not_signed(self, tmp_path, flag_value):
        entry = _run(
            _base_argv() + ["--cosign-signed", flag_value],
            tmp_path,
        )
        assert "cosign" not in entry, (
            f"cosign block should be absent for --cosign-signed {flag_value!r}"
        )

    def test_cosign_block_signed_only_no_optional_fields(self, tmp_path):
        """signed=true but no rekor/digest → block has only the signed key."""
        entry = _run(
            _base_argv() + ["--cosign-signed", "true"],
            tmp_path,
        )
        assert entry["cosign"] == {"signed": True}

    @pytest.mark.parametrize("flag_value", ["yes", "1", "Yes", "YES"])
    def test_cosign_signed_accepts_truthy_variants(self, tmp_path, flag_value):
        entry = _run(
            _base_argv() + ["--cosign-signed", flag_value],
            tmp_path,
        )
        assert entry["cosign"]["signed"] is True, (
            f"Expected signed=True for --cosign-signed {flag_value!r}"
        )


# ---------------------------------------------------------------------------
# Build provenance
# ---------------------------------------------------------------------------

class TestBuildProvenance:
    def test_build_block_with_all_fields(self, tmp_path):
        run_url = "https://github.com/per2jensen/scrubexif/actions/runs/9876543210"
        entry = _run(
            _base_argv() + [
                "--build-runner", "Linux-X64",
                "--github-run-id", "9876543210",
                "--github-run-url", run_url,
            ],
            tmp_path,
        )
        assert entry["build"]["runner"] == "Linux-X64"
        assert entry["build"]["github_run_id"] == "9876543210"
        assert entry["build"]["github_run_url"] == run_url

    def test_build_block_absent_when_no_flags(self, tmp_path):
        entry = _run(_base_argv(), tmp_path)
        assert "build" not in entry

    def test_build_block_partial_fields(self, tmp_path):
        """Only run-id provided → block has just that one key."""
        entry = _run(
            _base_argv() + ["--github-run-id", "42"],
            tmp_path,
        )
        assert entry["build"] == {"github_run_id": "42"}


# ---------------------------------------------------------------------------
# All optional fields together
# ---------------------------------------------------------------------------

class TestFullEntry:
    def test_all_optional_fields_in_one_entry(self, tmp_path):
        sarif = tmp_path / "grype-results-1.2.3.sarif"
        sarif.write_text("{}")
        rekor = "https://search.sigstore.dev/?logIndex=99999"
        sbom_url = (
            "https://github.com/per2jensen/scrubexif/releases/"
            "download/v1.2.3/sbom-1.2.3.spdx.json"
        )

        entry = _run_with_grype(
            _base_argv() + [
                "--grype-sarif", str(sarif),
                "--sbom-file", "sbom-1.2.3.spdx.json",
                "--sbom-release-asset-url", sbom_url,
                "--cosign-signed", "true",
                "--cosign-rekor-url", rekor,
                "--cosign-image-digest", "per2jensen/scrubexif@sha256:deadbeef",
                "--build-runner", "Linux-X64",
                "--github-run-id", "9876543210",
                "--github-run-url",
                "https://github.com/per2jensen/scrubexif/actions/runs/9876543210",
            ],
            tmp_path,
            FAKE_GRYPE_SUMMARY,
        )

        assert "grype_scan" in entry
        assert "sbom" in entry
        assert "cosign" in entry
        assert "build" in entry

    def test_json_is_valid_and_ends_with_newline(self, tmp_path):
        """The written file must be valid JSON and end with a newline."""
        _run(_base_argv(), tmp_path)
        raw = (tmp_path / "build-history.json").read_text()
        json.loads(raw)          # raises if invalid
        assert raw.endswith("\n")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_invalid_json_raises_systemexit(self, tmp_path):
        log = tmp_path / "build-history.json"
        log.write_text("this is not json")
        argv = ["update_build_log.py", "--log", str(log)] + _base_argv()
        with _stub_grype(return_value=None), \
             patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit):
                runpy.run_path(str(SCRIPT), run_name="__main__")

    def test_non_list_json_raises_systemexit(self, tmp_path):
        log = tmp_path / "build-history.json"
        log.write_text('{"key": "value"}')
        argv = ["update_build_log.py", "--log", str(log)] + _base_argv()
        with _stub_grype(return_value=None), \
             patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit):
                runpy.run_path(str(SCRIPT), run_name="__main__")

    def test_missing_required_arg_raises_systemexit(self, tmp_path):
        """Omitting --version (required) must cause argparse to exit non-zero."""
        log = tmp_path / "build-history.json"
        argv = [
            "update_build_log.py", "--log", str(log),
            "--build-number", "0",
            "--base", "scrubexif-base:24.04-1.0.0",
            "--git-rev", "abc",
            "--created", "2026-01-01T00:00:00Z",
            "--url", "https://example.com",
            "--digest", "sha256:aa",
            "--image-id", "sha256:bb",
            # --version intentionally omitted
        ]
        with _stub_grype(return_value=None), \
             patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit):
                runpy.run_path(str(SCRIPT), run_name="__main__")
