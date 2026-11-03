package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class ConcurrencyTest extends IntegrationTest {

    @Test
    void fiftyThreadsOfRandomTransfersKeepTheLedgerBalanced() throws Exception {
        int accountsN = 20;
        long initial = 100_000;
        List<UUID> accounts = new ArrayList<>();
        for (int i = 0; i < accountsN; i++) {
            accounts.add(createFundedAccount(initial));
        }

        int threads = 50;
        int perThread = 40;
        Map<Integer, AtomicInteger> statusCounts = new ConcurrentHashMap<>();
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<?>> futures = new ArrayList<>();
        for (int t = 0; t < threads; t++) {
            long seed = t;
            futures.add(pool.submit(() -> {
                Random rnd = new Random(seed);
                start.await();
                for (int i = 0; i < perThread; i++) {
                    UUID from = accounts.get(rnd.nextInt(accountsN));
                    UUID to = accounts.get(rnd.nextInt(accountsN));
                    if (from.equals(to)) {
                        continue;
                    }
                    long amount = 1 + rnd.nextInt(20_000);
                    Resp r = transfer(from, to, amount, UUID.randomUUID().toString());
                    statusCounts.computeIfAbsent(r.status(), k -> new AtomicInteger()).incrementAndGet();
                    if (r.status() != 201) {
                        assertThat(r.code()).as(r.body()).isEqualTo("insufficient_funds");
                    }
                }
                return null;
            }));
        }
        start.countDown();
        for (Future<?> f : futures) {
            f.get(5, TimeUnit.MINUTES);
        }
        pool.shutdown();

        assertThat(statusCounts.keySet()).as("statuses %s", statusCounts).isSubsetOf(201, 422);
        assertThat(statusCounts.get(201)).isNotNull();
        for (UUID a : accounts) {
            assertThat(balance(a)).as("balance of %s", a).isGreaterThanOrEqualTo(0);
        }
        long total = accounts.stream().mapToLong(this::balance).sum();
        assertThat(total).isEqualTo(accountsN * initial);
        assertReconciles();
    }

    @Test
    void opposingTransfersNeverSurfaceDeadlocks() throws Exception {
        UUID a = createFundedAccount(10_000_000);
        UUID b = createFundedAccount(10_000_000);
        int total = 1000;
        ExecutorService pool = Executors.newFixedThreadPool(32);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Resp>> futures = new ArrayList<>();
        for (int i = 0; i < total; i++) {
            boolean ab = i % 2 == 0;
            futures.add(pool.submit(() -> {
                start.await();
                return ab ? transfer(a, b, 1, UUID.randomUUID().toString())
                          : transfer(b, a, 1, UUID.randomUUID().toString());
            }));
        }
        start.countDown();
        for (Future<Resp> f : futures) {
            Resp r = f.get(5, TimeUnit.MINUTES);
            assertThat(r.status()).as(r.body()).isEqualTo(201);
        }
        pool.shutdown();

        Long count = jdbc.queryForObject("""
                select count(*) from transfers
                 where (from_account_id = ? and to_account_id = ?) or (from_account_id = ? and to_account_id = ?)
                """, Long.class, a, b, b, a);
        assertThat(count).isEqualTo(total);
        assertThat(balance(a)).isEqualTo(10_000_000);
        assertThat(balance(b)).isEqualTo(10_000_000);
        assertReconciles();
    }
}
