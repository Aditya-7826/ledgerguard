package dev.ledgerguard.ledger.error;

import java.net.URI;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;

/** RFC 7807 problem+json bodies with a stable machine-readable {@code code}. */
public final class Problems {

    private Problems() {}

    public static ProblemDetail of(HttpStatusCode status, String code, String detail) {
        ProblemDetail pd = ProblemDetail.forStatusAndDetail(status, detail);
        pd.setType(URI.create("urn:ledgerguard:problem:" + code));
        HttpStatus resolved = HttpStatus.resolve(status.value());
        pd.setTitle(resolved != null ? resolved.getReasonPhrase() : "Error");
        pd.setProperty("code", code);
        return pd;
    }
}
