package dev.ledgerguard.ledger.transfer;

import dev.ledgerguard.ledger.account.Account;
import dev.ledgerguard.ledger.account.AccountRepository;
import dev.ledgerguard.ledger.account.AccountStatus;
import dev.ledgerguard.ledger.error.ApiException;
import dev.ledgerguard.ledger.outbox.OutboxWriter;
import dev.ledgerguard.ledger.transfer.TransferViews.EntryView;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferView;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

/**
 * The double-entry core. Must run inside the caller's transaction (see IdempotentExecutor).
 * Locks both accounts in UUID order, validates against the locked state, then writes the transfer,
 * one DEBIT + one CREDIT entry, both balance projections and the outbox events.
 */
@Service
public class PostingService {

    public record Draft(String clientId, String idempotencyKey, UUID from, UUID to, long amountMinor, String currency,
                        String memo, TransferType type, UUID reversesTransferId) {}

    private final AccountRepository accounts;
    private final TransferRepository transfers;
    private final OutboxWriter outbox;

    public PostingService(AccountRepository accounts, TransferRepository transfers, OutboxWriter outbox) {
        this.accounts = accounts;
        this.transfers = transfers;
        this.outbox = outbox;
    }

    public TransferView post(Draft d) {
        if (d.from().equals(d.to())) {
            throw ApiException.unprocessable("self_transfer", "from_account_id and to_account_id must differ.");
        }
        Map<UUID, Account> locked = accounts.lockInOrder(d.from(), d.to());
        Account from = locked.get(d.from());
        Account to = locked.get(d.to());

        // All checks below see the locked, current row versions (e.g. a freeze that committed first).
        requireUsable(from, d.from(), "from");
        requireUsable(to, d.to(), "to");
        if (!from.currency().equals(d.currency()) || !to.currency().equals(d.currency())) {
            throw ApiException.unprocessable("currency_mismatch",
                    "Both accounts must be in the transfer currency " + d.currency() + ".");
        }
        if (d.reversesTransferId() != null && transfers.findReversalOf(d.reversesTransferId()).isPresent()) {
            throw ApiException.conflict("already_reversed", "This transfer has already been reversed.");
        }
        if (!from.system()) {
            long balance = accounts.balance(from.id());
            if (balance < d.amountMinor()) {
                throw ApiException.unprocessable("insufficient_funds",
                        "Account " + from.id() + " has insufficient funds.");
            }
        }

        UUID transferId = UUID.randomUUID();
        Instant createdAt;
        try {
            createdAt = transfers.insertTransfer(transferId, d.clientId(), d.idempotencyKey(), d.from(), d.to(),
                    d.amountMinor(), d.currency(), d.memo(), d.type(), d.reversesTransferId());
        } catch (DuplicateKeyException e) {
            if (d.reversesTransferId() != null) {
                throw ApiException.conflict("already_reversed", "This transfer has already been reversed.");
            }
            throw e;
        }
        UUID debitId = UUID.randomUUID();
        UUID creditId = UUID.randomUUID();
        transfers.insertEntry(debitId, transferId, d.from(), "DEBIT", d.amountMinor());
        transfers.insertEntry(creditId, transferId, d.to(), "CREDIT", d.amountMinor());
        accounts.addToBalance(d.from(), -d.amountMinor());
        accounts.addToBalance(d.to(), d.amountMinor());

        writeEvents(d, transferId, debitId, creditId, from, to, createdAt);

        return new TransferView(transferId, d.type(), d.from(), d.to(), d.amountMinor(), d.currency(), d.memo(),
                d.reversesTransferId(), createdAt, List.of(
                new EntryView(debitId, d.from(), "DEBIT", d.amountMinor()),
                new EntryView(creditId, d.to(), "CREDIT", d.amountMinor())));
    }

    private static void requireUsable(Account a, UUID id, String side) {
        if (a == null) {
            throw ApiException.unprocessable("account_not_found", "The " + side + " account " + id + " does not exist.");
        }
        if (a.status() == AccountStatus.FROZEN) {
            throw ApiException.forbidden("account_frozen", "The " + side + " account " + id + " is frozen.");
        }
        if (a.status() == AccountStatus.CLOSED) {
            throw ApiException.unprocessable("account_closed", "The " + side + " account " + id + " is closed.");
        }
    }

    private void writeEvents(Draft d, UUID transferId, UUID debitId, UUID creditId, Account from, Account to,
                             Instant createdAt) {
        outbox.append("EntryPosted", from.id(), createdAt,
                entryFields(d, transferId, debitId, "DEBIT", from, to, createdAt));
        outbox.append("EntryPosted", to.id(), createdAt,
                entryFields(d, transferId, creditId, "CREDIT", to, from, createdAt));

        Map<String, Object> t = new LinkedHashMap<>();
        t.put("transfer_id", transferId);
        t.put("type", d.type().name());
        t.put("from_account_id", from.id());
        t.put("to_account_id", to.id());
        t.put("amount_minor", d.amountMinor());
        t.put("currency", d.currency());
        t.put("memo", d.memo());
        t.put("reverses_transfer_id", d.reversesTransferId());
        t.put("from_account_created_at", from.createdAt());
        t.put("to_account_created_at", to.createdAt());
        t.put("from_is_system", from.system());
        t.put("to_is_system", to.system());
        t.put("posted_at", createdAt);
        outbox.append("TransferPosted", transferId, createdAt, t);
    }

    private static Map<String, Object> entryFields(Draft d, UUID transferId, UUID entryId, String direction,
                                                   Account account, Account counterparty, Instant createdAt) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("entry_id", entryId);
        e.put("transfer_id", transferId);
        e.put("account_id", account.id());
        e.put("counterparty_account_id", counterparty.id());
        e.put("direction", direction);
        e.put("amount_minor", d.amountMinor());
        e.put("currency", d.currency());
        e.put("transfer_type", d.type().name());
        e.put("memo", d.memo());
        e.put("account_created_at", account.createdAt());
        e.put("account_is_system", account.system());
        e.put("counterparty_created_at", counterparty.createdAt());
        e.put("counterparty_is_system", counterparty.system());
        e.put("posted_at", createdAt);
        return e;
    }
}
