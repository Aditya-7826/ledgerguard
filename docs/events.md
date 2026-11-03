# Ledger events: outbox → Kafka

## Guarantee: at-least-once, ordered per account

The ledger never writes to Kafka inside a request. A transfer's events are inserted into the
`outbox` table **in the same Postgres transaction** as the transfer and its entries, so an event
exists if and only if the money moved.

`OutboxPublisher` (a Spring `@Scheduled` poller in ledger-service) relays them:

1. In one DB transaction, claim up to 200 rows: `where published_at is null order by created_at
   for update skip locked`. Several ledger instances can poll at once; each skips rows another holds.
2. Send each row to topic `ledger.events` with an idempotent producer (`acks=all`,
   `enable.idempotence=true`). Kafka key = `aggregate_id`, which is the account id for `EntryPosted`,
   so all of one account's entries go to one partition, in order.
3. Wait for every ack, then set `published_at` and commit.

If the poller dies after Kafka acked but before step 3 commits, those rows are still unpublished
and are **sent again**. Delivery is therefore **at least once**: a consumer can see the same event
more than once.

Published rows older than 7 days are deleted by an hourly job. Unpublished rows are never deleted.

## Consumer contract: dedupe by `event_id`

Every consumer must record the event id **in the same transaction as its side effect**:

```sql
begin;
insert into processed_events (event_id) values ($1) on conflict do nothing;
-- only if that inserted a row:
--   apply the side effect (create/attach case, update counters, ...)
commit;
```

If the insert hits the conflict, the event was already applied, so skip it. Writing the marker and
the effect in a single transaction is what makes "at least once" behave like "exactly once" for
the consumer's own state. `OutboxTest` proves this: it simulates a poller crash after the acks
(each event reaches Kafka twice) and checks that a deduping consumer applies each event exactly once.

## Event shapes

Every payload carries an envelope: `event_id`, `event_type`, `schema_version`, `aggregate_id`,
`occurred_at`. Kafka headers repeat `event_id` and `event_type`.

| event_type | aggregate_id (Kafka key) | fields |
|---|---|---|
| `EntryPosted` | account id | `entry_id`, `transfer_id`, `account_id`, `counterparty_account_id`, `direction`, `amount_minor`, `currency`, `transfer_type`, `memo`, `account_created_at`, `counterparty_created_at`, `counterparty_is_system`, `posted_at` |
| `TransferPosted` | transfer id | `transfer_id`, `type`, `from_account_id`, `to_account_id`, `amount_minor`, `currency`, `memo`, `reverses_transfer_id`, `from_account_created_at`, `to_account_created_at`, `from_is_system`, `to_is_system`, `posted_at` |

`memo` is user-supplied free text. Consumers must treat it as untrusted.

## Known limits

- Ordering is per account within one poller. With several ledger instances polling at once, two
  batches holding different rows of the same account could publish out of order. Consumers
  shouldn't depend on cross-event order beyond what their own logic re-checks (the detector
  windows by `posted_at`).
- `TransferPosted` is keyed by transfer id, so it has no ordering relationship with the
  `EntryPosted` events of the same transfer.
