package dev.ledgerguard.ledger.idempotency;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.ledgerguard.ledger.config.LedgerProperties;
import dev.ledgerguard.ledger.error.ApiException;
import dev.ledgerguard.ledger.support.TransientRetry;
import java.time.Duration;
import java.util.List;
import java.util.function.Supplier;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Runs a write under an idempotency key, in ONE Postgres transaction with the ledger write itself.
 *
 * <p>The key row is inserted first with {@code ON CONFLICT DO NOTHING}. Because the row only commits
 * together with the transfer, a concurrent duplicate blocks on the primary key until the first request
 * commits (then replays its response) or rolls back (then runs itself). Business-rule failures roll
 * back the key too, so a rejected request leaves nothing behind and may be retried.
 */
@Component
public class IdempotentExecutor {

    public record Result(HttpStatus status, Object body) {}

    public record Outcome(int status, String body, boolean replayed) {}

    private static final Pattern KEY_PATTERN = Pattern.compile("^[A-Za-z0-9._:-]{1,64}$");

    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final TransientRetry retry;
    private final ObjectMapper mapper;
    private final Duration ttl;

    public IdempotentExecutor(JdbcTemplate jdbc, TransactionTemplate tx, TransientRetry retry,
                              ObjectMapper mapper, LedgerProperties props) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.retry = retry;
        this.mapper = mapper;
        this.ttl = props.idempotency().ttl();
    }

    public static void validateKey(String key) {
        if (key == null || !KEY_PATTERN.matcher(key).matches()) {
            throw ApiException.badRequest("invalid_idempotency_key",
                    "Idempotency-Key must be 1-64 characters of [A-Za-z0-9._:-].");
        }
    }

    public Outcome execute(String clientId, String key, String requestHash, Supplier<Result> work) {
        validateKey(key);
        return retry.run(() -> tx.execute(status -> once(clientId, key, requestHash, work)));
    }

    private Outcome once(String clientId, String key, String requestHash, Supplier<Result> work) {
        int inserted = jdbc.update("""
                insert into idempotency_keys (client_id, key, request_hash, status, created_at, expires_at)
                values (?, ?, ?, 'IN_PROGRESS', now(), now() + (? * interval '1 second'))
                on conflict do nothing
                """, clientId, key, requestHash, ttl.toSeconds());

        if (inserted == 0) {
            List<Existing> rows = jdbc.query("""
                    select request_hash, status, response_status, response_body::text as body
                    from idempotency_keys where client_id = ? and key = ?
                    """, (rs, i) -> new Existing(rs.getString("request_hash").trim(), rs.getString("status"),
                    rs.getInt("response_status"), rs.getString("body")), clientId, key);
            if (rows.isEmpty()) {
                // Deleted by the TTL job between our insert attempt and this read.
                throw ApiException.conflict("request_in_progress", "Request with this Idempotency-Key is in progress; retry.");
            }
            Existing ex = rows.get(0);
            if (!ex.requestHash().equals(requestHash)) {
                throw ApiException.unprocessable("idempotency_key_reused",
                        "Idempotency key reused with a different request.");
            }
            if (!"COMPLETED".equals(ex.status())) {
                throw ApiException.conflict("request_in_progress", "Request with this Idempotency-Key is in progress.");
            }
            return new Outcome(ex.responseStatus(), ex.body(), true);
        }

        Result result = work.get();
        String json;
        try {
            json = mapper.writeValueAsString(result.body());
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("could not serialise response", e);
        }
        // Read the body back as Postgres renders the jsonb, so first response and replays are byte-identical.
        String stored = jdbc.queryForObject("""
                update idempotency_keys
                   set status = 'COMPLETED', response_status = ?, response_body = ?::jsonb
                 where client_id = ? and key = ?
                returning response_body::text
                """, String.class, result.status().value(), json, clientId, key);
        return new Outcome(result.status().value(), stored, false);
    }

    private record Existing(String requestHash, String status, int responseStatus, String body) {}
}
