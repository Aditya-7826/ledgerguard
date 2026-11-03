import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorState, Loading } from "../components/States.tsx";
import { Button, Chip, Mono, RiskBar, StatusChip } from "../components/ui.tsx";
import { api, type CaseStatus } from "../lib/api.ts";
import { age, RULE_LABELS, shortId } from "../lib/format.ts";
import { useAsync } from "../lib/useAsync.ts";

const STATUS_FILTERS: { value: "" | CaseStatus; label: string }[] = [
  { value: "PENDING_REVIEW", label: "Needs review" },
  { value: "", label: "All" },
  { value: "INVESTIGATING", label: "Investigating" },
  { value: "OPEN", label: "Open" },
  { value: "ESCALATED", label: "Escalated" },
  { value: "AUTO_CLEARED", label: "Auto-cleared" },
  { value: "CLEARED", label: "Cleared" },
  { value: "FROZEN", label: "Frozen" },
];
const RISK_FILTERS = [0, 0.2, 0.5, 0.8];

function Rules({ rules }: { rules: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {rules.map((r) => <Chip key={r}>{RULE_LABELS[r] ?? r}</Chip>)}
    </div>
  );
}

export function CaseQueuePage() {
  const [status, setStatus] = useState<"" | CaseStatus>("PENDING_REVIEW");
  const [minRisk, setMinRisk] = useState(0);
  const navigate = useNavigate();
  const q = useAsync(() => api.cases({ status: status || undefined, minRisk }), [status, minRisk], 10_000);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Case queue</h1>
          <p className="mt-1 text-zinc-500 dark:text-zinc-400">Highest risk first. Refreshes every 10 seconds.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2">
            <span className="text-xs text-zinc-500">Status</span>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as "" | CaseStatus)}
              className="h-9 rounded-lg border border-zinc-300 bg-white px-2 dark:border-zinc-700 dark:bg-zinc-900"
            >
              {STATUS_FILTERS.map((f) => <option key={f.label} value={f.value}>{f.label}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-xs text-zinc-500">Min risk</span>
            <select
              value={minRisk}
              onChange={(e) => setMinRisk(Number(e.target.value))}
              className="h-9 rounded-lg border border-zinc-300 bg-white px-2 dark:border-zinc-700 dark:bg-zinc-900"
            >
              {RISK_FILTERS.map((r) => <option key={r} value={r}>{r === 0 ? "Any" : `≥ ${r * 100}%`}</option>)}
            </select>
          </label>
          <Button variant="ghost" onClick={q.reload}>Refresh</Button>
        </div>
      </div>

      {q.loading && !q.data ? (
        <Loading rows={6} label="Loading cases" />
      ) : q.error && !q.data ? (
        <ErrorState error={q.error} onRetry={q.reload} />
      ) : !q.data?.cases.length ? (
        <Empty title="No cases match these filters">
          New cases appear here when the detector flags an account. Run <Mono>make simulate</Mono> to generate traffic.
        </Empty>
      ) : (
        <>
          {/* ≥ md: table */}
          <div className="hidden overflow-hidden rounded-xl bg-white ring-1 ring-zinc-200 md:block dark:bg-zinc-900 dark:ring-zinc-800">
            <table className="w-full text-left">
              <thead className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Risk</th>
                  <th className="px-4 py-3 font-medium">Account</th>
                  <th className="px-4 py-3 font-medium">Rules hit</th>
                  <th className="px-4 py-3 text-right font-medium">Age</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {q.data.cases.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => navigate(`/cases/${c.id}`)}
                    className="cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
                  >
                    <td className="px-4 py-3"><StatusChip status={c.status} /></td>
                    <td className="px-4 py-3"><RiskBar value={c.risk_score} /></td>
                    <td className="px-4 py-3">
                      <Link to={`/cases/${c.id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                        <Mono title={c.account_id}>{shortId(c.account_id)}</Mono>
                      </Link>
                    </td>
                    <td className="px-4 py-3"><Rules rules={c.rules} /></td>
                    <td className="px-4 py-3 text-right tabular-nums text-zinc-500">{age(c.age_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* < md: cards */}
          <ul className="space-y-2 md:hidden">
            {q.data.cases.map((c) => (
              <li key={c.id}>
                <Link to={`/cases/${c.id}`} className="block rounded-xl bg-white p-4 ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-800">
                  <div className="flex items-center justify-between gap-2">
                    <StatusChip status={c.status} />
                    <span className="text-xs tabular-nums text-zinc-500">{age(c.age_seconds)} ago</span>
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <Mono>{shortId(c.account_id)}</Mono>
                    <RiskBar value={c.risk_score} />
                  </div>
                  <div className="mt-3"><Rules rules={c.rules} /></div>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
