# scrubexif

<div align="center">

<!-- 📦 Project Metadata -->
<a href="https://github.com/per2jensen/scrubexif/releases"><img alt="Tag" src="https://img.shields.io/github/v/tag/per2jensen/scrubexif"/></a>
<a href="https://github.com/per2jensen/scrubexif/actions/workflows/test.yml"><img alt="CI" src="https://github.com/per2jensen/scrubexif/actions/workflows/CI.yml/badge.svg"/></a>
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

**High‑trust JPEG scrubbing.** Removes location, serial and private camera tags while preserving photographic context. The most excellent [Exiftool](https://exiftool.org/) is used to process the JPEGs.

> **Full documentation moved** → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc//DETAILS.md)  
> This README is intentionally short for Docker Hub visibility.

## Quick Start

Scrub current directory (hardened container defaults):

```bash
docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD:/photos" \
  per2jensen/scrubexif:9.9.9
```

Batch workflow:

```bash
mkdir input output processed
docker run -it --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/output:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  per2jensen/scrubexif:0.7.8 --from-input
```

Uploads → `input/`  
Scrubbed → `output/`  
Originals → `processed/` (or deleted)  
Duplicates → deleted or `errors/`
Corrupted → logged as failures, originals relocated to `processed/` for inspection

### Build & Run Locally

```bash
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
```

Any arguments appended to `docker run … scrubexif:*` are forwarded to the underlying
`python3 -m scrubexif.scrub` entrypoint.

## Key Features

- Removes GPS and personal data
- Keeps camera + exposure metadata
- Default run uses read‑only + no-new-privileges hardening
- Duplicate handling: delete or move
- Optional state‑file for high‑volume pipelines
- `--preview`, `--paranoia`, `--stable-seconds N`

## Supply Chain Transparency

- Every release is produced by a public GitHub Actions workflow that builds the Docker image, runs Syft to publish an SPDX SBOM, and scans the image with Grype (failing on high/critical CVEs).
- The vulnerability results (`grype-results-<version>.sarif`) and SBOM (`sbom-v<version>.spdx.json`) are attached to each GitHub Release → see the **[Releases tab](https://github.com/per2jensen/scrubexif/releases)** for the latest artifacts.
- `doc/build-history.json` tracks every tag with the Git commit, image digest, and (when available) the Grype severity counts, giving downstream users a verifiable audit trail.

## Common Options

```
--from-input          auto mode
--preview             no write, view only
--paranoia            maximum scrub, removes ICC
--on-duplicate        delete | move
--stable-seconds N    intake stability window
--state-file PATH     override queue DB
```

Full CLI reference → in [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc/DETAILS.md)

## Example setup

This is an example of my workflow to quickly upload JPEG files to PhotoPrism.
One use case is to quickly show dog owners photos at exhibitions.

| Host filesystem path     | Container path       | Purpose                                                     |
| ------------------------ | -------------------- | ----------------------------------------------------------- |
| `/some/directory/`       | `/photos/input/`     | Location for new JPEG uploads on the server                 |
| `/photoprism/sooc/`      | `/photos/output/`    | Destination for scrubbed JPEG versions, for Photoprim import|
| `/photoprism/processed/` | `/photos/processed/` | Holding area for already-imported files.                    |

### Systemd

/etc/systemd/system/scrubexif.service:

```ini
[Service]
ExecStart=/usr/bin/docker run --rm \
  --read-only --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v /some/directory:/photos/input \
  -v /photoprism/sooc:/photos/output \
  -v /photoprism/processed:/photos/processed \
  per2jensen/scrubexif:0.7.8 --from-input --stable-seconds 10
```

/etc/systemd/system/scrubexif.timer:

```ini
[Unit]
Description=Run scrubexif every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

## Development

```bash
make dev-clean   # remove dev image
make test        # make dev image and run full test suite
pytest -m soak   # optional 10 min run or try scripts/soak.sh
```

## License

GPL‑3.0‑or‑later

Licensed under GNU GENERAL PUBLIC LICENSE v3, see the supplied file "LICENSE" for details.

THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW, not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See section 15 and section 16 in the supplied "LICENSE" file.

---

**GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

**Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

Full docs → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc//DETAILS.md)
