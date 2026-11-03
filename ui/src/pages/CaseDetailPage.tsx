import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DecisionPanel } from "../components/DecisionPanel.tsx";
import { MoneyFlowGraph } from "../components/MoneyFlowGraph.tsx";
import { Empty, ErrorState, Loading } from "../components/States.tsx";
import { Button, Card, Chip, Field, Mono, RiskBar, StatusChip, VerdictChip } from "../components/ui.tsx";
import { api, type CaseDetail } from "../lib/api.ts";
import { dateTime, pct, RULE_LABELS, shortId, since } from "../lib/format.ts";
import { useAsync } from "../lib/useAsync.ts";

const BUSY = new Set(["OPEN", "INVESTIGATING"]);

function StaleBanner({ c, onRerun }: { c: CaseDetail; onRerun: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!c.stale || BUSY.has(c.status)) return null;
  const canRerun = c.status === "PENDING_REVIEW" || c.status === "ESCALATED";
  return (
    <div role="status" className="flex flex-col gap-3 rounded-xl bg-indigo-50 px-4 py-3 ring-1 ring-indigo-200 sm:flex-row sm:items-center sm:justify-between dark:bg-indigo-950 dark:ring-indigo-900">
      <p className="text-indigo-900 dark:text-indigo-100">
        <span className="font-medium">Report may be stale.</span>{" "}
        {c.new_transfers_since_report} new transfer{c.new_transfers_since_report === 1 ? "" : "s"} since the report
        was written.
        {error && <span className="ml-2 text-red-600 dark:text-red-400">{error}</span>}
      </p>
      {canRerun && (
        <Button
          variant="primary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try { await api.rerun(c.id); onRerun(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
          }}
        >
          {busy ? "Queuing…" : "Re-run agent"}
        </Button>
      )}
    </div>
  );
}

function ReportCard({ c }: { c: CaseDetail }) {
  const r = c.report;
  if (!r) {
    return (
      <Card title="Agent report">
        {BUSY.has(c.status) ? (
          <Loading rows={3} label="Agent is investigating" />
        ) : (
          <Empty title="No report">
            {c.flags.includes("agent_failed") ? "The agent failed on this case; it needs a human review." : "The agent hasn't written a report."}
          </Empty>
        )}
      </Card>
    );
  }
  return (
    <Card title="Agent report" action={<span className="text-xs text-zinc-500">as of {dateTime(r.as_of)}</span>}>
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <VerdictChip verdict={r.verdict} />
          {r.injection_detected && <Chip tone="accent" title="Text in the data tried to instruct the agent">Prompt injection detected</Chip>}
          {r.low_quality && <Chip tone="muted" title="Some cited transfer ids were not in the tool outputs">Low-quality report</Chip>}
        </div>
        <dl className="grid grid-cols-2 gap-4">
          <Field label="Risk"><RiskBar value={r.risk_score} /></Field>
          <Field label="Confidence"><RiskBar value={r.confidence} label="Confidence" /></Field>
        </dl>
        <p className="leading-relaxed">{r.summary}</p>
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Evidence</h3>
          {r.evidence.length === 0 ? (
            <p className="mt-2 text-zinc-500">No evidence cited.</p>
          ) : (
            <ul className="mt-2 space-y-3">
              {r.evidence.map((ev, i) => (
                <li key={i} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                  <p>{ev.claim}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {ev.transfer_ids.map((t) => (
                      <Link key={t} to={`/transfers/${t}`} className="text-indigo-600 hover:underline dark:text-indigo-400">
                        <Mono title={t}>{shortId(t)}</Mono>
                      </Link>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        {r.red_flags.length > 0 && (
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Red flags</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5">{r.red_flags.map((f, i) => <li key={i}>{f}</li>)}</ul>
          </div>
        )}
      </div>
    </Card>
  );
}

function Facts({ c }: { c: CaseDetail }) {
  const run = c.latest_run;
  return (
    <Card title="Case facts">
      <div className="space-y-4">
        <ul className="space-y-2">
          {c.rule_hits.map((h) => (
            <li key={h.rule} className="flex items-center justify-between gap-2">
              <Chip>{RULE_LABELS[h.rule] ?? h.rule}</Chip>
              <span className="text-xs text-zinc-500">fired {h.hit_count}× · last {since(h.last_hit_at)} ago</span>
            </li>
          ))}
        </ul>
        {run && (
          <dl className="grid grid-cols-2 gap-3 border-t border-zinc-200 pt-4 sm:grid-cols-4 dark:border-zinc-800">
            <Field label="Agent run">{run.status.toLowerCase()}</Field>
            <Field label="Tool calls">{run.tool_calls}</Field>
            <Field label="Tokens">{(run.input_tokens + run.output_tokens).toLocaleString()}</Field>
            <Field label="Latency">{run.latency_ms == null ? "—" : `${(run.latency_ms / 1000).toFixed(1)}s`}</Field>
          </dl>
        )}
        {run?.error && <p className="text-xs text-zinc-500">Agent error: {run.error}</p>}
        {c.decisions.length > 0 && (
          <div className="border-t border-zinc-200 pt-4 dark:border-zinc-800">
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Decisions</h3>
            <ul className="mt-2 space-y-2">
              {c.decisions.map((d) => (
                <li key={d.id}>
                  <Chip tone={d.decision === "FREEZE" ? "freeze" : d.decision === "CLEAR" ? "clear" : "neutral"}>{d.decision}</Chip>{" "}
                  <span className="text-zinc-500">by {d.analyst}, {since(d.created_at)} ago:</span> {d.note}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}

export function CaseDetailPage() {
  const { id = "" } = useParams();
  const q = useAsync(() => api.case(id), [id], 3_000);
  const busy = q.data ? BUSY.has(q.data.status) : false;
  const graph = useAsync(() => api.graph(id), [id, q.data?.report?.id]);

  if (q.loading && !q.data) return <Loading rows={6} label="Loading case" />;
  if (q.error && !q.data) return <ErrorState error={q.error} onRetry={q.reload} />;
  const c = q.data!;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs text-zinc-500"><Link to="/" className="hover:underline">Cases</Link> / {shortId(c.id)}</p>
          <h1 className="mt-1 flex flex-wrap items-center gap-3 text-xl font-semibold tracking-tight">
            <span>Account <Link to={`/accounts/${c.account_id}`} className="font-mono text-lg hover:underline">{shortId(c.account_id)}</Link></span>
            <StatusChip status={c.status} />
          </h1>
        </div>
        <p className="text-zinc-500">Opened {since(c.opened_at)} ago · risk {pct(c.risk_score)}</p>
      </div>

      {busy && (
        <div role="status" className="rounded-xl bg-zinc-100 px-4 py-3 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
          The agent is investigating this case. This page updates automatically.
        </div>
      )}
      <StaleBanner c={c} onRerun={q.reload} />

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-2">
          <ReportCard c={c} />
          <DecisionPanel c={c} onDecided={q.reload} />
          <Facts c={c} />
        </div>
        <div className="lg:col-span-3">
          <Card
            title="Money flow"
            action={graph.data?.truncated ? <span className="text-xs text-zinc-500">Truncated: showing the nearest accounts</span> : undefined}
          >
            {graph.loading && !graph.data ? (
              <div className="h-[420px] animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" role="status" aria-label="Loading graph" />
            ) : graph.error && !graph.data ? (
              <ErrorState error={graph.error} onRetry={graph.reload} />
            ) : !graph.data || graph.data.edges.length === 0 ? (
              <Empty title="No transfers in the last 30 days" />
            ) : (
              <>
                <MoneyFlowGraph graph={graph.data} />
                <p className="mt-3 text-xs text-zinc-500">
                  Filled node: flagged account. Outlined: other accounts with an open case. Dashed: funding. Edge width ∝ amount (log).
                  Click a node to open the account.
                </p>
              </>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
