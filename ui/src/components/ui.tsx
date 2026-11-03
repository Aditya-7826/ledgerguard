import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { CaseStatus, Verdict } from "../lib/api.ts";

export function Card({ title, action, children, className = "" }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-xl bg-white ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-800 ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <h2 className="font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type Tone = "neutral" | "accent" | "clear" | "freeze" | "muted";
const TONES: Record<Tone, string> = {
  neutral: "bg-zinc-100 text-zinc-700 ring-zinc-200 dark:bg-zinc-800 dark:text-zinc-200 dark:ring-zinc-700",
  accent: "bg-indigo-50 text-indigo-700 ring-indigo-200 dark:bg-indigo-950 dark:text-indigo-300 dark:ring-indigo-900",
  clear: "bg-green-50 text-green-700 ring-green-200 dark:bg-green-950 dark:text-green-300 dark:ring-green-900",
  freeze: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-950 dark:text-red-300 dark:ring-red-900",
  muted: "bg-transparent text-zinc-500 ring-zinc-200 dark:text-zinc-400 dark:ring-zinc-800",
};

export function Chip({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATUS: Record<CaseStatus, { tone: Tone; label: string }> = {
  OPEN: { tone: "muted", label: "Open" },
  INVESTIGATING: { tone: "accent", label: "Investigating" },
  PENDING_REVIEW: { tone: "accent", label: "Needs review" },
  ESCALATED: { tone: "neutral", label: "Escalated" },
  AUTO_CLEARED: { tone: "clear", label: "Auto-cleared" },
  CLEARED: { tone: "clear", label: "Cleared" },
  FROZEN: { tone: "freeze", label: "Frozen" },
};

export function StatusChip({ status }: { status: CaseStatus }) {
  const s = STATUS[status];
  return <Chip tone={s.tone}>{s.label}</Chip>;
}

export function VerdictChip({ verdict }: { verdict: Verdict }) {
  if (verdict === "CLEAR") return <Chip tone="clear">Recommend clear</Chip>;
  if (verdict === "RECOMMEND_FREEZE") return <Chip tone="freeze">Recommend freeze</Chip>;
  return <Chip tone="neutral">Escalate</Chip>;
}

/** Risk 0–1 as a thin bar. Uses the accent colour; red/green are reserved for decisions. */
export function RiskBar({ value, label = "Risk" }: { value: number | null; label?: string }) {
  const pct = value == null ? 0 : Math.round(value * 100);
  return (
    <div className="flex items-center gap-2" aria-label={`${label} ${value == null ? "unknown" : `${pct}%`}`}>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
        <div className="h-full rounded-full bg-indigo-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 tabular-nums text-zinc-600 dark:text-zinc-300">{value == null ? "—" : `${pct}%`}</span>
    </div>
  );
}

type Variant = "primary" | "secondary" | "clear" | "freeze" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-indigo-600 text-white hover:bg-indigo-500",
  secondary: "bg-white text-zinc-900 ring-1 ring-inset ring-zinc-300 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-100 dark:ring-zinc-700 dark:hover:bg-zinc-800",
  clear: "bg-green-600 text-white hover:bg-green-500",
  freeze: "bg-red-600 text-white hover:bg-red-500",
  ghost: "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800",
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex h-9 items-center justify-center gap-2 rounded-lg px-4 font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${className}`}
    />
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return <span title={title} className="font-mono text-xs">{children}</span>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className="mt-0.5 font-medium">{children}</dd>
    </div>
  );
}
