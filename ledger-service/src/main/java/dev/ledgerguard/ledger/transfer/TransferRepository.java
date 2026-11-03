package dev.ledgerguard.ledger.transfer;

import dev.ledgerguard.ledger.support.Db;
import dev.ledgerguard.ledger.transfer.TransferViews.Counterparty;
import dev.ledgerguard.ledger.transfer.TransferViews.EntryView;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferView;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class TransferRepository {

    private static final String COLS =
            "id, type, from_account_id, to_account_id, amount_minor, currency, memo, reverses_transfer_id, created_at";

    private static final RowMapper<TransferView> MAPPER = (rs, i) -> new TransferView(
            Db.uuid(rs, "id"), TransferType.valueOf(rs.getString("type")), Db.uuid(rs, "from_account_id"),
            Db.uuid(rs, "to_account_id"), rs.getLong("amount_minor"), rs.getString("currency").trim(),
            rs.getString("memo"), Db.uuid(rs, "reverses_transfer_id"), Db.instant(rs, "created_at"), List.of());

    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate named;

    public TransferRepository(JdbcTemplate jdbc, NamedParameterJdbcTemplate named) {
        this.jdbc = jdbc;
        this.named = named;
    }

    public Instant insertTransfer(UUID id, String clientId, String idempotencyKey, UUID from, UUID to, long amount,
                                  String currency, String memo, TransferType type, UUID reverses) {
        return jdbc.queryForObject("""
                insert into transfers (id, client_id, idempotency_key, from_account_id, to_account_id, amount_minor,
                                       currency, memo, type, reverses_transfer_id, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
                returning created_at
                """, (rs, i) -> Db.instant(rs, "created_at"),
                id, clientId, idempotencyKey, from, to, amount, currency, memo, type.name(), reverses);
    }

    public void insertEntry(UUID id, UUID transferId, UUID accountId, String direction, long amount) {
        jdbc.update("""
                insert into entries (id, transfer_id, account_id, direction, amount_minor, created_at)
                values (?, ?, ?, ?, ?, now())
                """, id, transferId, accountId, direction, amount);
    }

    public Optional<TransferView> find(UUID id) {
        return jdbc.query("select " + COLS + " from transfers where id = ?", MAPPER, id).stream().findFirst()
                .map(t -> withEntries(List.of(t)).get(0));
    }

    public Optional<UUID> findReversalOf(UUID transferId) {
        return jdbc.queryForList("select id from transfers where reverses_transfer_id = ?", UUID.class, transferId)
                .stream().findFirst();
    }

    /** Keyset page, newest first. Cursor is the (created_at, id) of the last row of the previous page. */
    public List<TransferView> listForAccount(UUID accountId, Instant from, Instant to,
                                             Instant cursorTs, UUID cursorId, int limit) {
        var p = new MapSqlParameterSource()
                .addValue("acct", accountId)
                .addValue("from", Db.ts(from))
                .addValue("to", Db.ts(to))
                .addValue("limit", limit);
        String cursorClause = "";
        if (cursorTs != null) {
            cursorClause = " and (created_at, id) < (:cts, :cid)";
            p.addValue("cts", Db.ts(cursorTs)).addValue("cid", cursorId);
        }
        List<TransferView> rows = named.query("select " + COLS + " from transfers "
                + "where (from_account_id = :acct or to_account_id = :acct) "
                + "and created_at >= :from and created_at < :to" + cursorClause
                + " order by created_at desc, id desc limit :limit", p, MAPPER);
        return withEntries(rows);
    }

    public List<Counterparty> counterparties(UUID accountId, Instant from, Instant to, int limit) {
        var p = new MapSqlParameterSource()
                .addValue("acct", accountId)
                .addValue("from", Db.ts(from))
                .addValue("to", Db.ts(to))
                .addValue("limit", limit);
        return named.query("""
                select cp,
                       sum(sent)::bigint as sent_minor,
                       sum(received)::bigint as received_minor,
                       count(*) as transfer_count,
                       min(created_at) as first_at,
                       max(created_at) as last_at
                  from (select to_account_id as cp, amount_minor as sent, 0::bigint as received, created_at
                          from transfers
                         where from_account_id = :acct and created_at >= :from and created_at < :to
                        union all
                        select from_account_id as cp, 0::bigint as sent, amount_minor as received, created_at
                          from transfers
                         where to_account_id = :acct and created_at >= :from and created_at < :to) t
                 group by cp
                 order by count(*) desc, cp
                 limit :limit
                """, p, (rs, i) -> new Counterparty(Db.uuid(rs, "cp"), rs.getLong("sent_minor"),
                rs.getLong("received_minor"), rs.getLong("transfer_count"),
                Db.instant(rs, "first_at"), Db.instant(rs, "last_at")));
    }

    private List<TransferView> withEntries(List<TransferView> transfers) {
        if (transfers.isEmpty()) {
            return transfers;
        }
        Collection<UUID> ids = transfers.stream().map(TransferView::id).toList();
        Map<UUID, List<EntryView>> byTransfer = new HashMap<>();
        named.query("""
                select id, transfer_id, account_id, direction, amount_minor
                  from entries where transfer_id in (:ids) order by direction desc, id
                """, new MapSqlParameterSource("ids", ids), rs -> {
            byTransfer.computeIfAbsent(Db.uuid(rs, "transfer_id"), k -> new ArrayList<>())
                    .add(new EntryView(Db.uuid(rs, "id"), Db.uuid(rs, "account_id"),
                            rs.getString("direction"), rs.getLong("amount_minor")));
        });
        return transfers.stream().map(t -> new TransferView(t.id(), t.type(), t.fromAccountId(), t.toAccountId(),
                t.amountMinor(), t.currency(), t.memo(), t.reversesTransferId(), t.createdAt(),
                List.copyOf(byTransfer.getOrDefault(t.id(), List.of())))).toList();
    }
}
