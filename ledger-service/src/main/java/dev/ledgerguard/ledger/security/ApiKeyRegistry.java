package dev.ledgerguard.ledger.security;

import dev.ledgerguard.ledger.config.LedgerProperties;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import org.springframework.stereotype.Component;

/**
 * API keys parsed from {@code ledger.security.api-keys} ("name:ROLE:key,..."). The name becomes the
 * caller's client id, which scopes idempotency keys.
 */
@Component
public class ApiKeyRegistry {

    public enum Role { CLIENT, READONLY, ADMIN }

    public record Principal(String name, Role role) {}

    private record Entry(byte[] key, Principal principal) {}

    private static final int MIN_KEY_LENGTH = 16;

    private final List<Entry> entries;

    public ApiKeyRegistry(LedgerProperties props) {
        this.entries = parse(props.security().apiKeys());
    }

    static List<Entry> parse(String raw) {
        if (raw == null || raw.isBlank()) {
            throw new IllegalStateException("ledger.security.api-keys (LEDGER_API_KEYS) must be set");
        }
        List<Entry> out = new ArrayList<>();
        Set<String> names = new HashSet<>();
        Set<String> keys = new HashSet<>();
        for (String item : raw.split(",")) {
            String[] parts = item.trim().split(":", 3);
            if (parts.length != 3) {
                throw new IllegalStateException("api key entry must be name:ROLE:key");
            }
            String name = parts[0].trim();
            Role role = Role.valueOf(parts[1].trim());
            String key = parts[2].trim();
            if (name.isEmpty() || name.length() > 64) {
                throw new IllegalStateException("api key name must be 1-64 chars");
            }
            if (key.length() < MIN_KEY_LENGTH) {
                throw new IllegalStateException("api key for '" + name + "' is shorter than " + MIN_KEY_LENGTH + " chars");
            }
            if (!names.add(name) || !keys.add(key)) {
                throw new IllegalStateException("api key names and keys must be unique");
            }
            out.add(new Entry(key.getBytes(StandardCharsets.UTF_8), new Principal(name, role)));
        }
        return List.copyOf(out);
    }

    /** Constant-time comparison against every configured key. */
    public Optional<Principal> authenticate(String presented) {
        if (presented == null || presented.isEmpty()) {
            return Optional.empty();
        }
        byte[] candidate = presented.getBytes(StandardCharsets.UTF_8);
        Principal match = null;
        for (Entry e : entries) {
            if (MessageDigest.isEqual(e.key(), candidate)) {
                match = e.principal();
            }
        }
        return Optional.ofNullable(match);
    }
}
