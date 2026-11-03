import { Link, useParams } from "react-router-dom";
import { ErrorState, Loading } from "../components/States.tsx";
import { Card, Chip, Field, Mono } from "../components/ui.tsx";
import { api } from "../lib/api.ts";
import { dateTime, inr } from "../lib/format.ts";
import { useAsync } from "../lib/useAsync.ts";

export function TransferPage() {
  const { id = "" } = useParams();
  const q = useAsync(() => api.transfer(id), [id]);
  if (q.loading && !q.data) return <Loading rows={4} label="Loading transfer" />;
  if (q.error && !q.data) return <ErrorState error={q.error} onRetry={q.reload} />;
  const t = q.data!.transfer;
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold tracking-tight">Transfer</h1>
      <Card title={<Mono>{t.id}</Mono>} action={<Chip tone="muted">{t.type.toLowerCase()}</Chip>}>
        <dl className="grid gap-4 sm:grid-cols-2">
          <Field label="Amount"><span className="text-2xl font-semibold tabular-nums">{inr(t.amount_minor)}</span></Field>
          <Field label="When">{dateTime(t.created_at)}</Field>
          <Field label="From"><Link to={`/accounts/${t.from_account_id}`} className="text-indigo-600 hover:underline dark:text-indigo-400"><Mono>{t.from_account_id}</Mono></Link></Field>
          <Field label="To"><Link to={`/accounts/${t.to_account_id}`} className="text-indigo-600 hover:underline dark:text-indigo-400"><Mono>{t.to_account_id}</Mono></Link></Field>
          {/* Memo is customer-supplied: plain text only. */}
          <Field label="Memo">{t.memo ?? <span className="text-zinc-400">none</span>}</Field>
          {t.reverses_transfer_id && (
            <Field label="Reverses"><Link to={`/transfers/${t.reverses_transfer_id}`} className="hover:underline"><Mono>{t.reverses_transfer_id}</Mono></Link></Field>
          )}
        </dl>
        {t.entries && (
          <table className="mt-6 w-full text-left">
            <thead className="text-xs text-zinc-500"><tr><th className="py-2 font-medium">Entry</th><th className="py-2 font-medium">Account</th><th className="py-2 text-right font-medium">Amount</th></tr></thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {t.entries.map((e) => (
                <tr key={e.id}>
                  <td className="py-2">{e.direction.toLowerCase()}</td>
                  <td className="py-2"><Mono>{e.account_id}</Mono></td>
                  <td className="py-2 text-right tabular-nums">{e.direction === "DEBIT" ? "−" : "+"}{inr(e.amount_minor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
