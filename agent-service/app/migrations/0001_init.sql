-- agent-service's own database. It never touches ledger tables; it learns about money only from
-- Kafka events (detector) and the READONLY ledger API (agent, via MCP).

-- Consumer dedupe: written in the same transaction as the event's side effects.
create table processed_events (
    event_id     uuid        primary key,
    event_type   text        not null,
    processed_at timestamptz not null default now()
);

-- Detector's local copy of ledger activity (from EntryPosted), used to evaluate windowed rules.
create table det_entries (
    entry_id                uuid        primary key,
    transfer_id             uuid        not null,
    account_id              uuid        not null,
    counterparty_account_id uuid        not null,
    direction               text        not null check (direction in ('DEBIT', 'CREDIT')),
    amount_minor            bigint      not null check (amount_minor > 0),
    transfer_type           text        not null,
    counterparty_is_system  boolean     not null,
    account_created_at      timestamptz not null,
    posted_at               timestamptz not null
);
create index det_entries_account_time on det_entries (account_id, direction, posted_at);

-- Customer-to-customer transfer graph (from TransferPosted), used for round-trip cycle detection.
create table det_transfers (
    transfer_id     uuid        primary key,
    from_account_id uuid        not null,
    to_account_id   uuid        not null,
    amount_minor    bigint      not null check (amount_minor > 0),
    posted_at       timestamptz not null
);
create index det_transfers_from_time on det_transfers (from_account_id, posted_at);

create table cases (
    id               uuid        primary key,
    account_id       uuid        not null,
    status           text        not null check (status in
                         ('OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'ESCALATED',
                          'AUTO_CLEARED', 'CLEARED', 'FROZEN')),
    opened_at        timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    last_activity_at timestamptz not null,
    risk_score       double precision null check (risk_score between 0 and 1),
    flags            text[]      not null default '{}',
    version          integer     not null default 0
);
-- At most one non-terminal case per account; new rule hits attach to it.
create unique index cases_one_active_per_account on cases (account_id)
    where status in ('OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'ESCALATED');
create index cases_status_risk on cases (status, risk_score desc nulls last);

create table case_rule_hits (
    id           uuid        primary key,
    case_id      uuid        not null references cases (id),
    rule         text        not null,
    first_hit_at timestamptz not null,
    last_hit_at  timestamptz not null,
    hit_count    integer     not null default 1,
    details      jsonb       not null,
    constraint case_rule_hits_one_per_rule unique (case_id, rule)
);

-- Full trace of every agent investigation (Phase 4 fills these).
create table agent_runs (
    id             uuid        primary key,
    case_id        uuid        not null references cases (id),
    status         text        not null check (status in ('RUNNING', 'SUCCEEDED', 'FAILED')),
    provider       text        not null,
    model          text        not null,
    started_at     timestamptz not null default now(),
    finished_at    timestamptz null,
    latency_ms     integer     null,
    tool_calls     integer     not null default 0,
    input_tokens   integer     not null default 0,
    output_tokens  integer     not null default 0,
    -- Cost estimate in micro-USD (integer, like all money in this project).
    cost_micro_usd bigint      not null default 0,
    trace          jsonb       not null default '[]',
    error          text        null
);
create index agent_runs_case on agent_runs (case_id, started_at desc);

create table reports (
    id                 uuid             primary key,
    case_id            uuid             not null references cases (id),
    agent_run_id       uuid             not null references agent_runs (id),
    verdict            text             not null check (verdict in ('CLEAR', 'ESCALATE', 'RECOMMEND_FREEZE')),
    risk_score         double precision not null check (risk_score between 0 and 1),
    confidence         double precision not null check (confidence between 0 and 1),
    injection_detected boolean          not null,
    low_quality        boolean          not null default false,
    as_of              timestamptz      not null,
    body               jsonb            not null,
    created_at         timestamptz      not null default now()
);
create index reports_case on reports (case_id, created_at desc);

-- Human decisions: the audit trail. A FREEZE decision is the only path to a ledger freeze.
create table decisions (
    id              uuid        primary key,
    case_id         uuid        not null references cases (id),
    decision        text        not null check (decision in ('FREEZE', 'CLEAR', 'ESCALATE')),
    analyst         text        not null,
    note            text        not null,
    idempotency_key text        not null unique,
    ledger_response jsonb       null,
    created_at      timestamptz not null default now()
);
create index decisions_case on decisions (case_id, created_at);
