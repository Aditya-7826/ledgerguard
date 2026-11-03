package dev.ledgerguard.ledger.support;

import dev.ledgerguard.ledger.config.LedgerProperties;
import java.sql.SQLException;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Re-runs a whole transaction on Postgres deadlock (40P01) or serialization failure (40001), at most
 * {@code maxRetries} extra times with jittered backoff. Anything else (including validation errors)
 * propagates immediately.
 */
@Component
public class TransientRetry {

    private static final Logger log = LoggerFactory.getLogger(TransientRetry.class);
    private static final String DEADLOCK = "40P01";
    private static final String SERIALIZATION_FAILURE = "40001";

    private final int maxRetries;

    public TransientRetry(LedgerProperties props) {
        this.maxRetries = props.retry().maxRetries();
    }

    public <T> T run(Supplier<T> work) {
        for (int attempt = 0; ; attempt++) {
            try {
                return work.get();
            } catch (RuntimeException e) {
                if (!isTransient(e) || attempt >= maxRetries) {
                    throw e;
                }
                long backoffMs = (long) (10 * Math.pow(2, attempt)) + ThreadLocalRandom.current().nextLong(0, 25);
                log.info("transient db failure (attempt {}), retrying in {} ms", attempt + 1, backoffMs);
                sleep(backoffMs);
            }
        }
    }

    public static boolean isTransient(Throwable e) {
        for (Throwable t = e; t != null; t = t.getCause()) {
            if (t instanceof SQLException sql) {
                String state = sql.getSQLState();
                if (DEADLOCK.equals(state) || SERIALIZATION_FAILURE.equals(state)) {
                    return true;
                }
            }
            if (t.getCause() == t) {
                break;
            }
        }
        return false;
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted during retry backoff", ie);
        }
    }
}
