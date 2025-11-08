# SPDX-License-Identifier: GPL-3.0-or-later

FROM ubuntu:24.04

LABEL org.opencontainers.image.title="scrubexif" \
      org.opencontainers.image.description="Container for sanitizing EXIF data from JPEGs using ExifTool" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.source=https://github.com/per2jensen/scrubexif \
      org.opencontainers.image.authors="Per Jensen per2jensen@gmail.com"

ARG VERSION=dev

ENV DEBIAN_FRONTEND=noninteractive \
    CONTAINER_VERSION=${VERSION}

# Combine all apt operations into one layer and clean aggressively

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        util-linux \
        python3 \
        bash \
        ca-certificates \
        coreutils \
        jq \
        libimage-exiftool-perl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
        /usr/share/man/* /usr/share/doc/* /usr/share/doc-base/* \
        /usr/share/locale/* /usr/share/info/*

# Copy only necessary files (use .dockerignore to exclude extras)

COPY . /app/
WORKDIR /app

# Use ENTRYPOINT for CLI and default to UID 1000
ENTRYPOINT ["python3", "-m", "scrubexif.scrub"]
USER 1000

