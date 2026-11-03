package dev.ledgerguard.ledger.account;

import dev.ledgerguard.ledger.support.Db;
import dev.ledgerguard.ledger.support.Uuids;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Stream;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

@Repository
public class AccountRepository {

    private static final String COLS = "id, owner_name, currency, status, is_system, created_at, version";

    private static final RowMapper<Account> MAPPER = (rs, i) -> new Account(
            Db.uuid(rs, "id"), rs.getString("owner_name"), rs.getString("currency").trim(),
            AccountStatus.valueOf(rs.getString("status")), rs.getBoolean("is_system"),
            Db.instant(rs, "created_at"), rs.getLong("version"));

    private final JdbcTemplate jdbc;

    public AccountRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<Account> find(UUID id) {
        return jdbc.query("select " + COLS + " from accounts where id = ?", MAPPER, id).stream().findFirst();
    }

    public Optional<Account> lockForUpdate(UUID id) {
        return jdbc.query("select " + COLS + " from accounts where id = ? for update", MAPPER, id).stream().findFirst();
    }

    /**
     * Locks the given accounts one statement at a time in ascending (Postgres) UUID order, so two
     * transactions touching the same pair always acquire locks in the same order and cannot deadlock.
     * Missing accounts are simply absent from the result.
     */
    public Map<UUID, Account> lockInOrder(UUID... ids) {
        Map<UUID, Account> locked = new LinkedHashMap<>();
        Stream.of(ids).distinct().sorted(Uuids.PG_ORDER)
                .forEach(id -> lockForUpdate(id).ifPresent(a -> locked.put(id, a)));
        return locked;
    }

    public Optional<UUID> systemAccountFor(String currency) {
        List<UUID> ids = jdbc.queryForList(
                "select id from accounts where is_system and currency = ? order by id limit 1", UUID.class, currency);
        return ids.stream().findFirst();
    }

    public boolean currencySupported(String currency) {
        return systemAccountFor(currency).isPresent();
    }

    public Account insert(String ownerName, String currency) {
        UUID id = UUID.randomUUID();
        Instant createdAt = jdbc.queryForObject("""
                insert into accounts (id, owner_name, currency, status, is_system, created_at, version)
                values (?, ?, ?, 'ACTIVE', false, now(), 0)
                returning created_at
                """, (rs, i) -> Db.instant(rs, "created_at"), id, ownerName, currency);
        jdbc.update("insert into account_balances (account_id, balance_minor, updated_at) values (?, 0, now())", id);
        return new Account(id, ownerName, currency, AccountStatus.ACTIVE, false, createdAt, 0);
    }

    /** Projection balance. Callers that need it to be stable must hold the account row lock. */
    public long balance(UUID accountId) {
        Long b = jdbc.queryForObject("select balance_minor from account_balances where account_id = ?", Long.class, accountId);
        return b == null ? 0 : b;
    }

    public void addToBalance(UUID accountId, long deltaMinor) {
        int n = jdbc.update("""
                update account_balances set balance_minor = balance_minor + ?, updated_at = now()
                 where account_id = ?
                """, deltaMinor, accountId);
        if (n != 1) {
            throw new IllegalStateException("missing balance row for account " + accountId);
        }
    }

    /** Balance at a point in time, derived from entries (the source of truth), not the projection. */
    public long balanceAsOf(UUID accountId, Instant asOf) {
        Long b = jdbc.queryForObject("""
                select coalesce(sum(case when direction = 'CREDIT' then amount_minor else -amount_minor end), 0)
                  from entries where account_id = ? and created_at <= ?
                """, Long.class, accountId, Db.ts(asOf));
        return b == null ? 0 : b;
    }

    public void updateStatus(UUID accountId, AccountStatus status) {
        jdbc.update("update accounts set status = ?, version = version + 1 where id = ?", status.name(), accountId);
    }
}
