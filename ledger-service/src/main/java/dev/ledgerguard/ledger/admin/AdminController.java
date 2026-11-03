package dev.ledgerguard.ledger.admin;

import dev.ledgerguard.ledger.admin.AdminService.StatusChange;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AdminController {

    public record StatusRequest(@NotBlank @Size(max = 500) String reason,
                                @NotBlank @Size(max = 100) String actor,
                                @NotBlank @Size(max = 128) String approvalRef) {}

    private final AdminService admin;
    private final ReconcileService reconcile;

    public AdminController(AdminService admin, ReconcileService reconcile) {
        this.admin = admin;
        this.reconcile = reconcile;
    }

    @PostMapping("/v1/admin/accounts/{id}/freeze")
    StatusChange freeze(@PathVariable UUID id, @Valid @RequestBody StatusRequest r) {
        return admin.freeze(id, r.reason(), r.actor(), r.approvalRef());
    }

    @PostMapping("/v1/admin/accounts/{id}/unfreeze")
    StatusChange unfreeze(@PathVariable UUID id, @Valid @RequestBody StatusRequest r) {
        return admin.unfreeze(id, r.reason(), r.actor(), r.approvalRef());
    }

    @GetMapping("/v1/admin/reconcile")
    ReconcileService.Report reconcile() {
        return reconcile.reconcile();
    }
}
