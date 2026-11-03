package dev.ledgerguard.ledger.transfer;

import dev.ledgerguard.ledger.idempotency.IdempotentExecutor.Outcome;
import dev.ledgerguard.ledger.read.ReadService;
import dev.ledgerguard.ledger.transfer.TransferService.DepositCommand;
import dev.ledgerguard.ledger.transfer.TransferService.TransferCommand;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferWithAsOf;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class TransferController {

    public static final String IDEMPOTENCY_HEADER = "Idempotency-Key";

    public record TransferRequest(@NotNull UUID fromAccountId, @NotNull UUID toAccountId, @NotNull Long amountMinor,
                                  @NotNull @Pattern(regexp = "^[A-Z]{3}$") String currency, String memo) {}

    public record DepositRequest(@NotNull UUID accountId, @NotNull Long amountMinor,
                                 @NotNull @Pattern(regexp = "^[A-Z]{3}$") String currency, String memo) {}

    private final TransferService service;
    private final ReadService reads;

    public TransferController(TransferService service, ReadService reads) {
        this.service = service;
        this.reads = reads;
    }

    @PostMapping("/v1/transfers")
    ResponseEntity<String> transfer(Authentication auth, @RequestHeader(IDEMPOTENCY_HEADER) String key,
                                    @Valid @RequestBody TransferRequest r) {
        return respond(service.transfer(auth.getName(), key,
                new TransferCommand(r.fromAccountId(), r.toAccountId(), r.amountMinor(), r.currency(), r.memo())));
    }

    @PostMapping("/v1/deposits")
    ResponseEntity<String> deposit(Authentication auth, @RequestHeader(IDEMPOTENCY_HEADER) String key,
                                   @Valid @RequestBody DepositRequest r) {
        return respond(service.deposit(auth.getName(), key,
                new DepositCommand(r.accountId(), r.amountMinor(), r.currency(), r.memo())));
    }

    @PostMapping("/v1/transfers/{id}/reverse")
    ResponseEntity<String> reverse(Authentication auth, @RequestHeader(IDEMPOTENCY_HEADER) String key,
                                   @PathVariable UUID id) {
        return respond(service.reverse(auth.getName(), key, id));
    }

    @GetMapping("/v1/transfers/{id}")
    TransferWithAsOf get(@PathVariable UUID id) {
        return reads.transfer(id);
    }

    static ResponseEntity<String> respond(Outcome o) {
        return ResponseEntity.status(o.status())
                .contentType(MediaType.APPLICATION_JSON)
                .header("Idempotent-Replayed", Boolean.toString(o.replayed()))
                .body(o.body());
    }
}
