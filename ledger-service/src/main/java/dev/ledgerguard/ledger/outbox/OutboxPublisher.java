package dev.ledgerguard.ledger.outbox;

import dev.ledgerguard.ledger.config.LedgerProperties;
import dev.ledgerguard.ledger.support.Db;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Transactional-outbox relay: Postgres outbox → Kafka.
 *
 * <p>Each batch runs in one DB transaction: claim unpublished rows with {@code FOR UPDATE SKIP LOCKED}
 * (so several ledger instances can poll safely), send them in order, wait for every ack (acks=all,
 * idempotent producer), and only then set {@code published_at}. If the process dies after the acks but
 * before the commit, the rows stay unpublished and are sent again: delivery is <b>at least once</b>, and
 * consumers must dedupe by {@code event_id}.
 *
 * <p>The Kafka key is {@code aggregate_id} (the account id for EntryPosted), so all of an account's
 * entries land on one partition in order.
 */
@Component
@ConditionalOnProperty(name = "ledger.outbox.enabled", havingValue = "true", matchIfMissing = true)
public class OutboxPublisher {

    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);

    private record Row(UUID id, UUID eventId, UUID aggregateId, String eventType, String payload) {}

    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final KafkaTemplate<String, String> kafka;
    private final String topic;
    private final int batchSize;
    private final Duration sendTimeout;
    private final Duration retention;

    public OutboxPublisher(JdbcTemplate jdbc, TransactionTemplate tx, KafkaTemplate<String, String> kafka,
                           LedgerProperties props) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.kafka = kafka;
        this.topic = props.outbox().topic();
        this.batchSize = props.outbox().batchSize();
        this.sendTimeout = props.outbox().sendTimeout();
        this.retention = props.outbox().retention();
    }

    @Scheduled(initialDelayString = "${ledger.outbox.initial-delay:PT2S}",
            fixedDelayString = "${ledger.outbox.poll-interval:PT0.2S}")
    public void poll() {
        try {
            int n;
            do {
                n = publishBatch();
            } while (n == batchSize);
        } catch (RuntimeException e) {
            // Rows stay unpublished and are retried on the next tick.
            log.warn("outbox publish failed, will retry: {}", e.toString());
        }
    }

    /** Publishes one batch and returns how many rows were marked published. */
    public int publishBatch() {
        Integer n = tx.execute(status -> {
            List<Row> rows = jdbc.query("""
                    select id, event_id, aggregate_id, event_type, payload::text as payload
                      from outbox
                     where published_at is null
                     order by created_at, id
                     limit ?
                     for update skip locked
                    """, (rs, i) -> new Row(Db.uuid(rs, "id"), Db.uuid(rs, "event_id"), Db.uuid(rs, "aggregate_id"),
                    rs.getString("event_type"), rs.getString("payload")), batchSize);
            if (rows.isEmpty()) {
                return 0;
            }
            List<CompletableFuture<SendResult<String, String>>> acks = new ArrayList<>(rows.size());
            for (Row r : rows) {
                ProducerRecord<String, String> record =
                        new ProducerRecord<>(topic, r.aggregateId().toString(), r.payload());
                record.headers().add("event_id", r.eventId().toString().getBytes(StandardCharsets.UTF_8));
                record.headers().add("event_type", r.eventType().getBytes(StandardCharsets.UTF_8));
                CompletableFuture<SendResult<String, String>> ack = kafka.send(record);
                if (ack.isCompletedExceptionally()) {
                    // e.g. broker unreachable (metadata timeout): stop now instead of blocking on every row.
                    throw new IllegalStateException("kafka send failed immediately", ack.exceptionNow());
                }
                acks.add(ack);
            }
            try {
                CompletableFuture.allOf(acks.toArray(CompletableFuture[]::new))
                        .get(sendTimeout.toMillis(), TimeUnit.MILLISECONDS);
            } catch (Exception e) {
                // Roll back: nothing is marked published; whatever did reach Kafka is re-sent later (dedupe).
                throw new IllegalStateException("kafka send not acknowledged", e);
            }
            String[] ids = rows.stream().map(r -> r.id().toString()).toArray(String[]::new);
            jdbc.update(con -> {
                var ps = con.prepareStatement("update outbox set published_at = now() where id = any(?::uuid[])");
                ps.setArray(1, con.createArrayOf("text", ids));
                return ps;
            });
            return rows.size();
        });
        return n == null ? 0 : n;
    }

    @Scheduled(initialDelayString = "PT5M", fixedDelayString = "${ledger.outbox.cleanup-interval:PT1H}")
    public void cleanupJob() {
        deletePublishedOlderThanRetention();
    }

    public int deletePublishedOlderThanRetention() {
        int total = 0;
        int n;
        do {
            n = jdbc.update("""
                    delete from outbox where id in (
                        select id from outbox
                         where published_at is not null
                           and published_at < now() - (? * interval '1 second')
                         limit 1000)
                    """, retention.toSeconds());
            total += n;
        } while (n == 1000);
        if (total > 0) {
            log.info("deleted {} published outbox rows older than {}", total, retention);
        }
        return total;
    }
}
