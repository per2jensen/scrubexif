# SPDX-License-Identifier: GPL-3.0-or-later

FROM ubuntu:24.04

LABEL org.opencontainers.image.title="scrubexif" \
      org.opencontainers.image.description="Container for sanitizing EXIF data from JPEGs using ExifTool" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/per2jensen/scrubexif" \
      org.opencontainers.image.authors="Per Jensen <per2jensen@gmail.com>"

ARG VERSION=dev

ENV DEBIAN_FRONTEND=noninteractive \
    CONTAINER_VERSION=${VERSION}

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        coreutils \
        jq && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*


# Copy the whole package into /app
COPY . /app/
WORKDIR /app

# Install as CLI tool
RUN pip install .  --break-system-packages


# Run the main CLI
ENTRYPOINT ["scrub"]
