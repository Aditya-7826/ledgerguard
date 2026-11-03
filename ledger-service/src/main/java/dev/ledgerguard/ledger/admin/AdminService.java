package dev.ledgerguard.ledger.admin;

import dev.ledgerguard.ledger.account.Account;
import dev.ledgerguard.ledger.account.AccountRepository;
import dev.ledgerguard.ledger.account.AccountStatus;
import dev.ledgerguard.ledger.error.ApiException;
import dev.ledgerguard.ledger.support.Db;
import dev.ledgerguard.ledger.support.TransientRetry;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class AdminService {

    public record StatusChange(UUID accountId, String previousStatus, String status, String approvalRef,
                               boolean replayed, Instant asOf) {}

    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    private final TransientRetry retry;
    private final AccountRepository accounts;

    public AdminService(JdbcTemplate jdbc, TransactionTemplate tx, TransientRetry retry, AccountRepository accounts) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.retry = retry;
        this.accounts = accounts;
    }

    public StatusChange freeze(UUID id, String reason, String actor, String approvalRef) {
        return change(id, AccountStatus.FROZEN, reason, actor, approvalRef);
    }

    public StatusChange unfreeze(UUID id, String reason, String actor, String approvalRef) {
        return change(id, AccountStatus.ACTIVE, reason, actor, approvalRef);
    }

    /**
     * Takes the same row lock as transfers, so an in-flight transfer either commits before the status change
     * or waits, then sees the new status and is rejected. Idempotent on (approval_ref, target status).
     */
    private StatusChange change(UUID id, AccountStatus target, String reason, String actor, String approvalRef) {
        return retry.run(() -> tx.execute(s -> {
            Account a = accounts.lockForUpdate(id).orElseThrow(() ->
                    ApiException.notFound("account_not_found", "Account " + id + " does not exist."));
            Instant now = jdbc.queryForObject("select now() as now", (rs, i) -> Db.instant(rs, "now"));

            List<UUID> prior = jdbc.queryForList(
                    "select account_id from account_status_audit where approval_ref = ? and new_status = ?",
                    UUID.class, approvalRef, target.name());
            if (!prior.isEmpty()) {
                if (!prior.get(0).equals(id)) {
                    throw ApiException.unprocessable("approval_ref_reused",
                            "approval_ref was already used for a different account.");
                }
                return new StatusChange(id, a.status().name(), a.status().name(), approvalRef, true, now);
            }
            if (a.system()) {
                throw ApiException.unprocessable("system_account", "System accounts cannot change status.");
            }
            if (a.status() == AccountStatus.CLOSED) {
                throw ApiException.conflict("account_closed", "Account " + id + " is closed.");
            }
            if (a.status() != target) {
                accounts.updateStatus(id, target);
            }
            try {
                jdbc.update("""
                        insert into account_status_audit
                            (id, account_id, old_status, new_status, reason, actor, approval_ref, created_at)
                        values (?, ?, ?, ?, ?, ?, ?, now())
                        """, UUID.randomUUID(), id, a.status().name(), target.name(), reason, actor, approvalRef);
            } catch (DuplicateKeyException e) {
                // Same approval_ref used concurrently on another account.
                throw ApiException.unprocessable("approval_ref_reused",
                        "approval_ref was already used for a different account.");
            }
            return new StatusChange(id, a.status().name(), target.name(), approvalRef, false, now);
        }));
    }
}
