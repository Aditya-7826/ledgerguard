import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Empty, ErrorState, Loading } from "../components/States.tsx";
import { Button, Card, Chip, Field, Mono, StatusChip } from "../components/ui.tsx";
import { api } from "../lib/api.ts";
import { dateTime, inr, isUuid, shortId } from "../lib/format.ts";
import { useAsync } from "../lib/useAsync.ts";

function Lookup({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!isUuid(value)) { setError("Enter a full account id (UUID)."); return; }
    setError(null);
    navigate(`/accounts/${value.trim().toLowerCase()}`);
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row" noValidate>
      <label className="sr-only" htmlFor="account-id">Account id</label>
      <input
        id="account-id"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Account id, e.g. 3f2b8c1e-…"
        className="h-9 w-full rounded-lg border border-zinc-300 bg-white px-3 font-mono text-xs sm:w-auto sm:flex-1 dark:border-zinc-700 dark:bg-zinc-900"
        aria-invalid={!!error}
      />
      <Button variant="primary" type="submit">Look up</Button>
      {error && <p role="alert" className="text-red-600 sm:self-center dark:text-red-400">{error}</p>}
    </form>
  );
}

function AccountDetail({ id }: { id: string }) {
  const q = useAsync(() => api.account(id), [id]);
  if (q.loading && !q.data) return <Loading rows={5} label="Loading account" />;
  if (q.error && !q.data) return <ErrorState error={q.error} onRetry={q.reload} />;
  const a = q.data!;
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card title="Account" className="lg:col-span-1">
        <dl className="space-y-4">
          <Field label="Balance"><span className="text-2xl font-semibold tabular-nums">{inr(a.balance_minor)}</span></Field>
          <Field label="Status">
            <Chip tone={a.account.status === "FROZEN" ? "freeze" : a.account.status === "ACTIVE" ? "neutral" : "muted"}>
              {a.account.status.toLowerCase()}
            </Chip>
          </Field>
          {/* Owner name is customer-supplied text: rendered as plain text, never as markup. */}
          <Field label="Owner">{a.account.owner_name}</Field>
          <Field label="Id"><Mono>{a.account.id}</Mono></Field>
          <Field label="Opened">{dateTime(a.account.created_at)}</Field>
          <Field label="As of">{dateTime(a.as_of)}</Field>
        </dl>
        {a.cases.length > 0 && (
          <div className="mt-6 border-t border-zinc-200 pt-4 dark:border-zinc-800">
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Cases</h3>
            <ul className="mt-2 space-y-2">
              {a.cases.map((c) => (
                <li key={c.id} className="flex items-center justify-between">
                  <Link to={`/cases/${c.id}`} className="text-indigo-600 hover:underline dark:text-indigo-400"><Mono>{shortId(c.id)}</Mono></Link>
                  <StatusChip status={c.status} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
      <Card title="Recent transfers" className="lg:col-span-2">
        {a.recent_transfers.length === 0 ? (
          <Empty title="No transfers in the last 30 days" />
        ) : (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {a.recent_transfers.map((t) => {
              const out = t.from_account_id === a.account.id;
              const other = out ? t.to_account_id : t.from_account_id;
              return (
                <li key={t.id} className="flex items-start justify-between gap-4 py-3">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2">
                      <span className="text-zinc-500">{out ? "To" : "From"}</span>
                      <Link to={`/accounts/${other}`} className="hover:underline"><Mono>{shortId(other)}</Mono></Link>
                      {t.type !== "TRANSFER" && <Chip tone="muted">{t.type.toLowerCase()}</Chip>}
                    </p>
                    {t.memo && <p className="mt-1 truncate text-xs text-zinc-500" title={t.memo}>{t.memo}</p>}
                    <Link to={`/transfers/${t.id}`} className="mt-1 block text-xs text-zinc-400 hover:underline">{dateTime(t.created_at)}</Link>
                  </div>
                  <span className={`shrink-0 tabular-nums font-medium ${out ? "" : "text-zinc-500"}`}>
                    {out ? "−" : "+"}{inr(t.amount_minor).replace("−", "")}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {a.has_more_transfers && <p className="mt-3 text-xs text-zinc-500">Showing the 25 most recent.</p>}
      </Card>
    </div>
  );
}

export function AccountPage() {
  const { id } = useParams();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Account lookup</h1>
        <p className="mt-1 text-zinc-500 dark:text-zinc-400">Balance, status and recent transfers, read from the ledger.</p>
      </div>
      <Lookup key={id} initial={id} />
      {id ? <AccountDetail id={id} /> : <Empty title="Enter an account id to look it up" />}
    </div>
  );
}
