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
    - [Manual mode (default)](#manual-mode-default)
      - [Scrub specific files](#scrub-specific-files)
      - [Scrub all JPEGs in current directory](#scrub-all-jpegs-in-current-directory)
      - [Recursively scrub nested folders](#recursively-scrub-nested-folders)
    - [Auto mode (`--from-input`)](#auto-mode---from-input)
      - [Example](#example)
      - [Duplicate Handling (auto mode)](#duplicate-handling-auto-mode)
  - [Options](#options)
  - [Environment variables](#environment-variables)
    - [`ALLOW_ROOT`](#allow_root)
    - [`SCRUBEXIF_AUTOBUILD`](#scrubexif_autobuild)
    - [`SCRUBEXIF_ON_DUPLICATE`](#scrubexif_on_duplicate)
    - [`SCRUBEXIF_STABLE_SECONDS`](#scrubexif_stable_seconds)
    - [`SCRUBEXIF_STATE`](#scrubexif_state)
    - [Summary](#summary)
    - [Examples](#examples)
  - [Features](#features)
    - [Metadata Preservation Strategy](#metadata-preservation-strategy)
    - [`--paranoia` Mode](#--paranoia-mode)
    - [Example](#example-1)
    - [Inspecting Metadata with `--show-tags`](#inspecting-metadata-with---show-tags)
    - [Note on `--dry-run`](#note-on---dry-run)
    - [Usage Examples](#usage-examples)
    - [Preview Mode (`--preview`)](#preview-mode---preview)
    - [Typical Use](#typical-use)
  - [What It Cleans](#what-it-cleans)
  - [Work on stable files](#work-on-stable-files)
    - [Stability gate](#stability-gate)
    - [State tracking](#state-tracking)
    - [Temp/partial file filter](#temppartial-file-filter)
      - [Scope](#scope)
    - [Configuration](#configuration)
  - [Known limitations](#known-limitations)
  - [Docker Images](#docker-images)
  - [User Privileges and Running as Root](#user-privileges-and-running-as-root)
  - [Recommendations](#recommendations)
    - [Hardening](#hardening)
    - [Use Real Directories for Mounts](#use-real-directories-for-mounts)
    - [Run as a Non-Root User](#run-as-a-non-root-user)
    - [Always Pre-Check Mount Paths](#always-pre-check-mount-paths)
    - [Keep Metadata You Intend to Preserve Explicit](#keep-metadata-you-intend-to-preserve-explicit)
  - [Viewing Metadata](#viewing-metadata)
  - [Inspecting the Image Itself](#inspecting-the-image-itself)
  - [Example Integration](#example-integration)
  - [Build Locally](#build-locally)
  - [Test Image](#test-image)
  - [License](#license)
  - [Related Tools](#related-tools)
  - [Feedback](#feedback)
  - [Reference](#reference)
    - [CLI options](#cli-options)
    - [Environment variables](#environment-variables-1)
  - [Project Homepage](#project-homepage)

## Quick Start

There are **two modes**:

### Build & Run Locally

```bash
# build an image from the Dockerfile in this repo
docker build -t scrubexif:local .

# inspect CLI usage exported by python -m scrubexif.scrub
docker run --rm scrubexif:local --help

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

### Manual mode (default)

Manually scrub one or more `.jpg` / `.jpeg` files from the current directory.

#### Scrub specific files

```bash
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION "file1.jpg" "file2.jpeg"
```

#### Scrub all JPEGs in current directory

```bash
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION
```

#### Recursively scrub nested folders

```bash
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --recursive
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
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/output:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  per2jensen/scrubexif:$VERSION --from-input
```

#### Duplicate Handling (auto mode)

By default, if a file with the same name already exists in the output folder, it is treated as a **duplicate**:

- `--on-duplicate delete` (default): Skips scrubbing and deletes the original from input.
- `--on-duplicate move`: Moves the duplicate file to `/photos/errors` for inspection.

This ensures output is not overwritten and prevents silently skipping files.

The reason to delete a duplicate by default is that the files are probably not that important, mostly used to give viewers a quick glance. It also conserves disk space.

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

📌 **Observe**  the -v "$PWD/errors:/photos/errors" volume specification needed for the --on-duplicate move option.

## Options

- `--delete-original` — delete originals instead of moving them
- `--on-duplicate {delete|move}` - delete or move a duplicate
- `--dry-run` - show what would be scrubbed, but don’t write files
- `--debug` - shortcut for `--log-level debug`; also enables extra diagnostic logging (takes precedence if `--log-level` is also supplied)
- `--log-level` - choices=["debug", "info", "warn", "error", "crit"], default="info"
- `--max-files` - limit number of files to scrub (useful for testing or safe inspection)
- `--paranoia` - maximum metadata scrubbing, removes ICC profile including it's (potential) fingerprinting vector
- `--preview` - preview scrub effect on one file without modifying it (shows before/after metadata)
- `-r`, `--recursive` - Recurse into directories
- `--show-tags` - choices=["before", "after", "both"], show metadata tags before, after, or both for each image
- `--stable-seconds` <secs> - Number of seconds a file must not change before being processed. Default is 120 secs
- `-v`, `--version` - show version and license

## Environment variables

### `ALLOW_ROOT`

Must be `1` for `scrubexif` to operate as root.
If unset, running as UID 0 will exit with an error.

Example:

```sh
docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  --user 0 -e ALLOW_ROOT=1 \
  scrubexif:dev
```

### `SCRUBEXIF_AUTOBUILD`

Applies to development and testing only.

Controls whether `pytest` automatically builds the `scrubexif:dev` image if missing.

| Value | Meaning |
|-------|--------|
| `1` (default) | Auto-build `scrubexif:dev` on first test run |
| `0` | Do **not** build; tests fail if the image is missing |

Examples:

```sh
# Build image explicitly
make dev

# Standard test run (auto-build allowed)
pytest

# Strict run: fail if dev image is missing
SCRUBEXIF_AUTOBUILD=0 pytest
```

### `SCRUBEXIF_ON_DUPLICATE`

Applies to `--from-input` (auto mode).

Controls how duplicate files are handled.

| Value | Behavior |
|-------|---------|
| `delete` (default) | Delete duplicate input file |
| `move` | Move duplicate to `/photos/errors/` |

---

### `SCRUBEXIF_STABLE_SECONDS`

Applies to `--from-input` (auto mod
e).

Minimum stability window before a file is processed.
A file must remain unchanged for this duration.

Default: `120`

CLI override:

```sh
scrub --from-input --stable-seconds 0
```

### `SCRUBEXIF_STATE`

Applies to `--from-input` (auto mode).

Path to JSON state file used to track file stability across runs.
Allows efficient incremental scrubbing.

Defaults to disabled if not writable (container read-only mode fallback).

CLI override:

```sh
scrub --from-input --state-file /tmp/state.json
```

Disable state file entirely:

```sh
scrub --from-input --state-file disabled
```

### Summary

| Variable | Purpose |
|---------|--------|
| `ALLOW_ROOT` | Permit execution as root |
| `SCRUBEXIF_AUTOBUILD` | Auto-build dev image when running tests |
| `SCRUBEXIF_ON_DUPLICATE` | Duplicate file policy (`delete`/`move`) |
| `SCRUBEXIF_STABLE_SECONDS` | Stability window before scrubbing |
| `SCRUBEXIF_STATE` | Path to persistent mtime state tracking (supports CLI override) |

### Examples

Scrub all `.jpg` files in subdirectories:

```bash
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --recursive
```

Dry-run (preview only):

```bash
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --dry-run
```

Mix recursion and dry-run:

```bash
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION --recursive --dry-run
```

📌 **Observe**  In manual mode, files are scrubbed in-place and will overwrite the originals. Duplicate handling (e.g. move/delete) is not applicable here.

## Features

- Case insensitive, works on .jpg, .JPG, .jpeg & .JPEG
- Removes most EXIF, IPTC, and XMP metadata
- **Preserves** useful photography tags:
  - `Title`
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

By default, `scrubexif` preserves important non-private metadata such as **exposure**, **lens**, **ISO**, and **color profile** information. This ensures that images look correct in color-managed environments (e.g. Apple Photos, Lightroom, web browsers with ICC support).

For users who require maximum privacy, an optional `--paranoia` mode is available.

### `--paranoia` Mode

When enabled, `--paranoia` disables color profile preservation and removes fingerprintable metadata like ICC profile hashes (`ProfileID`). This may degrade color rendering on some devices, but ensures all embedded fingerprint vectors are scrubbed.

| Mode         | ICC Profile | Color Fidelity | Privacy Level |
|--------------|-------------|----------------|---------------|
| *(default)*  | ✅ Preserved   | ✅ High         | ⚠️ Moderate |
| `--paranoia` | ❌ Removed     | ❌ May degrade  | ✅ Maximum  |

### Example

```bash
# Safe color-preserving scrub (default)
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev image.jpg

# Maximum scrub, removes the ICC profile
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev image.jpg --paranoia
```

Note: The ICC profile includes values like ProfileDescription, ColorSpace, and ProfileID. The latter is a hash that may vary by device or editing software.

### Inspecting Metadata with `--show-tags`

The `--show-tags` option lets you inspect metadata **before**, **after**, or **both before and after** scrubbing. This is useful for:

- Auditing what data is present in your photos
- Verifying that scrubbed output removes private metadata
- Confirming what remains (e.g. lens info, exposure, etc.)

### Note on `--dry-run`

If you want to **inspect metadata only without modifying any files**, you must pass `--dry-run`.

Without `--dry-run`, scrubbing is performed as usual.

### Usage Examples

```bash
# See tags BEFORE scrub (scrub still happens)
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev image.jpg --show-tags before

# See both BEFORE and AFTER (scrub still happens)
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev image.jpg --show-tags both

# Just show metadata, DO NOT scrub
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev image.jpg --show-tags before --dry-run
```

Works in both modes

  Manual mode: for individual files or folders

  Auto mode (--from-input): applies to all JPEGs in `input`directory.

🛡 Tip: Combine `--dry-run --paranoia --show-tags before` to verify level of metadata removal before commiting.

### Preview Mode (`--preview`)

The `--preview` option lets you **safely simulate** the scrubbing process on a **single** JPEG **without modifying the original file**.

This mode:

- Copies the original image to a temporary file
- Scrubs the copy in memory
- Shows metadata **before and/or after** scrubbing
- Deletes the temp files automatically
- Never alters the original image

### Typical Use

```bash
docker run --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:dev test.jpg --preview
```

🛡 Tip: Combine `--preview --paranoia` to verify the color profile tags including the ProfileId tag has been scrubbed. 

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

A JSON file /photos/.scrubexif_state.json stores {path: {size, mtime, seen}}.

Each run updates entries for observed files and prunes paths that no longer exist.

Delete this file to reset history.

### Temp/partial file filter

Filenames with common temp prefixes/suffixes are always skipped: prefixes ., ~, ._; suffixes .tmp, .part, .partial, .crdownload, .download, .upload, .cache, .swp, .swx, .lck, or names ending with any of those (e.g., photo.jpg.uploading).

These are still recorded in state but never processed while they look temporary.

#### Scope

Applies to auto mode (--from-input) only. Manual mode stays unchanged.

Summary now reports “Skipped (unstable)”. Duplicates/error logic unaffected.

### Configuration

CLI: --stable-seconds N.

Env: SCRUBEXIF_STABLE_SECONDS if the flag is omitted. Default 120.

## Known limitations

> Symlinked input paths are not detected inside the container

If you bind-mount a symbolic link (e.g. `-v $(pwd)/symlink:/photos/input`), Docker resolves the symlink before passing it to the container. This means:

- The container sees `/photos/input` as a normal directory.
- `scrubexif` cannot detect it was originally a symlink.
- For safety, avoid mounting symbolic links to any of the required directories.

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
VERSION=0.7.4; docker pull per2jensen/scrubexif:$VERSION
```

Pull the latest `stable` release (when available)

```bash
docker pull per2jensen/scrubexif:stable
```

✔️ All `:0.5.x` and `:stable` images run the test suite successfully as part of the release pipeline.

>`:dev` → Bleeding edge development, **only built >locally**, not pushed to Docker Hub

🧼 Run to scrub all .jpg and .jpeg files in the current directory

```bash
VERSION=0.7.4; docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:$VERSION
```

🛠️ Show version and help

```bash
VERSION=0.7.4; docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  per2jensen/scrubexif:$VERSION --version
VERSION=0.7.4; docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  per2jensen/scrubexif:$VERSION --help
```

## User Privileges and Running as Root

By default, the `scrubexif` container runs as user ID 1000, not root. This is a best-practice security measure to avoid unintended file permission changes or elevated access.

🧑 Default Behavior

```bash
docker run --rm --read-only --security-opt no-new-privileges --tmpfs /tmp scrubexif:dev
```

Runs the container as UID 1000 by default

Ensures safer file operations on mounted volumes

Compatible with most host setups

👤 Running as a Custom User

You can specify a different UID (e.g., match your local user) using the --user flag:

```bash
docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  --user $(id -u) \
  scrubexif:dev
```

This ensures created or modified files match your current user permissions.

🚫 Root is Blocked by Default

Running the container as root (UID 0) is explicitly disallowed to prevent unsafe behavior:

```bash
docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  --user 0 \
  scrubexif:dev
# ❌ Running as root is not allowed unless ALLOW_ROOT=1 is set.
```

To override this safeguard, set the following environment variable:

```bash
docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  --user 0 \
  -e ALLOW_ROOT=1 \
  scrubexif:dev
```

  ⚠️ Use this option only if you know what you're doing. Writing files as root can cause permission issues on the host system.

## Recommendations

To ensure smooth and safe operation when using `scrubexif`, follow these guidelines:

### Hardening

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

#### What these flags do (and how they can bite you)

- **`--read-only`** mounts the container root filesystem as read-only so the image can’t be mutated at runtime. All app writes *must* land in writable mounts such as `/photos/*`.  
  - *Watch out*: if you rely on a custom `--state-file` path or duplicate handling output, make sure that path lives on a mounted volume. Docker normally provisions `/tmp` as a tmpfs when `--read-only` is used, but other runtimes might require an explicit `--tmpfs /tmp`.
- **`--security-opt no-new-privileges`** blocks any attempt to gain more privileges (e.g. via setuid binaries).  
  - *Watch out*: commands that expect to invoke `sudo`, or wrappers that rely on setuid/setgid helpers inside the container, will fail silently. `scrubexif` doesn’t need them, but your surrounding tooling might.

If you orchestrate with Kubernetes, set `readOnlyRootFilesystem: true` and `allowPrivilegeEscalation: false` to mirror these flags. Always verify that mounted host directories (input/output/processed/errors/state) stay writable by the container UID when the root filesystem is locked down.

### Use Real Directories for Mounts

Avoid using symbolic links for input, output, or processed paths. Due to Docker's volume resolution behavior, symlinks are flattened and no longer detectable inside the container.

Instead:

```bash
docker run --read-only --security-opt no-new-privileges \
           --tmpfs /tmp \
           -v "$PWD/input:/photos/input" \
           -v "$PWD/output:/photos/output" \
           -v "$PWD/processed:/photos/processed" \
           scrubexif:dev --from-input
```

### Run as a Non-Root User

`scrubexif` checks directory writability. If you mount a directory as root-only, and the container runs as a non-root user (recommended), it will detect and exit cleanly.

Tip: Use --user 1000 or ensure mounted dirs are writable by UID 1000.

### Always Pre-Check Mount Paths

Ensure the input, output, and processed directories:

  Exist on the host

  Are not files or symlinks

  Are writable by the container’s user

Otherwise, scrubexif will fail fast with a clear error message.

### Keep Metadata You Intend to Preserve Explicit

Configure your `scrub.py` to define which EXIF tags to preserve, rather than relying on defaults if privacy is critical.

## Viewing Metadata

To inspect the metadata of an image before/after scrubbing:

```bash
exiftool "image.jpg"
```

Inside the container (optional):

Observe the "/photos" in the filename, that is because the container has your $PWD mounted on /photos.

```bash
VERSION=0.7.4; docker run --rm --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  --entrypoint exiftool \
  per2jensen/scrubexif:$VERSION "/photos/image.jpg"
```

## Inspecting the Image Itself

To view embedded labels and metadata:

```bash
VERSION=0.7.4; docker inspect per2jensen/scrubexif:$VERSION | jq '.[0].Config.Labels'
```

You can also check the digest and ID:

```bash
VERSION=0.7.4; docker image inspect per2jensen/scrubexif:$VERSION --format '{{.RepoDigests}}'
```

## Example Integration

This image is ideal for:

- Web galleries
- Dog show photo sharing
- Social media publishing
- Backup pipelines before upload
- Static site generators like Hugo/Jekyll

## Build Locally

```bash
docker build -t scrubexif .
```

## Test Image

To verify that a specific scrubexif Docker image functions correctly, the test suite supports containerized testing using any image tag. By default, it uses the local tag  `scrubexif:dev` for testing. You can override this with the `SCRUBEXIF_IMAGE` environment variable.

🔧 Default behavior

When running pytest, the following fallback is used if no override is set:

IMAGE_TAG = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")

This means that the tests will attempt to run:

`docker run --read-only --security-opt no-new-privileges ... scrubexif:dev ...`

If no such local image exists, the test will fail.

## License

Licensed under the GNU General Public License v3.0 or later  
See the `LICENSE` file in this repository.

## Related Tools

📸 [file-manager-scripts](https://github.com/per2jensen/file-manager-scripts) — Nautilus context menu integrations  
📸 image-scrubber — Browser-based interactive metadata removal  
📸 jpg-exif-scrubber — Python tool that strips all metadata (no preservation)

`scrubexif` focuses on **automated, container-friendly workflows** with **safe defaults** for photographers.

## Feedback

Suggestions, issues, or pull requests are always welcome.  
Maintained by **Per Jensen**

## Reference

### CLI options

- `--delete-original` — Delete the input image after a successful auto-mode scrub.
- `--files` — Optional list of files or directories (relative to `/photos` when running in Docker).
- `--from-input` — Run in auto mode, consuming `/photos/input` and emitting to `/photos/output`.
- `--max-files N` — Limit the number of eligible files scrubbed in the current run.
- `--on-duplicate {delete,move}` — Auto-mode duplicate policy; default is `delete`.
- `--paranoia` — Remove ICC profiles as well as the standard EXIF/IPTC/XMP payload.
- `--preview` — Scrub a temporary copy to preview metadata changes without touching the original file.
- `-r`, `--recursive` — Recurse into subdirectories when scanning in manual mode.
- `--show-tags {before,after,both}` — Dump metadata before/after scrubbing.
- `--stable-seconds N` — Require files to be at least `N` seconds old (default `120`).
- `--state-file PATH|disabled` — Override the stability state JSON path, or disable persistence entirely.
- Non-functional options
- `--log-level {debug,info,warn,error,crit}` — Set logger verbosity (`info` by default).
- `--debug` — Convenience flag that forces `--log-level debug` and prints additional diagnostics.
- `--dry-run` — Describe planned actions without invoking ExifTool.
- `-v`, `--version` — Print version and license information, then exit.

### Environment variables

- `ALLOW_ROOT` — Set to `1` to allow running as UID 0 inside the container.
- `SCRUBEXIF_AUTOBUILD` — When truthy, tests auto-build the `scrubexif:dev` image if missing.
- `SCRUBEXIF_ON_DUPLICATE` — Default duplicate policy (`delete` or `move`) when the CLI switch is omitted.
- `SCRUBEXIF_STABLE_SECONDS` — Default stability window for auto mode.
- `SCRUBEXIF_STATE` — Preferred state-file path; must be writable to enable persistent stability tracking.

## Project Homepage

Source code, issues, and Dockerfile available on GitHub:

👉 [https://github.com/per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

📦 **Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)
