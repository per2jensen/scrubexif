# Changelog

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
