import { useEffect, useRef, useState } from "react";

/* ---------- colours that carry meaning ---------- */
export const STATE = {
  "S-UT": { bg: "var(--s-sut)", ink: "var(--s-sut-ink)", hex: "#0e6b3a", name: "Strong uptrend" },
  UT: { bg: "var(--s-ut)", ink: "var(--s-ut-ink)", hex: "#5dbe7e", name: "Uptrend" },
  TZ: { bg: "var(--s-tz)", ink: "var(--s-tz-ink)", hex: "#ebc03f", name: "Transition zone" },
  DT: { bg: "var(--s-dt)", ink: "var(--s-dt-ink)", hex: "#ec7a68", name: "Downtrend" },
  "S-DT": { bg: "var(--s-sdt)", ink: "var(--s-sdt-ink)", hex: "#a3222f", name: "Strong downtrend" },
};
export const POSTURE = {
  "Risk-on": STATE["S-UT"],
  Constructive: STATE.UT,
  Selective: STATE.TZ,
  Cautious: { bg: "var(--p-cautious)", ink: "var(--p-cautious-ink)", hex: "#f09a4a" },
  Defensive: STATE.DT,
  "Risk-off": STATE["S-DT"],
};
export const CHECK = {
  Confirms: { bg: "#d9efe1", ink: "#0b4d2a" },
  Diverges: { bg: "#f8dcd8", ink: "#7a1620" },
  Neutral: { bg: "#e9ece9", ink: "#3d4a43" },
  "n/a": { bg: "transparent", ink: "var(--ink-3)" },
};

/* rank position -> colour, green (1) to dark red (n) */
const RAMP = ["#0e6b3a", "#2f8f55", "#5dbe7e", "#a6d27a", "#ebc03f", "#f0a04e", "#ec7a68", "#c94a4d", "#a3222f"];
export function rankColor(pos, n) {
  if (pos == null) return "#e6ebe7";
  const x = n > 1 ? (pos - 1) / (n - 1) : 0;
  return RAMP[Math.min(RAMP.length - 1, Math.round(x * (RAMP.length - 1)))];
}
export function rankInk(pos, n) {
  const x = n > 1 ? (pos - 1) / (n - 1) : 0;
  return x < 0.2 || x > 0.85 ? "#fff" : "#13221b";
}

/* ---------- numbers ---------- */
const nf = (d) => new Intl.NumberFormat("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
export const num = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : nf(d).format(v));
export const pct = (v, d = 1) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf(d).format(Math.abs(v * 100))}%`);
export const pctPts = (v, d = 1) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf(d).format(Math.abs(v))}%`);
export const signed = (v, d = 0) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf(d).format(Math.abs(v))}`);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function day(iso, withYear = true) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}${withYear ? ` ${y}` : ""}`;
}
export const monthOf = (iso) => (iso ? `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(2, 4)}` : "");
export function retStyle(v, full = 0.15) {
  if (v == null) return {};
  const a = Math.min(Math.abs(v) / full, 1);
  return v >= 0
    ? { background: `rgba(93,190,126,${(0.14 + 0.5 * a).toFixed(2)})`, color: "#06311b" }
    : { background: `rgba(236,122,104,${(0.14 + 0.5 * a).toFixed(2)})`, color: "#4a0f08" };
}

/* ---------- small pieces ---------- */
export function Pill({ map, value, children, title }) {
  const c = map[value] || { bg: "var(--line-soft)", ink: "var(--ink-2)" };
  return (
    <span className="pill" style={{ background: c.bg, color: c.ink }} title={title}>
      {children ?? value}
    </span>
  );
}

export function Seg({ options, value, onChange, label }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function Toggle({ checked, onChange, children }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}</span>
    </label>
  );
}

export function useWidth(ref) {
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

/* tooltip that stays inside its chart */
export function useTip() {
  const box = useRef(null);
  const [tip, setTip] = useState(null);
  const show = (x, y, content) => {
    const w = box.current?.clientWidth || 0;
    setTip({ x: Math.min(Math.max(x + 14, 0), Math.max(w - 220, 0)), y, content });
  };
  const hide = () => setTip(null);
  const node = tip ? (
    <div className="tip" style={{ left: tip.x, top: Math.max(tip.y - 10, 0) }}>
      {tip.content}
    </div>
  ) : null;
  return { box, show, hide, node };
}

export function downloadCSV(name, header, rows) {
  const esc = (v) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [header, ...rows].map((r) => r.map(esc).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* sortable table state */
export function useSort(initialKey, initialDir = "asc") {
  const [sort, setSort] = useState({ key: initialKey, dir: initialDir });
  const toggle = (key, firstDir = "desc") =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: firstDir }));
  const apply = (rows, get) =>
    [...rows].sort((a, b) => {
      const va = get(a, sort.key);
      const vb = get(b, sort.key);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const c = typeof va === "string" ? va.localeCompare(vb) : va - vb;
      return sort.dir === "asc" ? c : -c;
    });
  const th = (key, label, opts = {}) => (
    <th
      key={key}
      className={opts.className}
      aria-sort={sort.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
    >
      <button type="button" onClick={() => toggle(key, opts.firstDir)}>
        {label}
      </button>
    </th>
  );
  return { sort, apply, th };
}
