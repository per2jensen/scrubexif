# scrubexif

<div align="center">

<!-- 📦 Project Metadata -->
<a href="https://github.com/per2jensen/scrubexif/releases"><img alt="Tag" src="https://img.shields.io/github/v/tag/per2jensen/scrubexif"/></a>
<a href="https://github.com/per2jensen/scrubexif/actions/workflows/CI.yml"><img alt="CI" src="https://github.com/per2jensen/scrubexif/actions/workflows/CI.yml/badge.svg"/></a>
<a href="https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md#image-signing-and-supply-chain-verification">
  <img alt="cosign badge" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/per2jensen/scrubexif/main/doc/cosign_badge.json"/>
</a>
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

**GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

**Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

## License

GPL-3.0-or-later

Licensed under GNU GENERAL PUBLIC LICENSE v3, see the supplied file "LICENSE" for details.

THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW, not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See section 15 and section 16 in the supplied "LICENSE" file.

## High‑trust EXIF removal with a transparent supply chain

Removes common privacy-sensitive JPEG metadata—including GPS coordinates, serial numbers, and maker notes—using a byte-level APP wipe followed by an allowlisted metadata rebuild.

Verify the integrity and origin of every published release and refresh image using Sigstore signatures, and review the accompanying vulnerability scan.

**Output safety behavior**: `scrubexif` builds each JPEG in a private temporary
directory outside the output folder, then independently parses the completed
JPEG against the active metadata policy. Only those audited bytes are copied to
a hidden `.part` file in the output folder and atomically published. A failed or
non-compliant result is never published under its intended output filename. In
output-directory modes, existing destination entries are never overwritten.

This is a failure-handling safeguard, **not a guarantee** that an image contains no privacy-sensitive information. Default mode preserves a few selected technical tags and the ICC profile. `--paranoia` removes JPEG APP metadata more aggressively and writes one canonical JFIF APP0 header, but scrubexif does not inspect visible image content, filenames unless `--rename` is used, sidecar files, or unsupported future formats. Always check the command’s exit status before publishing output.

> **High-stakes use**: Independently verify the resulting files before publishing or distributing them.

Detailed behavior and CLI reference → [`DETAILS.md`](doc/DETAILS.md)

## Quick Start

### Simple command line usage (default safe mode, non-destructive)

Scrub all JPEGs in the **current directory** (`$PWD`) and write cleaned copies to `$PWD/output/`:

````bash
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run --rm \
  --user "$RUN_AS_UID:$RUN_AS_GID" \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:0.7.28
````

**Verify the results:**
Verify that the sensitive metadata you expect to remove is absent and that only the intended technical tags remain.

Worth noting:

- scans **your current directory** (`$PWD`) for `*.jpg` / `*.jpeg` (also in capital letters)
- writes scrubbed copies to **$PWD/output/** (or a custom `--output` dir)  
- leaves the originals untouched in **$PWD/**
- refuses to run if the `$PWD/output` directory already exists
- prints host paths by default (use `--show-container-paths` to include `/photos/...` paths)

### Demo script

The [demo script](https://github.com/per2jensen/scrubexif/blob/main/scripts/scrubexif-demo.sh) shows how to non-destructively scrub JPEGs in a directory and output the scrubbed JPEGs to another directory. Features:

- Run as the user calling the script, root is not allowed
- write a log in /tmp/scrubexif.log (keep it under 100k)
- sanity checks
- send notifications to desktop

The demo requires the schema-2 summary contract from scrubexif 0.8.0 or newer.
It runs `per2jensen/scrubexif:0.8.0` by default; set `SCRUBEXIF_IMAGE` to use a
different schema-2-compatible image.

### Write to specific directory

Use `-o` to control where scrubbed files are written.

**Output to a subdirectory of `$PWD`** — `scrubexif` creates it if it does not exist:

````bash
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run --rm \
    --user "$RUN_AS_UID:$RUN_AS_GID" \
    -v "$PWD:/photos" \
    per2jensen/scrubexif:0.7.28 \
    -o scrubbed
````

Scrubbed files are written to `$PWD/scrubbed/`. The run is refused if `scrubbed/` already exists
(safety guard — use `-o` with a bind-mount instead if you need to reuse a directory).

**Output to an arbitrary host directory** — mount it independently and pass the container path to `-o`:

````bash
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run --rm \
  --user "$RUN_AS_UID:$RUN_AS_GID" \
  -v "$PWD:/photos" \
  -v "/tmp/scrub-test:/scrubbed" \
  per2jensen/scrubexif:0.7.28 \
  -o /scrubbed
````

`-v "/tmp/scrub-test:/scrubbed"` maps `/tmp/scrub-test` on the host to `/scrubbed` inside the
container. `-o /scrubbed` tells `scrubexif` to write there. Because `-o` was explicitly supplied,
`scrubexif` accepts the directory even though it already exists (created by Docker as the mount point).

Scrubbed photos end up in `/tmp/scrub-test/` on the host.

> **Note:** mount the output directory at a top-level container path (e.g. `/scrubbed`) rather
> than nested under `/photos` (e.g. `/photos/scrubbed`). Nesting requires `$PWD` to be writable
> so Docker can create the mount point there.

### Failure handling (important)

`Scrubexif` is designed to not place an unscrubbed JPEG into an output directory.
If a scrub fails, no output file should be created for that JPEG; processing continues for the remaining files, and the final exit status is nonzero.

What happens on failed scrubs depends on the mode `scrubexif` is run in:

- **Default safe mode** (the one-liner): failed files stay in the original directory, and **no file is written to the output directory** for those failures.
- **Auto mode** (`--from-input`): Failed files are archived in processed/ for inspection when possible. If archival also fails, the original remains in place and the run reports an error.
- **Manual (destructive) in-place** (`--clean-inline`): a failure leaves the original unchanged; there is no output directory involved.

### Hardened (destructive) in-line scrub (current directory)

Same idea, but with container hardening and in-line (destructive) overwrite:

```bash
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run -it --rm \
  --user "$RUN_AS_UID:$RUN_AS_GID" \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:0.7.28 --clean-inline
```

---

### GNOME Files (Nautilus) wrapper

I also maintain a [scrubexif wrapper for GNOME Files](https://github.com/per2jensen/file-manager-scripts/blob/main/scripts/scrubexif.sh), which I use myself almost every day. It lets you select one or more JPEGs and run `scrubexif` from the file manager's right-click **Scripts** menu.

The wrapper validates the selection, processes all selected files in one Docker run, handles FUSE/NFS/CIFS locations through a temporary local directory, and reports the result with a desktop notification and log. It uses destructive `--clean-inline` mode, so the selected files are replaced by their scrubbed versions.

See the [file-manager-scripts repository](https://github.com/per2jensen/file-manager-scripts) for prerequisites and installation instructions. The script belongs in `~/.local/share/nautilus/scripts/` and must be executable.

### Filename sanitisation (`--rename`)

Filenames can leak as much as EXIF. A name like `2026-04-07_11-13-45.jpeg`
reveals the exact capture time, and prefixes like `D80_` identify the camera
body. `--rename` replaces the output filename with a format string of your
choice so the original identifying filename does not survive.

```bash
# Randomized filename — 8-character random hex name
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run -it --rm \
  --user "$RUN_AS_UID:$RUN_AS_GID" \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:0.7.28 --clean-inline --rename "%r8" --recursive

# Keep your camera prefix, remove the timestamp
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run -it --rm \
  --user "$RUN_AS_UID:$RUN_AS_GID" \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:0.7.28 --clean-inline --rename "D80_%r6" --recursive
```

`--paranoia` implies `--rename "%r8"` when no `--rename` is given.  
Common tokens: `%r` (random hex), `%u` (UUID), `%n` (sequential counter), `%Y` (year from EXIF), `%m` (month from EXIF).

Before modifying any file, scrubexif builds a complete collision-checked rename
plan in a bounded temporary SQLite database. It prints progress for long scans,
re-rolls random/UUID collisions up to three times, and aborts the whole batch if
a collision cannot be resolved. Final files are published with an atomic
no-overwrite operation. If a destination appears concurrently, scrubexif logs
the active-filesystem event, reserves another random name, and continues without
repeating the scrub; the concurrent file is never replaced.
Planning defaults to at most 250,000 files, 30 minutes, and 512 MiB of temporary
storage; the corresponding `--rename-plan-*` options can tune those safeguards.

Full specification → [`doc/rename-spec.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/rename-spec.md)

### Batch workflow (PhotoPrism / intake style)

Use auto mode with explicit input/output/processed directories:

```bash
mkdir input scrubbed processed errors
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run -it --rm \
  --user "$RUN_AS_UID:$RUN_AS_GID" \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/scrubbed:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  -v "$PWD/errors:/photos/errors" \
  per2jensen/scrubexif:0.7.28 --from-input
```

These are the physical directories used on your file system:

Uploads → `$PWD/input/`  
Scrubbed → `$PWD/scrubbed/`  
Originals → `$PWD/processed/` (or deleted with `--delete-original`)  
Verified duplicates → moved to `$PWD/errors/` by default; use explicit `--on-duplicate delete` to delete them

Same filename, different content → incoming original moved to `$PWD/errors/`; reported as a collision with a nonzero exit

Failed scrubs (e.g., corrupted files) → logged as failures; originals are moved to `$PWD/processed/` for inspection

Existing output that fails its privacy audit → left untouched; incoming source stays in `$PWD/input/`; run exits nonzero

Archive entries are never overwritten. If the usual filename is already occupied
in `processed/` or `errors/`, scrubexif preserves it and publishes the incoming
original under a fresh random-suffixed name. The source is removed only after the
archive copy has been safely published; if archival fails, the source stays in
place and the run reports an error. This also works when intake and archive are
on different filesystems or mounts.

### Data flow overview (auto mode: `--from-input`)

This flow diagram describes what happens **only in auto mode** (`--from-input`),
where four directories (`input/`, `output/`, `processed/`, `errors/`) are used.

Please observe these directories are named like this **inside the container**. Your physical directories in your file system are mapped when you run the `docker run ...` command. See the `-v ....` options in the above example.

```txt
[input/]  -->  <scrubexif> runs  -->  [output/]
                 |
                 +-->  [processed/]   (original JPEGs moved here after successful scrub,
                                       unless --delete-original is used)
                 |
                 +-->  [errors/]      (verified duplicates and same-name
                                       content collisions)
```

Meaning:

- `input/`
    New JPEGs arrive here (e.g. from uploads, for example PhotoSync).

- `output/`
    Scrubbed JPEGs with allowlisted technical metadata.

- `processed/`
    Original JPEGs moved here after scrub (or deleted when requested).

- `errors/`
    Receives verified duplicates under the default `move` policy and preserves
    same-name/different-content collisions for inspection.

### Build & Run Locally

```bash
# build the image from the Dockerfile in this repo
docker build -t scrubexif:local .

# show CLI usage (ENTRYPOINT runs python -m scrubexif.scrub)
docker run --rm scrubexif:local --help

# scrub the current directory with hardened defaults
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}
if [ "$RUN_AS_UID" -eq 0 ]; then
    echo "Running as root is not allowed"
    exit 1
fi
docker run -it --rm \
  --user "$RUN_AS_UID:$RUN_AS_GID" \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  scrubexif:local
```

Any arguments appended to `docker run … scrubexif:*` are forwarded to the underlying
`python3 -m scrubexif.scrub` entrypoint.

## Key Features

- Scrubexif is JPEG‑only by design. This avoids format‑specific edge cases and ensures predictable behavior.
- Allowlist-based scrubbing: jpegtran discards input JPEG APP metadata (including unknown/proprietary vendor segments), then ExifTool writes back a small allowlist of technical tags (exposure, ISO, focal length, orientation). JPEG/JFIF structural fields may remain or be regenerated
- Removes common privacy-sensitive metadata, including GPS coordinates, serial numbers, and maker notes
- Preserves color profiles (ICC) by default; normal mode re-embeds the ICC profile after the `jpegtran` strip
- Auto mode audits and compares same-name files before applying duplicate handling (`--on-duplicate move|fail|delete`; default `move`)
- Optional stability gate for hot upload directories  (e.g., PhotoSync, rclone, FTP uploads) (`--stable-seconds`, `--state-file`)
- Metadata inspection and dry-run support (`--show-tags`, `--preview`, `--dry-run`)
- Optional stamping of copyright and comment into EXIF/XMP (`--copyright`, `--comment`)
- Hardened container examples using a read-only root filesystem, a writable `/tmp` tmpfs, and no-new-privileges

## Acknowledgements

- [ExifTool](https://exiftool.org/) by Phil Harvey — used for metadata extraction and selective tag write-back (GPL-1.0-or-later / Artistic License)
- [jpegtran](https://ijg.org/) from libjpeg-turbo — used for lossless byte-level JPEG transformation (IJG / BSD licence)
- [sigstore/cosign](https://github.com/sigstore/cosign) used to sign/upload artifacts 
- [Syft](https://github.com/anchore/syft) used to generate a Software Bill Of Materials
- [Grype](https://github.com/anchore/grype) used for image vulnerability scanning
- [Ubuntu](https://ubuntu.com/) for the base image Scrubexif is based on
  
## Supply Chain Transparency

Every release image is **cryptographically signed** using [cosign](https://github.com/sigstore/cosign) keyless signing via the [Sigstore](https://sigstore.dev) public infrastructure. The signature binds an image digest to the identity of the GitHub Actions workflow that signed it, without a long-lived project signing key. Verification establishes image integrity and workflow provenance; it does not prove that the source, workflow, runner, or resulting image was free from compromise.

**Verify any release in one command** (requires [cosign](https://docs.sigstore.dev/cosign/system_config/installation/)):

```bash
cosign verify per2jensen/scrubexif:0.7.28 \
  --certificate-identity="https://github.com/per2jensen/scrubexif/.github/workflows/release.yml@refs/heads/main" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

Replace the tag with another immutable release version when needed. Refresh
tags use the separate workflow identity documented in DETAILS.

A successful verification confirms that the image digest was signed under the expected release-workflow identity. Cosign's displayed certificate and bundle details vary by version; inspect the workflow identity, source revision, and run information when shown.

Full details on installation, verification, and what the certificate fields mean → [`doc/DETAILS.md#image-signing-and-supply-chain-verification`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md#image-signing-and-supply-chain-verification)

The `:latest` image is rebuilt every Saturday from the exact source of the
latest stable release. A successful refresh is tested, scanned, signed, and
published with an immutable numeric suffix such as `:0.7.28-1`. Weekly
refreshes do not change the stable release version shown in this README; their
complete audit trail is recorded in `doc/build-history.json`, with compressed
SBOM and SARIF files stored under `doc/`.

**Additional supply-chain artefacts:**

- SPDX SBOM (`sbom-<version>.spdx.json`) — a GitHub Release asset for releases, a compressed repository audit file for refreshes, and a signed in-toto image attestation for both
- Grype vulnerability scan (`grype-results-<version>.sarif`) — a release asset or compressed refresh audit file and an upload to GitHub Security; publication is blocked on any High or Critical finding
- `doc/build-history.json` — tracks every release and refresh with Git commit, image digest, Grype counts, cosign Rekor log entry, and CI run URL

## Common Options

    --from-input          auto mode
    --clean-inline        in-place scrub (destructive)
    --max-files N         limit JPEG operation attempts; skipped/unsupported files do not count
    --rename FORMAT       rename output files using a format string (see doc/rename-spec.md)
    --rename-plan-max-files N      planning file-count circuit breaker (default: 250000)
    --rename-plan-timeout-seconds S planning time circuit breaker (default: 1800)
    --rename-plan-max-mib MIB       planning storage circuit breaker (default: 512)
    --show-container-paths include container paths in output
    -q, --quiet           no output on success
    --preview             scrub/audit all in-scope JPEGs temporarily; unavailable in auto mode
    --paranoia            discard input APP metadata without restoring EXIF/XMP/IPTC/ICC; write one canonical JFIF header
    --comment             stamp comment into EXIF/XMP
    --copyright           stamp copyright into EXIF/XMP
    --on-duplicate        move | fail | delete (default: move)
    --stable-seconds N    intake stability window
    --state-file PATH     override queue DB
    -o, --output DIR      write scrubbed files to DIR (default safe mode)

Full CLI reference → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md)

The process exits `0` when the run completes without scrub, audit, or
post-processing errors. A controlled operational failure returns `1`; command-line
syntax errors return `2`; and a handled keyboard interruption returns `130`.
Handled skips and successfully handled verified duplicates are not errors. Help,
version, and argument-parser exits do not emit a summary. With `--quiet`,
successful operational runs remain silent, while failure diagnostics and the
summary are replayed to standard error. The machine-readable line begins with `schema=2`; consumers
must require that version because 0.8.0 changes counter meanings as well as
adding fields. `total` remains a JPEG-only alias for `jpeg_total`, while
`examined` reconciles all terminal outcomes, including unsupported inputs.
The complete field contract and reconciliation rules are documented in
[`DETAILS.md`](doc/DETAILS.md#machine-readable-summary-schema-2).

The implementation decisions and acceptance criteria are recorded in the
[output safety plan](doc/output-safety-plan.md).

## Example setup

This example runs an intake directory through scrubexif before PhotoPrism
indexing, using a systemd timer for periodic processing.

| Host filesystem path     | Container path       | Purpose                                                     |
| ------------------------ | -------------------- | ----------------------------------------------------------- |
| `/some/directory/`       | `/photos/input/`     | Location for new JPEG uploads on the server                 |
| `/photoprism/sooc/`      | `/photos/output/`    | Destination for scrubbed JPEG versions, for PhotoPrism import|
| `/photoprism/processed/` | `/photos/processed/` | Holding area for already-imported files.                    |
| `/photoprism/errors/`    | `/photos/errors/`    | Verified duplicates and same-name content collisions.       |

### Systemd

`/etc/systemd/system/scrubexif.service`:

```txt
[Unit]
Description=Run scrubexif intake processing
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker run --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v /some/directory:/photos/input \
  -v /photoprism/sooc:/photos/output \
  -v /photoprism/processed:/photos/processed \
  -v /photoprism/errors:/photos/errors \
  per2jensen/scrubexif:0.7.28 --from-input --stable-seconds 10
```

Create all four host directories first and ensure UID 1000, the image's default
runtime user, can write to them. Alternatively, add an explicit Docker `--user`
matching their host ownership.

`/etc/systemd/system/scrubexif.timer`:

```txt
[Unit]
Description=Run scrubexif every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true
Unit=scrubexif.service

[Install]
WantedBy=timers.target
```

### PhotoPrism systemd script

The repository includes a configurable
[PhotoPrism integration script](https://github.com/per2jensen/scrubexif/blob/main/scripts/run_scrubexif_photoprism.sh)
that validates schema-2 summaries and indexes only newly published outputs.

## Development

    make dev-clean   # remove dev image
    make test        # make dev image and run full test suite
    pytest -m soak   # optional 10 min run or try scripts/soak.sh

---

**GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

**Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

Full docs → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md)
