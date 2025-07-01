# scrubexif

🧼 `scrubexif` is a lightweight, Dockerized EXIF cleaner designed for fast publishing of JPEG photos without leaking sensitive metadata.

It removes most embedded EXIF, IPTC, and XMP data while preserving useful tags like exposure settings, lens information, and author credits — ideal for privacy-conscious photographers who still want to share meaningful technical info.

👉 **GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

📦 **Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)

---

## 🚀 Quick Start

There are **two modes**:

### ✅ Manual mode (default)

Manually scrub one or more `.jpg` / `.jpeg` files from the current directory.

#### Scrub specific files

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif "file1.jpg" "file2.jpeg"
```

#### Scrub all JPEGs in current directory

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif
```

#### Recursively scrub nested folders

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif -r
```

---

### 🤖 Auto mode (`--from-input`)

Scrubs everything in a predefined input directory and saves output to another — useful for batch processing.

You **must** mount three volumes:

- `/photos/input` — input directory (e.g. `$PWD/input`)
- `/photos/output` — scrubbed files saved here
- `/photos/processed` — originals are moved here (or deleted if `--delete-original` is used)

#### Example:

```bash
docker run -it --rm \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/output:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  per2jensen/scrubexif --from-input
```

Optional flags:

- `--delete-original` — Delete originals instead of moving them
- `--dry-run` — Show what would be scrubbed, but don’t write files

---

## 🔧 Options (Manual mode)

The container accepts:

- **Filenames**: one or more `.jpg` or `.jpeg` file names
- `-r`, `--recursive`: Recursively scrub `/photos` and all subfolders
- `--dry-run`: Show what would be scrubbed, without modifying files

**Examples:**

Scrub all `.jpg` files in subdirectories:

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif -r
```

Dry-run (preview only):

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif --dry-run
```

Mix recursion and dry-run:

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif -r --dry-run
```

If no arguments are provided, it defaults to scanning `/photos` for JPEGs.

---

## ✅ Features

- Case insensitive, works on .jpg, .JPG, .jpeg & .JPEG
- Removes most EXIF, IPTC, and XMP metadata
- **Preserves** useful photography tags:
  - `ExposureTime`, `FNumber`, `ISO`
  - `LensModel`, `FocalLength`
  - `Artist`, `Copyright`
- Based on [ExifTool](https://exiftool.org/) inside a minimal Ubuntu base image
- Docker-friendly for pipelines and automation

---

## 🧼 What It Cleans

The tool removes:

- GPS location data
- Camera serial numbers
- Software version strings
- Embedded thumbnails
- XMP/IPTC descriptive metadata
- MakerNotes (where safely possible)

It **preserves** key tags important for photographers and viewers.

---

## 🐳 Docker Image

For now I am not using `latest`, as the images are only development quality.

I am currently going with:

- `:0.5.x`  → Versioned releases
- `:stable` → Latest tested and approved version, perhaps with an :rc before declaring :stable
- `:dev`    → Bleeding edge development, may be broken, not put on Docker Hub

The Release pipeline in the Makefile automatically updates the [build-history.json](https://github.com/per2jensen/scrubexif/blob/main/doc/build-history.json) that keeps various metadata on the uploaded images.

📥 Pull Images

Versioned image:

```bash
docker pull per2jensen/scrubexif:0.5.2
```

Pull the latest stable release (when available)

```bash
docker pull per2jensen/scrubexif:stable
```

✔️ All `:0.5.x` and `:stable` images are verified via GitHub Actions CI.


>`:dev` → Bleeding edge development, **only built >locally**, not pushed to Docker Hub

🧼 Run to scrub all .jpg and .jpeg files in the current directory

```bash
docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:0.5.2
```

🛠️ Show version and help

```bash
docker run --rm per2jensen/scrubexif:0.5.2 --version
docker run --rm per2jensen/scrubexif:0.5.2 --help
```

---

## 🔍 Viewing Metadata

To inspect the metadata of an image before/after scrubbing:

```bash
exiftool "image.jpg"
```

Inside the container (optional):

```bash
docker run --rm -v "$PWD:/photos" per2jensen/scrubexif exiftool "image.jpg"
```

---

## 📦 Inspecting the Image Itself

To view embedded labels and metadata:

```bash
docker inspect per2jensen/scrubexif:latest | jq '.[0].Config.Labels'
```

You can also check the digest and ID:

```bash
docker image inspect per2jensen/scrubexif --format '{{.RepoDigests}}'
```

---

## 📁 Example Integration

This image is ideal for:

- Web galleries
- Dog show photo sharing
- Social media publishing
- Backup pipelines before upload
- Static site generators like Hugo/Jekyll

---

## 🔧 Build Locally (Optional)

```bash
docker build -t scrubexif .
```

---

## ✍️ License

Licensed under the GNU General Public License v3.0 or later  
See the `LICENSE` file in this repository.

---

## 🙌 Related Tools

📸 [file-manager-scripts](https://github.com/per2jensen/file-manager-scripts) — Nautilus context menu integrations  
📸 image-scrubber — Browser-based interactive metadata removal  
📸 jpg-exif-scrubber — Python tool that strips all metadata (no preservation)

`scrubexif` focuses on **automated, container-friendly workflows** with **safe defaults** for photographers.

---

## 💬 Feedback

Suggestions, issues, or pull requests are always welcome.  
Maintained by **Per Jensen**

---

## 🔗 Project Homepage

Source code, issues, and Dockerfile available on GitHub:

👉 [https://github.com/per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

📦 **Docker Hub**: [per2jensen/scrubexif](https://hub.docker.com/r/per2jensen/scrubexif)