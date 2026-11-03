package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/** Boots the real app on a random port against a Testcontainers Postgres 16 and talks to it over HTTP. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "ledger.security.api-keys=" + IntegrationTest.KEYS,
        "ledger.reconcile.interval=PT1H",
        "ledger.transfer.max-amount-minor=1000000000",
        "spring.datasource.hikari.maximum-pool-size=30",
        // Ledger-core tests don't need Kafka; OutboxTest runs its own context with a broker.
        "ledger.outbox.enabled=false",
        "spring.kafka.admin.auto-create=false"
})
@Import(IntegrationTest.Containers.class)
public abstract class IntegrationTest {

    public static final String CLIENT_KEY = "test-client-key-0123456789";
    public static final String READONLY_KEY = "test-readonly-key-0123456789";
    public static final String ADMIN_KEY = "test-admin-key-0123456789";
    static final String KEYS = "client:CLIENT:" + CLIENT_KEY + ",mcp:READONLY:" + READONLY_KEY + ",agent:ADMIN:" + ADMIN_KEY;

    public static final UUID SYSTEM_INR = UUID.fromString("00000000-0000-0000-0000-000000000001");

    @TestConfiguration(proxyBeanMethods = false)
    static class Containers {
        @Bean
        @ServiceConnection
        PostgreSQLContainer<?> postgres() {
            return new PostgreSQLContainer<>(DockerImageName.parse("postgres:16-alpine"));
        }
    }

    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .version(HttpClient.Version.HTTP_1_1)
            .build();
    protected static final ObjectMapper JSON = new ObjectMapper();

    @LocalServerPort
    protected int port;

    @Autowired
    protected JdbcTemplate jdbc;

    public record Resp(int status, String body, JsonNode json, HttpResponse<String> raw) {
        public String code() {
            return json != null && json.has("code") ? json.get("code").asText() : null;
        }
    }

    protected Resp post(String path, String apiKey, String idempotencyKey, String body) {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .timeout(Duration.ofSeconds(60))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body == null ? "" : body));
        if (apiKey != null) {
            b.header("X-API-Key", apiKey);
        }
        if (idempotencyKey != null) {
            b.header("Idempotency-Key", idempotencyKey);
        }
        return send(b.build());
    }

    protected Resp get(String path, String apiKey) {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .timeout(Duration.ofSeconds(60))
                .GET();
        if (apiKey != null) {
            b.header("X-API-Key", apiKey);
        }
        return send(b.build());
    }

    private static Resp send(HttpRequest req) {
        try {
            HttpResponse<String> r = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
            JsonNode json = null;
            if (r.body() != null && !r.body().isBlank()) {
                try {
                    json = JSON.readTree(r.body());
                } catch (IOException ignored) {
                    // non-JSON body
                }
            }
            return new Resp(r.statusCode(), r.body(), json, r);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
    }

    // ---- fixtures -------------------------------------------------------------------------------------

    protected UUID createAccount(String currency) {
        Resp r = post("/v1/accounts", CLIENT_KEY, null,
                "{\"owner_name\":\"Test Owner\",\"currency\":\"" + currency + "\"}");
        assertThat(r.status()).as(r.body()).isEqualTo(201);
        return UUID.fromString(r.json().get("id").asText());
    }

    protected UUID createFundedAccount(long amountMinor) {
        UUID id = createAccount("INR");
        deposit(id, amountMinor);
        return id;
    }

    protected Resp deposit(UUID account, long amountMinor) {
        Resp r = post("/v1/deposits", CLIENT_KEY, UUID.randomUUID().toString(),
                "{\"account_id\":\"" + account + "\",\"amount_minor\":" + amountMinor + ",\"currency\":\"INR\"}");
        assertThat(r.status()).as(r.body()).isEqualTo(201);
        return r;
    }

    protected Resp transfer(UUID from, UUID to, long amountMinor, String idemKey) {
        return post("/v1/transfers", CLIENT_KEY, idemKey, transferBody(from, to, amountMinor, "INR", null));
    }

    protected static String transferBody(UUID from, UUID to, Object amount, String currency, String memo) {
        StringBuilder sb = new StringBuilder("{\"from_account_id\":\"").append(from)
                .append("\",\"to_account_id\":\"").append(to)
                .append("\",\"amount_minor\":").append(amount)
                .append(",\"currency\":\"").append(currency).append('"');
        if (memo != null) {
            sb.append(",\"memo\":").append(quote(memo));
        }
        return sb.append('}').toString();
    }

    protected static String quote(String s) {
        try {
            return JSON.writeValueAsString(s);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    protected long balance(UUID account) {
        return jdbc.queryForObject("select balance_minor from account_balances where account_id = ?", Long.class, account);
    }

    protected void assertReconciles() {
        Resp r = get("/v1/admin/reconcile", ADMIN_KEY);
        assertThat(r.status()).as(r.body()).isEqualTo(200);
        assertThat(r.json().get("ok").asBoolean()).as(r.body()).isTrue();
        assertThat(r.json().get("entries_net_minor").asLong()).isZero();
    }

    protected Resp freeze(UUID account, String approvalRef) {
        return post("/v1/admin/accounts/" + account + "/freeze", ADMIN_KEY, null,
                "{\"reason\":\"test\",\"actor\":\"analyst@test\",\"approval_ref\":\"" + approvalRef + "\"}");
    }
}
