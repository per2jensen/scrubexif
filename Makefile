# SPDX-License-Identifier: GPL-3.0-or-later
#
# Usage examples:
# ---------------
# make dev-clean dev
# make FINAL_VERSION=1.0.0 final
# make FINAL_VERSION=1.0.0 release

# ================================
# Configuration
# ================================

SHELL := /bin/bash

DOCKER ?= docker
UBUNTU_VERSION ?= 24.04

BASE_IMAGE_NAME = scrubexif-base
FINAL_IMAGE_NAME = scrubexif
DOCKERHUB_REPO = per2jensen/scrubexif
BASE_LATEST_TAG = $(BASE_IMAGE_NAME):$(UBUNTU_VERSION)

BUILD_LOG_DIR ?= doc
BUILD_LOG_FILE ?= build-history.json
BUILD_LOG_PATH := $(BUILD_LOG_DIR)/$(BUILD_LOG_FILE)

# ================================
# Targets
# ================================

check_version:
	@if [ -z "$(FINAL_VERSION)" ]; then \
		echo "❌ ERROR: You must set FINAL_VERSION explicitly."; \
		echo "   Example: make FINAL_VERSION=1.0.0 final"; \
		exit 1; \
	fi

validate:
	@command -v jq >/dev/null || { echo "❌ jq not found"; exit 1; }
	@command -v docker >/dev/null || { echo "❌ docker not found"; exit 1; }


base: check_version validate
	@echo "Building base image..."
	$(DOCKER) build --pull -f Dockerfile \
		--build-arg VERSION=$(FINAL_VERSION) \
		--label org.opencontainers.image.base.name="ubuntu" \
		--label org.opencontainers.image.base.version="$(UBUNTU_VERSION)" \
		--label org.opencontainers.image.version="$(FINAL_VERSION)-base" \
		--label org.opencontainers.image.created="$(shell date -u +%Y-%m-%dT%H:%M:%SZ)" \
		--label org.opencontainers.image.authors="Per Jensen <per2jensen@gmail.com>" \
		-t $(BASE_IMAGE_NAME):$(UBUNTU_VERSION)-$(FINAL_VERSION) .
	$(DOCKER) tag $(BASE_IMAGE_NAME):$(UBUNTU_VERSION)-$(FINAL_VERSION) $(BASE_LATEST_TAG)


final: check_version validate base
	$(eval DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ))
	$(eval GIT_REV := $(shell git rev-parse --short HEAD))
	$(eval FINAL_TAG := $(FINAL_IMAGE_NAME):$(FINAL_VERSION))
	$(eval DOCKERHUB_TAG := $(DOCKERHUB_REPO):$(FINAL_VERSION))
	@echo "Building final image: $(FINAL_TAG)"
	$(DOCKER) build -f Dockerfile \
		--build-arg VERSION=$(FINAL_VERSION) \
		--label org.opencontainers.image.source=https://github.com/per2jensen/scrubexif \
		--label org.opencontainers.image.created="$(DATE)" \
		--label org.opencontainers.image.revision="$(GIT_REV)" \
		--label org.opencontainers.image.title="scrubexif" \
		--label org.opencontainers.image.version="$(FINAL_VERSION)" \
		--label org.opencontainers.image.ref.name="$(DOCKERHUB_REPO):$(FINAL_VERSION)" \
		--label org.opencontainers.image.description="Container for batch-scrubbing EXIF data from JPEGs using ExifTool" \
		--label org.opencontainers.image.licenses="GPL-3.0-or-later" \
		--label org.opencontainers.image.authors="Per Jensen <per2jensen@gmail.com>" \
		--label org.opencontainers.image.base.name="ubuntu" \
		--label org.opencontainers.image.base.version="$(UBUNTU_VERSION)" \
		--label org.opencontainers.image.url="https://hub.docker.com/r/per2jensen/scrubexif" \
		-t $(FINAL_TAG) \
		-t $(DOCKERHUB_TAG) .

release: check_version final log-build-json login push
	@echo "✅ Release complete for: $(DOCKERHUB_REPO):$(FINAL_VERSION)"

log-build-json: check_version
	@mkdir -p $(BUILD_LOG_DIR)
	@test -f $(BUILD_LOG_PATH) || echo "[]" > $(BUILD_LOG_PATH)

	$(eval DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ))
	$(eval GIT_REV := $(shell git rev-parse --short HEAD))
	$(eval DIGEST := $(shell docker inspect --format '{{ index .RepoDigests 0 }}' $(DOCKERHUB_REPO):$(FINAL_VERSION) 2>/dev/null || echo ""))
	$(eval IMAGE_ID := $(shell docker inspect --format '{{ .Id }}' $(FINAL_IMAGE_NAME):$(FINAL_VERSION)))

	@if [ -z "$(DIGEST)" ]; then \
		echo "❌ Digest not found. Make sure the image has been pushed."; \
		exit 1; \
	fi

	$(eval DIGEST_ONLY := $(shell echo "$(DIGEST)" | cut -d'@' -f2))
	$(eval BUILD_NUMBER := $(shell test -f $(BUILD_LOG_PATH) && jq length $(BUILD_LOG_PATH) || echo 0))

	@jq --arg version "$(FINAL_VERSION)" \
	    --arg base "$(BASE_IMAGE_NAME):$(UBUNTU_VERSION)-$(FINAL_VERSION)" \
	    --arg rev "$(GIT_REV)" \
	    --arg created "$(DATE)" \
	    --arg url "https://hub.docker.com/r/$(DOCKERHUB_REPO)/tags/$(FINAL_VERSION)" \
	    --arg digest "$(DIGEST_ONLY)" \
	    --arg image_id "$(IMAGE_ID)" \
	    --argjson build_number $(BUILD_NUMBER) \
	    '. += [{"build_number": $$build_number, "tag": $$version, "base_image": $$base, "git_revision": $$rev, "created": $$created, "dockerhub_tag_url": $$url, "digest": $$digest, "image_id": $$image_id}]' \
	    $(BUILD_LOG_PATH) > $(BUILD_LOG_PATH).tmp && mv $(BUILD_LOG_PATH).tmp $(BUILD_LOG_PATH)

push: check_version
	@echo "Pushing $(DOCKERHUB_REPO):$(FINAL_VERSION) to Docker Hub..."
	$(DOCKER) push $(DOCKERHUB_REPO):$(FINAL_VERSION)

login:
	@echo "🔐 Logging in to Docker Hub..."
	@if [ -z "$$DOCKER_USER" ] || [ -z "$$DOCKER_TOKEN" ]; then \
		echo "❌ ERROR: You must export DOCKER_USER and DOCKER_TOKEN."; \
		exit 1; \
	fi
	echo "$$DOCKER_TOKEN" | $(DOCKER) login -u "$$DOCKER_USER" --password-stdin

clean:
	@if [ -z "$(FINAL_VERSION)" ]; then \
		echo "❌ FINAL_VERSION not set"; exit 1; \
	fi
	-$(DOCKER) rmi -f $(BASE_IMAGE_NAME):$(UBUNTU_VERSION)-$(FINAL_VERSION) || true
	-$(DOCKER) rmi -f $(BASE_LATEST_TAG) || true
	-$(DOCKER) rmi -f $(FINAL_IMAGE_NAME):$(FINAL_VERSION) || true

clean-all:
	-docker images -q 'scrubexif*' | xargs -r docker rmi -f

# ================================
# Dev workflow
# ================================


BUILD_DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

dev: FINAL_VERSION=dev
dev: validate base
	@echo "Building development image: scrubexif:dev ..."
	$(DOCKER) build -f Dockerfile \
		--build-arg VERSION=$(FINAL_VERSION) \
		--label org.opencontainers.image.created="$(shell date -u +%Y-%m-%dT%H:%M:%SZ)" \
		--label org.opencontainers.image.source=https://github.com/per2jensen/scrubexif \
		--label org.opencontainers.image.revision="$(shell git rev-parse --short HEAD)" \
		--label org.opencontainers.image.title="scrubexif" \
		--label org.opencontainers.image.version="$(FINAL_VERSION)" \
		--label org.opencontainers.image.ref.name="$(DOCKERHUB_REPO):$(FINAL_VERSION)" \
		--label org.opencontainers.image.description="Container for batch-scrubbing EXIF data from JPEGs using ExifTool" \
		--label org.opencontainers.image.licenses="GPL-3.0-or-later" \
		--label org.opencontainers.image.authors="Per Jensen <per2jensen@gmail.com>" \
		--label org.opencontainers.image.base.name="ubuntu" \
		--label org.opencontainers.image.base.version="$(UBUNTU_VERSION)" \
        --label org.opencontainers.image.url="https://hub.docker.com/r/per2jensen/scrubexif" \
		-t $(FINAL_IMAGE_NAME):$(FINAL_VERSION) .


dev-clean:
	@echo "Removing dev image..."
	-$(DOCKER) rmi -f scrubexif:dev || true

show-labels:
	@if [ -z "$(FINAL_VERSION)" ]; then \
		echo "❌ FINAL_VERSION is not set."; \
	else \
		docker inspect $(FINAL_IMAGE_NAME):$(FINAL_VERSION) \
		--format '{{ range $$k, $$v := .Config.Labels }}{{ printf "%-40s %s\n" $$k $$v }}{{ end }}'; \
	fi

tag:
	@if [ -z "$(FINAL_VERSION)" ]; then \
		echo "❌ FINAL_VERSION is not set"; \
	else \
		echo "Base Image (versioned):  $(BASE_IMAGE_NAME):$(UBUNTU_VERSION)-$(FINAL_VERSION)"; \
		echo "Base Image (latest):     $(BASE_LATEST_TAG)"; \
		echo "Final Image (local):     $(FINAL_IMAGE_NAME):$(FINAL_VERSION)"; \
		echo "Docker Hub Image:        $(DOCKERHUB_REPO):$(FINAL_VERSION)"; \
	fi

help:
	@echo "Available targets:"
	@grep -E '^[a-zA-Z0-9_-]+:' Makefile | grep -v '^.PHONY' | cut -d: -f1 | xargs -n1 echo " -"
