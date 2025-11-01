# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path
from PIL import Image

import os
os.environ.setdefault("SCRUBEXIF_STABLE_SECONDS", "0")

SAMPLE_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # Minimal fake JPEG header


def create_fake_jpeg(path: Path, color: str = "white"):
    image = Image.new("RGB", (10, 10), color)
    image.save(path, "JPEG", quality=85)
