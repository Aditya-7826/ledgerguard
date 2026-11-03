package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

class FreezeTest extends IntegrationTest {

    record Attempt(long startedNanos, int status, String code) {}

    @Test
    void noTransferDebitsAnAccountAfterItsFreezeCommits() throws Exception {
        UUID a = createFundedAccount(100_000_000);
        UUID b = createAccount("INR");

        AtomicBoolean stop = new AtomicBoolean(false);
        AtomicLong freezeReturnedNanos = new AtomicLong(Long.MAX_VALUE);
        ConcurrentLinkedQueue<Attempt> attempts = new ConcurrentLinkedQueue<>();
        ExecutorService pool = Executors.newFixedThreadPool(8);
        List<Future<?>> futures = new ArrayList<>();
        for (int t = 0; t < 8; t++) {
            futures.add(pool.submit(() -> {
                while (!stop.get()) {
                    long started = System.nanoTime();
                    Resp r = transfer(a, b, 1, UUID.randomUUID().toString());
                    attempts.add(new Attempt(started, r.status(), r.code()));
                }
                return null;
            }));
        }

        Thread.sleep(500);
        Resp frozen = freeze(a, "race-" + UUID.randomUUID());
        freezeReturnedNanos.set(System.nanoTime());
        assertThat(frozen.status()).as(frozen.body()).isEqualTo(200);

        long debitsAtFreeze = debitCount(a);
        long balanceAtFreeze = balance(a);
        Thread.sleep(500);
        stop.set(true);
        for (Future<?> f : futures) {
            f.get(1, TimeUnit.MINUTES);
        }
        pool.shutdown();

        assertThat(debitCount(a)).isEqualTo(debitsAtFreeze);
        assertThat(balance(a)).isEqualTo(balanceAtFreeze);
        assertThat(attempts).allSatisfy(at -> assertThat(at.status()).isIn(201, 403));
        List<Attempt> afterFreeze = attempts.stream().filter(at -> at.startedNanos() > freezeReturnedNanos.get()).toList();
        assertThat(afterFreeze).isNotEmpty();
        assertThat(afterFreeze).allSatisfy(at -> {
            assertThat(at.status()).isEqualTo(403);
            assertThat(at.code()).isEqualTo("account_frozen");
        });
        assertThat(attempts.stream().filter(at -> at.status() == 201).count()).isPositive();
        assertReconciles();
    }

    @Test
    void freezeIsIdempotentOnApprovalRefAndAudited() {
        UUID a = createFundedAccount(1_000);
        String ref = "decision-" + UUID.randomUUID();

        Resp first = freeze(a, ref);
        Resp again = freeze(a, ref);

        assertThat(first.status()).isEqualTo(200);
        assertThat(first.json().get("status").asText()).isEqualTo("FROZEN");
        assertThat(first.json().get("replayed").asBoolean()).isFalse();
        assertThat(again.status()).isEqualTo(200);
        assertThat(again.json().get("replayed").asBoolean()).isTrue();
        assertThat(jdbc.queryForObject("select count(*) from account_status_audit where approval_ref = ?",
                Long.class, ref)).isEqualTo(1);
        assertThat(get("/v1/accounts/" + a, READONLY_KEY).json().get("status").asText()).isEqualTo("FROZEN");

        // A frozen account can neither send nor receive.
        UUID other = createFundedAccount(1_000);
        assertThat(transfer(a, other, 1, UUID.randomUUID().toString()).status()).isEqualTo(403);
        assertThat(transfer(other, a, 1, UUID.randomUUID().toString()).status()).isEqualTo(403);

        Resp unfrozen = post("/v1/admin/accounts/" + a + "/unfreeze", ADMIN_KEY, null,
                "{\"reason\":\"cleared\",\"actor\":\"analyst@test\",\"approval_ref\":\"" + ref + "\"}");
        assertThat(unfrozen.status()).as(unfrozen.body()).isEqualTo(200);
        assertThat(unfrozen.json().get("status").asText()).isEqualTo("ACTIVE");
        assertThat(transfer(a, other, 1, UUID.randomUUID().toString()).status()).isEqualTo(201);
    }

    @Test
    void approvalRefCannotBeReusedForAnotherAccount() {
        UUID a = createAccount("INR");
        UUID b = createAccount("INR");
        String ref = "decision-" + UUID.randomUUID();
        assertThat(freeze(a, ref).status()).isEqualTo(200);
        Resp reuse = freeze(b, ref);
        assertThat(reuse.status()).isEqualTo(422);
        assertThat(reuse.code()).isEqualTo("approval_ref_reused");
    }

    @Test
    void onlyAdminCanFreeze() {
        UUID a = createAccount("INR");
        String body = "{\"reason\":\"x\",\"actor\":\"y\",\"approval_ref\":\"z-" + UUID.randomUUID() + "\"}";
        assertThat(post("/v1/admin/accounts/" + a + "/freeze", CLIENT_KEY, null, body).status()).isEqualTo(403);
        assertThat(post("/v1/admin/accounts/" + a + "/freeze", READONLY_KEY, null, body).status()).isEqualTo(403);
        assertThat(post("/v1/admin/accounts/" + a + "/freeze", null, null, body).status()).isEqualTo(401);
    }

    private long debitCount(UUID account) {
        return jdbc.queryForObject("select count(*) from entries where account_id = ? and direction = 'DEBIT'",
                Long.class, account);
    }
}
