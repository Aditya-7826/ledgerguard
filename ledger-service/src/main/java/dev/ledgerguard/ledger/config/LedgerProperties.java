package dev.ledgerguard.ledger.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

@ConfigurationProperties("ledger")
public record LedgerProperties(
        @DefaultValue Security security,
        @DefaultValue Transfer transfer,
        @DefaultValue Idempotency idempotency,
        @DefaultValue Retry retry,
        @DefaultValue Read read) {

    public record Security(@DefaultValue("") String apiKeys) {}

    public record Transfer(@DefaultValue("1000000000") long maxAmountMinor) {}

    public record Idempotency(@DefaultValue("PT24H") Duration ttl) {}

    public record Retry(@DefaultValue("3") int maxRetries) {}

    public record Read(
            @DefaultValue("P30D") Duration maxWindow,
            @DefaultValue("100") int maxLimit,
            @DefaultValue("50") int defaultLimit) {}
}
