import { useEffect, useRef, useState } from "react";
import { connect, embedded, sendValue, setFrameHeight } from "./streamlit";
import Breadth from "./Breadth.jsx";
import Sectors from "./Sectors.jsx";
import { day } from "./ui.jsx";

const newNonce = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export default function App() {
  const [args, setArgs] = useState(null);
  const [pending, setPending] = useState({}); // kind -> nonce
  const rootRef = useRef(null);

  // inside Streamlit: listen for data; opened on its own (npm run dev): use the saved sample
  useEffect(() => {
    if (embedded) return connect(setArgs);
    if (import.meta.env.DEV) {
      import("./sample.json").then((m) => setArgs({ page: new URLSearchParams(location.search).get("page") || "breadth", ...m.default }));
    }
    return undefined;
  }, []);

  // keep the Streamlit frame exactly as tall as the page
  useEffect(() => {
    if (!embedded || !rootRef.current) return undefined;
    const report = () => rootRef.current && setFrameHeight(Math.ceil(rootRef.current.getBoundingClientRect().height) + 2);
    const ro = new ResizeObserver(report);
    ro.observe(rootRef.current);
    report();
    return () => ro.disconnect();
  }, [args?.page]);

  // a request is finished when its answer (same nonce) comes back
  useEffect(() => {
    if (!args) return;
    setPending((p) => {
      const next = { ...p };
      let changed = false;
      for (const k of Object.keys(p)) {
        if (args[k] && args[k].nonce === p[k]) {
          delete next[k];
          changed = true;
        }
      }
      return changed ? next : p;
    });
  }, [args]);

  const ask = (kind, payload = {}) => {
    const nonce = newNonce();
    if (kind !== "goto" && kind !== "refresh") setPending((p) => ({ ...p, [kind]: nonce }));
    if (embedded) sendValue({ nonce, kind, ...payload });
  };

  if (!args) return <div className="app" ref={rootRef}><p className="working">Loading the dashboard</p></div>;
  const page = args.page || "breadth";
  const asOf = page === "sectors" ? args.sectors?.asOf : args.breadth?.asOf;

  return (
    <div className="app" ref={rootRef}>
      <header className="masthead">
        <p className="wordmark">Abdullah <span>Dashboard</span></p>
        <nav className="tabs" aria-label="Pages">
          <button type="button" aria-current={page === "breadth" ? "page" : undefined} onClick={() => page !== "breadth" && ask("goto", { page: "breadth" })}>
            Market breadth
          </button>
          <button type="button" aria-current={page === "sectors" ? "page" : undefined} onClick={() => page !== "sectors" && ask("goto", { page: "sectors" })}>
            Sector rotation
          </button>
        </nav>
        <div className="masthead-right">
          {asOf && <span>Closing prices of {day(asOf)}</span>}
          <button type="button" className="btn small" onClick={() => ask("refresh")} title="Fetch fresh data from NSE and Yahoo instead of the saved copy">
            Refresh data
          </button>
        </div>
      </header>
      {page === "sectors" ? (
        <Sectors args={args} ask={ask} pending={pending} />
      ) : (
        <Breadth args={args} ask={ask} pending={pending} />
      )}
    </div>
  );
}
