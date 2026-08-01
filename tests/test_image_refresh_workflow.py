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
VERSION_CHECK_WORKFLOW = WORKFLOW.with_name("security-tool-version-check.yml")
DEPENDABOT_CONFIG = WORKFLOW.parents[1] / "dependabot.yml"


def _workflow_text() -> str:
    """Read the image-refresh workflow.

    Returns:
        Complete workflow source.
    """
    return WORKFLOW.read_text(encoding="utf-8")


def test_image_refresh_workflow_schedules_saturday_refresh() -> None:
    """The refresh workflow runs every Saturday at the agreed UTC time."""
    workflow = _workflow_text()

    assert 'cron: "37 4 * * 6"' in workflow
    assert "workflow_dispatch:" in workflow


def test_image_refresh_workflow_publishes_latest_after_attestation() -> None:
    """The trusted latest tag moves only after attestation and audit persistence."""
    workflow = _workflow_text()

    attest_position = workflow.index("- name: Attach signed SBOM attestation")
    audit_position = workflow.index("- name: Record audited immutable refresh")
    latest_position = workflow.index("- name: Publish audited refresh as latest")
    assert attest_position < audit_position < latest_position


def test_image_refresh_workflow_compresses_artifacts_after_attestation() -> None:
    """Raw audit inputs are consumed before compressed files are committed."""
    workflow = _workflow_text()

    attest_position = workflow.index("- name: Attach signed SBOM attestation")
    compress_position = workflow.index("- name: Compress repository audit artifacts")
    housekeeping_position = workflow.index("- name: Record audited immutable refresh")
    assert attest_position < compress_position < housekeeping_position
    assert 'gzip -9 -- "${SBOM_FILE}" "${SARIF_FILE}"' in workflow


def test_image_refresh_workflow_records_dereferenced_source_commit() -> None:
    """Build history records the tagged commit rather than the tag object."""
    workflow = _workflow_text()

    assert 'git rev-parse --short "v${BASE_VERSION}^{commit}"' in workflow
    assert 'git rev-parse --short "v${BASE_VERSION}")' not in workflow


def test_image_refresh_workflow_keeps_source_and_controller_isolated() -> None:
    """Stable source and controller tooling use independent complete checkouts."""
    workflow = _workflow_text()

    assert "path: controller" in workflow
    assert "path: stable-source" in workflow
    assert 'working-directory: controller' in workflow
    assert 'SOURCE_DIR="${STABLE_SOURCE_DIR}"' in workflow
    assert "validate-refresh-source" in workflow
    assert "test-refresh-controller" in workflow
    assert "git checkout origin/main --" not in workflow


def test_image_refresh_workflow_records_both_revisions() -> None:
    """Refresh audit metadata identifies source and controller revisions."""
    workflow = _workflow_text()

    assert 'SOURCE_GIT_REV=$(git rev-parse --short "v${BASE_VERSION}^{commit}")' in workflow
    assert '--controller-git-rev "${GITHUB_SHA}"' in workflow


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
    housekeeping = workflow.split(
        "- name: Record audited immutable refresh",
        maxsplit=1,
    )[1]
    stage_block = housekeeping.split("git add", maxsplit=1)[1].split(
        "git commit",
        maxsplit=1,
    )[0]

    assert "doc/build-history.json" in stage_block
    assert '"${SBOM_ARCHIVE}"' in stage_block
    assert '"${SARIF_ARCHIVE}"' in stage_block
    assert '"${SBOM_FILE}"' not in stage_block
    assert '"${SARIF_FILE}"' not in stage_block
    assert "README.md" not in stage_block
    assert "DETAILS.md" not in stage_block
    assert "index.html" not in stage_block
    assert "Changelog.md" not in stage_block


def test_image_refresh_workflow_checks_registry_fail_closed() -> None:
    """Refresh publication refuses ambiguity and local digest inference."""
    workflow = _workflow_text()

    assert "scripts/dockerhub_manifest.py assert-absent" in workflow
    assert "scripts/dockerhub_manifest.py resolve" in workflow
    assert "docker manifest inspect" not in workflow
    assert "RepoDigests" not in workflow


def test_image_refresh_workflow_persists_branch_and_tag_atomically() -> None:
    """Refresh audit metadata and its Git tag become visible together."""
    workflow = _workflow_text()

    assert "git push --atomic origin" in workflow
    assert 'HEAD:main \\' in workflow
    assert '"refs/tags/v${REFRESH_VERSION}"' in workflow


def test_image_refresh_workflow_keeps_audited_image_if_latest_fails() -> None:
    """A latest-tag failure does not delete a valid immutable refresh."""
    workflow = _workflow_text()
    latest = workflow.split(
        "- name: Publish audited refresh as latest",
        maxsplit=1,
    )[1]

    assert "Roll back immutable tag if latest publication fails" not in workflow
    assert "scripts/remove_dockerhub_tag.py" not in latest


def test_publication_workflows_install_exact_security_tool_versions() -> None:
    """Release and refresh builds install tools from their pinned tags."""
    expected_fragments = (
        "scripts/security_tool_versions.py export",
        "anchore/syft/${SYFT_VERSION}/install.sh",
        'sh -s -- -b /usr/local/bin "${SYFT_VERSION}"',
        "anchore/grype/${GRYPE_VERSION}/install.sh",
        'sh -s -- -b /usr/local/bin "${GRYPE_VERSION}"',
    )

    for workflow_path in (WORKFLOW, RELEASE_WORKFLOW):
        workflow = workflow_path.read_text(encoding="utf-8")
        for fragment in expected_fragments:
            assert fragment in workflow
        assert "anchore/syft/main/install.sh" not in workflow
        assert "anchore/grype/main/install.sh" not in workflow


def test_publication_workflows_key_grype_cache_by_pinned_version() -> None:
    """Scanner database caches cannot cross incompatible Grype versions."""
    for workflow_path in (WORKFLOW, RELEASE_WORKFLOW):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "grype-db-${{ runner.os }}-${{ env.GRYPE_VERSION }}" in workflow


def test_image_refresh_workflow_records_security_tool_versions() -> None:
    """Refresh build-history entries contain the exact scanner tool pins."""
    workflow = _workflow_text()

    assert '--syft-version "${SYFT_VERSION}"' in workflow
    assert '--grype-version "${GRYPE_VERSION}"' in workflow


def test_security_tool_version_check_runs_monthly_and_reports_updates() -> None:
    """A monthly workflow compares pins with the latest GitHub releases."""
    workflow = VERSION_CHECK_WORKFLOW.read_text(encoding="utf-8")

    assert 'cron: "17 6 1 * *"' in workflow
    assert "repos/anchore/syft/releases/latest" in workflow
    assert "repos/anchore/grype/releases/latest" in workflow
    assert "scripts/security_tool_versions.py check" in workflow


def test_dependabot_checks_github_actions_weekly() -> None:
    """Dependabot is configured to propose GitHub Actions updates."""
    config = DEPENDABOT_CONFIG.read_text(encoding="utf-8")

    assert "package-ecosystem: github-actions" in config
    assert "interval: weekly" in config
