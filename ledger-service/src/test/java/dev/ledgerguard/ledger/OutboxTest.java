package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import dev.ledgerguard.ledger.outbox.OutboxPublisher;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.kafka.KafkaContainer;

/**
 * Outbox → Kafka with a real broker. The scheduled poller is effectively off (1h delay) so the test
 * controls exactly when publishing happens, i.e. when the "poller" is stopped, crashes, or restarts.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "ledger.security.api-keys=" + IntegrationTest.KEYS,
        "ledger.reconcile.interval=PT1H",
        "ledger.outbox.enabled=true",
        "ledger.outbox.initial-delay=PT1H",
        "ledger.outbox.poll-interval=PT1H",
        "ledger.outbox.topic=ledger.events.test"
})
@Import({IntegrationTest.Containers.class, OutboxTest.KafkaContainers.class})
class OutboxTest extends IntegrationTest {

    @TestConfiguration(proxyBeanMethods = false)
    static class KafkaContainers {
        @Bean
        @ServiceConnection
        KafkaContainer kafka() {
            return new KafkaContainer("apache/kafka:3.9.2");
        }
    }

    @Autowired
    OutboxPublisher publisher;

    @Autowired
    TransactionTemplate tx;

    // @ServiceConnection supplies the broker via connection details, not spring.kafka.bootstrap-servers,
    // so ask the container directly.
    @Autowired
    KafkaContainer kafka;

    @BeforeEach
    void consumerSchema() {
        // Stand-in for a downstream consumer's own tables (agent-service will have the real ones).
        jdbc.execute("create schema if not exists consumer_test");
        jdbc.execute("create table if not exists consumer_test.processed_events (event_id uuid primary key, "
                + "processed_at timestamptz not null default now())");
        jdbc.execute("create table if not exists consumer_test.applied_effects (event_id uuid not null, "
                + "event_type text not null, transfer_id uuid not null)");
    }

    @Test
    void eventSurvivesPollerCrashAndConsumerAppliesItExactlyOnce() throws Exception {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String transferId = transfer(a, b, 1_234, UUID.randomUUID().toString()).json().get("id").asText();

        // 1. Poller stopped: the transfer committed, its 3 events wait in the outbox.
        assertThat(unpublishedFor(transferId)).isEqualTo(3);

        // 2. Poller "crashes" after Kafka acked but before its DB transaction committed.
        tx.executeWithoutResult(status -> {
            publisher.publishBatch();
            status.setRollbackOnly();
        });
        assertThat(unpublishedFor(transferId)).as("crash lost the published_at update").isEqualTo(3);

        // 3. Poller restarts and publishes again (at-least-once: these are duplicates in Kafka).
        while (publisher.publishBatch() > 0) {
            // drain
        }
        assertThat(unpublishedFor(transferId)).isZero();

        // 4. A deduping consumer sees each event twice but applies it once.
        Map<String, Integer> deliveries = new HashMap<>();
        Map<String, String> keyByEventType = new HashMap<>();
        try (KafkaConsumer<String, String> consumer = consumer()) {
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(60);
            while (System.nanoTime() < deadline && !seenTwice(deliveries)) {
                for (ConsumerRecord<String, String> rec : consumer.poll(Duration.ofMillis(500))) {
                    var json = JSON.readTree(rec.value());
                    if (!transferId.equals(json.path("transfer_id").asText())) {
                        continue;
                    }
                    String eventId = new String(rec.headers().lastHeader("event_id").value(), StandardCharsets.UTF_8);
                    assertThat(eventId).isEqualTo(json.get("event_id").asText());
                    deliveries.merge(eventId, 1, Integer::sum);
                    String type = json.get("event_type").asText();
                    keyByEventType.put(type + ":" + json.path("direction").asText(""), rec.key());
                    apply(UUID.fromString(eventId), type, UUID.fromString(transferId));
                }
            }
        }

        assertThat(deliveries).hasSize(3);
        assertThat(deliveries.values()).allSatisfy(n -> assertThat(n).isGreaterThanOrEqualTo(2));
        assertThat(jdbc.queryForObject(
                "select count(*) from consumer_test.applied_effects where transfer_id = ?::uuid", Long.class, transferId))
                .isEqualTo(3);
        // Kafka key = aggregate_id: EntryPosted keyed by account (per-account ordering).
        assertThat(keyByEventType).containsEntry("EntryPosted:DEBIT", a.toString())
                .containsEntry("EntryPosted:CREDIT", b.toString())
                .containsEntry("TransferPosted:", transferId);
    }

    @Test
    void skipLockedLetsTwoPublishersRunWithoutDoublePublishing() throws Exception {
        while (publisher.publishBatch() > 0) {
            // start from an empty outbox
        }
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String transferId = transfer(a, b, 10, UUID.randomUUID().toString()).json().get("id").asText();

        // "Instance 1" holds row locks on every unpublished row.
        CountDownLatch locked = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        ExecutorService pool = Executors.newSingleThreadExecutor();
        Future<Integer> holder = pool.submit(() -> tx.execute(s -> {
            List<UUID> ids = jdbc.queryForList(
                    "select id from outbox where published_at is null for update", UUID.class);
            locked.countDown();
            try {
                release.await(30, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return ids.size();
        }));
        assertThat(locked.await(30, TimeUnit.SECONDS)).isTrue();

        // "Instance 2" skips the locked rows instead of blocking or double-publishing them.
        assertThat(publisher.publishBatch()).isZero();
        assertThat(unpublishedFor(transferId)).isEqualTo(3);

        release.countDown();
        assertThat(holder.get(30, TimeUnit.SECONDS)).isGreaterThanOrEqualTo(3);
        pool.shutdown();
        assertThat(publisher.publishBatch()).isGreaterThanOrEqualTo(3);
        assertThat(unpublishedFor(transferId)).isZero();
    }

    @Test
    void publishedRowsOlderThanRetentionAreDeleted() {
        UUID oldId = UUID.randomUUID();
        UUID recentId = UUID.randomUUID();
        UUID pendingId = UUID.randomUUID();
        insertOutbox(oldId, "now() - interval '8 days'");
        insertOutbox(recentId, "now() - interval '1 day'");
        insertOutbox(pendingId, "null");

        assertThat(publisher.deletePublishedOlderThanRetention()).isGreaterThanOrEqualTo(1);

        assertThat(exists(oldId)).isFalse();
        assertThat(exists(recentId)).isTrue();
        assertThat(exists(pendingId)).as("unpublished rows are never deleted").isTrue();
        jdbc.update("delete from outbox where id = ?", pendingId); // don't leak into other tests
    }

    // ---- helpers ----------------------------------------------------------------------------------

    private KafkaConsumer<String, String> consumer() {
        Properties p = new Properties();
        p.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, kafka.getBootstrapServers());
        p.put(ConsumerConfig.GROUP_ID_CONFIG, "outbox-test-" + UUID.randomUUID());
        p.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        p.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        p.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        KafkaConsumer<String, String> c = new KafkaConsumer<>(p);
        c.subscribe(List.of("ledger.events.test"));
        return c;
    }

    /** Side effect + dedupe marker in ONE transaction: the pattern every real consumer must follow. */
    private void apply(UUID eventId, String eventType, UUID transferId) {
        tx.executeWithoutResult(s -> {
            int fresh = jdbc.update(
                    "insert into consumer_test.processed_events (event_id) values (?) on conflict do nothing", eventId);
            if (fresh == 1) {
                jdbc.update("insert into consumer_test.applied_effects (event_id, event_type, transfer_id) values (?, ?, ?)",
                        eventId, eventType, transferId);
            }
        });
    }

    private static boolean seenTwice(Map<String, Integer> deliveries) {
        return deliveries.size() == 3 && deliveries.values().stream().allMatch(n -> n >= 2);
    }

    private long unpublishedFor(String transferId) {
        return jdbc.queryForObject(
                "select count(*) from outbox where payload->>'transfer_id' = ? and published_at is null",
                Long.class, transferId);
    }

    private void insertOutbox(UUID id, String publishedAtSql) {
        jdbc.update("insert into outbox (id, event_id, aggregate_id, event_type, payload, created_at, published_at) "
                + "values (?, ?, ?, 'Test', '{}'::jsonb, now() - interval '9 days', " + publishedAtSql + ")",
                id, UUID.randomUUID(), UUID.randomUUID());
    }

    private boolean exists(UUID id) {
        return jdbc.queryForObject("select count(*) from outbox where id = ?", Long.class, id) == 1;
    }
}
