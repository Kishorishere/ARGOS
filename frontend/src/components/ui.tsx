import type { ReactNode } from "react";

export function Panel({
  title,
  right,
  children,
  className = "",
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-hairline px-3 py-2">
          <h3 className="label-micro text-dim">{title}</h3>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Micro({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`label-micro ${className}`}>{children}</span>;
}

export function Chip({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "bull" | "bear" | "amber" | "data" | "neutral";
}) {
  const tones = {
    bull: "text-bull border-bull/40 bg-bull/10",
    bear: "text-bear border-bear/40 bg-bear/10",
    amber: "text-amber border-amber/40 bg-amber/10",
    data: "text-data border-data/40 bg-data/10",
    neutral: "text-dim border-hairline-2 bg-panel-2",
  } as const;
  return (
    <span className={`inline-block border px-1.5 py-px text-[10px] tracking-wider uppercase ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function ConfidenceBar({ value, label }: { value: number; label?: string }) {
  const v = Math.max(0, Math.min(1, value));
  const tone = v >= 0.66 ? "bg-bull" : v >= 0.4 ? "bg-amber" : "bg-bear";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 bg-panel-2">
        <div
          className={`h-full ${tone} transition-[width] duration-500`}
          style={{ width: `${v * 100}%` }}
        />
      </div>
      <span className="w-9 text-right text-[11px] text-ink tabular-nums">
        {label ?? `${Math.round(v * 100)}`}
      </span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 py-6 text-center text-[11px] tracking-wider text-faint uppercase">
      {children}
    </div>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-hairline/60 py-1 last:border-0">
      <span className="label-micro shrink-0">{k}</span>
      <span className="truncate text-right text-[12px] text-ink tabular-nums">{v}</span>
    </div>
  );
}
