# jpeg-scrubber

🧼 `jpeg-scrubber` is a lightweight, Dockerized EXIF cleaner designed for fast publishing of JPEG photos without leaking sensitive metadata.

It removes most embedded EXIF, IPTC, and XMP data while preserving useful tags like exposure settings, lens information, and author credits — ideal for privacy-conscious photographers who still want to share meaningful technical info.

## 🚀 Quick Start

Clean all `.jpg` files in your current directory:

```bash
docker run --rm -v "$PWD:/photos" jpeg-scrubber *.jpg *.jpeg
```

or clean a named directory for jpg's and jpeg's

```bash
docker run --rm -v "/path/to/folder:/photos" per2jensen/jpeg-scrubber *.jpg *.jpeg
```

## ✅ Features

Removes a lot of EXIF/IPTC/XMP metadata

    Preserves useful tags:

        ExposureTime, FNumber, ISO

        LensModel, FocalLength

        Artist, Copyright

Built with ExifTool inside a minimal Debian image

CLI and Docker compatible — use in automated workflows or shell scripts

## 🧼 What It Cleans

jpeg-scrubber removes:

    GPS location data

    Camera serial numbers

    Software version strings

    Embedded thumbnails

    XMP/IPTC descriptive metadata

    MakerNotes (where safe)

It **preserves** key photography tags useful for display or cataloging.

## 📁 Example Integration

This image is ideal for:

    Web galleries

    Dog show photo sharing

    Social media publishing

    Backup pipelines before upload

    Static site generators like Hugo/Jekyll

## 🐳 Docker Image

You can build it locally:

```bash
docker build -t jpeg-scrubber .
```

Or use the prebuilt image:

```bash
docker pull per2jensen/jpeg-scrubber
```

## ✍️ License

Licensed under the GNU General Public License v3.0 or later

See `LICENSE` file in this repo

## 🙌 Related Tools

📸  I have a very similiar [Nautilus support script](https://github.com/per2jensen/file-manager-scripts), probably a carbon copy for starters, integrated into my Nautilus file manager.    
    
📸 image-scrubber — Interactive, browser-based metadata removal and face blurring tool.

📸  jpg-exif-scrubber (Python) — Full-strip script written in Python, no metadata preserved.

`jpeg-scrubber` focuses on automated, container-friendly workflows with safe defaults for photographers.

## 💬 Feedback

Suggestions, issues, or pull requests are always welcome.
Maintained by Per Jensen
