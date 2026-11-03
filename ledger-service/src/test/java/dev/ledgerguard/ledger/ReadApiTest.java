package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ReadApiTest extends IntegrationTest {

    private static String enc(Instant i) {
        return URLEncoder.encode(i.toString(), StandardCharsets.UTF_8);
    }

    @Test
    void everyReadIncludesAsOf() {
        UUID a = createFundedAccount(5_000);
        UUID b = createAccount("INR");
        String tid = transfer(a, b, 1_000, UUID.randomUUID().toString()).json().get("id").asText();

        for (String path : new String[] {
                "/v1/accounts/" + a, "/v1/accounts/" + a + "/balance", "/v1/accounts/" + a + "/transfers",
                "/v1/accounts/" + a + "/counterparties", "/v1/transfers/" + tid}) {
            Resp r = get(path, READONLY_KEY);
            assertThat(r.status()).as(path + " " + r.body()).isEqualTo(200);
            assertThat(r.json().hasNonNull("as_of")).as(path).isTrue();
        }
        Resp t = get("/v1/transfers/" + tid, READONLY_KEY);
        assertThat(t.json().get("transfer").get("entries")).hasSize(2);
        assertThat(get("/v1/accounts/" + a + "/balance", READONLY_KEY).json().get("balance_minor").asLong())
                .isEqualTo(4_000);
    }

    @Test
    void transfersPaginateWithCursorWithoutGapsOrDuplicates() {
        UUID a = createFundedAccount(100_000);
        UUID b = createAccount("INR");
        for (int i = 0; i < 25; i++) {
            assertThat(transfer(a, b, 10, UUID.randomUUID().toString()).status()).isEqualTo(201);
        }
        // 25 transfers + 1 deposit = 26 rows touching a
        Set<String> seen = new HashSet<>();
        String cursor = null;
        int pages = 0;
        do {
            String path = "/v1/accounts/" + a + "/transfers?limit=10" + (cursor == null ? "" : "&cursor=" + cursor);
            Resp r = get(path, READONLY_KEY);
            assertThat(r.status()).as(r.body()).isEqualTo(200);
            for (JsonNode item : r.json().get("items")) {
                assertThat(seen.add(item.get("id").asText())).as("duplicate across pages").isTrue();
            }
            JsonNode next = r.json().get("next_cursor");
            cursor = next == null || next.isNull() ? null : next.asText();
            pages++;
        } while (cursor != null);
        assertThat(seen).hasSize(26);
        assertThat(pages).isEqualTo(3);
    }

    @Test
    void windowAndLimitAreBounded() {
        UUID a = createAccount("INR");
        Instant now = Instant.now();
        Resp tooWide = get("/v1/accounts/" + a + "/transfers?from=" + enc(now.minus(31, ChronoUnit.DAYS))
                + "&to=" + enc(now), READONLY_KEY);
        assertThat(tooWide.status()).isEqualTo(422);
        assertThat(tooWide.code()).isEqualTo("window_too_large");

        Resp limit = get("/v1/accounts/" + a + "/transfers?limit=101", READONLY_KEY);
        assertThat(limit.status()).isEqualTo(422);
        assertThat(limit.code()).isEqualTo("invalid_limit");

        Resp inverted = get("/v1/accounts/" + a + "/counterparties?from=" + enc(now) + "&to="
                + enc(now.minus(1, ChronoUnit.HOURS)), READONLY_KEY);
        assertThat(inverted.status()).isEqualTo(422);

        assertThat(get("/v1/accounts/" + a + "/transfers?cursor=bm90LWEtY3Vyc29y", READONLY_KEY).status()).isEqualTo(400);
        assertThat(get("/v1/accounts/" + a + "/transfers?from=yesterday", READONLY_KEY).status()).isEqualTo(400);
        assertThat(get("/v1/accounts/" + UUID.randomUUID(), READONLY_KEY).status()).isEqualTo(404);
    }

    @Test
    void counterpartiesAggregateBothDirections() {
        UUID a = createFundedAccount(100_000);
        UUID b = createFundedAccount(100_000);
        UUID c = createAccount("INR");
        transfer(a, b, 100, UUID.randomUUID().toString());
        transfer(a, b, 200, UUID.randomUUID().toString());
        transfer(b, a, 50, UUID.randomUUID().toString());
        transfer(a, c, 10, UUID.randomUUID().toString());

        Resp r = get("/v1/accounts/" + a + "/counterparties", READONLY_KEY);
        assertThat(r.status()).isEqualTo(200);
        JsonNode bRow = null;
        for (JsonNode n : r.json().get("counterparties")) {
            if (n.get("account_id").asText().equals(b.toString())) {
                bRow = n;
            }
        }
        assertThat(bRow).isNotNull();
        assertThat(bRow.get("sent_minor").asLong()).isEqualTo(300);
        assertThat(bRow.get("received_minor").asLong()).isEqualTo(50);
        assertThat(bRow.get("transfer_count").asLong()).isEqualTo(3);
        assertThat(r.json().get("truncated").asBoolean()).isFalse();
    }

    @Test
    void balanceAsOfIsDerivedFromEntries() throws Exception {
        UUID a = createFundedAccount(1_000);
        Thread.sleep(20);
        Instant between = jdbc.queryForObject("select now()", java.sql.Timestamp.class).toInstant();
        Thread.sleep(20);
        deposit(a, 500);

        Resp past = get("/v1/accounts/" + a + "/balance?as_of=" + enc(between), READONLY_KEY);
        assertThat(past.status()).as(past.body()).isEqualTo(200);
        assertThat(past.json().get("balance_minor").asLong()).isEqualTo(1_000);
        assertThat(get("/v1/accounts/" + a + "/balance", READONLY_KEY).json().get("balance_minor").asLong())
                .isEqualTo(1_500);
    }
}
