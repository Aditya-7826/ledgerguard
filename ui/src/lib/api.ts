// Typed client for agent-service. The UI never calls the ledger directly.

export type CaseStatus =
  | "OPEN" | "INVESTIGATING" | "PENDING_REVIEW" | "ESCALATED" | "AUTO_CLEARED" | "CLEARED" | "FROZEN";
export type Verdict = "CLEAR" | "ESCALATE" | "RECOMMEND_FREEZE";
export type Decision = "FREEZE" | "CLEAR" | "ESCALATE";

export interface CaseSummary {
  id: string;
  account_id: string;
  status: CaseStatus;
  risk_score: number | null;
  flags: string[];
  opened_at: string;
  updated_at: string;
  age_seconds: number;
  rules: string[];
  verdict: Verdict | null;
}

export interface Evidence { claim: string; transfer_ids: string[] }

export interface Report {
  id: string;
  verdict: Verdict;
  risk_score: number;
  confidence: number;
  summary: string;
  evidence: Evidence[];
  red_flags: string[];
  injection_detected: boolean;
  low_quality: boolean;
  as_of: string;
  created_at: string;
}

export interface RuleHit {
  rule: string;
  hit_count: number;
  first_hit_at: string;
  last_hit_at: string;
  details: Record<string, unknown>;
}

export interface AgentRun {
  id: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  provider: string;
  model: string;
  started_at: string;
  latency_ms: number | null;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_micro_usd: number;
  error: string | null;
}

export interface DecisionRecord { id: string; decision: Decision; analyst: string; note: string; created_at: string }

export interface CaseDetail {
  id: string;
  account_id: string;
  status: CaseStatus;
  risk_score: number | null;
  flags: string[];
  opened_at: string;
  updated_at: string;
  last_activity_at: string;
  rule_hits: RuleHit[];
  report: Report | null;
  stale: boolean;
  new_transfers_since_report: number;
  latest_run: AgentRun | null;
  decisions: DecisionRecord[];
}

export interface Transfer {
  id: string;
  type: "TRANSFER" | "DEPOSIT" | "REVERSAL";
  from_account_id: string;
  to_account_id: string;
  amount_minor: number;
  currency: string;
  memo: string | null;
  reverses_transfer_id: string | null;
  created_at: string;
  entries?: { id: string; account_id: string; direction: "DEBIT" | "CREDIT"; amount_minor: number }[];
}

export interface AccountView {
  account: { id: string; owner_name: string; currency: string; status: string; is_system: boolean; created_at: string };
  balance_minor: number;
  currency: string;
  recent_transfers: Transfer[];
  has_more_transfers: boolean;
  cases: { id: string; status: CaseStatus; risk_score: number | null; opened_at: string }[];
  as_of: string;
}

export interface GraphNode { id: string; hop: number; is_root: boolean; is_system: boolean; has_open_case: boolean }
export interface GraphEdge { id: string; source: string; target: string; amount_minor: number; type: string; created_at: string }
export interface MoneyGraph { root: string; hops: number; truncated: boolean; as_of: string | null; nodes: GraphNode[]; edges: GraphEdge[] }

export interface DecisionResult { id: string; case_status: CaseStatus; replayed: boolean }

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the cases API. Is agent-api running?");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, body?.code ?? "error", body?.detail ?? `Request failed (HTTP ${res.status})`);
  }
  return body as T;
}

export const api = {
  cases: (q: { status?: string; minRisk?: number }) => {
    const p = new URLSearchParams();
    if (q.status) p.set("status", q.status);
    if (q.minRisk) p.set("min_risk", String(q.minRisk));
    return request<{ cases: CaseSummary[] }>(`/cases?${p}`);
  },
  case: (id: string) => request<CaseDetail>(`/cases/${id}`),
  graph: (id: string) => request<MoneyGraph>(`/cases/${id}/graph`),
  rerun: (id: string) => request<{ status: string }>(`/cases/${id}/rerun`, { method: "POST" }),
  decide: (id: string, body: { decision: Decision; analyst: string; note: string }, idempotencyKey: string) =>
    request<DecisionResult>(`/cases/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  account: (id: string) => request<AccountView>(`/accounts/${id}`),
  transfer: (id: string) => request<{ transfer: Transfer; as_of: string }>(`/transfers/${id}`),
};
