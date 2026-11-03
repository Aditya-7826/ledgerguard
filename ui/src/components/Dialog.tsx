import { useEffect, useRef, type ReactNode } from "react";

/** Accessible modal: focus moves in, Esc closes, background is inert to the pointer. */
export function Dialog({ open, title, onClose, children, footer }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; footer: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-zinc-950/40 p-4" onMouseDown={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        className="w-full max-w-md rounded-xl bg-white ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-800"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="p-6">
          <h2 id="dialog-title" className="text-base font-semibold">{title}</h2>
          <div className="mt-3 text-zinc-600 dark:text-zinc-300">{children}</div>
        </div>
        <div className="flex flex-col-reverse gap-2 border-t border-zinc-200 px-6 py-4 sm:flex-row sm:justify-end dark:border-zinc-800">
          {footer}
        </div>
      </div>
    </div>
  );
}
