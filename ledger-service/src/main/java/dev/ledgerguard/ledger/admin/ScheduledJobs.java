package dev.ledgerguard.ledger.admin;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class ScheduledJobs {

    private static final Logger log = LoggerFactory.getLogger(ScheduledJobs.class);
    private static final int CLEANUP_BATCH = 1000;

    private final ReconcileService reconcile;
    private final JdbcTemplate jdbc;

    public ScheduledJobs(ReconcileService reconcile, JdbcTemplate jdbc) {
        this.reconcile = reconcile;
        this.jdbc = jdbc;
    }

    @Scheduled(initialDelayString = "PT30S", fixedDelayString = "${ledger.reconcile.interval:PT5M}")
    public void reconcileJob() {
        ReconcileService.Report r = reconcile.reconcile();
        if (r.ok()) {
            log.info("reconcile ok: accounts={} entries={}", r.accountsChecked(), r.entriesChecked());
        } else {
            log.error("RECONCILE FAILED: entries_net={} mismatches={}", r.entriesNetMinor(), r.mismatches());
        }
    }

    /** Deletes expired idempotency keys in small batches so it never holds long locks. */
    @Scheduled(initialDelayString = "PT1M", fixedDelayString = "${ledger.idempotency.cleanup-interval:PT10M}")
    public void idempotencyCleanupJob() {
        cleanupExpiredIdempotencyKeys();
    }

    public int cleanupExpiredIdempotencyKeys() {
        int total = 0;
        int n;
        do {
            n = jdbc.update("""
                    delete from idempotency_keys
                     where ctid in (select ctid from idempotency_keys where expires_at < now() limit ?)
                    """, CLEANUP_BATCH);
            total += n;
        } while (n == CLEANUP_BATCH);
        if (total > 0) {
            log.info("deleted {} expired idempotency keys", total);
        }
        return total;
    }
}
