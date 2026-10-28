UV ?= uv
NPM ?= npm
PYTHON ?= python3
PYTHON_VERSION ?= 3.11

.PHONY: help setup web-deps build quickstart configure run run-proxy run-gateway test test-backend test-agent test-web test-browser-compat check-models
.DEFAULT_GOAL := help

help:
	@printf '%s\n' \
	  'quickstart           Build the web UI, install the core backend and initialize local configuration' \
	  'configure            Create local backend configuration with fresh credentials; keep existing files' \
	  'run                  Run the prepared core workbench; Ctrl+C stops Proxy and Gateway' \
	  'run-proxy            Run the terminal proxy in the foreground' \
	  'run-gateway          Run the web/API gateway in the foreground' \
	  'setup                Install locked application and frontend dependencies, with Agent dev tools' \
	  'build                Install locked frontend dependencies and build the production web UI' \
	  'test                 Run backend, Agent and frontend tests (local Chrome/Chromium required)' \
	  'test-backend         Run backend and deployment-tool regression tests' \
	  'test-agent           Run Agent regression tests' \
	  'test-web             Run frontend logic and UI tests' \
	  'test-browser-compat  Check browser compatibility and DOM polyfills' \
	  'check-models MODEL_DIR=/path/to/models  Verify both model files against the manifest'

web-deps:
	$(NPM) --prefix web ci

setup: web-deps
	cd box && $(UV) sync --frozen --python $(PYTHON_VERSION)
	cd agent && $(UV) sync --frozen --python $(PYTHON_VERSION) --extra dev

build: web-deps
	$(NPM) --prefix web run build

quickstart:
	UV="$(UV)" NPM="$(NPM)" PYTHON="$(PYTHON)" PYTHON_VERSION="$(PYTHON_VERSION)" ./scripts/quickstart.sh --build-only

run:
	./scripts/quickstart.sh --start-only

configure:
	$(PYTHON) tools/init_local_config.py

run-proxy:
	cd box && $(UV) run --frozen --python $(PYTHON_VERSION) auditbox-proxy

run-gateway:
	cd box && $(UV) run --frozen --python $(PYTHON_VERSION) auditbox-gateway

test: test-backend test-agent test-web

test-backend:
	cd box && $(UV) run --frozen --python $(PYTHON_VERSION) --with 'pytest>=8.4,<10' python -m pytest -q test

test-agent:
	cd agent && $(UV) run --frozen --python $(PYTHON_VERSION) --extra dev pytest -q

test-web:
	$(NPM) --prefix web test

test-browser-compat:
	$(NPM) --prefix web run test:browser-compat

check-models:
	@test -n "$(MODEL_DIR)" || { echo 'Usage: make check-models MODEL_DIR=/path/to/models' >&2; exit 2; }
	$(PYTHON) tools/check_models.py "$(MODEL_DIR)"
