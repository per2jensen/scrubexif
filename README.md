# scrubexif

<div align="center">

<!-- 📦 Project Metadata -->
<a href="https://github.com/per2jensen/scrubexif/releases"><img alt="Tag" src="https://img.shields.io/github/v/tag/per2jensen/scrubexif"/></a>
<a href="https://github.com/per2jensen/scrubexif/actions/workflows/CI.yml"><img alt="CI" src="https://github.com/per2jensen/scrubexif/actions/workflows/CI.yml/badge.svg"/></a>
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

**High-trust JPEG scrubbing.** Removes location, serial and private camera tags while preserving photographic context. The most excellent [Exiftool](https://exiftool.org/) is used to process the JPEGs.

> **Promise:** scrubexif will not write an unscrubbed JPEG into an output directory.  
If a scrub fails for any reason, **no output file is created** for that JPEG.
<sub>This is true when writing scrubbed JPEGS to an output directory, **not** if you scrub JPEGS inline. Scrubbing quality depends on `exiftool's` ability to remove exif data</sub>

**Full documentation moved** → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md)  
 This README is intentionally short for Docker Hub visibility.

## Quick Start

### Easiest one-liner (default safe mode, non-destructive)

Scrub all JPEGs in the **current directory** (`$PWD`) and write cleaned copies to  
`$PWD/output/`:

    docker run --rm -v "$PWD:/photos" per2jensen/scrubexif:0.7.13

To write scrubbed files to a different directory (created if missing):

    docker run --rm -v "$PWD:/photos" per2jensen/scrubexif:0.7.13 -o /photos/scrubbed

This:

- scans **your current directory** (`$PWD`) for `*.jpg` / `*.jpeg` (also in capital letters)
- writes scrubbed copies to **$PWD/output/** (or a custom `--output` dir)  
- leaves the originals untouched in **$PWD/**
- refuses to run if the `$PWD/output` directory already exists
- prints host paths by default (use `--show-container-paths` to include `/photos/...` paths)

### Failure handling (important)

scrubexif is designed to **never place an unscrubbed JPEG into an output directory**.
If a scrub fails for any reason, **no output file is created for that JPEG** and the run continues for the rest.

What happens on failed scrubs depends on the mode `scrubexif` is run in:

- **Default safe mode** (the one-liner): failed files stay in the original directory, and **no file is written to the output directory** for those failures.
- **Auto mode** (`--from-input`): failed files are moved to `processed/` for inspection, and **no file is written to `output/`** for those failures.
- **Manual (destructive) in-place** (`--clean-inline`): a failure leaves the original unchanged; there is no output directory involved.

### Hardened (destructive) in-line scrub (current directory)

Same idea, but with container hardening and in-line (destructive) overwrite:

    docker run -it --rm \
      --read-only --security-opt no-new-privileges \
      --tmpfs /tmp \
      -v "$PWD:/photos" \
      per2jensen/scrubexif:0.7.13 --clean-inline

### Batch workflow (PhotoPrism / intake style)

Use auto mode with explicit input/output/processed directories:

    mkdir input scrubbed processed errors
    docker run -it --rm \
      --read-only --security-opt no-new-privileges \
      --tmpfs /tmp \
      -v "$PWD/input:/photos/input" \
      -v "$PWD/scrubbed:/photos/output" \
      -v "$PWD/processed:/photos/processed" \
      -v "$PWD/errors:/photos/errors" \
      per2jensen/scrubexif:0.7.13 --from-input

These are the physical directories used on your file system:

Uploads → `$PWD/input/`  
Scrubbed → `$PWD/scrubbed/`  
Originals → `$PWD/processed/` (or deleted with `--delete-original`)  
Duplicates → deleted by default; use `--on-duplicate move` to move them into `$PWD/errors/`  
Failed scrubs (e.g., corrupted files) → logged as failures; originals are moved to `$PWD/processed/` for inspection  
`errors/` is a misnomer today; it is only used for duplicates when `--on-duplicate move` is set. Will be fixed in a later version.

### Data flow overview (auto mode: `--from-input`)

This flow diagram describes what happens **only in auto mode** (`--from-input`),
where four directories (`input/`, `output/`, `processed/`, `errors/`) are used.

Please observe these directories are named like this **inside the container**. Your physical directories in your file system are mapped when you run the `docker run ...` command. See the `-v ....` options in the above example.

```
[input/]  -->  <scrubexif> runs  -->  [output/]
                 |
                 +-->  [processed/]   (original JPEGs moved here after successful scrub,
                                       unless --delete-original is used)
                 |
                 +-->  [errors/]      (duplicates only — only used when
                                       --on-duplicate move)
```

Meaning:

- `input/`
    New JPEGs arrive here (e.g. from uploads, for example PhotoSync).

- `output/`
    Scrubbed JPEGs with safe EXIF metadata.

- `processed/`
    Original JPEGs moved here after scrub (or deleted when requested).

- `errors/`
    Only created/used when `--on-duplicate move` is enabled.

### Build & Run Locally

    # build the image from the Dockerfile in this repo
    docker build -t scrubexif:local .

    # show CLI usage (ENTRYPOINT runs python -m scrubexif.scrub)
    docker run --rm scrubexif:local --help

    # scrub the current directory with hardened defaults
    docker run -it --rm \
      --read-only --security-opt no-new-privileges \
      --tmpfs /tmp \
      -v "$PWD:/photos" \
      scrubexif:local

Any arguments appended to `docker run … scrubexif:*` are forwarded to the underlying
`python3 -m scrubexif.scrub` entrypoint.

## Key Features

- Allowlist-based scrubbing: preserves a small set of technical tags (exposure, ISO, focal length, orientation, image size)
- Removes GPS, serial numbers, and other private metadata
- Preserves color profiles by default (use `--paranoia` to remove ICC data)
- Auto mode with duplicate handling (`--on-duplicate delete|move`)
- Optional stability gate for hot upload directories (`--stable-seconds`, `--state-file`)
- Metadata inspection and dry-run support (`--show-tags`, `--preview`, `--dry-run`)
- Optional stamping of copyright and comment into EXIF/XMP (`--copyright`, `--comment`)
- Hardened container defaults in examples (read-only + no-new-privileges)

## Supply Chain Transparency

- Releases are produced by the public **Release** GitHub Actions workflow (`.github/workflows/release.yml`), which builds the Docker image, runs Syft to generate an SPDX SBOM, and scans the image with Grype (failing on high/critical CVEs). CI (`.github/workflows/CI.yml`) runs tests only.
- Release assets include the SBOM (`sbom-v<version>.spdx.json`) and the Grype SARIF report (`grype-results-<version>.sarif`). The SARIF is also uploaded to the GitHub Security tab and kept as an Actions artifact → see the **[Releases tab](https://github.com/per2jensen/scrubexif/releases)** for release assets.
- `doc/build-history.json` tracks each tag with the Git commit, image digest, and (when available) the Grype severity counts, giving downstream users a verifiable audit trail.

## Common Options

    --from-input          auto mode
    --clean-inline        in-place scrub (destructive)
    --show-container-paths include container paths in output
    -q, --quiet           no output on success
    --preview             no write, view only
    --paranoia            maximum scrub, removes ICC
    --comment             stamp comment into EXIF/XMP
    --copyright           stamp copyright into EXIF/XMP
    --on-duplicate        delete | move
    --stable-seconds N    intake stability window
    --state-file PATH     override queue DB
    -o, --output DIR      write scrubbed files to DIR (default safe mode)

Full CLI reference → in [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md)

## Example setup

This is an example of my workflow to quickly upload JPEG files to PhotoPrism.
One use case is to quickly show dog owners photos at exhibitions.

| Host filesystem path     | Container path       | Purpose                                                     |
| ------------------------ | -------------------- | ----------------------------------------------------------- |
| `/some/directory/`       | `/photos/input/`     | Location for new JPEG uploads on the server                 |
| `/photoprism/sooc/`      | `/photos/output/`    | Destination for scrubbed JPEG versions, for PhotoPrism import|
| `/photoprism/processed/` | `/photos/processed/` | Holding area for already-imported files.                    |

### Systemd

`/etc/systemd/system/scrubexif.service`:

    [Service]
    ExecStart=/usr/bin/docker run --rm \
      --read-only --security-opt no-new-privileges \
      --tmpfs /tmp \
      -v /some/directory:/photos/input \
      -v /photoprism/sooc:/photos/output \
      -v /photoprism/processed:/photos/processed \
      per2jensen/scrubexif:0.7.13 --from-input --stable-seconds 10

`/etc/systemd/system/scrubexif.timer`:

    [Unit]
    Description=Run scrubexif every 5 minutes

    [Timer]
    OnBootSec=1min
    OnUnitActiveSec=5min
    Persistent=true

    [Install]
    WantedBy=timers.target

### Photoprism systemd script

I use `scrubexif` to clean my jpegs on dog exhibitions. I upload the files to a server using rclone and a systemd timer runs the script below every 5 minutes.

You can see my (anonymized) script in [the Github scrubexif repo](https://github.com/per2jensen/scrubexif/blob/main/scripts/run_scrubexif_photoprism.sh)

## Development

    make dev-clean   # remove dev image
    make test        # make dev image and run full test suite
    pytest -m soak   # optional 10 min run or try scripts/soak.sh

## License

GPL-3.0-or-later

Licensed under GNU GENERAL PUBLIC LICENSE v3, see the supplied file "LICENSE" for details.

THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW, not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See section 15 and section 16 in the supplied "LICENSE" file.

---

**GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

**Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

Full docs → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md)
