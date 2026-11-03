package dev.ledgerguard.ledger.account;

import dev.ledgerguard.ledger.error.ApiException;
import dev.ledgerguard.ledger.read.ReadService;
import dev.ledgerguard.ledger.read.ReadService.AccountView;
import dev.ledgerguard.ledger.read.ReadService.BalanceView;
import dev.ledgerguard.ledger.transfer.TransferViews.CounterpartyPage;
import dev.ledgerguard.ledger.transfer.TransferViews.TransferPage;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AccountController {

    public record CreateAccountRequest(@NotBlank @Size(max = 200) String ownerName,
                                       @NotNull @Pattern(regexp = "^[A-Z]{3}$") String currency) {}

    private final AccountRepository accounts;
    private final ReadService reads;

    public AccountController(AccountRepository accounts, ReadService reads) {
        this.accounts = accounts;
        this.reads = reads;
    }

    @PostMapping("/v1/accounts")
    @Transactional
    public ResponseEntity<AccountView> create(@Valid @RequestBody CreateAccountRequest r) {
        if (!accounts.currencySupported(r.currency())) {
            throw ApiException.unprocessable("unsupported_currency", "Currency " + r.currency() + " is not supported.");
        }
        Account a = accounts.insert(r.ownerName().strip(), r.currency());
        return ResponseEntity.status(HttpStatus.CREATED).body(new AccountView(a.id(), a.ownerName(), a.currency(),
                a.status().name(), a.system(), a.createdAt(), a.createdAt()));
    }

    @GetMapping("/v1/accounts/{id}")
    public AccountView get(@PathVariable UUID id) {
        return reads.account(id);
    }

    @GetMapping("/v1/accounts/{id}/balance")
    public BalanceView balance(@PathVariable UUID id, @RequestParam(name = "as_of", required = false) String asOf) {
        return reads.balance(id, asOf);
    }

    @GetMapping("/v1/accounts/{id}/transfers")
    public TransferPage transfers(@PathVariable UUID id,
                                  @RequestParam(required = false) String from,
                                  @RequestParam(required = false) String to,
                                  @RequestParam(required = false) String cursor,
                                  @RequestParam(required = false) Integer limit) {
        return reads.transfers(id, from, to, cursor, limit);
    }

    @GetMapping("/v1/accounts/{id}/counterparties")
    public CounterpartyPage counterparties(@PathVariable UUID id,
                                           @RequestParam(required = false) String from,
                                           @RequestParam(required = false) String to) {
        return reads.counterparties(id, from, to);
    }
}
