# scrubexif

🧼 `scrubexif` is a lightweight, Dockerized EXIF cleaner designed for fast publishing of JPEG photos without leaking sensitive metadata.

It removes most embedded EXIF, IPTC, and XMP data while preserving useful tags like exposure settings, lens information, and author credits — ideal for privacy-conscious photographers who still want to share meaningful technical info.

📦 **GitHub**: [per2jensen/scrubexif](https://github.com/per2jensen/scrubexif)

---

## 🚀 Quick Start

### Scrub specific files

```bash
docker run --rm -v "$PWD:/photos" per2jensen/scrubexif "file1.jpg" "file2.jpeg"
```

### Scrub all JPEGs in current directory

```bash
docker run --rm -v "$PWD:/photos" per2jensen/scrubexif *.jpg *.jpeg
```

### Scrub an entire folder (non-recursive)

```bash
docker run --rm -v "/path/to/folder:/photos" per2jensen/scrubexif
```

---

## 🔧 Options

The container accepts:

- **Filenames**: one or more `.jpg` or `.jpeg` file names
- `-r`, `--recursive`: Recursively scrub `/photos` and all subfolders
- `--dry-run`: Show what would be scrubbed, without modifying files

**Examples:**

Scrub all `.jpg` files in subdirectories:

```bash
docker run --rm -v "$PWD:/photos" per2jensen/scrubexif -r
```

Dry-run (preview only):

```bash
docker run --rm -v "$PWD:/photos" per2jensen/scrubexif *.jpg --dry-run
```

Mix recursion and dry-run:

```bash
docker run --rm -v "$PWD:/photos" per2jensen/scrubexif -r --dry-run
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

Pull the image:

```bash
docker pull per2jensen/scrubexif
```

Use it:

```bash
docker run --rm -v "$PWD:/photos" per2jensen/scrubexif *.jpg *.jpeg
```

Inspect version and help:

```bash
docker run --rm per2jensen/scrubexif --version
docker run --rm per2jensen/scrubexif --help
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