# scrubexif

<div align="center">

<!-- 📦 Project Metadata -->
<a href="https://github.com/per2jensen/scrubexif/releases"><img alt="Tag" src="https://img.shields.io/github/v/tag/per2jensen/scrubexif"/></a>
<a href="https://github.com/per2jensen/scrubexif/actions/workflows/test.yml"><img alt="CI" src="https://github.com/per2jensen/scrubexif/actions/workflows/test.yml/badge.svg"/></a>
<img alt="License" src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue"/>

<!-- 🐳 Docker Hub Stats -->
<img alt="Docker Pulls" src="https://img.shields.io/docker/pulls/per2jensen/scrubexif"/>
<img alt="Base OS" src="https://img.shields.io/badge/base%20image-ubuntu%2024.04-brightgreen"/>

<!-- 📊 GitHub ClonePulse Analytics -->
<a href="https://github.com/per2jensen/scrubexif/blob/main/clonepulse/weekly_clones.png">
  <img alt="# clones" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/per2jensen/scrubexif/main/clonepulse/badge_clones.json"/>
</a>
<a href="https://github.com/per2jensen/scrubexif/blob/main/clonepulse/weekly_clones.png">
  <img alt="Milestone" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/per2jensen/scrubexif/main/clonepulse/milestone_badge.json"/>
</a>

<sub>🎯 Stats powered by <a href="https://github.com/per2jensen/clonepulse">ClonePulse</a></sub>

</div>

`scrubexif` is a lightweight, Dockerized EXIF cleaner designed for fast publishing of JPEG photos without leaking sensitive metadata.

It removes most embedded EXIF, IPTC, and XMP data while preserving useful tags like exposure settings — ideal for privacy-conscious photographers who still want to share some technical info.

**GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

**Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

## Table of Contents

- [scrubexif](#scrubexif)
  - [Table of Contents](#table-of-contents)
  - [Quick Start](#quick-start)
    - [Build \& Run Locally](#build--run-locally)
    - [Default safe mode (copy)](#default-safe-mode-copy)
    - [Clean-inline mode (`--clean-inline`)](#clean-inline-mode---clean-inline)
    - [Auto mode (`--from-input`)](#auto-mode---from-input)
      - [Example](#example)
      - [Duplicate Handling](#duplicate-handling)
  - [Options](#options)
  - [Environment variables](#environment-variables)
  - [Features](#features)
    - [Metadata Preservation Strategy](#metadata-preservation-strategy)
    - [`--paranoia` Mode](#--paranoia-mode)
    - [Inspecting Metadata with `--show-tags`](#inspecting-metadata-with---show-tags)
    - [Preview Mode (`--preview`)](#preview-mode---preview)
    - [Filename sanitisation (`--rename`)](#filename-sanitisation---rename)
      - [Format string tokens](#format-string-tokens)
      - [Examples](#examples)
      - [Integration with existing flags](#integration-with-existing-flags)
      - [Missing EXIF DateTimeOriginal](#missing-exif-datetimeoriginal)
      - [Limits and validation](#limits-and-validation)
  - [What It Cleans](#what-it-cleans)
  - [Work on stable files](#work-on-stable-files)
    - [Stability gate](#stability-gate)
    - [State tracking](#state-tracking)
    - [Temp/partial file filter](#temppartial-file-filter)
      - [Scope](#scope)
    - [Configuration](#configuration)
  - [Integration (optional)](#integration-optional)
    - [Integration script](#integration-script)
    - [Example systemd service and timer](#example-systemd-service-and-timer)
  - [User Privileges and Running as Root](#user-privileges-and-running-as-root)
  - [Hardening \& Recommendations](#hardening--recommendations)
  - [Known limitations](#known-limitations)
  - [Docker Images](#docker-images)
  - [Viewing Metadata](#viewing-metadata)
  - [Inspecting the Image Itself](#inspecting-the-image-itself)
  - [Image Signing and Supply Chain Verification](#image-signing-and-supply-chain-verification)
    - [The problem cosign solves](#the-problem-cosign-solves)
    - [Installing cosign](#installing-cosign)
    - [Verifying a release image](#verifying-a-release-image)
    - [Verifying the SBOM attestation](#verifying-the-sbom-attestation)
    - [Checking the Rekor transparency log entry](#checking-the-rekor-transparency-log-entry)
    - [What a verified certificate looks like](#what-a-verified-certificate-looks-like)
    - [Why sign by digest, not tag?](#why-sign-by-digest-not-tag)
    - [Supply chain artefacts in build-history.json](#supply-chain-artefacts-in-build-historyjson)
  - [Dev setup](#dev-setup)
  - [Test Image](#test-image)
  - [License](#license)
  - [Related Tools](#related-tools)
  - [Feedback](#feedback)
  - [Project Homepage](#project-homepage)
  - [Reference](#reference)
    - [CLI Options (`scrubexif.py`)](#cli-options-scrubexifpy)

## Quick Start

There are **two modes**:

### Build & Run Locally

```bash
# build an image from the Dockerfile in this repo
docker build -t scrubexif:local .

# inspect CLI usage exported by python -m scrubexif.scrub
docker run --rm --read-only --security-opt no-new-privileges scrubexif:local --help

# scrub the current directory with hardened defaults
docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:local
```

Arguments placed after the image name are passed straight through to the
`python3 -m scrubexif.scrub` entrypoint, so all CLI flags shown below work the
same whether you use the published image or a locally built one.

### Default safe mode (copy)

Scrub all JPEGs in the current directory and write cleaned copies to `output/`.
Originals are left untouched. This mode refuses to run if `output/` already exists.

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION
```

Recursively scrub nested folders:

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --recursive
```

### Clean-inline mode (`--clean-inline`)

Manually scrub one or more `.jpg` / `.jpeg` files in-place (destructive).

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --clean-inline "file1.jpg" "file2.jpeg"
```

Recursively scrub nested folders in-place:

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --clean-inline --recursive
```

### Auto mode (`--from-input`)

Scrubs everything in a predefined input directory and saves output to another — useful for batch processing.

You **must** mount three volumes:

- `/photos/input` — input directory (e.g. `$PWD/input`)
- `/photos/output` — scrubbed files saved here
- `/photos/processed` — originals are moved here (or deleted if `--delete-original` is used)
- Any file ExifTool cannot scrub (e.g. corrupted JPEG) is logged and moved to `/photos/processed` so it does not loop

#### Example

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/output:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  per2jensen/scrubexif:$VERSION --from-input
```

#### Duplicate Handling

By default, if a file with the same name already exists in the output folder, it is treated as a **duplicate**:

- `--on-duplicate delete` (default): Skips scrubbing and deletes the original from input.
- `--on-duplicate move`: Moves the duplicate file to `/photos/errors` for inspection.

This ensures output is not overwritten and prevents silently skipping files.

```bash
# Move duplicates to /photos/errors instead of deleting
docker run --read-only --security-opt no-new-privileges \
           --tmpfs /tmp \
           -v "$PWD/input:/photos/input" \
           -v "$PWD/output:/photos/output" \
           -v "$PWD/processed:/photos/processed" \
           -v "$PWD/errors:/photos/errors" \
           scrubexif:dev --from-input --on-duplicate move
```

📌 **Observe** the `-v "$PWD/errors:/photos/errors"` volume specification needed for the `--on-duplicate move` option.

## Options

- `--delete-original` — delete originals instead of moving them
- `--clean-inline` — scrub in-place (destructive). Required for positional file/dir arguments
- `--output PATH` — override output directory in default safe mode (not allowed with `--from-input` or `--clean-inline`)
- `--rename FORMAT` — rename output files using a format string; see [Filename sanitisation](#filename-sanitisation---rename) and [`doc/rename-spec.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/rename-spec.md)
- `-q`, `--quiet` — suppress all output on success
- `--on-duplicate {delete|move}` - delete or move a duplicate
- `--dry-run` - show what would be scrubbed, but don’t write files
- `--debug` - shortcut for `--log-level debug`; also enables extra diagnostic logging (takes precedence if `--log-level` is also supplied)
- `--log-level` - choices=["debug", "info", "warn", "error", "crit"], default="info"
- `--max-files` - limit number of files to scrub (useful for testing or safe inspection)
- `--paranoia` - maximum metadata scrubbing, removes ICC profile including its (potential) fingerprinting vector
- `--preview` - preview scrub effect on one file without modifying it (shows before/after metadata)
- `--copyright` - stamp a copyright notice into EXIF and XMP (replaces existing values)
- `--comment` - stamp a comment into EXIF and XMP (replaces existing values)
- `--show-container-paths` - include container paths alongside host paths in output
- `-r`, `--recursive` - Recurse into directories
- `--show-tags` - choices=["before", "after", "both"], show metadata tags before, after, or both for each image
- `--stable-seconds` <secs> - Number of seconds a file must not change before being processed. Default is 120 secs
- `--state-file PATH|disabled` — Override stability tracking path or disable persistence entirely
- positional files/dirs — Optional list of files or directories (relative to `/photos` when running in Docker); requires `--clean-inline`
- `--from-input` — Run in auto mode, consuming `/photos/input` and emitting to `/photos/output`
- `-v`, `--version` - show version and license

## Environment variables

| Variable | Purpose |
|---------|--------|
| `ALLOW_ROOT` | Permit execution as root (must be `1`) |
| `SCRUBEXIF_AUTOBUILD` | Auto-build `scrubexif:dev` on first test run when running pytest |
| `SCRUBEXIF_ON_DUPLICATE` | Default duplicate policy (`delete`/`move`) for auto mode |
| `SCRUBEXIF_STABLE_SECONDS` | Default stability window before scrubbing |
| `SCRUBEXIF_STATE` | Path to persistent mtime state tracking (supports CLI override) |

Examples:

```sh
# Build image explicitly
make dev

# Standard test run (auto-build allowed)
pytest

# Strict run: fail if dev image is missing
SCRUBEXIF_AUTOBUILD=0 pytest
```

Scrub all `.jpg` files in subdirectories:

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --recursive
```

Dry-run (preview only):

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --dry-run
```

Mix recursion and dry-run:

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --recursive --dry-run
```

📌 **Observe** In clean-inline mode, files are scrubbed in-place and will overwrite the originals. Duplicate handling (e.g. move/delete) is not applicable here.

## Features

- Case insensitive, works on .jpg, .JPG, .jpeg & .JPEG
- Removes most EXIF, IPTC, and XMP metadata
- **Preserves** useful photography tags:
  - `ExposureTime`, `FNumber`, `ISO`
  - `ImageSize`, `Orientation`
  - `FocalLength`
- Show tags before & after (see below)
- Preserves Color profile, with a compromise in scrubbing (see below)
- A --paranoia option to scrub color profile tags (see below)
- A --preview option to check tag before/after scrub (see below)
- An --on-duplicate option controlling what to do if a file in /output is already there
- Based on the most excellent [ExifTool](https://exiftool.org/) inside a minimal Ubuntu base image
- Docker-friendly for pipelines and automation

### Metadata Preservation Strategy

By default, `scrubexif` preserves important non-private metadata such as **exposure settings**, **ISO**, **focal length**, **image size/orientation**, and **color profile** information. This ensures that images look correct in color-managed environments (e.g. Apple Photos, Lightroom, web browsers with ICC support).

For users who require maximum privacy, an optional `--paranoia` mode is available.

### `--paranoia` Mode

When enabled, `--paranoia` disables color profile preservation and removes fingerprintable metadata like ICC profile hashes (`ProfileID`). This may degrade color rendering on some devices, but ensures all embedded fingerprint vectors are scrubbed.

| Mode         | ICC Profile | Color Fidelity | Privacy Level |
|--------------|-------------|----------------|---------------|
| *(default)*  | ✅ Preserved   | ✅ High         | ⚠️ Moderate |
| `--paranoia` | ❌ Removed     | ❌ May degrade  | ✅ Maximum  |

### Inspecting Metadata with `--show-tags`

The `--show-tags` option lets you inspect metadata **before**, **after**, or **both before and after** scrubbing. This is useful for:

- Auditing what data is present in your photos
- Verifying that scrubbed output removes private metadata
- Confirming what remains (e.g. focal length, exposure settings, etc.)

If you want to **inspect metadata only without modifying any files**, you must pass `--dry-run` and use `--clean-inline` when targeting specific files.

```bash
# See tags BEFORE scrub (no modifications)
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev --clean-inline image.jpg --show-tags before --dry-run

# See both BEFORE and AFTER (scrub still happens)
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev --clean-inline image.jpg --show-tags both
```

### Preview Mode (`--preview`)

The `--preview` option lets you **safely simulate** the scrubbing process on a **single** JPEG **without modifying the original file**.

This mode:

- Copies the original image to a temporary file
- Scrubs the copy to a temporary output file
- Shows metadata **before and/or after** scrubbing
- Deletes the temp files automatically
- Never alters the original image

```bash
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev --clean-inline test.jpg --preview
```

🛡 Tip: Combine `--preview --paranoia` to verify the color profile tags including the ProfileId tag has been scrubbed.

### Filename sanitisation (`--rename`)

Filenames are a privacy vector that EXIF scrubbing alone does not address. A
filename like `2026-04-07_11-13-45.jpeg` reveals the exact capture time.
Camera-derived prefixes such as `D80_` or `Z50_` identify the device model.
`--rename` replaces the output filename with a format string you control, so
the original name never appears in the output.

**Design principle:** there is no auto-detection of prefixes or timestamps from
the original filename. If you want a prefix in the output, write it literally
in the format string. This makes behaviour explicit and predictable regardless
of what the camera named the file.

#### Format string tokens

| Token | Description |
|-------|-------------|
| `%r`  | Random hex string. Default length 8. Configure with `%r6`, `%r12`, etc. Maximum: 32. |
| `%u`  | RFC 4122 UUID v4 (e.g. `f47ac10b-58cc-4372-a567-0e02b2c3d479`). |
| `%n`  | Sequential counter per invocation. Zero-padded to 4 digits by default. Configure with `%n6`. Maximum: 12 digits. |
| `%Y`  | 4-digit year from EXIF `DateTimeOriginal` (e.g. `2026`). |
| `%m`  | 2-digit month from EXIF `DateTimeOriginal` (e.g. `04`). |
| `%%`  | Literal percent sign. |

Tokens `%d`, `%H`, `%M`, and `%S` are deliberately not supported — allowing
day or time-of-day in the output filename would undermine the purpose of the
tool. Using them produces an immediate error before any files are touched.

#### Examples

```bash
# Fully anonymous — 8-character random hex
--rename "%r8"                   # → d4e7b1a9.jpg

# Keep your camera prefix, remove the timestamp
--rename "D80_%r6"               # → D80_f3a91c.jpg

# Retain year and month only (sourced from EXIF DateTimeOriginal)
--rename "%Y%m_%r6"              # → 202604_f3a91c.jpg

# UUID — formally unique, visually unambiguous
--rename "%u"                    # → f47ac10b-58cc-4372-a567-0e02b2c3d479.jpg

# Sequential counter — one camera body at a time
--rename "D80_%n4"               # → D80_0001.jpg  D80_0002.jpg ...
```

#### Integration with existing flags

- `--paranoia` implies `--rename "%r8"` if no `--rename` is given. An explicit `--rename` always takes precedence.
- `--dry-run` prints proposed new filenames without modifying any files.
- `--clean-inline --rename` scrubs the file in place and renames it in the same directory. The original path disappears — this is intentional, since the user has explicitly opted into destructive in-place modification.
- Without `--rename`, original filenames are preserved (existing behaviour).

#### Missing EXIF DateTimeOriginal

If `%Y` or `%m` is used and a file has no `EXIF DateTimeOriginal`, the file is
fully scrubbed as normal but the filename falls back to a UUID v4. A clear
warning is printed. The exit code is not affected — this is a handled fallback,
not an error.

```
WARNING: IMG_0042.jpeg — EXIF DateTimeOriginal absent,
         renamed to f47ac10b-58cc-4372-a567-0e02b2c3d479.jpg
```

#### Limits and validation

| Constraint | Limit |
|------------|-------|
| Prefix (literal chars before first token) | 16 chars |
| Postfix (literal chars after last token) | 16 chars |
| `%r` length | max 32 |
| `%n` digits | max 12 |
| Total expanded filename (before extension) | 64 chars |

Allowed literal characters: uppercase and lowercase letters, digits, hyphen
(`-`), underscore (`_`), space (` `). Dots, slashes, URL-encoded sequences, and
any other character produce an immediate error.

Full specification including validation order, collision handling, and
implementation notes → [`doc/rename-spec.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/rename-spec.md)

## What It Cleans

The tool removes:

- GPS location data
- Camera serial numbers
- Software version strings
- Embedded thumbnails
- XMP/IPTC descriptive metadata
- MakerNotes (where safely possible)

It **preserves** key tags important for photographers and viewers.

## Work on stable files

Scrubexif defers processing until a file is not changing. The use case I have built this for is uploading photos (jpegs from the camera) to a [photoprism](https://github.com/photoprism/photoprism) instance during dog shows to let owners quickly see the photos.

`Scrubexif` is started every 5 minutes by a systemd timer, and I could very well be uploading photos. `Scrubexif` takes (some) care not to start processing photos until they are finished being uploaded.

### Stability gate

A JPEG is eligible only if now - mtime >= stable_seconds (default 120).

If the file was seen before, its size and mtime must be unchanged since the last run.

First-seen files that are already older than the threshold pass on the next run unless they change again.

### State tracking

A JSON file stores `{path: {size, mtime, seen}}` to remember previous runs:

- If you pass `--state-file` or set `SCRUBEXIF_STATE`, that exact path is used **only if writable**. If it is not writable, scrubexif logs a warning and disables state (mtime-only) instead of silently relocating it.
- When no explicit path is provided, scrubexif auto-selects `/photos/.scrubexif_state.json` if writable, otherwise `/tmp/.scrubexif_state.json`. The chosen auto path is logged; if neither is writable, state is disabled and mtime-only checks are used.
- Each run updates entries for observed files and prunes paths that no longer exist.
- Delete the state file to reset history.

### Temp/partial file filter

Filenames with common temp prefixes/suffixes are always skipped: prefixes ., ~, ._; suffixes .tmp, .part, .partial, .crdownload, .download, .upload, .cache, .swp, .swx, .lck, or names ending with any of those (e.g., photo.jpg.uploading).

These are still recorded in state but never processed while they look temporary.

#### Scope

Applies to auto mode (--from-input) only. Clean-inline mode stays unchanged.

Summary now reports “Skipped (unstable)”. Duplicates/error logic unaffected.

### Configuration

CLI: --stable-seconds N.

Env: SCRUBEXIF_STABLE_SECONDS if the flag is omitted. Default 120.

## Integration (optional)

If you want scrubexif to automatically process newly arrived JPEG files and then trigger a PhotoPrism import, you can use the example script included in `scripts/run_scrubexif_photoprism.sh`.

This script:

- runs scrubexif in **auto mode** (`--from-input`)
- uses the hardened Docker flags recommended in this document
- parses the machine-readable `SCRUBEXIF_SUMMARY` line
- **only triggers PhotoPrism indexing when new files were actually scrubbed**
- prevents overlapping runs through a lock file
- allows users to quickly integrate scrubexif into an existing SOOC → scrubbed → PhotoPrism pipeline

The script expects that you have three directories:

``` bash
/photos/input       # new JPEGs arrive here
/photos/output      # scrubbed JPEGs written here
/photos/processed   # originals moved here after scrub
```

These can be mapped to any host paths via `-v` mounts, as shown in the example.

### Integration script

The script in the repository **does not** set `ALLOW_ROOT=1`, for security reasons. If your system requires containers to run as root (e.g., some NAS systems), you can enable this explicitly by uncommenting the line shown in the script.

```bash
# -e ALLOW_ROOT=1 \
```

### Example systemd service and timer

```ini
[Unit]
Description=Run scrubexif and trigger PhotoPrism indexing

[Service]
Type=oneshot
ExecStart=/usr/local/bin/run_scrubexif_photoprism.sh
```

```ini
[Unit]
Description=Run scrubexif every 5 minutes

[Timer]
OnBootSec=300s
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

This makes scrubexif completely automatic in a PhotoPrism setup.

## User Privileges and Running as Root

By default, the `scrubexif` container runs as user ID 1000, not root. This is a best-practice security measure to avoid unintended file permission changes or elevated access.

```bash
docker run --rm --read-only --security-opt no-new-privileges --tmpfs /tmp scrubexif:dev
```

- Specify a custom UID with `--user $(id -u)` to match host permissions.
- Running as root is blocked unless `ALLOW_ROOT=1` is set. Use with caution:

```bash
docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  --user 0 \
  -e ALLOW_ROOT=1 \
  scrubexif:dev
```

## Hardening & Recommendations

Use these options when starting a container:

- [--read-only](https://docs.docker.com/reference/cli/docker/container/run/#read-only)
- [--security-opt no-new-privileges](https://docs.docker.com/reference/cli/docker/container/run/#security-opt)
- `--tmpfs /tmp`

```bash
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/output:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  scrubexif:dev --from-input
```

What these flags do (and how they can bite you)

- **`--read-only`** mounts the container root filesystem as read-only so the image can’t be mutated at runtime. All app writes *must* land in writable mounts such as `/photos/*`.  
  - *Watch out*: if you rely on a custom `--state-file` path or duplicate handling output, make sure that path lives on a mounted volume. Docker normally provisions `/tmp` as a tmpfs when `--read-only` is used, but other runtimes might require an explicit `--tmpfs /tmp`.
- **`--security-opt no-new-privileges`** blocks any attempt to gain more privileges (e.g. via setuid binaries).  
  - *Watch out*: commands that expect to invoke `sudo`, or wrappers that rely on setuid/setgid helpers inside the container, will fail silently. `scrubexif` doesn’t need them, but your surrounding tooling might.

If you orchestrate with Kubernetes, set `readOnlyRootFilesystem: true` and `allowPrivilegeEscalation: false` to mirror these flags. Always verify that mounted host directories (input/output/processed/errors/state) stay writable by the container UID when the root filesystem is locked down.

Avoid using symbolic links for input, output, or processed paths. Due to Docker's volume resolution behavior, symlinks are flattened and no longer detectable inside the container.

Ensure the input, output, and processed directories exist on the host, are not files or symlinks, and are writable by the container’s user.

## Known limitations

> Symlinked input paths are not detected inside the container

If you bind-mount a symbolic link (e.g. `-v $(pwd)/symlink:/photos/input`), Docker resolves the symlink before passing it to the container. For safety, avoid mounting symbolic links to any of the required directories.

## Docker Images

For now I am not using `latest`, as the images are only development quality.

I am currently going with:

| Tag        | Description                                      | Docker Hub | Example Usage  |
|------------|--------------------------------------------------|------------|----------------|
| `:0.x.y`   | Versioned releases following semantic versioning | ✅ Yes     | `docker pull per2jensen/scrubexif:0.5.11`   |
| `:stable`  | Latest "good" and trusted version; perhaps `:rc` | ✅ Yes     | `docker pull per2jensen/scrubexif:stable` |
| `:dev`     | Development version; may be broken or incomplete | ❌ No      | `docker run --rm --read-only --security-opt no-new-privileges --tmpfs /tmp scrubexif:dev` |

🔄 The release pipeline automatically updates build-history.json, which contains metadata for each uploaded image.

> Pull Images

Versioned image:

```bash
VERSION=0.7.21; docker pull per2jensen/scrubexif:$VERSION
```

Pull the latest `stable` release (when available)

```bash
docker pull per2jensen/scrubexif:stable
```

✔️ All `:0.5.x` and `:stable` images run the test suite successfully as part of the release pipeline.

>`:dev` → Bleeding edge development, **only built >locally**, not pushed to Docker Hub

🧼 Run to scrub all .jpg and .jpeg files in the current directory

```bash
VERSION=0.7.21; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION
```

🛠️ Show version and help

```bash
VERSION=0.7.21; docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  per2jensen/scrubexif:$VERSION --version
VERSION=0.7.21; docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  per2jensen/scrubexif:$VERSION --help
```

## Viewing Metadata

To inspect the metadata of an image before/after scrubbing:

```bash
exiftool "image.jpg"
```

Inside the container (optional):

Observe the "/photos" in the filename, that is because the container has your $PWD mounted on /photos.

```bash
VERSION=0.7.21; docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  --entrypoint exiftool \
  per2jensen/scrubexif:$VERSION "/photos/image.jpg"
```

## Inspecting the Image Itself

To view embedded labels and metadata:

```bash
VERSION=0.7.21; docker inspect per2jensen/scrubexif:$VERSION | jq '.[0].Config.Labels'
```

You can also check the digest and ID:

```bash
VERSION=0.7.21; docker image inspect per2jensen/scrubexif:$VERSION --format '{{.RepoDigests}}'
```

## Image Signing and Supply Chain Verification


### The problem cosign solves

When you pull a Docker image you are trusting that the bytes you download are exactly what the author intended. Without a signature, three things can go wrong without you knowing: the image could be tampered with on Docker Hub, a compromised build machine could have injected malicious layers, or someone could push a rogue image to the same repository name. A signed image closes all three gaps.

`scrubexif` uses [cosign](https://github.com/sigstore/cosign) keyless signing via the [Sigstore](https://sigstore.dev) public infrastructure. Every release image (starting with 0.7.16) is signed by the GitHub Actions runner that built it, using a short-lived certificate issued by Sigstore's certificate authority and anchored to a GitHub OIDC token. The signature and certificate are recorded permanently in the [Rekor](https://rekor.sigstore.dev) transparency log — a public, append-only audit ledger. No long-lived private key exists anywhere; there is nothing to leak, rotate, or protect.

When you verify an image, cosign checks three things simultaneously:

1. The cryptographic signature matches the image digest — confirming the image was not modified after signing
2. The signing certificate was issued to the exact GitHub Actions workflow in this repository — confirming it was not signed by some other party
3. The certificate and signature are present in the Rekor transparency log — confirming the signature was made publicly and cannot be silently revoked

### Installing cosign

**Linux (amd64):**
```bash
curl -sSfL https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64 \
  -o /usr/local/bin/cosign
chmod +x /usr/local/bin/cosign
```

**macOS (Homebrew):**
```bash
brew install cosign
```

**Windows:**
```powershell
winget install sigstore.cosign
```

Or download a binary directly from the [cosign releases page](https://github.com/sigstore/cosign/releases).

### Verifying a release image

Replace `0.7.16` with the version you pulled:

```bash
cosign verify per2jensen/scrubexif:0.7.16 \
  --certificate-identity-regexp="https://github.com/per2jensen/scrubexif" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

A successful result prints a JSON array containing the signing certificate details, followed by a confirmation line. The key fields to look at in the certificate:

| Field | What it proves |
|---|---|
| `Build Signer URI` | The exact workflow file that performed the signing |
| `GitHub Workflow SHA` | The Git commit the image was built from |
| `Run Invocation URI` | A direct link to the GitHub Actions run |
| `Runner Environment` | `github-hosted` — signed on GitHub's own infrastructure |
| `OIDC Issuer` | `token.actions.githubusercontent.com` — GitHub issued the identity token |
| `Source Repository URI` | `https://github.com/per2jensen/scrubexif` — your expected repository |

If any of these fields are wrong or missing, the verification fails. You cannot spoof them — they are bound into the certificate by Sigstore at signing time.

### Verifying the SBOM attestation

Each release also attaches the SPDX SBOM as a signed [in-toto attestation](https://in-toto.io/) directly to the image in the registry. You can retrieve and verify it:

```bash
cosign verify-attestation per2jensen/scrubexif:0.7.16 \
  --type spdxjson \
  --certificate-identity-regexp="https://github.com/per2jensen/scrubexif" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" \
  | jq '.payload | @base64d | fromjson | .predicate.name'
```

This confirms that the SBOM was produced by the same workflow that built the image, not added separately after the fact.

### Checking the Rekor transparency log entry

Every release's `doc/build-history.json` records the Rekor log URL for that release. You can also look it up directly:

```bash
# Get the image digest
DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' \
  per2jensen/scrubexif:0.7.16 | cut -d'@' -f2)

# Search Rekor for entries matching this digest
rekor-cli search --sha "$DIGEST"
```

Or simply visit the URL recorded in `build-history.json` under `cosign.rekor_log_entry` for that release — for example, the 0.7.16 entry is at `https://search.sigstore.dev/?logIndex=1203521350`.

### What a verified certificate looks like

Here is the actual certificate from the `0.7.16` release for reference:

```
Build Signer URI:          https://github.com/per2jensen/scrubexif/.github/workflows/release.yml@refs/heads/main
Build Signer Digest:       df28a7e75b33da47830aefe715a53dad738fc5fa
GitHub Workflow Trigger:   workflow_dispatch
GitHub Workflow Name:      Manual Docker Release
GitHub Workflow Repository: per2jensen/scrubexif
Runner Environment:        github-hosted
Source Repository URI:     https://github.com/per2jensen/scrubexif
Source Repository Ref:     refs/heads/main
OIDC Issuer:               https://token.actions.githubusercontent.com
Run Invocation URI:        https://github.com/per2jensen/scrubexif/actions/runs/23804852692/attempts/1
```

### Why sign by digest, not tag?

Docker tags are mutable — the same tag can point to a different image at any time. `scrubexif` signs the image by its immutable `sha256` digest, so the signature is permanently bound to a specific set of bytes. Even if someone pushed a different image under the same tag, the signature would not match and verification would fail.

### Supply chain artefacts in build-history.json

From release `0.7.16` onwards, each entry in `doc/build-history.json` includes:

```json
"cosign": {
  "signed": true,
  "rekor_log_entry": "https://search.sigstore.dev/?logIndex=1203521350",
  "image_digest": "per2jensen/scrubexif@sha256:b48daee1..."
},
"sbom": {
  "file": "sbom-0.7.16.spdx.json",
  "release_asset_url": "https://github.com/per2jensen/scrubexif/releases/download/v0.7.16/sbom-0.7.16.spdx.json"
},
"build": {
  "runner": "Linux-X64",
  "github_run_id": "23804852692",
  "github_run_url": "https://github.com/per2jensen/scrubexif/actions/runs/23804852692"
}
```

This gives every release a permanent, human-readable audit trail linking the Docker image digest to the exact source commit, the CI run that built it, the Sigstore transparency log entry, and the SBOM.

## Dev setup

On Ubuntu, some extras are needed

```bash
sudo apt-get update && sudo apt-get install -y exiftool
python -m pip install --upgrade pip
pip install pytest
```

## Test Image

To verify that a specific scrubexif Docker image functions correctly, the test suite supports containerized testing using any image tag. By default, it uses the local tag  `scrubexif:dev` for testing. You can override this with the `SCRUBEXIF_IMAGE` environment variable.

🔧 Default behavior

When running pytest, the following fallback is used if no override is set:

IMAGE_TAG = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")

This means that the tests will attempt to run:

`docker run --read-only --security-opt no-new-privileges ... scrubexif:dev ...`

A good methodology is:

```bash
make dev-clean
make test
```

## License

Licensed under GNU GENERAL PUBLIC LICENSE v3, see the supplied file "LICENSE" for details.

THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW, not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See section 15 and section 16 in the supplied "LICENSE" file.

## Related Tools

📸 [Exiftool](https://github.com/exiftool/exiftool) - The wonderful swiss knife of metadata handling
📸 [file-manager-scripts](https://github.com/per2jensen/file-manager-scripts) — Nautilus context menu integrations  
📸 image-scrubber — Browser-based interactive metadata removal  
📸 jpg-exif-scrubber — Python tool that strips all metadata (no preservation)

`scrubexif` focuses on **automated, container-friendly workflows** with **safe defaults** for photographers.

## Feedback

Suggestions, issues, or pull requests are always welcome.  
Maintained by **Per Jensen**

## Project Homepage

Source code, issues, and Dockerfile available on GitHub:

👉 [https://github.com/per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

📦 **Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

## Reference

### CLI Options (`scrubexif.py`)

All arguments are passed to `python3 -m scrubexif.scrub` inside the container.

| Option | Description |
|---|---|
| `--clean-inline` | Destructive, in-place scrubbing. Required when passing positional file/dir arguments. |
| `--comment TEXT` | Stamp a comment into EXIF and XMP (replaces existing values). |
| `--copyright TEXT` | Stamp a copyright notice into EXIF and XMP (replaces existing values). |
| `--debug` | Shortcut for `--log-level debug`; enables extra diagnostic logging. |
| `--delete-original` | Auto mode only. Delete originals instead of moving them to `/photos/processed`. |
| `--dry-run` | Print planned actions without modifying files. |
| `files...` | Positional files/dirs (relative to `/photos` in Docker). Requires `--clean-inline`. |
| `--from-input` | Auto mode. Reads `/photos/input`, writes to `/photos/output`, and moves originals to `/photos/processed` (or deletes with `--delete-original`). |
| `--log-level {debug,info,warn,error,crit}` | Set log verbosity (default: `info`). |
| `--max-files N` | Limit number of eligible files scrubbed in the current run. |
| `--on-duplicate {delete,move}` | Auto/default mode duplicate handling. `delete` removes input; `move` sends duplicates to `/photos/errors`. |
| `--output PATH` | Override output directory in default safe mode. Not allowed with `--from-input` or `--clean-inline`. |
| `--paranoia` | Maximum metadata scrubbing (removes ICC profile). |
| `--preview` | Preview scrub effect on one file without modifying it (implies `--dry-run` + `--show-tags both`). |
| `-q`, `--quiet` | Suppress all output on success. |
| `-r`, `--recursive` | Recurse into directories when scanning. |
| `--rename FORMAT` | Rename output files using a format string. Tokens: `%r` (hex), `%u` (UUID), `%n` (counter), `%Y` (year from EXIF), `%m` (month from EXIF). See [`doc/rename-spec.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/rename-spec.md). |
| `--show-container-paths` | Include container paths alongside host paths in output. |
| `--show-tags {before,after,both}` | Print metadata before/after scrub for each file. |
| `--stable-seconds SECS` | Only process files whose mtime age is at least this many seconds (default: 120). |
| `--state-file PATH\|disabled` | Override stability tracking path or disable persistence (`disabled`, `none`, or `-`). |
| `-v`, `--version` | Show version and license. |

**Constraints**

- `--from-input` cannot be used with `--clean-inline` or `--output`.
- Positional `files...` require `--clean-inline`.
