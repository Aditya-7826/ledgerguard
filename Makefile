# LedgerGuard. Recipes avoid shell-specific syntax so they work from cmd, PowerShell and bash.
-include .env
export

ifeq ($(OS),Windows_NT)
MVNW := .\mvnw.cmd
else
MVNW := ./mvnw
endif

LEDGER_URL ?= http://localhost:$(or $(LEDGER_PORT),8080)
SEED ?= 42

.PHONY: help up down logs ps test test-ledger test-agent test-mcp test-sim test-ui test-eval ui-dev simulate reconcile eval loadtest

help:
	@echo "make up          - build and start the stack (docker compose)"
	@echo "make down        - stop the stack"
	@echo "make logs        - follow service logs"
	@echo "make simulate    - drive the ledger with baseline traffic + fraud scenarios (writes eval/labels.jsonl)"
	@echo "                   then open the analyst console at http://localhost:3000"
	@echo "make test        - run every service's tests"
	@echo "make eval        - score the agent on 3 full scenario runs (needs LLM_MODEL + ANTHROPIC_API_KEY)"
	@echo "make loadtest    - k6 load test + retry storm against an isolated ledger stack"
	@echo "make reconcile   - call the ledger reconcile endpoint"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

test: test-ledger test-agent test-mcp test-sim test-eval test-ui

test-ledger:
	cd ledger-service && $(MVNW) -B test

test-agent:
	cd agent-service && uv run pytest -q

test-mcp:
	cd mcp-server && uv run pytest -q

test-sim:
	cd simulator && uv run pytest -q

test-eval:
	cd eval && uv run pytest -q

test-ui:
	cd ui && npm ci --no-audit --no-fund && npm run build

ui-dev:
	cd ui && npm run dev

simulate:
	cd simulator && uv run python -m simulator --seed $(SEED) --ledger-url $(LEDGER_URL)

reconcile:
	curl -s -H "X-API-Key: $(LEDGER_ADMIN_API_KEY)" $(LEDGER_URL)/v1/admin/reconcile

eval:
	uv run --no-project --python 3.12 python eval/run_eval.py

loadtest:
	uv run --no-project --python 3.12 python loadtest/run_loadtest.py
