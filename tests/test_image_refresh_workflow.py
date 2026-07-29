# SPDX-License-Identifier: GPL-3.0-or-later
"""Static safety tests for the weekly image-refresh workflow."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "image-refresh.yml"
)
RELEASE_WORKFLOW = WORKFLOW.with_name("release.yml")


def _workflow_text() -> str:
    """Read the image-refresh workflow.

    Returns:
        Complete workflow source.
    """
    return WORKFLOW.read_text(encoding="utf-8")


def test_image_refresh_workflow_schedules_saturday_refresh() -> None:
    """The refresh workflow runs every Saturday at the agreed UTC time."""
    workflow = _workflow_text()

    assert 'cron: "17 4 * * 6"' in workflow
    assert "workflow_dispatch:" in workflow


def test_image_refresh_workflow_publishes_latest_after_attestation() -> None:
    """The trusted latest tag moves only after signing and attestation."""
    workflow = _workflow_text()

    attest_position = workflow.index("- name: Attach signed SBOM attestation")
    latest_position = workflow.index("- name: Publish signed refresh as latest")
    assert attest_position < latest_position


def test_image_refresh_workflow_shares_release_concurrency_group() -> None:
    """Manual releases and refreshes cannot publish images concurrently."""
    refresh_workflow = _workflow_text()
    release_workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    concurrency_group = "group: scrubexif-image-publication"

    assert concurrency_group in refresh_workflow
    assert concurrency_group in release_workflow
    assert "cancel-in-progress: false" in refresh_workflow
    assert "cancel-in-progress: false" in release_workflow


def test_image_refresh_workflow_stages_audit_files_without_readmes() -> None:
    """Weekly housekeeping stages audit data but no documentation files."""
    workflow = _workflow_text()
    housekeeping = workflow.split("- name: Record successful refresh", maxsplit=1)[1]
    stage_block = housekeeping.split("git add", maxsplit=1)[1].split(
        "git commit",
        maxsplit=1,
    )[0]

    assert "doc/build-history.json" in stage_block
    assert '"${SBOM_FILE}"' in stage_block
    assert '"${SARIF_FILE}"' in stage_block
    assert "README.md" not in stage_block
    assert "DETAILS.md" not in stage_block
    assert "index.html" not in stage_block
    assert "Changelog.md" not in stage_block
