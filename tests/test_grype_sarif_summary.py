# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Grype severity summaries from raw and compressed SARIF."""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "grype_sarif_summary.py"


def _load_module() -> ModuleType:
    """Load the summary script as a Python module.

    Returns:
        Imported script module.

    Raises:
        RuntimeError: If Python cannot construct a module spec.
    """
    spec = importlib.util.spec_from_file_location("grype_sarif_summary_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sarif_document() -> dict[str, Any]:
    """Create SARIF whose display levels differ from Grype severities.

    Returns:
        Minimal representative Grype SARIF document.
    """
    return {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "rules": [
                            {
                                "id": "CVE-MEDIUM",
                                "shortDescription": {
                                    "text": "CVE-MEDIUM medium vulnerability",
                                },
                            },
                            {
                                "id": "CVE-LOW",
                                "help": {
                                    "text": "Vulnerability CVE-LOW\nSeverity: low\n",
                                },
                            },
                        ],
                    },
                },
                "results": [
                    {
                        "ruleId": "CVE-MEDIUM",
                        "level": "warning",
                        "message": {"text": "A medium vulnerability was found"},
                    },
                    {
                        "ruleId": "CVE-LOW",
                        "level": "note",
                        "message": {"text": "A low vulnerability was found"},
                    },
                ],
            },
        ],
    }


def _write_sarif(path: Path, compressed: bool) -> None:
    """Write a test SARIF document.

    Args:
        path: Destination file.
        compressed: Whether to gzip-compress the JSON.

    Returns:
        None.
    """
    document = _sarif_document()
    if compressed:
        with gzip.open(path, "wt", encoding="utf-8") as sarif_file:
            json.dump(document, sarif_file)
        return
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.parametrize(
    ("filename", "compressed"),
    [
        ("grype.sarif", False),
        ("grype.sarif.gz", True),
    ],
)
def test_summarize_grype_severity_ignores_sarif_display_level(
    tmp_path: Path,
    filename: str,
    compressed: bool,
) -> None:
    """Raw and compressed reports use Grype severities, not SARIF levels."""
    module = _load_module()
    sarif_path = tmp_path / filename
    _write_sarif(sarif_path, compressed)

    summary = module.summarize(str(sarif_path))

    assert summary["file"] == filename
    assert summary["total"] == 2
    assert summary["counts"]["medium"] == 1
    assert summary["counts"]["low"] == 1
    assert summary["counts"]["warning"] == 0
    assert summary["counts"]["note"] == 0


def test_summarize_missing_sarif_returns_none(tmp_path: Path) -> None:
    """A missing optional report is skipped explicitly."""
    module = _load_module()

    summary = module.summarize(str(tmp_path / "missing.sarif.gz"))

    assert summary is None


def test_summarize_invalid_gzip_sarif_raises_value_error(tmp_path: Path) -> None:
    """An existing corrupt compressed report fails instead of being swallowed."""
    module = _load_module()
    sarif_path = tmp_path / "invalid.sarif.gz"
    sarif_path.write_bytes(b"not-gzip")

    with pytest.raises(ValueError, match="Invalid SARIF"):
        module.summarize(str(sarif_path))
