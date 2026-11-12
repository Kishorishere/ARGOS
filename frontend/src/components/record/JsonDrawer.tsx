import { useState } from "react";

type Node = { key?: string; value: unknown; depth: number };

function collect(key: string | undefined, value: unknown, depth: number, out: Node[]) {
  out.push({ key, value, depth });
  if (value && typeof value === "object" && depth < 6) {
    const entries = Array.isArray(value)
      ? value.map((v, i) => [String(i), v] as const)
      : Object.entries(value as Record<string, unknown>);
    for (const [k, v] of entries) collect(k, v, depth + 1, out);
  }
}

function preview(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "";
  if (typeof v === "object") return Array.isArray(v) ? `Array(${v.length})` : `{${Object.keys(v).length}}`;
  if (typeof v === "string") return v.length > 80 ? `"${v.slice(0, 80)}…"` : `"${v}"`;
  return String(v);
}

export default function JsonDrawer({ data, label }: { data: unknown; label?: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const json = JSON.stringify(data, null, 2);
  const nodes: Node[] = [];
  if (open) collect(undefined, data, 0, nodes);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(json);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const download = () => {
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${label ?? "record"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="panel">
      <header className="flex items-center gap-3 border-b border-hairline px-3 py-2">
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-[10px] tracking-[0.2em] text-data uppercase hover:underline"
        >
          {open ? "▾ raw jsonl" : "▸ raw jsonl"}
        </button>
        <span className="text-[10px] text-faint tabular-nums">{(json.length / 1024).toFixed(1)} kb</span>
        <div className="ml-auto flex gap-2">
          <button onClick={copy} className="text-[10px] tracking-widest text-dim uppercase hover:text-ink">
            {copied ? "copied ✓" : "copy"}
          </button>
          <button onClick={download} className="text-[10px] tracking-widest text-dim uppercase hover:text-ink">
            download
          </button>
        </div>
      </header>
      {open && (
        <div className="max-h-[420px] overflow-auto p-2 text-[11px] leading-[1.45]">
          {nodes.map((n, i) => (
            <div key={i} className="flex gap-1 whitespace-pre" style={{ paddingLeft: n.depth * 14 }}>
              {n.key !== undefined && <span className="text-data">{n.key}:</span>}
              <span className="text-dim">{preview(n.value)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
