# Connection used by test, demo, migrate and seed when DATABASE_URL is not set.
# It matches the database scripts/cloud_session_start.sh creates in a Claude
# Code cloud session. Anything set in the environment wins.
DATABASE_URL ?= postgresql://pokearb:pokearb@127.0.0.1:5432/pokearb
export DATABASE_URL

# python3, not python: Ubuntu does not ship a bare `python` by default.
PYTHON ?= python3

.PHONY: help install test test-unit test-integration lint typecheck up down \
        db-up db-down migrate seed seed-demo smoke demo

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

install:  ## Install dev dependencies and the core package
	$(PYTHON) -m pip install -r requirements-dev.txt
	$(PYTHON) -m pip install -e packages/core

test:  ## Everything. Integration tests skip loudly without a database.
	PYTHONPATH=packages/core $(PYTHON) -m pytest tests/ -v

test-unit:  ## No database, no network
	PYTHONPATH=packages/core $(PYTHON) -m pytest tests/ -v --ignore=tests/test_integration_db.py

test-integration:  ## Requires a database; set DATABASE_URL or run make db-up
	PYTHONPATH=packages/core $(PYTHON) -m pytest tests/test_integration_db.py -v

lint:  ## Ruff
	ruff check packages services tests

typecheck:  ## mypy over the core engines
	mypy packages/core/pokearb_core

up:  ## Start the stack
	docker compose up -d --build

down:  ## Stop the stack
	docker compose down

migrate:  ## Apply migrations in order to a running database
	for f in migrations/versions/*.sql; do \
	  echo "applying $$f"; \
	  psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$$f" || exit 1; \
	done

seed:  ## Load sources, policy parameters and provisional condition priors
	psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -f seed/sources.sql
	psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -f seed/policy.sql
	psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -f seed/condition_priors.sql

seed-demo:  ## Load the labelled synthetic sources used by make demo
	psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -f seed/demo/sources.sql

db-up:  ## Just the database, for tests
	docker compose up -d db

db-down:
	docker compose stop db

demo:  ## One complete workflow: import to snapshot and alert decision
	PYTHONPATH=packages/core:services/worker $(PYTHON) scripts/demo.py

smoke:  ## End-to-end check against live ECB rates
	PYTHONPATH=packages/core $(PYTHON) scripts/smoke.py
