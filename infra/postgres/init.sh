#!/bin/sh
# Runs once, on first start of an empty Postgres volume.
# One database + owner role per service, so agent-service can never touch ledger tables.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v ledger_pw="$LEDGER_DB_PASSWORD" -v agent_pw="$AGENT_DB_PASSWORD" <<'SQL'
CREATE ROLE ledger LOGIN PASSWORD :'ledger_pw';
CREATE DATABASE ledger OWNER ledger;
REVOKE ALL ON DATABASE ledger FROM PUBLIC;

CREATE ROLE agent LOGIN PASSWORD :'agent_pw';
CREATE DATABASE agent OWNER agent;
REVOKE ALL ON DATABASE agent FROM PUBLIC;
SQL
