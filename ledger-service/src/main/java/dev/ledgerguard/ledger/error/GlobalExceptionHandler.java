package dev.ledgerguard.ledger.error;

import dev.ledgerguard.ledger.support.TransientRetry;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.lang.Nullable;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

/**
 * Maps every failure to RFC 7807 problem+json. Messages from frameworks (Jackson, JDBC) are never
 * forwarded because they can contain class names, SQL, or values; stack traces are never returned.
 */
@RestControllerAdvice
public class GlobalExceptionHandler extends ResponseEntityExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ProblemDetail> handleApi(ApiException e) {
        return ResponseEntity.status(e.status()).body(Problems.of(e.status(), e.code(), e.getMessage()));
    }

    @ExceptionHandler(DataAccessException.class)
    ResponseEntity<ProblemDetail> handleDataAccess(DataAccessException e) {
        if (TransientRetry.isTransient(e)) {
            log.warn("transient database failure after retries: {}", e.getClass().getSimpleName());
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Problems.of(HttpStatus.SERVICE_UNAVAILABLE, "retry_later",
                            "The ledger is busy. Retry the request with the same Idempotency-Key."));
        }
        log.error("unhandled database error", e);
        return internal();
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ProblemDetail> handleUnexpected(Exception e) {
        log.error("unhandled error", e);
        return internal();
    }

    @Override
    protected ResponseEntity<Object> handleHttpMessageNotReadable(
            HttpMessageNotReadableException ex, HttpHeaders headers, HttpStatusCode status, WebRequest request) {
        return ResponseEntity.badRequest().body(Problems.of(HttpStatus.BAD_REQUEST, "malformed_request",
                "Request body is missing, is not valid JSON, has an unknown field, or has a value of the wrong type or range."));
    }

    @Override
    protected ResponseEntity<Object> handleMethodArgumentNotValid(
            MethodArgumentNotValidException ex, HttpHeaders headers, HttpStatusCode status, WebRequest request) {
        List<String> errors = ex.getBindingResult().getFieldErrors().stream()
                .map(fe -> fe.getField() + ": " + fe.getDefaultMessage())
                .sorted()
                .toList();
        ProblemDetail pd = Problems.of(HttpStatus.UNPROCESSABLE_ENTITY, "validation_failed", "Request validation failed.");
        pd.setProperty("errors", errors);
        return ResponseEntity.unprocessableEntity().body(pd);
    }

    @Override
    protected ResponseEntity<Object> handleHandlerMethodValidationException(
            HandlerMethodValidationException ex, HttpHeaders headers, HttpStatusCode status, WebRequest request) {
        return ResponseEntity.unprocessableEntity()
                .body(Problems.of(HttpStatus.UNPROCESSABLE_ENTITY, "validation_failed", "Request validation failed."));
    }

    @Override
    protected ResponseEntity<Object> handleExceptionInternal(
            Exception ex, @Nullable Object body, HttpHeaders headers, HttpStatusCode statusCode, WebRequest request) {
        // Spring MVC's own errors (missing header, type mismatch, 404, 405...): keep status, normalise body.
        String code = switch (statusCode.value()) {
            case 400 -> "bad_request";
            case 404 -> "not_found";
            case 405 -> "method_not_allowed";
            case 406 -> "not_acceptable";
            case 415 -> "unsupported_media_type";
            default -> "error";
        };
        String detail = body instanceof ProblemDetail pd && pd.getDetail() != null ? pd.getDetail() : "Request failed.";
        return ResponseEntity.status(statusCode).headers(headers).body(Problems.of(statusCode, code, detail));
    }

    private static ResponseEntity<ProblemDetail> internal() {
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Problems.of(HttpStatus.INTERNAL_SERVER_ERROR, "internal_error", "An unexpected error occurred."));
    }
}
