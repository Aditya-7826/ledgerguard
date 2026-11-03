const grouping = new Intl.NumberFormat("en-IN");

/** Integer paise → "₹1,23,456.78". Integer math only; money is never a float in this project. */
export function inr(minor: number): string {
  const sign = minor < 0 ? "−" : "";
  const abs = Math.abs(Math.trunc(minor));
  const rupees = Math.floor(abs / 100);
  const paise = abs % 100;
  return `${sign}₹${grouping.format(rupees)}.${String(paise).padStart(2, "0")}`;
}

/** Compact label for graph edges: ₹950, ₹9.5k, ₹1.2L, ₹3.4Cr (Indian units). */
export function inrCompact(minor: number): string {
  const r = Math.floor(Math.abs(minor) / 100);
  if (r >= 1_00_00_000) return `₹${(r / 1_00_00_000).toFixed(1)}Cr`;
  if (r >= 1_00_000) return `₹${(r / 1_00_000).toFixed(1)}L`;
  if (r >= 1_000) return `₹${(r / 1_000).toFixed(1)}k`;
  return `₹${r}`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function age(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

export function since(iso: string): string {
  return age((Date.now() - new Date(iso).getTime()) / 1000);
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export function pct(x: number | null | undefined): string {
  return x == null ? "—" : `${Math.round(x * 100)}%`;
}

export const RULE_LABELS: Record<string, string> = {
  velocity: "Velocity",
  structuring: "Structuring",
  fan_in_new_account: "Fan-in to new account",
  rapid_fanout_after_receiving: "Rapid fan-out",
  round_trip: "Round trip",
};

export const isUuid = (s: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s.trim());
