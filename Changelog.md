# Changelog

## 0.7.20 - not released

### Changed

- wording in README modified

## 0.7.19 - 2026-04-11

### Fixed

- `write_cosign_badge.py`: success badge color changed to bright pink (`ff69b4`) with message "ok"; failure badge color changed to dull gray (`9e9e9e`) with message "failed".
- `write_cosign_badge.py`: script is now idempotent — reads existing badge and skips the write if all fields already match, preventing spurious git diffs.
- `release.yml`: SBOM attestation step (`cosign attest`) now runs before the failure badge and rollback steps so its outcome is available to both. Rollback and failure badge now trigger on either image signing failure (`cosign_sign`) or SBOM attestation failure (`cosign_attest`), removing the image from Docker Hub in both cases to prevent a partially-attested release from remaining publicly available.

### Deleted

- `make release` target removed from Makefile — superseded by the `release.yml` GitHub Actions workflow and dangerous to run locally (no cosign, no SBOM, no Grype).

## 0.7.19 - 2026-04-01

### Fixed

- `simple_scrub` no longer deletes originals on a second run: duplicates are now skipped (output preserved, original untouched) instead of being deleted.
- Removed `on_duplicate` parameter from `simple_scrub`; safe mode always uses `skip` policy.
- `log-build-json` Makefile target and `update_build_log.py` now skip writing to `build-history.json` when `FINAL_VERSION=dev`, preventing dev builds from polluting the release history. Previously, the guard in the `release` target was dead code (dependency ran before the check); fixed with a Make-level `ifeq` in `log-build-json` and an early return in the Python script.
- Manually cleaned up build_history.json to remove the "dev" builds

### Tests

- `test_scrub_file_skip_leaves_original_untouched`: verifies `on_duplicate="skip"` returns `status="skipped"` and leaves original byte-identical.
- `test_simple_scrub_second_run_skips_and_preserves_originals`: verifies a second run skips already-scrubbed files without touching originals.
- `test_simple_scrub_on_duplicate_delete_ignored_originals_safe`: verifies `--on-duplicate delete` is never forwarded to `simple_scrub`.

## 0.7.17 - 2026-04-01

### Added

- Tests: expanded symlink security coverage, strengthened assertions to verify file content/metadata instead of existence, and added positive+negative tests for all documented CLI argument constraints (`--clean-inline`, `--from-input`, `--output`, positional files).
- Tests: positive+negative tests proving `--paranoia` is incompatible with `--copyright`/`--comment`, and that each flag is correctly forwarded to the scrubbing layer.
- Tests: positive+negative tests for the `-o` pre-existing directory behaviour.
- `_format_path_with_host` and `_format_relative_path_with_host` now correctly resolve the physical host path for output directories mounted outside `/photos` (e.g. `-v "/tmp/scrub-test:/scrubbed" -o /scrubbed`).
- Tests: path display tests covering output directories outside `PHOTOS_ROOT`, with and without `SHOW_CONTAINER_PATHS`.
- README: updated `-o` examples to use a top-level independent bind-mount (`-v "/tmp/scrub-test:/scrubbed"`) with a note explaining why nesting under `/photos` should be avoided.

### Fixed

- `-o <dir>` now accepts a pre-existing directory when the user explicitly supplies the flag (e.g. via a bind-mount), while still refusing a pre-existing default output directory as a safety guard.
- Output directory banner showed the container path instead of the host path when `-o` pointed outside `/photos`.

## 0.7.16 - 2026-03-31

### Added

- `cosign` of released image and SBOM. See more at [DETAILS.md](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md#image-signing-and-supply-chain-verification)

## 0.7.15 - 2026-03-29

### Added

- More paranoia (!), `jpegtran` now used to strip JPEGS of metadata. This ensures all APP segmenst are removed, including those `exiftool` does not "know".

- New pipeline for scrubbing. Save some metadata tags using `exiftool`, strip everything using `jpegtran`,  add the saved metadata using `exiftool`.

- --paranoia cannot be used with --copyright and --comment,  `scrubexif` fails immediately.

## 0.7.14 - 2026-02-22

### Added

- New `--comment` option to stamp comments into EXIF and XMP metadata (replaces existing values; truncates long input with warning).
- New `--copyright` option to stamp copyright notices into EXIF and XMP metadata (replaces existing values; truncates long input with warning).
- Release workflow now attaches Grype SARIF reports to GitHub Releases in addition to SBOMs.

### Changed

- Metadata preservation restricted to EXIF + XMP tag groups while still preserving `ColorSpaceTags` for accurate color.
- Updated README and DETAILS with corrected behavior, examples, and option lists; supply chain transparency text now matches actual artifacts.

### Tests

- Integration coverage for comment/copyright stamping, truncation warnings, and removal of disallowed metadata sections/tags.

### Docs

- README: fixed CI badge link, example version typo, auto-mode flow notes, and clarified release artifacts.
- DETAILS: corrected examples to require `--clean-inline` where needed; full CLI reference added and alphabetized.

## 0.7.13 - 2026-02-13

### Added

- `-o`, `--output` option in default safe mode to write scrubbed files to a custom directory (created if missing).
- Output path safety guard: refuse to create output dirs under system paths and reject symlinks/escape attempts.
- Hard promise: scrubbing now writes to a temp file and only moves into place on success.

### Fixed

- ExifTool failures/exceptions now clean up temp outputs and keep originals untouched.

### Tests

- Unit tests covering no-output-on-failure and in-place failure behavior.
- Docker/integration test ensuring corrupted inputs never appear in output.
- Simple mode unit stubs updated to account for temp output behavior.

### Docs

- README updated with the hard promise, failure handling, and `--output` examples.

## 0.7.12 - 2026-01-16

- Image refreshed

## 0.7.11 - 2025-12-20

### Breaking

- Default behavior now uses safe copy mode (previous `--simple`) and refuses to run if `/<current_directory>/output` already exists.
- `--simple` flag removed; in-place scrubbing now requires `--clean-inline`.

### Added

- `--clean-inline` to explicitly allow destructive in-place scrubbing.
- `--show-container-paths` to include container paths alongside host paths in output.
- `-q`, `--quiet` to suppress all output on success (errors still print to stderr).
- Host-path output for directories and files to make copy/paste work outside containers.

### Changed

- Default output now prints host paths only unless `--show-container-paths` is set.
- Tests updated to reflect new defaults and output formatting.

## 0.7.10 - 2025-12-03

### Fixed

- State path resolution now correctly creates parent directories, enabling stability state files to be written instead of silently falling back to mtime-only mode.
- Explicit state paths that are unwritable now log a warning and disable state rather than quietly relocating the file to a fallback path.

### Added

- Unit coverage for state resolution: positive (writable env path) and negative (unwritable env path) cases to guard against regressions.
- Additional tests for auto path selection: /tmp fallback when /photos is unwritable, and disabling state when no writable defaults exist.

## 0.7.9 - 2025-11-30

### Added

- Machine-readable summary output. `SCRUBEXIF_SUMMARY` line is now emitted at the end of every run with `total= scrubbed= skipped= errors= duplicates_* duration=` fields for automation and integration scripts.
- Test case. Verify the various counters reported are correct.
- Duration reporting. All runs now include a precise `Duration:` field in the human-readable summary.
- Integration script. A hardened example (`scripts/run_scrubexif_photoprism.sh`) was added for users who want automatic SOOC → scrubbed → PhotoPrism indexing. It only triggers `photoprism index` when `scrubbed > 0`.
- Documentation: `doc/DETAILS.md` updated with a full Integration Script section, security notes on `ALLOW_ROOT`, systemd examples, and recommended directory layout.

### Changed

- Image build is now staged. Make `scrubexif` a pip wheel in stage 1, copy it over in stage 2. This should make `syft` pick it up when scanning the image and add `scrubexif` to the SBOM.
- Build metadata logging is now more comprehensive. The `log-build-json` target archives a detailed record of each build in `doc/build-history.json`, including the git revision, image digest, and a summary of vulnerability scans from Grype. This provides a complete and auditable history of all releases.


## 0.7.8 - 2025-11-09

### Added

- **Supply chain transparency**. SBOM and Grype vuln summary now linked in the README.md for all to study.
- Nightly test now includes soak.sh
- Be clear that GPL 3 license means no guarantees on scrubexif code or docs
- Automating housekeeping on release

### Security

- Call exiftool with path on input file names, security advice from [exiftool website](http://exiftool.com)

## 0.7.7 - 2025-11-08

### Added

- Grype findings summary added to docs/build-history.json

## 0.7.6 - 2025-11-08

### Added

- Automated release workflow that runs Syft SBOM generation plus Grype vulnerability scanning before publishing Docker images and GitHub releases
- SBOM artifact upload and hardened release gate so every tag ships with provenance and scan results
- The workflow fails if Grype finds vulnerablilities >= High

## 0.7.5 - 2025-11-06

### Added

- Expanded `--debug` logging with environment snapshots and per-file stability timing to aid troubleshooting
- Integration test covering mixed valid/corrupted JPEG batches to ensure graceful recovery

### Improved

- README and DETAILS quick-start examples now include `--tmpfs /tmp` for writable scratch space
- Dockerfile trims pip install step and runs `python3 -m scrubexif.scrub` directly, reducing installed surface area
- Auto mode now relocates failed scrubs into `processed/` with an explicit warning, preventing retry loops on broken inputs
- ExifTool stderr handling tolerates non-UTF8 noise emitted by bad files, keeping long runs alive

## 0.7.4 - 2025-11-05

### Added

- Integration test `test_auto_mode_scrubs_with_hardening_flags` to verify auto mode succeeds under `--read-only` and `--security-opt no-new-privileges`
- Embedded minimal JPEG fixture for container tests so the hardened run covers real data writes

### Improved

- README and DETAILS quick-start examples now default to the hardened Docker flags
- DETAILS now documents what the hardening flags do, when they matter, and common pitfalls (tmpfs for `/tmp`, ensuring mount writability)

### Security

- Formalized hardened launch guidance across docs to drive consistent `--read-only` + `no-new-privileges` usage

## 0.7.3 - 2025-11-04

### Added

- New `--debug` CLI flag to enable verbose logging without remembering `--log-level` values

### Fixed

- `scrub_file` now passes the full destination filename to ExifTool, so auto mode writes into `/output/<name>.jpg` and duplicate reporting uses the correct path
- Added bulk auto-mode coverage that seeds 50 JPEGs with EXIF/XMP/IPTC/GPS data and confirms every output is scrubbed in the container suite

### Security

- Refuse to scrub into symlinked output files or move originals onto symlink targets

## 0.7.2 - 2025-11-03

### Security

- Hardened manual-mode path handling to keep CLI inputs confined to `/photos`
- Added regression tests exercising container breakout attempts
- Reject symlinked JPEGs in manual and auto flows to block host-path traversal
- Refuse to scrub into symlinked output files or move originals onto symlink targets

### Fixed

- Auto mode no longer crashes when `--delete-original` removes the source before moving to `/photos/processed`
- Preview mode now closes temporary files before copying so Windows runs succeed

## 0.7.1 - 2025-11-02

### Added

- New `--state-file` CLI option to override `SCRUBEXIF_STATE`
- Documentation split: short README + full `doc/DETAILS.md`
- Added summary tests validating state-file override logic
- Added `SCRUBEXIF_AUTOBUILD` explanation + behavior matrix

### Improved

- Default read-only container path resolution for state file
- Test suite reliability for auto-mode stability logic
- Soak-test framework (not run by default)
- Log messages for state disable, override, and fallback

### Fixed

- `run_container()` regression from state-file refactor
- Stability path resolution in `main()` flow
- Minor path sanitization and quoting issues
- README truncation on Docker Hub due to length

### Notes

- This release tightens container behavior and test rigor
- Full documentation moved to `doc/DETAILS.md` for clarity
- Recommended upgrade for automation users

## 0.7.0 — 2025-11-01

### Added

- Full container-integration test suite using shared `_docker.py` harness
- Soak test script with real JPEG inputs to validate long-run stability
- Automatic “final flush” option for soak scenario to clear pending files
- Persistent stability-window behavior across test and soak runs
- Support for mounting processed and errors dirs in tests

### Improved

- Auto-mode: Stability logic hardened for CI and real ingest pipelines
- Duplicate handling tests now cover delete and move paths
- Container log level made explicit in tests (`--log-level debug|info`)
- Deterministic temp state path in CI for reproducibility
- Makefile test defaults exclude soak/nightly paths via pytest markers
- Developer Docker build workflows with stable tag strategy
- Introduced centralized Docker run flags via `_docker.py`

### Fixed

- Tests now use real JPEGs instead of synthetic 4-byte placeholders
- Stability-window false positives during soak runs
- Marker confusion: nightly/soak now explicitly applied at file scope
- Input file lingering after scrub in edge cases
- False duplicate detections in transient mounts
- ExifTool JSON parsing errors from invalid fake test files

### Notes

- This release finalizes the CI-safe, read-only, tmpfs-backed execution model
- All standard tests pass in <10s; soak runs intentionally long form
- No breaking CLI changes

## 0.6.x

- Previous refactors and incremental container improvements
