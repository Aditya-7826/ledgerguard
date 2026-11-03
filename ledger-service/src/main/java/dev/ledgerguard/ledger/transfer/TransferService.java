package dev.ledgerguard.ledger.transfer;

import dev.ledgerguard.ledger.account.AccountRepository;
import dev.ledgerguard.ledger.config.LedgerProperties;
import dev.ledgerguard.ledger.error.ApiException;
import dev.ledgerguard.ledger.idempotency.IdempotentExecutor;
import dev.ledgerguard.ledger.idempotency.IdempotentExecutor.Outcome;
import dev.ledgerguard.ledger.idempotency.IdempotentExecutor.Result;
import dev.ledgerguard.ledger.support.Hashing;
import dev.ledgerguard.ledger.transfer.PostingService.Draft;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferView;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public class TransferService {

    public static final int MAX_MEMO = 280;

    public record TransferCommand(UUID from, UUID to, long amountMinor, String currency, String memo) {}

    public record DepositCommand(UUID accountId, long amountMinor, String currency, String memo) {}

    private final IdempotentExecutor executor;
    private final PostingService posting;
    private final AccountRepository accounts;
    private final TransferRepository transfers;
    private final long maxAmountMinor;

    public TransferService(IdempotentExecutor executor, PostingService posting, AccountRepository accounts,
                           TransferRepository transfers, LedgerProperties props) {
        this.executor = executor;
        this.posting = posting;
        this.accounts = accounts;
        this.transfers = transfers;
        this.maxAmountMinor = props.transfer().maxAmountMinor();
    }

    public Outcome transfer(String clientId, String key, TransferCommand c) {
        IdempotentExecutor.validateKey(key);
        validateAmountAndMemo(c.amountMinor(), c.memo());
        if (c.from().equals(c.to())) {
            throw ApiException.unprocessable("self_transfer", "from_account_id and to_account_id must differ.");
        }
        String hash = Hashing.requestHash("POST /v1/transfers", c.from(), c.to(), c.amountMinor(), c.currency(), c.memo());
        return executor.execute(clientId, key, hash, () -> new Result(HttpStatus.CREATED, posting.post(new Draft(
                clientId, key, c.from(), c.to(), c.amountMinor(), c.currency(), c.memo(), TransferType.TRANSFER, null))));
    }

    public Outcome deposit(String clientId, String key, DepositCommand c) {
        IdempotentExecutor.validateKey(key);
        validateAmountAndMemo(c.amountMinor(), c.memo());
        UUID system = accounts.systemAccountFor(c.currency()).orElseThrow(() ->
                ApiException.unprocessable("unsupported_currency", "No funding account for currency " + c.currency() + "."));
        String hash = Hashing.requestHash("POST /v1/deposits", c.accountId(), c.amountMinor(), c.currency(), c.memo());
        return executor.execute(clientId, key, hash, () -> new Result(HttpStatus.CREATED, posting.post(new Draft(
                clientId, key, system, c.accountId(), c.amountMinor(), c.currency(), c.memo(), TransferType.DEPOSIT, null))));
    }

    /**
     * Compensating transfer: moves the original amount back from the original receiver to the original
     * sender. A transfer can be reversed once; a reversal cannot be reversed; it fails with
     * insufficient_funds if the receiver has already moved the money on.
     */
    public Outcome reverse(String clientId, String key, UUID transferId) {
        IdempotentExecutor.validateKey(key);
        String hash = Hashing.requestHash("POST /v1/transfers/{id}/reverse", transferId);
        return executor.execute(clientId, key, hash, () -> {
            TransferView original = transfers.find(transferId).orElseThrow(() ->
                    ApiException.notFound("transfer_not_found", "Transfer " + transferId + " does not exist."));
            if (original.type() == TransferType.REVERSAL) {
                throw ApiException.unprocessable("cannot_reverse_reversal", "A reversal cannot be reversed.");
            }
            return new Result(HttpStatus.CREATED, posting.post(new Draft(clientId, key,
                    original.toAccountId(), original.fromAccountId(), original.amountMinor(), original.currency(),
                    "Reversal of " + original.id(), TransferType.REVERSAL, original.id())));
        });
    }

    private void validateAmountAndMemo(long amountMinor, String memo) {
        if (amountMinor <= 0) {
            throw ApiException.unprocessable("amount_not_positive", "amount_minor must be greater than 0.");
        }
        if (amountMinor > maxAmountMinor) {
            throw ApiException.unprocessable("amount_too_large", "amount_minor must be at most " + maxAmountMinor + ".");
        }
        if (memo != null && memo.length() > MAX_MEMO) {
            throw ApiException.unprocessable("memo_too_long", "memo must be at most " + MAX_MEMO + " characters.");
        }
    }
}
