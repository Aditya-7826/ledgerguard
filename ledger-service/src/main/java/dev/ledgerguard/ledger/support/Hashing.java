package dev.ledgerguard.ledger.support;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

public final class Hashing {

    private Hashing() {}

    /**
     * SHA-256 over the request's semantic fields (not raw bytes), so whitespace or key order in the JSON
     * doesn't change the hash. Null fields hash as a NUL marker so null and "" differ.
     */
    public static String requestHash(String operation, Object... fields) {
        StringBuilder sb = new StringBuilder(operation);
        for (Object f : fields) {
            sb.append('\n').append(f == null ? "\u0000" : f.toString());
        }
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(sb.toString().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
