package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.UUID;
import org.junit.jupiter.api.Test;

class ReversalTest extends IntegrationTest {

    private Resp reverse(String transferId, String key) {
        return post("/v1/transfers/" + transferId + "/reverse", CLIENT_KEY, key, null);
    }

    @Test
    void reversalRestoresBalancesAndLinksToOriginal() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String tid = transfer(a, b, 4_000, UUID.randomUUID().toString()).json().get("id").asText();

        Resp rev = reverse(tid, UUID.randomUUID().toString());

        assertThat(rev.status()).as(rev.body()).isEqualTo(201);
        assertThat(rev.json().get("type").asText()).isEqualTo("REVERSAL");
        assertThat(rev.json().get("reverses_transfer_id").asText()).isEqualTo(tid);
        assertThat(rev.json().get("from_account_id").asText()).isEqualTo(b.toString());
        assertThat(balance(a)).isEqualTo(10_000);
        assertThat(balance(b)).isZero();
        assertReconciles();
    }

    @Test
    void reversalIsIdempotentOnKey() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String tid = transfer(a, b, 1_000, UUID.randomUUID().toString()).json().get("id").asText();
        String key = UUID.randomUUID().toString();

        Resp first = reverse(tid, key);
        Resp replay = reverse(tid, key);

        assertThat(first.status()).isEqualTo(201);
        assertThat(replay.status()).isEqualTo(201);
        assertThat(replay.body()).isEqualTo(first.body());
        assertThat(balance(a)).isEqualTo(10_000);
    }

    @Test
    void transferCanBeReversedOnlyOnce() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String tid = transfer(a, b, 1_000, UUID.randomUUID().toString()).json().get("id").asText();

        assertThat(reverse(tid, UUID.randomUUID().toString()).status()).isEqualTo(201);
        Resp second = reverse(tid, UUID.randomUUID().toString());

        assertThat(second.status()).isEqualTo(409);
        assertThat(second.code()).isEqualTo("already_reversed");
        assertThat(balance(a)).isEqualTo(10_000);
    }

    @Test
    void reversalCannotBeReversed() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String tid = transfer(a, b, 1_000, UUID.randomUUID().toString()).json().get("id").asText();
        String revId = reverse(tid, UUID.randomUUID().toString()).json().get("id").asText();

        Resp r = reverse(revId, UUID.randomUUID().toString());

        assertThat(r.status()).isEqualTo(422);
        assertThat(r.code()).isEqualTo("cannot_reverse_reversal");
    }

    @Test
    void reversalFailsCleanlyWhenReceiverMovedTheFunds() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        UUID c = createAccount("INR");
        String tid = transfer(a, b, 5_000, UUID.randomUUID().toString()).json().get("id").asText();
        assertThat(transfer(b, c, 4_000, UUID.randomUUID().toString()).status()).isEqualTo(201);

        Resp r = reverse(tid, UUID.randomUUID().toString());

        assertThat(r.status()).isEqualTo(422);
        assertThat(r.code()).isEqualTo("insufficient_funds");
        assertThat(balance(a)).isEqualTo(5_000);
        assertThat(balance(b)).isEqualTo(1_000);
        assertThat(jdbc.queryForObject("select count(*) from transfers where reverses_transfer_id = ?::uuid",
                Long.class, tid)).isZero();
        assertReconciles();
    }

    @Test
    void unknownTransferIs404() {
        Resp r = reverse(UUID.randomUUID().toString(), UUID.randomUUID().toString());
        assertThat(r.status()).isEqualTo(404);
        assertThat(r.code()).isEqualTo("transfer_not_found");
    }

    @Test
    void reversalRequiresIdempotencyKey() {
        UUID a = createFundedAccount(10_000);
        UUID b = createAccount("INR");
        String tid = transfer(a, b, 1_000, UUID.randomUUID().toString()).json().get("id").asText();
        assertThat(reverse(tid, null).status()).isEqualTo(400);
    }
}
