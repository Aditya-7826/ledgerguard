package dev.ledgerguard.ledger.read;

import dev.ledgerguard.ledger.account.Account;
import dev.ledgerguard.ledger.account.AccountRepository;
import dev.ledgerguard.ledger.config.LedgerProperties;
import dev.ledgerguard.ledger.error.ApiException;
import dev.ledgerguard.ledger.support.Db;
import dev.ledgerguard.ledger.transfer.TransferRepository;
import dev.ledgerguard.ledger.transfer.TransferViews.Counterparty;
import dev.ledgerguard.ledger.transfer.TransferViews.CounterpartyPage;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferPage;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferView;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferWithAsOf;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Read endpoints. Each runs in a REPEATABLE READ read-only transaction, so every query in a response sees
 * the same snapshot, and {@code as_of} is the database's transaction timestamp.
 */
@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class ReadService {

    public static final int MAX_COUNTERPARTIES = 200;

    public record AccountView(UUID id, String ownerName, String currency, String status, boolean isSystem,
                              Instant createdAt, Instant asOf) {}

    public record BalanceView(UUID accountId, long balanceMinor, String currency, Instant asOf) {}

    private final JdbcTemplate jdbc;
    private final AccountRepository accounts;
    private final TransferRepository transfers;
    private final LedgerProperties.Read limits;

    public ReadService(JdbcTemplate jdbc, AccountRepository accounts, TransferRepository transfers,
                       LedgerProperties props) {
        this.jdbc = jdbc;
        this.accounts = accounts;
        this.transfers = transfers;
        this.limits = props.read();
    }

    public AccountView account(UUID id) {
        Instant asOf = dbNow();
        Account a = require(id);
        return new AccountView(a.id(), a.ownerName(), a.currency(), a.status().name(), a.system(), a.createdAt(), asOf);
    }

    public BalanceView balance(UUID id, String asOfParam) {
        Instant now = dbNow();
        Account a = require(id);
        if (asOfParam == null || asOfParam.isBlank()) {
            return new BalanceView(id, accounts.balance(id), a.currency(), now);
        }
        Instant asOf = parseInstant(asOfParam, "as_of");
        if (asOf.isAfter(now)) {
            throw ApiException.unprocessable("as_of_in_future", "as_of must not be in the future.");
        }
        return new BalanceView(id, accounts.balanceAsOf(id, asOf), a.currency(), asOf);
    }

    public TransferPage transfers(UUID id, String fromParam, String toParam, String cursorParam, Integer limitParam) {
        Instant now = dbNow();
        require(id);
        Instant[] window = window(fromParam, toParam, now);
        int limit = limit(limitParam);
        Instant cursorTs = null;
        UUID cursorId = null;
        if (cursorParam != null && !cursorParam.isBlank()) {
            Object[] c = decodeCursor(cursorParam);
            cursorTs = (Instant) c[0];
            cursorId = (UUID) c[1];
        }
        List<TransferView> rows = transfers.listForAccount(id, window[0], window[1], cursorTs, cursorId, limit + 1);
        String next = null;
        if (rows.size() > limit) {
            rows = rows.subList(0, limit);
            TransferView last = rows.get(limit - 1);
            next = encodeCursor(last.createdAt(), last.id());
        }
        return new TransferPage(id, window[0], window[1], List.copyOf(rows), next, now);
    }

    public CounterpartyPage counterparties(UUID id, String fromParam, String toParam) {
        Instant now = dbNow();
        require(id);
        Instant[] window = window(fromParam, toParam, now);
        List<Counterparty> rows = transfers.counterparties(id, window[0], window[1], MAX_COUNTERPARTIES + 1);
        boolean truncated = rows.size() > MAX_COUNTERPARTIES;
        if (truncated) {
            rows = rows.subList(0, MAX_COUNTERPARTIES);
        }
        return new CounterpartyPage(id, window[0], window[1], List.copyOf(rows), truncated, now);
    }

    public TransferWithAsOf transfer(UUID id) {
        Instant now = dbNow();
        TransferView t = transfers.find(id).orElseThrow(() ->
                ApiException.notFound("transfer_not_found", "Transfer " + id + " does not exist."));
        return new TransferWithAsOf(t, now);
    }

    private Account require(UUID id) {
        return accounts.find(id).orElseThrow(() ->
                ApiException.notFound("account_not_found", "Account " + id + " does not exist."));
    }

    private Instant dbNow() {
        return jdbc.queryForObject("select now() as now", (rs, i) -> Db.instant(rs, "now"));
    }

    private Instant[] window(String fromParam, String toParam, Instant now) {
        Instant to = toParam == null || toParam.isBlank() ? now : parseInstant(toParam, "to");
        Instant from = fromParam == null || fromParam.isBlank()
                ? to.minus(limits.maxWindow()) : parseInstant(fromParam, "from");
        if (!from.isBefore(to)) {
            throw ApiException.unprocessable("invalid_window", "from must be before to.");
        }
        if (Duration.between(from, to).compareTo(limits.maxWindow()) > 0) {
            throw ApiException.unprocessable("window_too_large",
                    "The time window must be at most " + limits.maxWindow().toDays() + " days.");
        }
        return new Instant[] {from, to};
    }

    private int limit(Integer limitParam) {
        int limit = limitParam == null ? limits.defaultLimit() : limitParam;
        if (limit < 1 || limit > limits.maxLimit()) {
            throw ApiException.unprocessable("invalid_limit", "limit must be between 1 and " + limits.maxLimit() + ".");
        }
        return limit;
    }

    static Instant parseInstant(String s, String name) {
        try {
            return OffsetDateTime.parse(s).toInstant();
        } catch (DateTimeParseException e) {
            throw ApiException.badRequest("invalid_timestamp",
                    name + " must be an ISO-8601 timestamp with offset, e.g. 2026-01-31T10:00:00Z.");
        }
    }

    static String encodeCursor(Instant ts, UUID id) {
        long micros = ChronoUnit.MICROS.between(Instant.EPOCH, ts);
        String raw = micros + ":" + id;
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }

    static Object[] decodeCursor(String cursor) {
        try {
            String raw = new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8);
            int sep = raw.indexOf(':');
            long micros = Long.parseLong(raw.substring(0, sep));
            UUID id = UUID.fromString(raw.substring(sep + 1));
            return new Object[] {Instant.EPOCH.plus(micros, ChronoUnit.MICROS), id};
        } catch (RuntimeException e) {
            throw ApiException.badRequest("invalid_cursor", "cursor is malformed.");
        }
    }
}
