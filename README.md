# scrubexif

![CI](https://github.com/per2jensen/scrubexif/actions/workflows/test.yml/badge.svg)
![Docker Pulls](https://img.shields.io/docker/pulls/per2jensen/scrubexif)

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
VERSION=0.5.7; docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:$VERSION "file1.jpg" "file2.jpeg"
```

#### Scrub all JPEGs in current directory

```bash
VERSION=0.5.7; docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:$VERSION
```

#### Recursively scrub nested folders

```bash
VERSION=0.5.7; docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:$VERSION --recursive
```

---

### 🤖 Auto mode (`--from-input`)

Scrubs everything in a predefined input directory and saves output to another — useful for batch processing.

You **must** mount three volumes:

- `/photos/input` — input directory (e.g. `$PWD/input`)
- `/photos/output` — scrubbed files saved here
- `/photos/processed` — originals are moved here (or deleted if `--delete-original` is used)

#### Example

```bash
VERSION=0.5.7; docker run -it --rm \
  -v "$PWD/input:/photos/input" \
  -v "$PWD/output:/photos/output" \
  -v "$PWD/processed:/photos/processed" \
  per2jensen/scrubexif:$VERSION --from-input
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
VERSION=0.5.7; docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:$VERSION --recursive
```

Dry-run (preview only):

```bash
VERSION=0.5.7; docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:$VERSION --dry-run
```

Mix recursion and dry-run:

```bash
VERSION=0.5.7; docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:$VERSION --recursive --dry-run
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
- Show tags before & after (see below)
- Preserves Color profile, with a compromise in scrubbing (see below)
- A --paranoia option to scrub color profile tags (see below)
- A --preview option to check tag before/after scrub (see below)
- Based on the most excellent [ExifTool](https://exiftool.org/) inside a minimal Ubuntu base image
- Docker-friendly for pipelines and automation

### 🎯 Metadata Preservation Strategy

By default, `scrubexif` preserves important non-private metadata such as **exposure**, **lens**, **ISO**, and **color profile** information. This ensures that images look correct in color-managed environments (e.g. Apple Photos, Lightroom, web browsers with ICC support).

For users who require maximum privacy, an optional `--paranoia` mode is available.

### 🛡️ `--paranoia` Mode

When enabled, `--paranoia` disables color profile preservation and removes fingerprintable metadata like ICC profile hashes (`ProfileID`). This may degrade color rendering on some devices, but ensures all embedded fingerprint vectors are scrubbed.

| Mode         | ICC Profile | Color Fidelity | Privacy Level |
|--------------|-------------|----------------|---------------|
| *(default)*  | ✅ Preserved   | ✅ High         | ⚠️ Moderate      |
| `--paranoia` | ❌ Removed     | ❌ May degrade  | ✅ Maximum       |

### 📸 Example

```bash
# Safe color-preserving scrub (default)
docker run -v "$PWD:/photos" scrubexif:dev image.jpg

# Maximum scrub, removes the ICC profile
docker run -v "$PWD:/photos" scrubexif:dev image.jpg --paranoia
```

Note: The ICC profile includes values like ProfileDescription, ColorSpace, and ProfileID. The latter is a hash that may vary by device or editing software.

### 🔍 Inspecting Metadata with `--show-tags`

The `--show-tags` option lets you inspect metadata **before**, **after**, or **both before and after** scrubbing. This is useful for:

- Auditing what data is present in your photos
- Verifying that scrubbed output removes private metadata
- Confirming what remains (e.g. lens info, exposure, etc.)

---

### ⚠️ Note on `--dry-run`

If you want to **inspect metadata only without modifying any files**, you must pass `--dry-run`.

Without `--dry-run`, scrubbing is performed as usual.

---

### 📌 Usage Examples

```bash
# 🔎 See tags BEFORE scrub (scrub still happens)
docker run -v "$PWD:/photos" scrubexif:dev image.jpg --show-tags before

# 🔎 See both BEFORE and AFTER (scrub still happens)
docker run -v "$PWD:/photos" scrubexif:dev image.jpg --show-tags both

# ✅ Just show metadata, DO NOT scrub
docker run -v "$PWD:/photos" scrubexif:dev image.jpg --show-tags before --dry-run
```

Works in both modes

  Manual mode: for individual files or folders

  Auto mode (--from-input): applies to all JPEGs in `input`directory.

🛡 Tip: Combine `--dry-run --paranoia --show-tags before` to verify level of metadata removal before commiting.

### 🔍 Preview Mode (`--preview`)

The `--preview` option lets you **safely simulate** the scrubbing process on a **single** JPEG **without modifying the original file**.

This mode:

- Copies the original image to a temporary file
- Scrubs the copy in memory
- Shows metadata **before and/or after** scrubbing
- Deletes the temp files automatically
- Never alters the original image

### ✅ Typical Use

```bash
docker run -v "$PWD:/photos" scrubexif:dev test.jpg --preview
```

🛡 Tip: Combine `--preview --paranoia` to veriy the color profile tags including the ProfileId tag has been scrubbed. 

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

## Known limitations

🚧 **Symlinked input paths are not detected inside the container**

If you bind-mount a symbolic link (e.g. `-v $(pwd)/symlink:/photos/input`), Docker resolves the symlink before passing it to the container. This means:

- The container sees `/photos/input` as a normal directory.
- `scrubexif` cannot detect it was originally a symlink.
- For safety, avoid mounting symbolic links to any of the required directories.

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
VERSION=0.5.7; docker pull per2jensen/scrubexif:$VERSION
```

Pull the latest stable release (when available)

```bash
docker pull per2jensen/scrubexif:stable
```

✔️ All `:0.5.x` and `:stable` images are verified via GitHub Actions CI.


>`:dev` → Bleeding edge development, **only built >locally**, not pushed to Docker Hub

🧼 Run to scrub all .jpg and .jpeg files in the current directory

```bash
VERSION=0.5.7; docker run -it --rm -v "$PWD:/photos" per2jensen/scrubexif:$VERSION
```

🛠️ Show version and help

```bash
VERSION=0.5.7; docker run --rm per2jensen/scrubexif:$VERSION --version
VERSION=0.5.7; docker run --rm per2jensen/scrubexif:$VERSION --help
```

---

## 🔐 User Privileges and Running as Root

By default, the `scrubexif` container runs as user ID 1000, not root. This is a best-practice security measure to avoid unintended file permission changes or elevated access.

🧑 Default Behavior

```bash
docker run --rm scrubexif:dev
```

Runs the container as UID 1000 by default

Ensures safer file operations on mounted volumes

Compatible with most host setups

👤 Running as a Custom User

You can specify a different UID (e.g., match your local user) using the --user flag:

```bash
docker run --rm --user $(id -u) scrubexif:dev
```

This ensures created or modified files match your current user permissions.

🚫 Root is Blocked by Default

Running the container as root (UID 0) is explicitly disallowed to prevent unsafe behavior:

```bash
docker run --rm --user 0 scrubexif:dev
# ❌ Running as root is not allowed unless ALLOW_ROOT=1 is set.
```

To override this safeguard, set the following environment variable:

```bash
docker run --rm --user 0 -e ALLOW_ROOT=1 scrubexif:dev
```

  ⚠️ Use this option only if you know what you're doing. Writing files as root can cause permission issues on the host system.

---

## 📌 Recommendations

To ensure smooth and safe operation when using `scrubexif`, follow these guidelines:

### ✅ Use Real Directories for Mounts

Avoid using symbolic links for input, output, or processed paths. Due to Docker's volume resolution behavior, symlinks are flattened and no longer detectable inside the container.
Instead:

docker run -v "$PWD/input:/photos/input" \
           -v "$PWD/output:/photos/output" \
           -v "$PWD/processed:/photos/processed" \
           scrubexif:dev --from-input

### ✅ Run as a Non-Root User

SCRUBEXIF checks directory writability. If you mount a directory as root-only, and the container runs as a non-root user (recommended), it will detect and exit cleanly.

Tip: Use --user 1000 or ensure mounted dirs are writable by UID 1000.

### ✅ Always Pre-Check Mount Paths

Ensure the input, output, and processed directories:

    Exist on the host

    Are not files or symlinks

    Are writable by the container’s user

Otherwise, scrubexif will fail fast with a clear error message.

### ✅ Keep Metadata You Intend to Preserve Explicit

Configure your `scrub.py` to define which EXIF tags to preserve, rather than relying on defaults if privacy is critical.

---

## 🔍 Viewing Metadata

To inspect the metadata of an image before/after scrubbing:

```bash
exiftool "image.jpg"
```

Inside the container (optional):

Observe the "/photos" in the filename, that is because the container has your $PWD mounted on /photos.

```bash
VERSION=0.5.7; docker run --rm -v "$PWD:/photos" --entrypoint exiftool  per2jensen/scrubexif:$VERSION  "/photos/image.jpg"
```

---

## 📦 Inspecting the Image Itself

To view embedded labels and metadata:

```bash
VERSION=0.5.7; docker inspect per2jensen/scrubexif:$VERSION | jq '.[0].Config.Labels'
```

You can also check the digest and ID:

```bash
VERSION=0.5.7; docker image inspect per2jensen/scrubexif:$VERSION --format '{{.RepoDigests}}'
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

🧪 Test Image

To verify that a specific scrubexif Docker image functions correctly, the test suite supports containerized testing using any image tag. By default, it uses the local `scrubexif:dev` image. You can override this with the `SCRUBEXIF_IMAGE` environment variable.

🔧 Default behavior

When running pytest, the following fallback is used if no override is set:

IMAGE_TAG = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")

This means that the tests will attempt to run:

docker run ... scrubexif:dev ...

If no such local image exists, the test will fail.

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