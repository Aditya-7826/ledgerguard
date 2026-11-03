import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount / dependency change, optional polling. Stale responses are ignored. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: number | null): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const run = useCallback((background: boolean) => {
    const mine = ++seq.current;
    if (!background) setLoading(true);
    fn().then(
      (d) => { if (mine === seq.current) { setData(d); setError(null); setLoading(false); } },
      (e: Error) => { if (mine === seq.current) { setError(e); setLoading(false); } },
    );
  }, deps); // caller-provided deps, like useEffect

  useEffect(() => {
    run(false);
    if (!pollMs) return;
    const t = setInterval(() => run(true), pollMs);
    return () => clearInterval(t);
  }, [run, pollMs]);

  return { data, error, loading, reload: () => run(false) };
}
