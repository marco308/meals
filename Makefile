.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND_DIR := backend
UV := uv

# ------------------------------------------------------------------ meta

.PHONY: help
help: ## Show this help
	@echo "Meals — dev environment commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup

.PHONY: setup
setup: ## Install backend + mcp deps into local .venvs (uses uv)
	cd $(BACKEND_DIR) && $(UV) sync
	cd mcp && $(UV) sync

# ------------------------------------------------------------------ full dev stack (Docker: Postgres + API)

.PHONY: up
up: dev ## Self-host the whole stack (Postgres + API + MCP) in Docker — alias the site advertises

.PHONY: dev
dev: ## Start the full dev stack (Postgres + API) in Docker
	docker compose up --build -d
	@echo ""
	@echo "  API:      http://localhost:8000"
	@echo "  Docs:     http://localhost:8000/docs"
	@echo "  Health:   http://localhost:8000/healthz"
	@echo ""
	@echo "  make logs   to tail logs"
	@echo "  make seed   to load demo data"

.PHONY: down
down: ## Stop the dev stack
	docker compose down

.PHONY: logs
logs: ## Tail dev stack logs
	docker compose logs -f

.PHONY: ps
ps: ## Show dev stack status
	docker compose ps

.PHONY: nuke
nuke: ## Stop the stack AND delete the database volume (destructive)
	docker compose down -v

# ------------------------------------------------------------------ local (no Docker: SQLite + uvicorn)

.PHONY: run
run: setup ## Run the API locally on SQLite (no Docker needed)
	cd $(BACKEND_DIR) && $(UV) run alembic upgrade head && $(UV) run uvicorn app.main:app --reload --port 8000

.PHONY: db
db: ## Start just Postgres in Docker (for local API against Postgres)
	docker compose up -d db

# ------------------------------------------------------------------ quality

.PHONY: test
test: setup ## Run all tests (backend with coverage + mcp); no Docker needed
	cd $(BACKEND_DIR) && $(UV) run pytest --cov --cov-report=term-missing
	cd mcp && $(UV) run pytest -q

.PHONY: test-fast
test-fast: ## Run tests without coverage
	cd $(BACKEND_DIR) && $(UV) run pytest -q
	cd mcp && $(UV) run pytest -q

.PHONY: lint
lint: setup ## Ruff lint + format check (backend + mcp)
	cd $(BACKEND_DIR) && $(UV) run ruff check app tests && $(UV) run ruff format --check app tests
	cd mcp && $(UV) run ruff check . && $(UV) run ruff format --check .

.PHONY: fmt
fmt: ## Auto-format + fix lint issues
	cd $(BACKEND_DIR) && $(UV) run ruff check --fix app tests && $(UV) run ruff format app tests
	cd mcp && $(UV) run ruff check --fix . && $(UV) run ruff format .

# ------------------------------------------------------------------ ios (needs Xcode + xcodegen)

IOS_DIR := ios/Meals
IOS_DEST := platform=iOS Simulator,name=iPhone 17

.PHONY: ios-build
ios-build: ## Build the iOS app for the simulator
	cd $(IOS_DIR) && xcodegen generate && xcodebuild -project Meals.xcodeproj -scheme Meals \
		-destination '$(IOS_DEST)' -derivedDataPath build build 2>&1 | grep -E "error:|warning:|BUILD" | tail -5

.PHONY: ios-screenshots
ios-screenshots: ## Capture the App Store screenshot set (throwaway API + throwaway simulator)
	./ios/screenshots/capture.sh

.PHONY: ios-test
ios-test: ## Run the iOS unit tests
	cd $(IOS_DIR) && xcodegen generate && xcodebuild -project Meals.xcodeproj -scheme Meals \
		-destination '$(IOS_DEST)' -derivedDataPath build test 2>&1 | grep -E "error:|Executed.*test|TEST " | tail -6

# Apple account identifiers. Not credentials, but they identify *an* Apple
# account, so they live in an untracked ios/.env rather than in the repo — put
# your own there:
#
#   MEALS_DEVELOPMENT_TEAM=ABCDE12345          # needed to archive for a device
#   ASC_KEY_ID=ABCD123456                      # the two below are for TestFlight
#   ASC_ISSUER=00000000-0000-0000-0000-000000000000
#
# MEALS_DEVELOPMENT_TEAM is exported because xcodegen expands it while generating
# the project. The App Store Connect private key (AuthKey_<id>.p8) is a real
# credential and never leaves ~/.appstoreconnect.
-include ios/.env
export MEALS_DEVELOPMENT_TEAM
ASC_KEY_PATH := $(HOME)/.appstoreconnect/private_keys/AuthKey_$(ASC_KEY_ID).p8

.PHONY: ios-export-options
ios-export-options:
	@test -n "$(MEALS_DEVELOPMENT_TEAM)" || \
		{ echo "MEALS_DEVELOPMENT_TEAM must be set — see the comment above ios-testflight in the Makefile"; exit 1; }
	@printf '%s\n' \
		'<?xml version="1.0" encoding="UTF-8"?>' \
		'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
		'<plist version="1.0">' \
		'<dict>' \
		'    <key>method</key>' \
		'    <string>app-store-connect</string>' \
		'    <key>signingStyle</key>' \
		'    <string>automatic</string>' \
		'    <key>teamID</key>' \
		'    <string>$(MEALS_DEVELOPMENT_TEAM)</string>' \
		'</dict>' \
		'</plist>' > $(IOS_DIR)/ExportOptions.plist

.PHONY: ios-testflight
ios-testflight: ios-export-options ## Archive, export, and upload the iOS app to TestFlight (needs ios/.env)
	@test -n "$(ASC_KEY_ID)" -a -n "$(ASC_ISSUER)" || \
		{ echo "ASC_KEY_ID and ASC_ISSUER must be set — see the comment above ios-testflight in the Makefile"; exit 1; }
	cd $(IOS_DIR) && xcodegen generate && \
	xcodebuild archive -project Meals.xcodeproj -scheme Meals \
		-archivePath ./build/Meals.xcarchive -destination 'generic/platform=iOS' \
		-allowProvisioningUpdates -authenticationKeyPath $(ASC_KEY_PATH) \
		-authenticationKeyID $(ASC_KEY_ID) -authenticationKeyIssuerID $(ASC_ISSUER) 2>&1 | grep -E "error:|ARCHIVE" && \
	xcodebuild -exportArchive -archivePath ./build/Meals.xcarchive -exportPath ./build/export \
		-exportOptionsPlist ExportOptions.plist -allowProvisioningUpdates \
		-authenticationKeyPath $(ASC_KEY_PATH) -authenticationKeyID $(ASC_KEY_ID) \
		-authenticationKeyIssuerID $(ASC_ISSUER) 2>&1 | grep -E "error|EXPORT" && \
	xcrun altool --upload-app -f ./build/export/Meals.ipa -t ios \
		--apiKey $(ASC_KEY_ID) --apiIssuer $(ASC_ISSUER)

# ------------------------------------------------------------------ database

.PHONY: migrate
migrate: ## Apply migrations (local SQLite by default; set DATABASE_URL for Postgres)
	cd $(BACKEND_DIR) && $(UV) run alembic upgrade head

.PHONY: migration
migration: ## Create a new migration: make migration m="add foo"
	cd $(BACKEND_DIR) && $(UV) run alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Load demo data (into the Docker stack's API by default)
	cd $(BACKEND_DIR) && $(UV) run python -m app.seed

# ------------------------------------------------------------------ deployment

# deploy/ is gitignored: a swarm stack file describes one specific set of
# machines, which isn't much use to anyone else and tells the internet more about
# them than it needs to know. docker-compose.yml is the reference deployment.
#
# Untracked also means it exists only in the checkout it was created in: a
# `git worktree` tree gets a clean copy of tracked files and no deploy/ at all,
# so `make deploy` from a worktree used to fail. Fall back to the main
# worktree's copy, and hand the script *this* tree as the sources to sync
# (MEALS_REPO_ROOT) — otherwise deploying from a worktree would quietly ship
# main's code while claiming success.
#
# The script pulls released images by digest by default, so MEALS_REPO_ROOT
# only decides what gets built in its MEALS_DEPLOY_BUILD=1 mode. Deploying a
# specific release is MEALS_VERSION=1.2.3 make deploy.
MAIN_WORKTREE := $(patsubst %/.git,%,$(shell git rev-parse --path-format=absolute --git-common-dir 2>/dev/null))
DEPLOY_SH := $(firstword $(wildcard deploy/deploy.sh $(MAIN_WORKTREE)/deploy/deploy.sh))

.PHONY: deploy
deploy: ## Deploy to your swarm (needs an untracked deploy/deploy.sh)
	@test -n "$(DEPLOY_SH)" && test -x "$(DEPLOY_SH)" || \
		{ echo "no deploy/deploy.sh — it's gitignored and environment-specific; see 'Deployment notes' in README.md"; exit 1; }
	MEALS_REPO_ROOT="$(CURDIR)" "$(DEPLOY_SH)"

# ------------------------------------------------------------------ cleanup

.PHONY: clean
clean: ## Remove caches, venv, coverage artifacts
	rm -rf $(BACKEND_DIR)/.venv $(BACKEND_DIR)/.pytest_cache $(BACKEND_DIR)/.ruff_cache $(BACKEND_DIR)/htmlcov $(BACKEND_DIR)/.coverage
	find . -type d -name __pycache__ -not -path "./.git/*" -exec rm -rf {} +
