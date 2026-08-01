# SPDX-License-Identifier: GPL-3.0-or-later
#
# Usage examples:
# ---------------
# make dev-clean dev
# make FINAL_VERSION=1.0.0 final


# ================================
# Configuration
# ================================

SHELL := /bin/bash

DOCKER ?= docker
DOCKER_BUILD_FLAGS ?=
DOCKER_RUN := $(DOCKER) run --read-only --security-opt no-new-privileges --rm $(if $(CI),,-it) \
  --tmpfs /tmp:rw,exec,nosuid,size=64m \
  -e SCRUBEXIF_STABLE_SECONDS=$(SCRUBEXIF_STABLE_SECONDS) \
  -e SCRUBEXIF_STATE=$(SCRUBEXIF_STATE)

UBUNTU_VERSION ?= 24.04

BASE_IMAGE_NAME = scrubexif-base
FINAL_IMAGE_NAME = scrubexif
DOCKERHUB_REPO = per2jensen/scrubexif
BASE_LATEST_TAG = $(BASE_IMAGE_NAME):$(UBUNTU_VERSION)

BUILD_LOG_DIR ?= doc
BUILD_LOG_FILE ?= build-history.json
BUILD_LOG_PATH := $(BUILD_LOG_DIR)/$(BUILD_LOG_FILE)
EXPECTED_CLI_VERSION ?= $(FINAL_VERSION)
BUILD_GIT_REV ?=
PUSHED_IMAGE_DIGEST ?=
SOURCE_DIR ?= .
EXPECTED_SOURCE_COMMIT ?=

REFRESH_CONTROLLER_TESTS := \
	tests/test_compute_refresh_version.py \
	tests/test_dockerhub_manifest.py \
	tests/test_grype_sarif_summary.py \
	tests/test_image_refresh_workflow.py \
	tests/test_refresh_source_boundary.py \
	tests/test_remove_dockerhub_tag.py \
	tests/test_security_tool_versions.py \
	tests/test_update_build_log.py

export SCRUBEXIF_STABLE_SECONDS ?= 0
export SCRUBEXIF_STATE ?= /tmp/.scrubexif_state.test.json


# Declare phony targets (they don't correspond to files)
.PHONY: \
  check_version validate base final verify-labels verify-cli-version \
  validate-refresh-source refresh-final refresh-test test-refresh-controller \
  test-release dry-run-release _dryrun-release-internal \
  log-build-json update-readme-version update-scrub-version update-details-version update-index-html-version \
  push login clean clean-all dev dev-clean paranoia test test-nightly test-soak soak \
  show-labels show-tags help


# ================================
# Targets
# ================================

check_version:
	@if [ -z "$(FINAL_VERSION)" ]; then \
		echo "❌ ERROR: You must set FINAL_VERSION explicitly."; \
		echo "   Example: make FINAL_VERSION=1.0.0 final"; \
		exit 1; \
	fi
	@if ! echo "$(FINAL_VERSION)" | grep -Eq '^(dev|[0-9]+\.[0-9]+\.[0-9]+(-[1-9][0-9]*)?)$$'; then \
		echo "❌ FINAL_VERSION must be 'dev', semantic (0.5.3), or a numeric refresh (0.5.3-1)"; \
		exit 1; \
	fi


validate:
	@command -v jq >/dev/null || { echo "❌ jq not found"; exit 1; }
	@command -v docker >/dev/null || { echo "❌ docker not found"; exit 1; }


validate-refresh-source:
	@if [ -z "$(SOURCE_DIR)" ]; then \
		echo "❌ SOURCE_DIR must be a non-empty path"; \
		exit 1; \
	fi
	@if [ -z "$(EXPECTED_SOURCE_COMMIT)" ]; then \
		echo "❌ EXPECTED_SOURCE_COMMIT must be set for a refresh build"; \
		exit 1; \
	fi
	@if [ ! -d "$(SOURCE_DIR)" ]; then \
		echo "❌ Refresh source directory does not exist: $(SOURCE_DIR)"; \
		exit 1; \
	fi
	@controller_dir="$$(realpath "$(CURDIR)")"; \
	source_dir="$$(realpath "$(SOURCE_DIR)")"; \
	if [ "$$controller_dir" = "$$source_dir" ]; then \
		echo "❌ Refresh source must be isolated from the controller checkout"; \
		exit 1; \
	fi
	@if ! git -C "$(SOURCE_DIR)" rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		echo "❌ Refresh source is not a Git worktree: $(SOURCE_DIR)"; \
		exit 1; \
	fi
	@source_dir="$$(realpath "$(SOURCE_DIR)")"; \
	source_root="$$(git -C "$(SOURCE_DIR)" rev-parse --show-toplevel)"; \
	if [ "$$source_dir" != "$$source_root" ]; then \
		echo "❌ SOURCE_DIR must be the root of the stable source worktree"; \
		exit 1; \
	fi
	@actual_commit="$$(git -C "$(SOURCE_DIR)" rev-parse HEAD)"; \
	expected_commit="$$(git -C "$(SOURCE_DIR)" rev-parse "$(EXPECTED_SOURCE_COMMIT)^{commit}")"; \
	if [ "$$actual_commit" != "$$expected_commit" ]; then \
		echo "❌ Refresh source commit mismatch: expected $$expected_commit, found $$actual_commit"; \
		exit 1; \
	fi
	@if [ -n "$$(git -C "$(SOURCE_DIR)" status --porcelain --untracked-files=all)" ]; then \
		echo "❌ Refresh source worktree contains modifications or untracked files"; \
		git -C "$(SOURCE_DIR)" status --short; \
		exit 1; \
	fi
	@echo "✅ Refresh source is an isolated, clean checkout of $(EXPECTED_SOURCE_COMMIT)"


refresh-final: validate-refresh-source final


refresh-test: validate-refresh-source test-release


test-refresh-controller:
	@echo "🧪 Running refresh controller tests from $(CURDIR)"
	PYTHONPATH=. pytest $(REFRESH_CONTROLLER_TESTS)


final: check_version validate
	$(eval DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ))
	$(eval GIT_REV := $(shell git -C "$(SOURCE_DIR)" rev-parse --short HEAD))
	$(eval FINAL_TAG := $(FINAL_IMAGE_NAME):$(FINAL_VERSION))
	$(eval DOCKERHUB_TAG := $(DOCKERHUB_REPO):$(FINAL_VERSION))
	$(eval DOCKERHUB_LATEST := $(DOCKERHUB_REPO):latest)
	@echo "Building final image: $(FINAL_TAG)"
	$(DOCKER) build $(DOCKER_BUILD_FLAGS) -f "$(SOURCE_DIR)/Dockerfile" \
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
		-t $(DOCKERHUB_TAG) \
		-t $(DOCKERHUB_LATEST) "$(SOURCE_DIR)"


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


verify-cli-version: check_version
	@if ! echo "$(EXPECTED_CLI_VERSION)" | grep -Eq '^(dev|[0-9]+\.[0-9]+\.[0-9]+)$$'; then \
	  echo "❌ EXPECTED_CLI_VERSION must be 'dev' or semantic, like 0.5.3"; \
	  exit 1; \
	fi
	@echo "🔎 Verifying scrub --version matches EXPECTED_CLI_VERSION ($(EXPECTED_CLI_VERSION))"
	@actual_version="$$($(DOCKER) run  --read-only --security-opt no-new-privileges --rm $(FINAL_IMAGE_NAME):$(FINAL_VERSION) --version | head -n1 | awk '{print $$2}')" && \
	if [ "$$actual_version" != "$(EXPECTED_CLI_VERSION)" ]; then \
	  echo "❌ Version mismatch: CLI reports '$$actual_version', expected '$(EXPECTED_CLI_VERSION)'"; \
	  exit 1; \
	else \
	  echo "✅ scrub --version is correct: $(EXPECTED_CLI_VERSION)"; \
	fi



test-release: check_version
	@echo "🧪 Running test suite against image: $(FINAL_IMAGE_NAME):$(FINAL_VERSION)"
	cd "$(SOURCE_DIR)" && \
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
	@make FINAL_VERSION=$(FINAL_VERSION) update-scrub-version final verify-labels test-release update-details-version
	@make FINAL_VERSION=$(FINAL_VERSION) verify-cli-version --no-print-directory


log-build-json: check_version
ifeq ($(FINAL_VERSION),dev)
	@echo "ℹ️ Skipping build log for dev build"
else

	@mkdir -p $(BUILD_LOG_DIR)
	@test -f $(BUILD_LOG_PATH) || echo "[]" > $(BUILD_LOG_PATH)

	$(eval DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ))
	$(eval GIT_REV := $(if $(BUILD_GIT_REV),$(BUILD_GIT_REV),$(shell git rev-parse --short HEAD)))

	$(eval DIGEST := $(if $(PUSHED_IMAGE_DIGEST),$(PUSHED_IMAGE_DIGEST),$(shell docker inspect --format '{{ index .RepoDigests 0 }}' $(DOCKERHUB_REPO):$(FINAL_VERSION) 2>/dev/null || echo "")))
	@if [ -z "$(DIGEST)" ]; then \
		if [ "$(DRY_RUN)" = "1" ]; then \
			echo "⚠️  Skipping digest check in dry-run mode"; \
			exit 0; \
		else \
			echo "❌ Digest not found. Make sure the image has been pushed."; \
			exit 1; \
		fi; \
	fi

	$(eval IMAGE_ID := $(shell docker inspect --format '{{ .Id }}' $(FINAL_IMAGE_NAME):$(FINAL_VERSION) 2>/dev/null || echo ""))
	@if [ -z "$(IMAGE_ID)" ]; then \
		echo "❌ Image ID not found. Did you build the final image?"; \
		exit 1; \
	fi

	$(eval DIGEST_ONLY := $(shell echo "$(DIGEST)" | cut -d'@' -f2))
	$(eval BUILD_NUMBER := $(shell test -f $(BUILD_LOG_PATH) && jq length $(BUILD_LOG_PATH) || echo 0))

	@GRYPE_SARIF="grype-results-$(FINAL_VERSION).sarif"; \
	SBOM_FILE="sbom-$(FINAL_VERSION).spdx.json"; \
	if [ -f "$$GRYPE_SARIF" ]; then \
	  echo "ℹ️ Including Grype scan summary from $$GRYPE_SARIF"; \
	else \
	  echo "ℹ️ No Grype SARIF file found at $$GRYPE_SARIF; skipping vulnerability summary."; \
	fi; \
	if [ -f "$$SBOM_FILE" ]; then \
	  echo "ℹ️ Including SBOM reference: $$SBOM_FILE"; \
	else \
	  echo "ℹ️ No SBOM file found at $$SBOM_FILE; skipping SBOM metadata."; \
	fi; \
	python3 scripts/update_build_log.py \
	  --log "$(BUILD_LOG_PATH)" \
	  --build-number $(BUILD_NUMBER) \
	  --version "$(FINAL_VERSION)" \
	  --base "$(BASE_IMAGE_NAME):$(UBUNTU_VERSION)-$(FINAL_VERSION)" \
	  --git-rev "$(GIT_REV)" \
	  --created "$(DATE)" \
	  --url "https://hub.docker.com/r/$(DOCKERHUB_REPO)/tags/$(FINAL_VERSION)" \
	  --digest "$(DIGEST_ONLY)" \
	  --image-id "$(IMAGE_ID)" \
	  --grype-sarif "$$GRYPE_SARIF" \
	  --sbom-file "$$SBOM_FILE" \
	  --sbom-release-asset-url "$${SBOM_RELEASE_ASSET_URL:-}" \
	  --cosign-signed "$${COSIGN_SIGNED:-false}" \
	  --cosign-rekor-url "$${COSIGN_REKOR_URL:-}" \
	  --cosign-image-digest "$${COSIGN_IMAGE_DIGEST:-}" \
	  --build-runner "$${BUILD_RUNNER:-}" \
	  --github-run-id "$${GITHUB_RUN_ID:-}" \
	  --github-run-url "$${GITHUB_RUN_URL:-}" \
	  --syft-version "$${SYFT_VERSION:-}" \
	  --grype-version "$${GRYPE_VERSION:-}"

	@echo "✅ $(BUILD_LOG_PATH) updated"

endif


update-scrub-version:
	@echo "🔄 Updating __version__ in scrub.py to VERSION=$(FINAL_VERSION)"
	@if sed -i -E 's/^__version__\s*=\s*".*"/__version__ = "$(FINAL_VERSION)"/' scrubexif/scrub.py; then \
	  echo "✅ scrub.py updated"; \
	else \
	  echo "❌ sed command failed — scrub.py not updated"; \
	  exit 1; \
	fi

update-details-version:
	@echo "🔄 Updating version examples in DETAILS.md to VERSION=$(FINAL_VERSION)"
	@if sed -i -E "s/VERSION=[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9]+)?;/VERSION=$(FINAL_VERSION);/" doc/DETAILS.md; then \
	  echo "✅ DETAILS.md updated"; \
	else \
	  echo "❌ sed command failed — DETAILS.md not updated"; \
	  exit 1; \
	fi

update-readme-version:
	@echo "🔄 Updating version examples in README.md to version: $(FINAL_VERSION)"
	@if sed -i -E "s/:[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?(-[a-zA-Z0-9]+)?/:$(FINAL_VERSION)/" README.md; then \
	  echo "✅ README.md updated"; \
	else \
	  echo "❌ sed command failed — README.md not updated"; \
	  exit 1; \
	fi

update-index-html-version:
	@echo "🔄 Updating version examples in index.html to version: $(FINAL_VERSION)"
	@if sed -i -E "s/:[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?(-[a-zA-Z0-9]+)?/:$(FINAL_VERSION)/" index.html; then \
	  echo "✅ index.html updated"; \
	else \
	  echo "❌ sed command failed — index.html not updated"; \
	  exit 1; \
	fi


push: check_version
	@echo "Pushing $(DOCKERHUB_REPO):$(FINAL_VERSION) to Docker Hub..."
	$(DOCKER) push $(DOCKERHUB_REPO):$(FINAL_VERSION)
	@echo "Pushing $(DOCKERHUB_REPO):latest to Docker Hub..."
	$(DOCKER) push $(DOCKERHUB_REPO):latest


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

dev:
	$(eval FINAL_VERSION := dev)
	@echo "Building development image: scrubexif:dev ..."
	$(DOCKER) build $(DOCKER_BUILD_FLAGS) -f Dockerfile \
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
	# Safe TTY for local, non-TTY for CI
	$(DOCKER_RUN) --entrypoint  python3   scrubexif:dev -c "import scrubexif.scrub; print('✅ scrubexif is importable')"


dev-clean:
	@echo "Removing dev image..."
	-$(DOCKER) rmi -f scrubexif:dev || true


paranoia:
	@echo "🧪 Manually running paranoia tests only"
	PYTHONPATH=. pytest tests/test_paranoia_gps.py


test: dev
	@echo "🔧 SCRUBEXIF_STABLE_SECONDS=$(SCRUBEXIF_STABLE_SECONDS)"
	@echo "🔧 SCRUBEXIF_STATE=$(SCRUBEXIF_STATE)"
	PYTHONPATH=. pytest


test-nightly: dev
	@echo "Running nightly (stability-gate) tests…"
	PYTHONPATH=. pytest -m nightly -q


test-soak: dev
	@echo "⏲️  Running real-time soak via pytest (slow)."
	@echo "    Set SOAK_MINUTES, SOAK_INTERVAL_SEC, SOAK_STABLE_SECONDS, SOAK_BATCH to tune."
	PYTHONPATH=. pytest -m soak -q


soak: dev
	@echo "⏲️  Running standalone soak script (slow)."
	@chmod +x scripts/soak.sh
	SCRUBEXIF_IMAGE=scrubexif:dev ./scripts/soak.sh


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
		echo "Docker Hub Latest:       $(DOCKERHUB_REPO):latest"; \
	fi


help:
	@echo "Available targets:"
	@grep -E '^[a-zA-Z0-9_-]+:' Makefile | grep -v '^.PHONY' | cut -d: -f1 | xargs -n1 echo " -"
