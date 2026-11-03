import { useRef, useState } from "react";
import { api, ApiError, type CaseDetail, type Decision } from "../lib/api.ts";
import { inr, shortId } from "../lib/format.ts";
import { useAsync } from "../lib/useAsync.ts";
import { Dialog } from "./Dialog.tsx";
import { Button, Card, Mono } from "./ui.tsx";

const ANALYST_KEY = "ledgerguard.analyst";

/**
 * Clear / Escalate / Freeze with a required note.
 *
 * Double-submit safety, in two layers: buttons are disabled while a request is in flight, and every
 * attempt at the same (decision, note) reuses one Idempotency-Key, so even a retry after a timeout is
 * replayed by the server instead of creating a second decision or a second ledger freeze.
 */
export function DecisionPanel({ c, onDecided }: { c: CaseDetail; onDecided: () => void }) {
  const [note, setNote] = useState("");
  const [analyst, setAnalyst] = useState(() => {
    try { return localStorage.getItem(ANALYST_KEY) ?? ""; } catch { return ""; }
  });
  const [inFlight, setInFlight] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmFreeze, setConfirmFreeze] = useState(false);
  const intent = useRef<{ sig: string; key: string } | null>(null);
  const account = useAsync(() => api.account(c.account_id), [c.account_id]);

  const decidable = c.status === "PENDING_REVIEW" || c.status === "ESCALATED";
  const ready = note.trim().length > 0 && analyst.trim().length > 0 && !inFlight;

  async function submit(decision: Decision) {
    if (!ready) return;
    const body = { decision, analyst: analyst.trim(), note: note.trim() };
    const sig = JSON.stringify(body);
    if (intent.current?.sig !== sig) intent.current = { sig, key: crypto.randomUUID() };
    setInFlight(decision);
    setError(null);
    try {
      try { localStorage.setItem(ANALYST_KEY, body.analyst); } catch { /* storage unavailable */ }
      await api.decide(c.id, body, intent.current.key);
      intent.current = null;
      setConfirmFreeze(false);
      setNote("");
      onDecided();
    } catch (e) {
      // Keep the key: retrying the same decision is safe and will be de-duplicated server-side.
      setError(e instanceof ApiError ? e.message : "The decision could not be saved. Try again.");
    } finally {
      setInFlight(null);
    }
  }

  if (!decidable) {
    return (
      <Card title="Decision">
        <p className="text-zinc-500 dark:text-zinc-400">
          {c.status === "OPEN" || c.status === "INVESTIGATING"
            ? "The agent is still investigating. A decision opens once its report is ready."
            : "This case is closed."}
        </p>
      </Card>
    );
  }

  return (
    <Card title="Decision">
      <div className="space-y-4">
        <label className="block">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Analyst</span>
          <input
            value={analyst}
            onChange={(e) => setAnalyst(e.target.value)}
            maxLength={100}
            placeholder="you@bank.example"
            className="mt-1 h-9 w-full rounded-lg border border-zinc-300 bg-white px-3 dark:border-zinc-700 dark:bg-zinc-950"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Note (required)</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={1000}
            rows={3}
            placeholder="What did you check, and why this decision?"
            className="mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-950"
          />
        </label>
        {error && <p role="alert" className="text-red-600 dark:text-red-400">{error}</p>}
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button variant="clear" disabled={!ready} onClick={() => submit("CLEAR")}>
            {inFlight === "CLEAR" ? "Clearing…" : "Clear"}
          </Button>
          {c.status !== "ESCALATED" && (
            <Button disabled={!ready} onClick={() => submit("ESCALATE")}>
              {inFlight === "ESCALATE" ? "Escalating…" : "Escalate"}
            </Button>
          )}
          <Button variant="freeze" disabled={!ready} onClick={() => setConfirmFreeze(true)} className="sm:ml-auto">
            Freeze account…
          </Button>
        </div>
      </div>

      <Dialog
        open={confirmFreeze}
        title="Freeze this account?"
        onClose={() => !inFlight && setConfirmFreeze(false)}
        footer={
          <>
            <Button data-autofocus disabled={!!inFlight} onClick={() => setConfirmFreeze(false)}>Cancel</Button>
            <Button variant="freeze" disabled={!ready} onClick={() => submit("FREEZE")}>
              {inFlight === "FREEZE" ? "Freezing…" : "Freeze account"}
            </Button>
          </>
        }
      >
        <p>The ledger will reject every transfer into or out of this account until it is unfrozen.</p>
        <dl className="mt-4 grid grid-cols-2 gap-3 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950">
          <div>
            <dt className="text-xs text-zinc-500">Account</dt>
            <dd className="mt-0.5"><Mono title={c.account_id}>{shortId(c.account_id)}…</Mono></dd>
            {account.data && <dd className="truncate text-xs text-zinc-500">{account.data.account.owner_name}</dd>}
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Balance</dt>
            <dd className="mt-0.5 font-semibold tabular-nums">
              {account.loading ? "Loading…" : account.data ? inr(account.data.balance_minor) : "Unavailable"}
            </dd>
          </div>
        </dl>
        {error && <p role="alert" className="mt-3 text-red-600 dark:text-red-400">{error}</p>}
      </Dialog>
    </Card>
  );
}
