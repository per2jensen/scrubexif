# SPDX-License-Identifier: GPL-3.0-or-later

# ==============================================================================
# Stage 1: Builder
# This stage builds the Python wheel from source. It contains build tools
# like pip and setuptools, but they will not be part of the final image.
# ==============================================================================
FROM ubuntu:24.04 AS builder

ARG VERSION=dev

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3-pip \
        python3-build \
        python3-venv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only the files needed to build the package
COPY pyproject.toml README.md LICENSE ./
COPY scrubexif ./scrubexif

# Build the wheel for our app AND download wheels for all its dependencies.
RUN python3 -m pip wheel --wheel-dir=/wheels .

# ==============================================================================
# Stage 2: Final Image
# This is the lean, final image. It copies the built wheel from the builder
# stage and installs it, along with runtime dependencies.
# ==============================================================================
FROM ubuntu:24.04 AS final

ARG VERSION=dev

LABEL org.opencontainers.image.title="scrubexif" \
      org.opencontainers.image.description="Container for sanitizing EXIF data from JPEGs using ExifTool" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.source=https://github.com/per2jensen/scrubexif \
      org.opencontainers.image.authors="Per Jensen per2jensen@gmail.com"

ENV DEBIAN_FRONTEND=noninteractive \
    CONTAINER_VERSION=${VERSION}

# Copy the built wheels from the builder stage before trying to install them
COPY --from=builder /wheels /wheels

RUN apt-get update && \
    apt-get install -y --no-install-recommends --no-install-suggests \
        python3 \
        python3-pip \
        libimage-exiftool-perl && \
    \
    # Install the wheel from the builder stage and clean up in the same layer
    python3 -m pip install --no-cache-dir --break-system-packages /wheels/*.whl && \
    \
    # Clean up apt and other unnecessary files to reduce image size
    apt-get purge -y --auto-remove python3-pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
           /wheels \
           /usr/share/man/* \
           /usr/share/doc/* \
           /usr/share/locale/*

# The entrypoint now uses the installed 'scrub' script from pyproject.toml
ENTRYPOINT ["python3", "-m", "scrubexif.scrub"]
USER 1000
