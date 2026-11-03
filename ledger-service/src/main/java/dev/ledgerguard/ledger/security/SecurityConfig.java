package dev.ledgerguard.ledger.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.ledgerguard.ledger.error.Problems;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.AnonymousAuthenticationFilter;

@Configuration
public class SecurityConfig {

    @Bean
    SecurityFilterChain filterChain(HttpSecurity http, ApiKeyRegistry registry, ObjectMapper mapper) throws Exception {
        http.csrf(c -> c.disable())
                .httpBasic(b -> b.disable())
                .formLogin(f -> f.disable())
                .logout(l -> l.disable())
                .requestCache(r -> r.disable())
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .addFilterBefore(new ApiKeyAuthFilter(registry), AnonymousAuthenticationFilter.class)
                .authorizeHttpRequests(a -> a
                        .requestMatchers("/actuator/health", "/actuator/health/**").permitAll()
                        .requestMatchers("/error").permitAll()
                        .requestMatchers("/v1/admin/**").hasRole("ADMIN")
                        .requestMatchers(HttpMethod.GET, "/v1/**").hasRole("READONLY")
                        .requestMatchers(HttpMethod.POST,
                                "/v1/accounts", "/v1/transfers", "/v1/deposits", "/v1/transfers/*/reverse")
                        .hasRole("CLIENT")
                        .anyRequest().denyAll())
                .exceptionHandling(e -> e
                        .authenticationEntryPoint((req, res, ex) -> write(res, mapper, HttpStatus.UNAUTHORIZED,
                                "unauthenticated", "A valid X-API-Key header is required."))
                        .accessDeniedHandler((req, res, ex) -> write(res, mapper, HttpStatus.FORBIDDEN,
                                "forbidden", "This API key's role is not allowed to call this endpoint.")));
        return http.build();
    }

    /** Disables Spring Boot's generated default user/password; API keys are the only credential. */
    @Bean
    UserDetailsService noUsers() {
        return new InMemoryUserDetailsManager();
    }

    private static void write(HttpServletResponse res, ObjectMapper mapper, HttpStatus status, String code, String detail)
            throws IOException {
        res.setStatus(status.value());
        res.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
        mapper.writeValue(res.getOutputStream(), Problems.of(status, code, detail));
    }
}
