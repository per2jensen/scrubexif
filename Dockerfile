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
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        util-linux \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        coreutils \
        jq \
        libimage-exiftool-perl && \
    # Remove man pages, docs, locales, and unused share files
    rm -rf /usr/share/man/* \
           /usr/share/doc/* \
           /usr/share/doc-base/* \
           /usr/share/locale/* \
           /usr/share/info/* \
           /var/lib/apt/lists/* \
           /tmp/* \
           /var/tmp/* && \
    apt-get clean

# Copy the whole package into /app
COPY . /app/
WORKDIR /app

# Install as CLI tool
RUN pip install .  --break-system-packages


# Run the main CLI
ENTRYPOINT ["scrub"]

# Default to UID 1000 unless overridden with --user
USER 1000
