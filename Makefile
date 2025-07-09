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
	@if ! echo "$(FINAL_VERSION)" | grep -Eq '^(dev|[0-9]+\.[0-9]+\.[0-9]+)$$'; then \
		echo "❌ FINAL_VERSION must be 'dev' or semantic, like 0.5.3"; \
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


verify-labels:
	@echo "🔍 Verifying OCI image labels on $(FINAL_IMAGE_NAME):$(FINAL_VERSION)"
	@$(eval LABELS := org.opencontainers.image.authors \
	                  org.opencontainers.image.base.name \
	                  org.opencontainers.image.base.version \
	                  org.opencontainers.image.created \
	                  org.opencontainers.image.description \
	                  org.opencontainers.image.licenses \
	                  org.opencontainers.image.ref.name \
	                  org.opencontainers.image.revision \
	                  org.opencontainers.image.source \
	                  org.opencontainers.image.title \
	                  org.opencontainers.image.url \
	                  org.opencontainers.image.version)

	@for label in $(LABELS); do \
	  value=$$(docker inspect -f "$$${label}={{ index .Config.Labels \"$$label\" }}" $(FINAL_IMAGE_NAME):$(FINAL_VERSION) 2>/dev/null | cut -d= -f2-); \
	  if [ -z "$$value" ]; then \
	    echo "❌ Missing or empty label: $$label"; \
	    exit 1; \
	  else \
	    echo "✅ $$label: $$value"; \
	  fi; \
	done

	@echo "🎉 All required OCI labels are present."


verify-cli-version:
	@echo "🔎 Verifying scrub --version matches FINAL_VERSION ($(FINAL_VERSION))"
	@actual_version="$$(docker run --rm --entrypoint scrub $(FINAL_IMAGE_NAME):$(FINAL_VERSION) --version | head -n1 | awk '{print $$2}')" && \
	if [ "$$actual_version" != "$(FINAL_VERSION)" ]; then \
	  echo "❌ Version mismatch: CLI reports '$$actual_version', expected '$(FINAL_VERSION)'"; \
	  exit 1; \
	else \
	  echo "✅ scrub --version is correct: $(FINAL_VERSION)"; \
	fi



test-release: check_version
	@echo "🧪 Running test suite against image: $(FINAL_IMAGE_NAME):$(FINAL_VERSION)"
	SCRUBEXIF_IMAGE=$(FINAL_IMAGE_NAME):$(FINAL_VERSION) PYTHONPATH=. pytest



dry-run-release:
	@echo "🔍 Creating temporary dry-run environment..."
	@if [ -d .dryrun ]; then \
		echo "🧹 Removing stale .dryrun worktree..."; \
		git worktree remove --force .dryrun; \
	fi
	@git worktree add -f .dryrun HEAD
	@cd .dryrun && \
		echo "🚧 Running release steps in .dryrun..." && \
		DRY_RUN=1 make FINAL_VERSION=$(FINAL_VERSION) _dryrun-release-internal
	@git worktree remove .dryrun
	@echo "✅ Dry-run complete — no changes made to working directory"



_dryrun-release-internal: check_version
	@echo "🔧 Building image scrubexif:$(FINAL_VERSION) (dry-run, no push to Docker Hub)"
	@make FINAL_VERSION=$(FINAL_VERSION) update-scrub-version final verify-labels test-release update-readme-version log-build-json
	@make FINAL_VERSION=$(FINAL_VERSION) verify-cli-version --no-print-directory


release: check_version update-scrub-version final  verify-cli-version verify-labels test-release update-readme-version login push log-build-json
	@echo "✅ Release complete for: $(DOCKERHUB_REPO):$(FINAL_VERSION)"



log-build-json: check_version
	@mkdir -p $(BUILD_LOG_DIR)
	@test -f $(BUILD_LOG_PATH) || echo "[]" > $(BUILD_LOG_PATH)

	$(eval DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ))
	$(eval GIT_REV := $(shell git rev-parse --short HEAD))

	$(eval DIGEST := $(shell docker inspect --format '{{ index .RepoDigests 0 }}' $(DOCKERHUB_REPO):$(FINAL_VERSION) 2>/dev/null || echo ""))
	@if [ -z "$(DIGEST)" ]; then \
		if [ "$(DRY_RUN)" = "1" ]; then \
			echo "⚠️  Skipping digest check in dry-run mode"; \
			exit 0; \
		else \
			echo "❌ Digest not found. Make sure the image has been pushed."; \
			exit 1; \
		fi; \
	fi

	$(eval IMAGE_ID := $(shell docker inspect --format '{{ .Id }}' $(FINAL_IMAGE_NAME):$(FINAL_VERSION)))
	@if [ -z "$(IMAGE_ID)" ]; then \
		echo "❌ Image ID not found. Did you build the final image?"; \
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

	@echo "🔄 Checking if $(BUILD_LOG_PATH) changed"
	@if ! git diff --quiet $(BUILD_LOG_PATH); then \
	  git add $(BUILD_LOG_PATH); \
	  git commit -m "build-history: add $(FINAL_VERSION) metadata"; \
	  echo "✅ $(BUILD_LOG_PATH) updated and committed"; \
	else \
	  echo "ℹ️ No changes to commit — build history already up to date"; \
	fi



log-build-json-old: check_version
	@mkdir -p $(BUILD_LOG_DIR)
	@test -f $(BUILD_LOG_PATH) || echo "[]" > $(BUILD_LOG_PATH)

	$(eval DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ))
	$(eval GIT_REV := $(shell git rev-parse --short HEAD))

	$(eval DIGEST := $(shell docker inspect --format '{{ index .RepoDigests 0 }}' $(DOCKERHUB_REPO):$(FINAL_VERSION) 2>/dev/null || echo ""))
	@if [ -z "$(DIGEST)" ]; then \
		echo "❌ Digest not found. Make sure the image has been pushed."; \
		exit 1; \
	fi

	$(eval IMAGE_ID := $(shell docker inspect --format '{{ .Id }}' $(FINAL_IMAGE_NAME):$(FINAL_VERSION)))
	@if [ -z "$(IMAGE_ID)" ]; then \
		echo "❌ Image ID not found. Did you build the final image?"; \
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





update-scrub-version:
	@echo "🔄 Updating __version__ in scrub.py to VERSION=$(FINAL_VERSION)"
	@if sed -i -E 's/^__version__\s*=\s*".*"/__version__ = "$(FINAL_VERSION)"/' scrubexif/scrub.py; then \
	  if ! git diff --quiet scrubexif/scrub.py; then \
	    git add scrubexif/scrub.py; \
	    git commit -m "version updated to VERSION=$(FINAL_VERSION)"; \
	    echo "✅ scrub.py updated and committed"; \
	  else \
	    echo "ℹ️ No changes to commit — scrub.py already up to date"; \
	  fi; \
	else \
	  echo "❌ sed command failed — scrub.py not updated"; \
	  exit 1; \
	fi

update-readme-version:
	@echo "🔄 Updating version examples in README.md to VERSION=$(FINAL_VERSION)"
	@if sed -i -E "s/VERSION=[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9]+)?;/VERSION=$(FINAL_VERSION);/" README.md; then \
	  if ! git diff --quiet README.md; then \
	    git add README.md; \
	    git commit -m "examples updated to VERSION=$(FINAL_VERSION)"; \
	    echo "✅ README.md updated and committed"; \
	  else \
	    echo "ℹ️ No changes to commit — README.md already up to date"; \
	  fi; \
	else \
	  echo "❌ sed command failed — README.md not updated"; \
	  exit 1; \
	fi

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
	@echo "Check import of scrubexif.scrub in dev image"
	$(DOCKER) run --rm -it --entrypoint  python3   scrubexif:dev -c "import scrubexif.scrub; print('✅ scrubexif is importable')"


dev-clean:
	@echo "Removing dev image..."
	-$(DOCKER) rmi -f scrubexif:dev || true


paranoia:
	@echo "🧪 Manually running paranoia tests only"
	PYTHONPATH=. pytest tests/test_paranoia_gps.py

test: dev
	PYTHONPATH=. pytest


show-labels:
	@if [ -z "$(FINAL_VERSION)" ]; then \
		echo "❌ FINAL_VERSION is not set."; \
	else \
		docker inspect $(FINAL_IMAGE_NAME):$(FINAL_VERSION) \
		--format '{{ range $$k, $$v := .Config.Labels }}{{ printf "%-40s %s\n" $$k $$v }}{{ end }}'; \
	fi


show-tags:
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
