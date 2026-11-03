package dev.ledgerguard.ledger.outbox;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Writes events to the outbox in the caller's transaction (transactional outbox pattern). The event
 * commits if and only if the ledger write commits.
 */
@Component
public class OutboxWriter {

    public static final int SCHEMA_VERSION = 1;

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public OutboxWriter(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    /** {@code fields} are written in snake_case as given; the envelope fields are added here. */
    public UUID append(String eventType, UUID aggregateId, Instant occurredAt, Map<String, Object> fields) {
        UUID eventId = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("event_id", eventId);
        payload.put("event_type", eventType);
        payload.put("schema_version", SCHEMA_VERSION);
        payload.put("aggregate_id", aggregateId);
        payload.put("occurred_at", occurredAt);
        payload.putAll(fields);
        String json;
        try {
            json = mapper.writeValueAsString(payload);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("could not serialise event", e);
        }
        jdbc.update("""
                insert into outbox (id, event_id, aggregate_id, event_type, payload, created_at)
                values (?, ?, ?, ?, ?::jsonb, now())
                """, UUID.randomUUID(), eventId, aggregateId, eventType, json);
        return eventId;
    }
}
