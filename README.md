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

**GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

**Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

**High‑trust JPEG scrubbing.** Removes location, serial and private camera tags while preserving photographic context.

> **Full documentation moved** → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc//DETAILS.md)  
> This README is intentionally short for Docker Hub visibility.

## Quick Start

Scrub current directory:

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:0.7.3
```

Batch workflow:

```bash
mkdir input output processed
docker run -it --rm \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/output:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  per2jensen/scrubexif:0.7.3 --from-input
```

Uploads → `input/`  
Scrubbed → `output/`  
Originals → `processed/` (or deleted)  
Duplicates → deleted or `errors/`

## Key Features

- Removes GPS and personal data
- Keeps camera + exposure metadata
- Runs in secure read‑only container mode
- Duplicate handling: delete or move
- Optional state‑file for high‑volume pipelines
- `--preview`, `--paranoia`, `--stable-seconds N`

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

## Systemd Example

This is an example of my workflow, when uploading jpeg files to photoprism and showing the photos to a group of people.

I upload the jpeg files to /tmp/upload/, mapped to /photos/input/ in the container.

`scrubexif` processes the files /photos/input/ and put scrubbed versions in /photoprism/sooc/, mapped to /photos/output/ in the container.

Once processed, a jpeg file is moved to /photoprism/processed, mapped to /photos/processed in the container.

In the real world the systemd services starts a script, which nudges photoprism to import the newly processed jpeg files.

/etc/systemd/system/scrubexif.service example:

```ini
[Service]
ExecStart=/usr/bin/docker run --rm \
  -v /tmp/upload:/photos/input \
  -v /photoprism/sooc:/photos/output \
  -v /photoprism/processed:/photos/processed \
  per2jensen/scrubexif:0.7.3 --from-input --stable-seconds 10
```

/etc/systemd/system/scrubexif.timer example:

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
make dev     # build dev image
make test    # full test suite
pytest -m soak   # optional long-run
```

## License

GPL‑3.0‑or‑later

---

**GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

**Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

Full docs → [`DETAILS.md`](https://github.com/per2jensen/scrubexif/blob/main/doc//DETAILS.md)