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

.PHONY: ios-test
ios-test: ## Run the iOS unit tests
	cd $(IOS_DIR) && xcodegen generate && xcodebuild -project Meals.xcodeproj -scheme Meals \
		-destination '$(IOS_DEST)' -derivedDataPath build test 2>&1 | grep -E "error:|Executed.*test|TEST " | tail -6

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

# ------------------------------------------------------------------ cleanup

.PHONY: clean
clean: ## Remove caches, venv, coverage artifacts
	rm -rf $(BACKEND_DIR)/.venv $(BACKEND_DIR)/.pytest_cache $(BACKEND_DIR)/.ruff_cache $(BACKEND_DIR)/htmlcov $(BACKEND_DIR)/.coverage
	find . -type d -name __pycache__ -not -path "./.git/*" -exec rm -rf {} +
