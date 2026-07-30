# SPDX-License-Identifier: GPL-3.0-or-later
"""Static safety tests for the manual release workflow."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "release.yml"
)
MAKEFILE = WORKFLOW.parents[2] / "Makefile"


def _workflow_text() -> str:
    """Read the manual release workflow.

    Returns:
        Complete workflow source.
    """
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_validates_stable_tagged_main_source() -> None:
    """A release must be stable and match a tag on the checked-out main commit."""
    workflow = _workflow_text()

    assert "refs/heads/main" in workflow
    assert r"^[0-9]+\.[0-9]+\.[0-9]+$" in workflow
    assert 'TAG_REF="refs/tags/v${FINAL_VERSION}"' in workflow
    assert 'git rev-parse "${TAG_REF}^{commit}"' in workflow
    assert 'git rev-parse HEAD' in workflow


def test_release_workflow_builds_from_scratch_and_tests_final_image() -> None:
    """Release images use fresh bases and pass the container test suite."""
    workflow = _workflow_text()

    assert 'DOCKER_BUILD_FLAGS="--pull --no-cache"' in workflow
    assert 'make FINAL_VERSION="${FINAL_VERSION}" test-release' in workflow
    assert 'EXPECTED_CLI_VERSION="${FINAL_VERSION}"' in workflow
    assert 'make FINAL_VERSION="${FINAL_VERSION}" verify-labels' in workflow


def test_release_workflow_scans_the_attested_sbom() -> None:
    """Grype scans the exact SBOM later attached to the image."""
    workflow = _workflow_text()

    assert 'grype \\\n              "sbom:${SBOM_FILE}"' in workflow
    assert '--predicate "${SBOM_FILE}"' in workflow
    assert workflow.count('test -s "${SBOM_FILE}"') == 1
    assert workflow.count('test -s "${SARIF_FILE}"') == 1


def test_release_workflow_checks_registry_fail_closed() -> None:
    """Release publication uses authenticated registry state for safety."""
    workflow = _workflow_text()

    assert "scripts/dockerhub_manifest.py assert-absent" in workflow
    assert "scripts/dockerhub_manifest.py resolve" in workflow
    assert "docker manifest inspect" not in workflow
    assert "RepoDigests" not in workflow
    assert "make FINAL_VERSION=\"${FINAL_VERSION}\" push" not in workflow


def test_release_workflow_orders_audit_before_mutable_latest() -> None:
    """Only an immutable, trusted, recorded release can become latest."""
    workflow = _workflow_text()
    ordered_steps = (
        "- name: Push immutable release tag",
        "- name: Sign image digest with cosign",
        "- name: Attach signed SBOM attestation",
        "- name: Record audited immutable release",
        "- name: Create GitHub Release with audit assets",
        "- name: Publish audited release as latest",
    )
    positions = [workflow.index(step) for step in ordered_steps]

    assert positions == sorted(positions)


def test_release_workflow_creates_one_release_with_both_audit_assets() -> None:
    """The GitHub release is created once with its SBOM and scan report."""
    workflow = _workflow_text()

    assert workflow.count("uses: softprops/action-gh-release@v2") == 1
    assert "fail_on_unmatched_files: true" in workflow
    release_step = workflow.split(
        "- name: Create GitHub Release with audit assets",
        maxsplit=1,
    )[1]
    latest_step = release_step.split(
        "- name: Publish audited release as latest",
        maxsplit=1,
    )[0]
    assert "${{ env.SBOM_FILE }}" in latest_step
    assert "${{ env.SARIF_FILE }}" in latest_step


def test_release_workflow_keeps_audited_image_if_latest_fails() -> None:
    """A latest-tag failure does not delete a valid immutable release."""
    workflow = _workflow_text()
    latest = workflow.split(
        "- name: Publish audited release as latest",
        maxsplit=1,
    )[1]

    assert "scripts/remove_dockerhub_tag.py" not in latest


def test_release_workflow_records_registry_digest_and_source_revision() -> None:
    """Build history receives registry and tagged-source provenance."""
    workflow = _workflow_text()
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert 'export BUILD_GIT_REV="${SOURCE_GIT_REV}"' in workflow
    assert (
        'export PUSHED_IMAGE_DIGEST="${{ steps.image_digest.outputs.digest }}"'
        in workflow
    )
    assert "$(BUILD_GIT_REV)" in makefile
    assert "$(PUSHED_IMAGE_DIGEST)" in makefile


def test_release_workflow_has_bounded_runtime_and_minimal_permissions() -> None:
    """The release job has a timeout and no pull-request write access."""
    workflow = _workflow_text()

    assert "timeout-minutes: 90" in workflow
    assert "pull-requests:" not in workflow
