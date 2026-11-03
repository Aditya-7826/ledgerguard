package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class ValidationTest extends IntegrationTest {

    UUID a;
    UUID b;

    @BeforeEach
    void accounts() {
        a = createFundedAccount(10_000);
        b = createAccount("INR");
    }

    private Resp send(String body) {
        return post("/v1/transfers", CLIENT_KEY, UUID.randomUUID().toString(), body);
    }

    private void assertProblem(Resp r, int status, String code) {
        assertThat(r.status()).as(r.body()).isEqualTo(status);
        assertThat(r.code()).as(r.body()).isEqualTo(code);
        assertThat(r.raw().headers().firstValue("Content-Type").orElse("")).startsWith("application/problem+json");
        assertThat(r.body()).doesNotContain("Exception").doesNotContain("at dev.").doesNotContain("org.");
    }

    @Test
    void zeroAmount() {
        assertProblem(send(transferBody(a, b, 0, "INR", null)), 422, "amount_not_positive");
    }

    @Test
    void negativeAmount() {
        assertProblem(send(transferBody(a, b, -5, "INR", null)), 422, "amount_not_positive");
    }

    @Test
    void amountAboveConfiguredMax() {
        assertProblem(send(transferBody(a, b, 1_000_000_001L, "INR", null)), 422, "amount_too_large");
    }

    @Test
    void overflowSizedAmountIsRejectedAsMalformed() {
        assertProblem(send(transferBody(a, b, "9223372036854775808", "INR", null)), 400, "malformed_request");
        assertProblem(send(transferBody(a, b, "1e30", "INR", null)), 400, "malformed_request");
    }

    @Test
    void fractionalOrStringAmountIsRejected() {
        assertProblem(send(transferBody(a, b, "10.5", "INR", null)), 400, "malformed_request");
        assertProblem(send(transferBody(a, b, "\"100\"", "INR", null)), 400, "malformed_request");
    }

    @Test
    void selfTransfer() {
        assertProblem(send(transferBody(a, a, 100, "INR", null)), 422, "self_transfer");
    }

    @Test
    void currencyMismatch() {
        UUID usd = createAccount("USD");
        assertProblem(send(transferBody(a, usd, 100, "INR", null)), 422, "currency_mismatch");
        assertProblem(send(transferBody(a, b, 100, "USD", null)), 422, "currency_mismatch");
    }

    @Test
    void unknownAccount() {
        assertProblem(send(transferBody(a, UUID.randomUUID(), 100, "INR", null)), 422, "account_not_found");
        assertProblem(send(transferBody(UUID.randomUUID(), b, 100, "INR", null)), 422, "account_not_found");
    }

    @Test
    void closedAccount() {
        UUID closed = createAccount("INR");
        jdbc.update("update accounts set status = 'CLOSED' where id = ?", closed);
        assertProblem(send(transferBody(a, closed, 100, "INR", null)), 422, "account_closed");
    }

    @Test
    void insufficientFunds() {
        assertProblem(send(transferBody(a, b, 10_001, "INR", null)), 422, "insufficient_funds");
        assertThat(balance(a)).isEqualTo(10_000);
    }

    @Test
    void memoTooLong() {
        assertProblem(send(transferBody(a, b, 100, "INR", "x".repeat(281))), 422, "memo_too_long");
        assertThat(send(transferBody(a, b, 100, "INR", "x".repeat(280))).status()).isEqualTo(201);
    }

    @Test
    void malformedUuidInBodyAndPath() {
        String body = "{\"from_account_id\":\"not-a-uuid\",\"to_account_id\":\"" + b
                + "\",\"amount_minor\":100,\"currency\":\"INR\"}";
        assertProblem(send(body), 400, "malformed_request");
        assertProblem(get("/v1/accounts/not-a-uuid", READONLY_KEY), 400, "bad_request");
    }

    @Test
    void missingIdempotencyKeyHeader() {
        Resp r = post("/v1/transfers", CLIENT_KEY, null, transferBody(a, b, 100, "INR", null));
        assertProblem(r, 400, "bad_request");
    }

    @Test
    void invalidIdempotencyKey() {
        Resp r = post("/v1/transfers", CLIENT_KEY, "x".repeat(65), transferBody(a, b, 100, "INR", null));
        assertProblem(r, 400, "invalid_idempotency_key");
    }

    @Test
    void missingFieldsAndBadCurrencyFormat() {
        assertProblem(send("{\"from_account_id\":\"" + a + "\"}"), 422, "validation_failed");
        assertProblem(send(transferBody(a, b, 100, "inr", null)), 422, "validation_failed");
    }

    @Test
    void unknownFieldAndInvalidJson() {
        assertProblem(send("{\"amount\":100}"), 400, "malformed_request");
        assertProblem(send("{not json"), 400, "malformed_request");
    }

    @Test
    void depositToUnsupportedCurrency() {
        Resp r = post("/v1/deposits", CLIENT_KEY, UUID.randomUUID().toString(),
                "{\"account_id\":\"" + b + "\",\"amount_minor\":100,\"currency\":\"EUR\"}");
        assertProblem(r, 422, "unsupported_currency");
    }

    @Test
    void authRoles() {
        String body = transferBody(a, b, 1, "INR", null);
        assertProblem(post("/v1/transfers", null, UUID.randomUUID().toString(), body), 401, "unauthenticated");
        assertProblem(post("/v1/transfers", "wrong-key-0123456789", UUID.randomUUID().toString(), body), 401,
                "unauthenticated");
        assertProblem(post("/v1/transfers", READONLY_KEY, UUID.randomUUID().toString(), body), 403, "forbidden");
        assertProblem(post("/v1/transfers", ADMIN_KEY, UUID.randomUUID().toString(), body), 403, "forbidden");
        assertProblem(get("/v1/accounts/" + a, CLIENT_KEY), 403, "forbidden");
        assertProblem(get("/v1/admin/reconcile", READONLY_KEY), 403, "forbidden");
        assertThat(get("/v1/accounts/" + a, READONLY_KEY).status()).isEqualTo(200);
    }
}
