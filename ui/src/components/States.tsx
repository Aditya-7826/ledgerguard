import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../lib/api.ts";
import { Button } from "./ui.tsx";

export function Loading({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-12 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-zinc-300 px-6 py-12 text-center dark:border-zinc-700">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-zinc-500 dark:text-zinc-400">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const notFound = error instanceof ApiError && error.status === 404;
  return (
    <div role="alert" className="rounded-xl border border-zinc-200 bg-white px-6 py-8 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="font-medium">{notFound ? "Not found" : "Something went wrong"}</p>
      <p className="mt-1 text-zinc-500 dark:text-zinc-400">{error.message}</p>
      {onRetry && !notFound && (
        <Button className="mt-4" onClick={onRetry}>Try again</Button>
      )}
    </div>
  );
}

export function NotFound() {
  return (
    <Empty title="Page not found">
      <Link to="/" className="text-indigo-600 hover:underline dark:text-indigo-400">Back to cases</Link>
    </Empty>
  );
}
