package dev.ledgerguard.ledger.admin;

import dev.ledgerguard.ledger.support.Db;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Checks the two ledger invariants on one consistent snapshot:
 * (1) all entries net to zero (every debit has a matching credit), and
 * (2) every balance projection equals the sum of that account's entries.
 */
@Service
public class ReconcileService {

    public record Mismatch(UUID accountId, long balanceMinor, long entriesSumMinor) {}

    public record Report(boolean ok, long entriesNetMinor, long accountsChecked, long entriesChecked,
                         List<Mismatch> mismatches, Instant asOf) {}

    private final JdbcTemplate jdbc;

    public ReconcileService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public Report reconcile() {
        Instant asOf = jdbc.queryForObject("select now() as now", (rs, i) -> Db.instant(rs, "now"));
        Long net = jdbc.queryForObject("""
                select coalesce(sum(case when direction = 'CREDIT' then amount_minor else -amount_minor end), 0)
                  from entries
                """, Long.class);
        Long entryCount = jdbc.queryForObject("select count(*) from entries", Long.class);
        Long accountCount = jdbc.queryForObject("select count(*) from account_balances", Long.class);
        List<Mismatch> mismatches = jdbc.query("""
                select b.account_id, b.balance_minor, coalesce(e.total, 0) as entries_sum
                  from account_balances b
                  left join (select account_id,
                                    sum(case when direction = 'CREDIT' then amount_minor else -amount_minor end) as total
                               from entries group by account_id) e on e.account_id = b.account_id
                 where b.balance_minor <> coalesce(e.total, 0)
                 order by b.account_id
                 limit 100
                """, (rs, i) -> new Mismatch(Db.uuid(rs, "account_id"), rs.getLong("balance_minor"),
                rs.getLong("entries_sum")));
        long netValue = net == null ? 0 : net;
        return new Report(netValue == 0 && mismatches.isEmpty(), netValue,
                accountCount == null ? 0 : accountCount, entryCount == null ? 0 : entryCount, mismatches, asOf);
    }
}
