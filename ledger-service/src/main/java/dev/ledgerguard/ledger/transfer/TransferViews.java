package dev.ledgerguard.ledger.transfer;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** Response shapes (serialised snake_case). */
public final class TransferViews {

    private TransferViews() {}

    public record EntryView(UUID id, UUID accountId, String direction, long amountMinor) {}

    public record TransferView(UUID id, TransferType type, UUID fromAccountId, UUID toAccountId, long amountMinor,
                               String currency, String memo, UUID reversesTransferId, Instant createdAt,
                               List<EntryView> entries) {}

    public record TransferWithAsOf(TransferView transfer, Instant asOf) {}

    public record TransferPage(UUID accountId, Instant from, Instant to, List<TransferView> items,
                               String nextCursor, Instant asOf) {}

    public record Counterparty(UUID accountId, long sentMinor, long receivedMinor, long transferCount,
                               Instant firstAt, Instant lastAt) {}

    public record CounterpartyPage(UUID accountId, Instant from, Instant to, List<Counterparty> counterparties,
                                   boolean truncated, Instant asOf) {}
}
