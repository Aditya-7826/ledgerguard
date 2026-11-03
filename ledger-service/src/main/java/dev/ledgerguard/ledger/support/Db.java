package dev.ledgerguard.ledger.support;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;

/** Small JDBC helpers for timestamptz and uuid columns. */
public final class Db {

    private Db() {}

    public static Instant instant(ResultSet rs, String col) throws SQLException {
        OffsetDateTime odt = rs.getObject(col, OffsetDateTime.class);
        return odt == null ? null : odt.toInstant();
    }

    public static UUID uuid(ResultSet rs, String col) throws SQLException {
        return rs.getObject(col, UUID.class);
    }

    public static OffsetDateTime ts(Instant i) {
        return i == null ? null : OffsetDateTime.ofInstant(i, ZoneOffset.UTC);
    }
}
