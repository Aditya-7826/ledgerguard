# LedgerGuard. Recipes avoid shell-specific syntax so they work from cmd, PowerShell and bash.
-include .env

ifeq ($(OS),Windows_NT)
MVNW := .\mvnw.cmd
else
MVNW := ./mvnw
endif

LEDGER_URL ?= http://localhost:$(or $(LEDGER_PORT),8080)

.PHONY: help up down logs ps test test-ledger reconcile

help:
	@echo "make up          - build and start the stack (docker compose)"
	@echo "make down        - stop the stack"
	@echo "make logs        - follow service logs"
	@echo "make test        - run every service's tests"
	@echo "make test-ledger - run ledger-service tests (Testcontainers; needs Docker)"
	@echo "make reconcile   - call the ledger reconcile endpoint"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

test: test-ledger

test-ledger:
	cd ledger-service && $(MVNW) -B test

reconcile:
	curl -s -H "X-API-Key: $(LEDGER_ADMIN_API_KEY)" $(LEDGER_URL)/v1/admin/reconcile
