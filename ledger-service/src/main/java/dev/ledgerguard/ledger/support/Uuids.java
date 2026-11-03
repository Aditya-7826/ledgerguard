package dev.ledgerguard.ledger.support;

import java.util.Comparator;
import java.util.UUID;

public final class Uuids {

    private Uuids() {}

    /**
     * Postgres orders uuid values by unsigned bytes. {@link UUID#compareTo} compares signed longs, which
     * disagrees for ids with the high bit set, so lock ordering uses this comparator instead.
     */
    public static final Comparator<UUID> PG_ORDER = (a, b) -> {
        int c = Long.compareUnsigned(a.getMostSignificantBits(), b.getMostSignificantBits());
        return c != 0 ? c : Long.compareUnsigned(a.getLeastSignificantBits(), b.getLeastSignificantBits());
    };
}
