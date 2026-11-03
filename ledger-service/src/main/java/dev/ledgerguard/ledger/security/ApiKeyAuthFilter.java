package dev.ledgerguard.ledger.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

/** Reads {@code X-API-Key}. Unknown or missing keys leave the request anonymous (→ 401 later). */
public class ApiKeyAuthFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-API-Key";

    private final ApiKeyRegistry registry;

    public ApiKeyAuthFilter(ApiKeyRegistry registry) {
        this.registry = registry;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        registry.authenticate(request.getHeader(HEADER)).ifPresent(p -> {
            var auth = new UsernamePasswordAuthenticationToken(
                    p.name(), null, List.of(new SimpleGrantedAuthority("ROLE_" + p.role().name())));
            SecurityContextHolder.getContext().setAuthentication(auth);
        });
        chain.doFilter(request, response);
    }
}
