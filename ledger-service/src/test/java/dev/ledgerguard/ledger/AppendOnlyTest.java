package dev.ledgerguard.ledger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessException;

class AppendOnlyTest extends IntegrationTest {

    @Test
    void entriesRejectUpdateDeleteAndTruncate() {
        UUID a = createFundedAccount(1_000);
        UUID entry = jdbc.queryForObject("select id from entries where account_id = ? limit 1", UUID.class, a);

        assertThatThrownBy(() -> jdbc.update("update entries set amount_minor = 1 where id = ?", entry))
                .isInstanceOf(DataAccessException.class)
                .hasMessageContaining("append-only");
        assertThatThrownBy(() -> jdbc.update("delete from entries where id = ?", entry))
                .isInstanceOf(DataAccessException.class)
                .hasMessageContaining("append-only");
        assertThatThrownBy(() -> jdbc.execute("truncate entries"))
                .isInstanceOf(DataAccessException.class)
                .hasMessageContaining("append-only");

        assertThat(jdbc.queryForObject("select amount_minor from entries where id = ?", Long.class, entry))
                .isEqualTo(1_000);
        assertReconciles();
    }
}
