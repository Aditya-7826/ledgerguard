package dev.ledgerguard.ledger.account;

import java.time.Instant;
import java.util.UUID;

public record Account(UUID id, String ownerName, String currency, AccountStatus status, boolean system,
                      Instant createdAt, long version) {}
