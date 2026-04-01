#!/usr/bin/env python3
"""
Append a build entry to doc/build-history.json, optionally embedding Grype
results, SBOM reference, cosign signature metadata, and CI provenance.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List

from grype_sarif_summary import summarize as summarize_grype


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update build history metadata.")

    # ── core ──────────────────────────────────────────────────────────────────
    parser.add_argument("--log", required=True, help="Path to build-history.json")
    parser.add_argument("--build-number", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--git-rev", required=True)
    parser.add_argument("--created", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--image-id", required=True)

    # ── vulnerability scan ────────────────────────────────────────────────────
    parser.add_argument(
        "--grype-sarif", default="",
        help="Path to Grype SARIF report (optional)",
    )

    # ── SBOM ──────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--sbom-file", default="",
        help="Local SBOM filename (e.g. sbom-1.2.3.spdx.json)",
    )
    parser.add_argument(
        "--sbom-release-asset-url", default="",
        help="Full URL to the SBOM attached to the GitHub Release",
    )

    # ── cosign ────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--cosign-signed", default="false",
        help="'true' if the image was signed with cosign",
    )
    parser.add_argument(
        "--cosign-rekor-url", default="",
        help="Sigstore Rekor browser URL for the transparency-log entry",
    )
    parser.add_argument(
        "--cosign-image-digest", default="",
        help="Full repo@sha256:... digest that was signed",
    )

    # ── CI provenance ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--build-runner", default="",
        help="Runner label, e.g. 'Linux-X64'",
    )
    parser.add_argument(
        "--github-run-id", default="",
        help="GITHUB_RUN_ID of the Actions run",
    )
    parser.add_argument(
        "--github-run-url", default="",
        help="Full URL to the GitHub Actions run",
    )

    return parser.parse_args()


def load_history(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, list):
        raise SystemExit(f"Expected list in {path}, found {type(data).__name__}")
    return data


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def main() -> None:
    args = parse_args()

    if args.version == "dev":
        print("ℹ️  Skipping build log for dev build")
        return

    log_path = pathlib.Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    history = load_history(log_path)

    # ── core entry ────────────────────────────────────────────────────────────
    entry: Dict[str, Any] = {
        "build_number": args.build_number,
        "tag":          args.version,
        "base_image":   args.base,
        "git_revision": args.git_rev,
        "created":      args.created,
        "dockerhub_tag_url": args.url,
        "digest":       args.digest,
        "image_id":     args.image_id,
    }

    # ── grype scan ────────────────────────────────────────────────────────────
    if args.grype_sarif:
        summary = summarize_grype(args.grype_sarif)
        if summary:
            entry["grype_scan"] = summary

    # ── SBOM ──────────────────────────────────────────────────────────────────
    sbom: Dict[str, Any] = {}
    if args.sbom_file:
        sbom["file"] = args.sbom_file
    if args.sbom_release_asset_url:
        sbom["release_asset_url"] = args.sbom_release_asset_url
    if sbom:
        entry["sbom"] = sbom

    # ── cosign ────────────────────────────────────────────────────────────────
    if _truthy(args.cosign_signed):
        cosign: Dict[str, Any] = {"signed": True}
        if args.cosign_rekor_url:
            cosign["rekor_log_entry"] = args.cosign_rekor_url
        if args.cosign_image_digest:
            cosign["image_digest"] = args.cosign_image_digest
        entry["cosign"] = cosign

    # ── CI provenance ─────────────────────────────────────────────────────────
    build_prov: Dict[str, Any] = {}
    if args.build_runner:
        build_prov["runner"] = args.build_runner
    if args.github_run_id:
        build_prov["github_run_id"] = args.github_run_id
    if args.github_run_url:
        build_prov["github_run_url"] = args.github_run_url
    if build_prov:
        entry["build"] = build_prov

    history.append(entry)
    log_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
