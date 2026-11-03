-- LedgerGuard ledger schema.
-- All money is BIGINT minor units (paise). Never float/double/numeric for stored amounts.

create table accounts (
    id          uuid        primary key,
    owner_name  varchar(200) not null,
    currency    char(3)     not null check (currency ~ '^[A-Z]{3}$'),
    status      varchar(10) not null check (status in ('ACTIVE', 'FROZEN', 'CLOSED')),
    is_system   boolean     not null default false,
    created_at  timestamptz not null default now(),
    version     bigint      not null default 0
);

-- Projection of sum(entries) per account, updated in the same transaction as the entries.
-- Sign convention: balance = credits - debits.
create table account_balances (
    account_id    uuid        primary key references accounts (id),
    balance_minor bigint      not null default 0,
    updated_at    timestamptz not null default now()
);

create table transfers (
    id                   uuid        primary key,
    client_id            varchar(64) not null,
    idempotency_key      varchar(64) not null,
    from_account_id      uuid        not null references accounts (id),
    to_account_id        uuid        not null references accounts (id),
    amount_minor         bigint      not null check (amount_minor > 0),
    currency             char(3)     not null,
    memo                 varchar(280),
    type                 varchar(10) not null check (type in ('TRANSFER', 'DEPOSIT', 'REVERSAL')),
    reverses_transfer_id uuid        null references transfers (id),
    created_at           timestamptz not null default now(),
    constraint transfers_not_self check (from_account_id <> to_account_id),
    constraint transfers_reversal_link check ((type = 'REVERSAL') = (reverses_transfer_id is not null)),
    -- Backstop: even if the idempotency table were bypassed, one key can create one transfer.
    constraint transfers_idem_unique unique (client_id, idempotency_key)
);

-- A transfer can be reversed at most once (backstop for the application check).
create unique index transfers_one_reversal on transfers (reverses_transfer_id) where reverses_transfer_id is not null;
create index transfers_from_created on transfers (from_account_id, created_at desc, id desc);
create index transfers_to_created on transfers (to_account_id, created_at desc, id desc);

create table entries (
    id           uuid        primary key,
    transfer_id  uuid        not null references transfers (id),
    account_id   uuid        not null references accounts (id),
    direction    varchar(6)  not null check (direction in ('DEBIT', 'CREDIT')),
    amount_minor bigint      not null check (amount_minor > 0),
    created_at   timestamptz not null default now()
);
create index entries_account_created on entries (account_id, created_at);
create index entries_transfer on entries (transfer_id);

-- Entries are append-only. Row triggers block UPDATE/DELETE; a statement trigger blocks TRUNCATE
-- (TRUNCATE does not fire row-level triggers).
create function entries_reject_mutation() returns trigger language plpgsql as $$
begin
    raise exception 'entries are append-only: % is not allowed', tg_op
        using errcode = 'P0001';
end;
$$;

create trigger entries_no_update_delete
    before update or delete on entries
    for each row execute function entries_reject_mutation();

create trigger entries_no_truncate
    before truncate on entries
    for each statement execute function entries_reject_mutation();

create table idempotency_keys (
    client_id       varchar(64) not null,
    key             varchar(64) not null,
    request_hash    char(64)    not null,
    status          varchar(12) not null check (status in ('IN_PROGRESS', 'COMPLETED')),
    response_status int         null,
    response_body   jsonb       null,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null,
    primary key (client_id, key)
);
create index idempotency_keys_expires on idempotency_keys (expires_at);

create table outbox (
    id           uuid        primary key,
    event_id     uuid        not null unique,
    aggregate_id uuid        not null,
    event_type   varchar(64) not null,
    payload      jsonb       not null,
    created_at   timestamptz not null default now(),
    published_at timestamptz null
);
create index outbox_unpublished on outbox (created_at) where published_at is null;

create table account_status_audit (
    id           uuid         primary key,
    account_id   uuid         not null references accounts (id),
    old_status   varchar(10)  not null,
    new_status   varchar(10)  not null,
    reason       varchar(500) not null,
    actor        varchar(100) not null,
    approval_ref varchar(128) not null,
    created_at   timestamptz  not null default now(),
    constraint account_status_audit_ref unique (approval_ref, new_status)
);
create index account_status_audit_account on account_status_audit (account_id, created_at);
