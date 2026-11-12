import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { api } from "./lib/api";
import type { SystemStatus } from "./types/api";
import Dashboard from "./views/Dashboard";
import Ticker from "./views/Ticker";
import Analytics from "./views/Analytics";
import CostChip from "./components/CostChip";

/* Phase E — two top-level views plus a running cost meter in the header. */
const NAV = [
  { to: "/", label: "Dashboard", glyph: "▦", end: true },
  { to: "/analytics", label: "Analytics", glyph: "◫", end: true },
] as const;

function Footer({ sys }: { sys: SystemStatus | null }) {
  if (!sys) {
    return (
      <footer className="border-t border-hairline px-4 py-1.5 text-[10px] tracking-widest text-faint uppercase">
        connecting…
      </footer>
    );
  }
  const cache = sys.llm_cache;
  return (
    <footer className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-hairline px-4 py-1.5 text-[10px] tracking-widest text-dim uppercase">
      <span className="text-bull">● online</span>
      <span>
        market <span className="text-data">{sys.market}</span>
      </span>
      <span>
        cache{" "}
        <span className="text-data">
          {cache.enabled ? `${cache.total_rows ?? 0} rows` : "off"}
        </span>
      </span>
      <span>
        kronos{" "}
        <span className={sys.kronos.reachable ? "text-bull" : "text-amber"}>
          {sys.kronos.configured ? (sys.kronos.reachable ? "up" : "down") : "unset"}
        </span>
      </span>
      <span>
        debates <span className="text-data">{sys.max_debate_rounds}</span>
      </span>
      <span className="ml-auto text-faint">argos research desk · phase 0</span>
    </footer>
  );
}

export default function App() {
  const [sys, setSys] = useState<SystemStatus | null>(null);
  const [sysErr, setSysErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .system()
      .then((s) => alive && setSys(s))
      .catch((e: Error) => alive && setSysErr(e.message));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-void">
      <header className="flex items-center gap-6 border-b border-hairline bg-panel px-4 py-2">
        <div className="flex items-baseline gap-2">
          <span className="text-[15px] font-bold tracking-[0.3em] text-ink">ARGOS</span>
          <span className="text-[10px] tracking-[0.35em] text-faint uppercase">research desk</span>
        </div>
        <nav className="flex gap-1">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `border px-3 py-1 text-[11px] tracking-[0.15em] uppercase transition-colors ${
                  isActive
                    ? "border-bull/50 bg-bull/10 text-bull"
                    : "border-hairline text-dim hover:border-hairline-2 hover:text-ink"
                }`
              }
            >
              <span className="mr-1.5 opacity-60">{n.glyph}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        {sysErr && <span className="text-[10px] tracking-widest text-bear uppercase">api: {sysErr}</span>}
        <span className="ml-auto">
          <CostChip />
        </span>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/t/:ticker" element={<Ticker />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <Footer sys={sys} />
    </div>
  );
}
