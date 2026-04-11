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

**High-trust JPEG scrubbing.** Removes location, serial and private camera tags while preserving photographic context. Uses [jpegtran](https://ijg.org/) for a byte-level APP segment wipe, then [ExifTool](https://exiftool.org/) to write back a small allowlist of technical tags — closing the gap that parser-based tools leave open for unknown or proprietary segments.

> **Promise:** scrubexif will not write an unscrubbed JPEG into an output directory.  
If a scrub fails for any reason, **no output file is created** for that JPEG.
<sub>This is true when writing scrubbed JPEGs to an output directory, **not** if you scrub JPEGs inline. The jpegtran byte-level strip ensures removal of all APP segments, including unknown or proprietary ones that parser-based tools cannot see.</sub>

**Full documentation moved** → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md)  
 This README is intentionally short for Docker Hub visibility.

## Quick Start

### Easiest one-liner (default safe mode, non-destructive)

Scrub all JPEGs in the **current directory** (`$PWD`) and write cleaned copies to  
`$PWD/output/`:

````bash
docker run --rm \
-v "$PWD:/photos" \
per2jensen/scrubexif:0.7.19
````

This:

- scans **your current directory** (`$PWD`) for `*.jpg` / `*.jpeg` (also in capital letters)
- writes scrubbed copies to **$PWD/output/** (or a custom `--output` dir)  
- leaves the originals untouched in **$PWD/**
- refuses to run if the `$PWD/output` directory already exists
- prints host paths by default (use `--show-container-paths` to include `/photos/...` paths)

### Write to specific directory

Use `-o` to control where scrubbed files are written.

**Output to a subdirectory of `$PWD`** — `scrubexif` creates it if it does not exist:

````bash
docker run --rm \
    -v "$PWD:/photos" \
    per2jensen/scrubexif:0.7.19 \
    -o scrubbed
````

Scrubbed files are written to `$PWD/scrubbed/`. The run is refused if `scrubbed/` already exists
(safety guard — use `-o` with a bind-mount instead if you need to reuse a directory).

**Output to an arbitrary host directory** — mount it independently and pass the container path to `-o`:

````bash
docker run --rm \
    -v "$PWD:/photos" \
    -v "/tmp/scrub-test:/scrubbed" \
    per2jensen/scrubexif:0.7.19 \
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
      per2jensen/scrubexif:0.7.19 --clean-inline

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
      per2jensen/scrubexif:0.7.19 --from-input

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

- Allowlist-based scrubbing: jpegtran strips all JPEG APP segments at the byte level (including unknown/proprietary vendor segments), then ExifTool writes back a small allowlist of technical tags (exposure, ISO, focal length, orientation)
- Removes GPS, serial numbers, maker notes, and all other private metadata — including segments invisible to parser-based tools
- Preserves color profiles (ICC) by default; normal mode re-embeds the ICC profile after the jpegtran strip
- Auto mode with duplicate handling (`--on-duplicate delete|move`)
- Optional stability gate for hot upload directories (`--stable-seconds`, `--state-file`)
- Metadata inspection and dry-run support (`--show-tags`, `--preview`, `--dry-run`)
- Optional stamping of copyright and comment into EXIF/XMP (`--copyright`, `--comment`)
- Hardened container defaults in examples (read-only + no-new-privileges)

## Acknowledgements

- [ExifTool](https://exiftool.org/) by Phil Harvey — used for metadata extraction and selective tag write-back (GPL-1.0-or-later / Artistic License)
- [jpegtran](https://ijg.org/) from libjpeg-turbo — used for lossless byte-level JPEG transformation (IJG / BSD licence)
- [sigstore/cosign](https://github.com/sigstore/cosign) used to sign/upload artifacts 
- [Syft](https://github.com/anchore/syft) used to generate a Software Bill Of Materials
- [Grype](https://github.com/anchore/grype) used for image vulnerability scanning
- [Ubuntu](https://ubuntu.com/) for the base image Scrubexif is based on
  
## Supply Chain Transparency

Every release image is **cryptographically signed** using [cosign](https://github.com/sigstore/cosign) keyless signing via the [Sigstore](https://sigstore.dev) public infrastructure. The signature is tied to the exact GitHub Actions run that built the image — no long-lived signing keys exist anywhere. Anyone can verify that a pulled image genuinely came from this repository and was not tampered with in transit or on Docker Hub.

**Verify any release in one command** (requires [cosign](https://docs.sigstore.dev/cosign/system_config/installation/)):

```bash
cosign verify per2jensen/scrubexif:0.7.19 \
  --certificate-identity-regexp="https://github.com/per2jensen/scrubexif" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

A successful verification prints the signing certificate, which includes the exact workflow URL, the Git commit SHA, and the GitHub Actions run URL — proving provenance down to the individual CI run.

Full details on installation, verification, and what the certificate fields mean → [`doc/DETAILS.md#image-signing-and-supply-chain-verification`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md#image-signing-and-supply-chain-verification)

**Additional supply chain artefacts per release:**
- SPDX SBOM (`sbom-<version>.spdx.json`) — attached to each GitHub Release and as a signed in-toto attestation on the image itself
- Grype vulnerability scan (`grype-results-<version>.sarif`) — attached to the release, uploaded to the GitHub Security tab; releases are blocked on any high or critical CVE
- `doc/build-history.json` — tracks every release with Git commit, image digest, Grype counts, cosign Rekor log entry, and CI run URL

## Common Options

    --from-input          auto mode
    --clean-inline        in-place scrub (destructive)
    --show-container-paths include container paths in output
    -q, --quiet           no output on success
    --preview             no write, view only
    --paranoia            byte-level wipe via jpegtran only — zero metadata survives (no EXIF, no ICC)
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
      per2jensen/scrubexif:0.7.19 --from-input --stable-seconds 10

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
