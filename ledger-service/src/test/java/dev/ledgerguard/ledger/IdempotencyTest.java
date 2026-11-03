package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import dev.ledgerguard.ledger.admin.ScheduledJobs;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

class IdempotencyTest extends IntegrationTest {

    @Autowired
    ScheduledJobs jobs;

    @Test
    void replayReturnsIdenticalResponseAndCreatesOneTransfer() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String key = UUID.randomUUID().toString();

        Resp first = transfer(a, b, 2_500, key);
        Resp second = transfer(a, b, 2_500, key);

        assertThat(first.status()).as(first.body()).isEqualTo(201);
        assertThat(second.status()).isEqualTo(201);
        assertThat(second.body()).isEqualTo(first.body());
        assertThat(first.raw().headers().firstValue("Idempotent-Replayed")).hasValue("false");
        assertThat(second.raw().headers().firstValue("Idempotent-Replayed")).hasValue("true");
        assertThat(countByKey(key)).isEqualTo(1);
        assertThat(balance(a)).isEqualTo(7_500);

        // Outbox: one EntryPosted per entry (keyed by account) + one TransferPosted, written once.
        String tid = first.json().get("id").asText();
        assertThat(jdbc.queryForList("""
                select event_type || ':' || aggregate_id from outbox
                 where payload->>'transfer_id' = ? order by event_type, aggregate_id
                """, String.class, tid)).containsExactlyInAnyOrder(
                "EntryPosted:" + a, "EntryPosted:" + b, "TransferPosted:" + tid);
    }

    @Test
    void sameKeyWithDifferentBodyIs422() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String key = UUID.randomUUID().toString();

        assertThat(transfer(a, b, 100, key).status()).isEqualTo(201);
        Resp mismatch = transfer(a, b, 101, key);

        assertThat(mismatch.status()).isEqualTo(422);
        assertThat(mismatch.code()).isEqualTo("idempotency_key_reused");
        assertThat(mismatch.raw().headers().firstValue("Content-Type")).hasValueSatisfying(
                ct -> assertThat(ct).startsWith("application/problem+json"));
        assertThat(countByKey(key)).isEqualTo(1);
    }

    @Test
    void sameKeyOnDifferentEndpointIs422() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String key = UUID.randomUUID().toString();
        assertThat(transfer(a, b, 100, key).status()).isEqualTo(201);

        Resp deposit = post("/v1/deposits", CLIENT_KEY, key,
                "{\"account_id\":\"" + b + "\",\"amount_minor\":100,\"currency\":\"INR\"}");
        assertThat(deposit.status()).isEqualTo(422);
        assertThat(deposit.code()).isEqualTo("idempotency_key_reused");
    }

    @Test
    void twentyConcurrentRequestsWithSameKeyCreateExactlyOneTransfer() throws Exception {
        UUID a = createFundedAccount(1_000_000);
        UUID b = createAccount("INR");
        String key = UUID.randomUUID().toString();

        ExecutorService pool = Executors.newFixedThreadPool(20);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Resp>> futures = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            futures.add(pool.submit(() -> {
                start.await();
                return transfer(a, b, 1_000, key);
            }));
        }
        start.countDown();
        List<Resp> responses = new ArrayList<>();
        for (Future<Resp> f : futures) {
            responses.add(f.get(1, TimeUnit.MINUTES));
        }
        pool.shutdown();

        String firstBody = null;
        for (Resp r : responses) {
            // 201 (original or replay) or 409 if it observed the request in progress; never a second transfer.
            assertThat(r.status()).as(r.body()).isIn(201, 409);
            if (r.status() == 201) {
                if (firstBody == null) {
                    firstBody = r.body();
                }
                assertThat(r.body()).isEqualTo(firstBody);
            }
        }
        assertThat(countByKey(key)).isEqualTo(1);
        assertThat(balance(a)).isEqualTo(999_000);
        assertThat(balance(b)).isEqualTo(1_000);
        assertReconciles();
    }

    @Test
    void rejectedRequestLeavesNoKeySoItCanBeRetried() {
        UUID a = createAccount("INR");
        UUID b = createAccount("INR");
        String key = UUID.randomUUID().toString();

        Resp poor = transfer(a, b, 500, key);
        assertThat(poor.status()).isEqualTo(422);
        assertThat(poor.code()).isEqualTo("insufficient_funds");

        deposit(a, 500);
        Resp retry = transfer(a, b, 500, key);
        assertThat(retry.status()).as(retry.body()).isEqualTo(201);
        assertThat(countByKey(key)).isEqualTo(1);
    }

    @Test
    void expiredKeysAreCleanedUp() {
        jdbc.update("""
                insert into idempotency_keys (client_id, key, request_hash, status, created_at, expires_at)
                values ('client', 'expired-key-1', repeat('a', 64), 'COMPLETED', now() - interval '2 days',
                        now() - interval '1 day')
                """);
        jdbc.update("""
                insert into idempotency_keys (client_id, key, request_hash, status, created_at, expires_at)
                values ('client', 'fresh-key-1', repeat('a', 64), 'COMPLETED', now(), now() + interval '1 day')
                """);

        int deleted = jobs.cleanupExpiredIdempotencyKeys();

        assertThat(deleted).isGreaterThanOrEqualTo(1);
        assertThat(jdbc.queryForObject("select count(*) from idempotency_keys where key = 'expired-key-1'", Long.class))
                .isZero();
        assertThat(jdbc.queryForObject("select count(*) from idempotency_keys where key = 'fresh-key-1'", Long.class))
                .isEqualTo(1);
    }

    private long countByKey(String key) {
        return jdbc.queryForObject("select count(*) from transfers where idempotency_key = ?", Long.class, key);
    }
}
