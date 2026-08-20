# Single entry point for the project: everything runs inside Docker.
# `make help` lists targets; ARGS passes extra arguments, e.g.
#   make test ARGS="tests/test_freight_models.py -x"

COMPOSE := docker compose
RUN     := $(COMPOSE) run --rm web

.PHONY: help build up down restart logs ps test test-live lint fmt check \
        migrate makemigrations shell dbshell manage seed

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

build: ## Build the application image
	$(COMPOSE) build

up: ## Start the full stack (web, worker deps, db, redis)
	$(COMPOSE) up -d

down: ## Stop the stack
	$(COMPOSE) down

restart: down up ## Restart the stack

logs: ## Tail application logs
	$(COMPOSE) logs -f web

ps: ## Show container status
	$(COMPOSE) ps

test: ## Run the deterministic test suite
	$(RUN) pytest $(ARGS)

test-live: ## Run live provider-contract tests (real API calls, costs money)
	$(RUN) pytest -m live $(ARGS)

lint: ## Ruff lint + format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

fmt: ## Auto-format
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

check: lint test ## Lint then test

migrate: ## Apply database migrations
	$(RUN) python manage.py migrate

makemigrations: ## Create migrations (ARGS=<app>)
	$(RUN) python manage.py makemigrations $(ARGS)

shell: ## Django shell
	$(RUN) python manage.py shell

dbshell: ## psql into the dev database
	$(COMPOSE) exec db psql -U goodlane -d carrier_agent

manage: ## Arbitrary manage.py command (ARGS="...")
	$(RUN) python manage.py $(ARGS)

seed: ## Seed the dataset (available once the seed command lands)
	$(RUN) python manage.py seed $(ARGS)
