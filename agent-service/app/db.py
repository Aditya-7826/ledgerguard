"""Database connection + a tiny forward-only SQL migration runner."""

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_LOCK_ID = 7_401_331  # arbitrary advisory-lock key so concurrent starters migrate one at a time


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True)


def migrate(url: str) -> list[str]:
    """Applies migrations/NNNN_*.sql in order, once each. Returns the versions applied now."""
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(
                "create table if not exists schema_migrations "
                "(version text primary key, applied_at timestamptz not null default now())"
            )
            done = {r[0] for r in conn.execute("select version from schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.stem
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("insert into schema_migrations (version) values (%s)", (version,))
                applied.append(version)
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied
