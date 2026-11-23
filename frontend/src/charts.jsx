import { useRef } from "react";
import { STATE, POSTURE, Pill, day, monthOf, num, useTip, useWidth, rankColor, rankInk } from "./ui.jsx";

const ROWS = [
  ["n50", "Nifty 50"],
  ["mid", "Midcap 150"],
  ["sml", "Smallcap 250"],
];

/* month boundaries for an x axis of daily rows */
function monthTicks(dates) {
  const out = [];
  dates.forEach((d, i) => {
    if (i === 0 || d.slice(0, 7) !== dates[i - 1].slice(0, 7)) out.push([i, monthOf(d)]);
  });
  return out;
}

/* ---------- the 90-session ribbon: one row per index, plus the regime ---------- */
export function Ribbon({ rows }) {
  const tip = useTip();
  const w = useWidth(tip.box);
  const labelW = w < 520 ? 84 : 110;
  const n = rows.length;
  const cw = n ? Math.max((w - labelW) / n, 1) : 0;
  const rh = 22;
  const gap = 4;
  const ticks = monthTicks(rows.map((r) => r.date));
  const H = 4 * (rh + gap) + 10 + 18;
  const cell = (i, y, fill) => <rect key={i} x={labelW + i * cw} y={y} width={Math.max(cw - (cw > 5 ? 1 : 0), 1)} height={rh} fill={fill} />;
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - r.left - labelW) / cw);
    if (i < 0 || i >= n) return tip.hide();
    const d = rows[i];
    tip.show(e.clientX - r.left, 0, (
      <>
        <div className="tip-date">{day(d.date)} · score {d.score > 0 ? "+" : ""}{d.score}</div>
        {ROWS.map(([k, name]) => (
          <div key={k}>
            <Pill map={STATE} value={d[`${k}_state`]} /> {name}
          </div>
        ))}
        <div style={{ marginTop: 3 }}>
          <Pill map={POSTURE} value={d.posture} /> {d.regime}
        </div>
      </>
    ));
  };
  return (
    <div className="chart" ref={tip.box} onMouseLeave={tip.hide}>
      {w > 0 && (
        <svg viewBox={`0 0 ${w} ${H}`} height={H} onMouseMove={onMove} role="img" aria-label="Daily trend state of each index and the market regime">
          {ROWS.map(([k, name], j) => (
            <g key={k}>
              <text x={0} y={j * (rh + gap) + 15} style={{ fill: "var(--ink-2)", fontSize: 12 }}>{name}</text>
              {rows.map((d, i) => cell(i, j * (rh + gap), STATE[d[`${k}_state`]]?.hex || "#ddd"))}
            </g>
          ))}
          <text x={0} y={3 * (rh + gap) + 10 + 15} style={{ fill: "var(--ink)", fontSize: 12, fontWeight: 650 }}>Regime</text>
          {rows.map((d, i) => cell(i, 3 * (rh + gap) + 10, POSTURE[d.posture]?.hex || "#ddd"))}
          {ticks.map(([i, lab], t) => (
            <g key={t}>
              <line x1={labelW + i * cw} x2={labelW + i * cw} y1={0} y2={4 * (rh + gap) + 10} stroke="#fff" strokeWidth={1.5} />
              {(t === 0 || i * cw - ticks[t - 1][0] * cw > 34) && (
                <text x={labelW + i * cw + 2} y={H - 3}>{lab}</text>
              )}
            </g>
          ))}
        </svg>
      )}
      {tip.node}
    </div>
  );
}

/* ---------- line chart: close vs EMAs, or any set of series ---------- */
export function LineChart({ dates, series, height = 180, yFormat = (v) => num(v, 0), bands, refLines, markers, strip, extra }) {
  const tip = useTip();
  const w = useWidth(tip.box);
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  const bandVals = bands ? bands.flatMap((b) => [b.from, b.to]) : [];
  let lo = Math.min(...all, ...(bands ? [] : []));
  let hi = Math.max(...all);
  if (bands) { lo = Math.min(...bandVals); hi = Math.max(...bandVals); }
  const pad = (hi - lo) * 0.06 || 1;
  if (!bands) { lo -= pad; hi += pad; }
  const left = 4;
  const right = 46;
  const top = 6;
  const bottom = strip ? 30 : 20;
  const H = height;
  const iw = Math.max(w - left - right, 10);
  const ih = H - top - bottom;
  const x = (i) => left + (dates.length > 1 ? (i / (dates.length - 1)) * iw : iw / 2);
  const y = (v) => top + ih - ((v - lo) / (hi - lo)) * ih;
  const ticksY = niceTicks(lo, hi, 4);
  const mt = monthTicks(dates);
  const path = (vals) => vals.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(" L");
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left - left) / iw) * (dates.length - 1));
    if (i < 0 || i >= dates.length) return tip.hide();
    tip.show(x(i), 0, (
      <>
        <div className="tip-date">{day(dates[i])}</div>
        {series.map((s) => (
          <div key={s.name}>
            <i style={{ display: "inline-block", width: 10, height: 2, background: s.color, verticalAlign: 3, marginRight: 6 }} />
            {s.name} {s.values[i] == null ? "—" : yFormat(s.values[i])}
          </div>
        ))}
        {extra && extra(i)}
        {strip && <div style={{ marginTop: 3 }}><Pill map={STATE} value={strip[i]} /></div>}
      </>
    ));
  };
  const hoverX = tip.node ? null : null;
  return (
    <div className="chart" ref={tip.box} onMouseLeave={tip.hide}>
      {w > 0 && (
        <svg viewBox={`0 0 ${w} ${H}`} height={H} onMouseMove={onMove}>
          {bands && bands.map((b) => (
            <rect key={b.label} x={left} width={iw} y={y(b.to)} height={y(b.from) - y(b.to)} fill={b.color} />
          ))}
          {ticksY.map((t) => (
            <g key={t}>
              <line x1={left} x2={left + iw} y1={y(t)} y2={y(t)} stroke="var(--line-soft)" />
              <text x={left + iw + 6} y={y(t) + 4}>{yFormat(t)}</text>
            </g>
          ))}
          {refLines && refLines.map((r) => (
            <line key={r} x1={left} x2={left + iw} y1={y(r)} y2={y(r)} stroke="var(--ink-3)" strokeDasharray="3 3" />
          ))}
          {series.map((s) => (
            <path key={s.name} d={`M${path(s.values)}`} fill="none" stroke={s.color} strokeWidth={s.width || 1.6}
              strokeDasharray={s.dash} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {markers && markers.map((m) => (
            <circle key={m.i} cx={x(m.i)} cy={y(m.v)} r={5} fill="var(--s-sut)" stroke="#fff" strokeWidth={1.5} />
          ))}
          {strip && strip.map((s, i) => (
            <rect key={i} x={x(i) - iw / dates.length / 2} width={iw / dates.length + 0.6} y={top + ih + 4} height={6} fill={STATE[s]?.hex || "#ddd"} />
          ))}
          {mt.map(([i, lab], t) => (t === 0 || x(i) - x(mt[t - 1][0]) > 34) && (
            <text key={t} x={x(i)} y={H - 3}>{lab}</text>
          ))}
          {hoverX}
        </svg>
      )}
      {tip.node}
    </div>
  );
}

function niceTicks(lo, hi, n) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

/* ---------- breadth score bars ---------- */
export function ScoreBars({ rows, height = 170 }) {
  const tip = useTip();
  const w = useWidth(tip.box);
  const left = 4, right = 30, top = 6, bottom = 20;
  const iw = Math.max(w - left - right, 10);
  const ih = height - top - bottom;
  const n = rows.length;
  const bw = iw / Math.max(n, 1);
  const y = (v) => top + ih / 2 - (v / 6) * (ih / 2);
  const mt = monthTicks(rows.map((r) => r.date));
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - r.left - left) / bw);
    if (i < 0 || i >= n) return tip.hide();
    tip.show(left + i * bw, 0, (<><div className="tip-date">{day(rows[i].date)}</div>Score {rows[i].score > 0 ? "+" : ""}{rows[i].score} of ±6</>));
  };
  return (
    <div className="chart" ref={tip.box} onMouseLeave={tip.hide}>
      {w > 0 && (
        <svg viewBox={`0 0 ${w} ${height}`} height={height} onMouseMove={onMove}>
          {[-6, -3, 0, 3, 6].map((t) => (
            <g key={t}>
              <line x1={left} x2={left + iw} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--ink-3)" : "var(--line-soft)"} />
              <text x={left + iw + 6} y={y(t) + 4}>{t > 0 ? `+${t}` : t}</text>
            </g>
          ))}
          {rows.map((r, i) => (
            <rect key={i} x={left + i * bw + (bw > 4 ? 0.5 : 0)} width={Math.max(bw - (bw > 4 ? 1 : 0), 0.8)}
              y={Math.min(y(r.score), y(0))} height={Math.max(Math.abs(y(r.score) - y(0)), r.score === 0 ? 1 : 0)}
              fill={r.score > 0 ? "#2f8f55" : r.score < 0 ? "#c94a4d" : "#b8c2bc"} rx={bw > 6 ? 1.5 : 0} />
          ))}
          {mt.map(([i, lab], t) => (t === 0 || (i - mt[t - 1][0]) * bw > 34) && (
            <text key={t} x={left + i * bw} y={height - 3}>{lab}</text>
          ))}
        </svg>
      )}
      {tip.node}
    </div>
  );
}

/* ---------- small sparkline of close vs EMAs (index cards) ---------- */
export function Spark({ values, ema1, ema2, height = 46 }) {
  const box = useRef(null);
  const w = useWidth(box);
  const all = [...values, ...ema1, ...ema2].filter((v) => v != null);
  const lo = Math.min(...all), hi = Math.max(...all);
  const x = (i) => (values.length > 1 ? (i / (values.length - 1)) * w : 0);
  const y = (v) => 3 + (height - 6) * (1 - (v - lo) / (hi - lo || 1));
  const p = (vals) => vals.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(" L");
  return (
    <div className="chart" ref={box} aria-hidden="true">
      {w > 0 && (
        <svg viewBox={`0 0 ${w} ${height}`} height={height}>
          <path d={`M${p(ema2)}`} fill="none" stroke="#d99a2b" strokeWidth={1.2} strokeDasharray="4 3" />
          <path d={`M${p(ema1)}`} fill="none" stroke="#4a63c9" strokeWidth={1.2} />
          <path d={`M${p(values)}`} fill="none" stroke="var(--ink)" strokeWidth={1.8} strokeLinejoin="round" />
        </svg>
      )}
    </div>
  );
}

/* ---------- rank history heatmap: index x week ---------- */
export function RankHeat({ rows, weeks, n }) {
  const tip = useTip();
  const w = useWidth(tip.box);
  const labelW = w < 560 ? 150 : 250;
  const cols = weeks.length;
  const cw = Math.max((w - labelW) / Math.max(cols, 1), 8);
  const rh = 18;
  const H = 22 + rows.length * (rh + 2);
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const j = Math.floor((e.clientX - r.left - labelW) / cw);
    const i = Math.floor((e.clientY - r.top - 22) / (rh + 2));
    if (j < 0 || j >= cols || i < 0 || i >= rows.length) return tip.hide();
    const p = rows[i].trail[j];
    tip.show(e.clientX - r.left, e.clientY - r.top + 12, (<><div className="tip-date">Week of {day(weeks[j])}</div>{rows[i].name}: rank {p ?? "—"} of {n}</>));
  };
  return (
    <div className="chart" ref={tip.box} onMouseLeave={tip.hide}>
      {w > 0 && (
        <svg viewBox={`0 0 ${w} ${H}`} height={H} onMouseMove={onMove} role="img" aria-label="Weekly rank of every index">
          {weeks.map((d, j) => (j % Math.ceil(46 / cw) === 0 || j === cols - 1) && (
            <text key={j} x={labelW + j * cw + cw / 2} y={13} textAnchor="middle">{day(d, false)}</text>
          ))}
          {rows.map((r, i) => (
            <g key={r.name} transform={`translate(0, ${22 + i * (rh + 2)})`}>
              <text x={labelW - 8} y={13} textAnchor="end" style={{ fill: "var(--ink-2)", fontSize: 11.5 }}>
                {r.name.length > (labelW < 200 ? 22 : 40) ? `${r.name.slice(0, labelW < 200 ? 21 : 39)}…` : r.name}
              </text>
              {r.trail.map((p, j) => (
                <g key={j}>
                  <rect x={labelW + j * cw} width={cw - 2} height={rh} rx={2} fill={rankColor(p, n)} />
                  {cw > 22 && p != null && (
                    <text x={labelW + j * cw + (cw - 2) / 2} y={13} textAnchor="middle" style={{ fill: rankInk(p, n), fontSize: 10.5, fontWeight: 600 }}>{p}</text>
                  )}
                </g>
              ))}
            </g>
          ))}
        </svg>
      )}
      {tip.node}
    </div>
  );
}

/* ---------- bump chart: rank movement of chosen indices ---------- */
const BUMP = ["#2f3c8f", "#0e6b3a", "#c2410c", "#7c3aed", "#0f766e", "#b91c1c", "#a16207", "#475569"];
export function Bump({ rows, weeks, n, height = 300 }) {
  const tip = useTip();
  const w = useWidth(tip.box);
  const left = 30, right = 210, top = 12, bottom = 22;
  const iw = Math.max(w - left - right, 40);
  const ih = height - top - bottom;
  // zoom to the ranks the chosen indices actually occupy, so leaders are not squashed at the top
  const shown = rows.flatMap((r) => r.trail).filter((p) => p != null);
  const lo = 1;
  const hi = Math.min(n, Math.max(5, Math.ceil((Math.max(...shown, 1) + 1) / 5) * 5));
  const x = (j) => left + (weeks.length > 1 ? (j / (weeks.length - 1)) * iw : iw / 2);
  const y = (p) => top + ((p - lo) / Math.max(hi - lo, 1)) * ih;
  const ticks = [...new Set([1, ...niceTicks(1, hi, 4).filter((t) => t >= 1), hi])];
  // end labels: keep at least 14 px apart
  const ends = rows.map((r, k) => ({ k, p: r.trail[r.trail.length - 1], r }))
    .filter((e) => e.p != null).sort((a, b) => a.p - b.p);
  let prevY = -Infinity;
  ends.forEach((e) => { e.y = Math.max(y(e.p) + 4, prevY + 14); prevY = e.y; });
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const j = Math.round(((e.clientX - r.left - left) / iw) * (weeks.length - 1));
    if (j < 0 || j >= weeks.length) return tip.hide();
    tip.show(x(j), 0, (
      <>
        <div className="tip-date">Week of {day(weeks[j])}</div>
        {rows.map((r, k) => (
          <div key={r.name}><i style={{ display: "inline-block", width: 10, height: 2, background: BUMP[k % BUMP.length], verticalAlign: 3, marginRight: 6 }} />{r.name}: {r.trail[j] ?? "—"}</div>
        ))}
      </>
    ));
  };
  return (
    <div className="chart" ref={tip.box} onMouseLeave={tip.hide}>
      {w > 0 && (
        <svg viewBox={`0 0 ${w} ${height}`} height={height} onMouseMove={onMove}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={left + iw} y1={y(t)} y2={y(t)} stroke="var(--line-soft)" />
              <text x={left - 6} y={y(t) + 4} textAnchor="end">{t}</text>
            </g>
          ))}
          {weeks.map((d, j) => (j % Math.ceil(50 / (iw / Math.max(weeks.length - 1, 1))) === 0 || j === weeks.length - 1) && (
            <text key={j} x={x(j)} y={height - 4} textAnchor="middle">{day(d, false)}</text>
          ))}
          {rows.map((r, k) => {
            const segs = [];
            let cur = [];
            r.trail.forEach((p, j) => {
              if (p == null || p > hi) { if (cur.length) segs.push(cur); cur = []; }
              else cur.push(`${x(j).toFixed(1)},${y(p).toFixed(1)}`);
            });
            if (cur.length) segs.push(cur);
            return (
              <g key={r.name}>
                {segs.map((sg, i) => <path key={i} d={`M${sg.join(" L")}`} fill="none" stroke={BUMP[k % BUMP.length]} strokeWidth={2.2} strokeLinejoin="round" strokeLinecap="round" />)}
                {r.trail.map((p, j) => p != null && p <= hi && <circle key={j} cx={x(j)} cy={y(p)} r={2.6} fill={BUMP[k % BUMP.length]} />)}
              </g>
            );
          })}
          {ends.map((e) => (
            <text key={e.r.name} x={x(weeks.length - 1) + 10} y={e.y} style={{ fill: BUMP[e.k % BUMP.length], fontWeight: 650, fontSize: 11.5 }}>
              {e.p} {e.r.name.replace(/^Nifty /, "").length > 28 ? `${e.r.name.replace(/^Nifty /, "").slice(0, 27)}…` : e.r.name.replace(/^Nifty /, "")}
            </text>
          ))}
        </svg>
      )}
      {tip.node}
    </div>
  );
}

/* ---------- a stock's relative strength against Nifty 50, with its 12-month high (as in Abu Abdullah) ---------- */
export function RSChart({ trail, months }) {
  const tip = useTip();
  const w = useWidth(tip.box);
  const k = Math.min(months, trail.rs.length);
  const raw = trail.rs.slice(-k);
  const rawHigh = trail.high.slice(-k);
  const labels = trail.labels.slice(-k);
  const base = raw.find((v) => v != null) || 1;
  const rs = raw.map((v) => (v == null ? null : (v / base) * 100));
  const high = rawHigh.map((v) => (v == null ? null : (v / base) * 100));
  const isHigh = raw.map((v, i) => v != null && rawHigh[i] != null && v >= rawHigh[i] * 0.9995);
  const H = 230;
  const pad = { l: 44, r: 14, t: 12, b: 26 };
  const all = [...rs, ...high].filter((v) => v != null);
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = hi - lo || 1;
  const yMin = lo - span * 0.1, yMax = hi + span * 0.1;
  const iw = Math.max(w - pad.l - pad.r, 40);
  const x = (i) => pad.l + (k > 1 ? (i / (k - 1)) * iw : iw / 2);
  const y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin)) * (H - pad.t - pad.b);
  const line = rs.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(" L");
  // the 12-month high only changes at month-ends, so it is drawn as steps
  const steps = high.map((v, i) => (v == null ? "" : i === 0 ? `M${x(0).toFixed(1)},${y(v).toFixed(1)}` : `H${x(i).toFixed(1)} V${y(v).toFixed(1)}`)).join(" ");
  const ticks = niceTicks(yMin, yMax, 4);
  const every = k > 18 ? 3 : k > 9 ? 2 : 1;
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left - pad.l) / iw) * (k - 1));
    if (i < 0 || i >= k) return tip.hide();
    tip.show(x(i), 0, (
      <>
        <div className="tip-date">{labels[i]}</div>
        <div>Relative strength {rs[i] == null ? "—" : num(rs[i], 1)}</div>
        <div>Highest of the past 12 months {high[i] == null ? "—" : num(high[i], 1)}</div>
        <div>{rs[i] == null || high[i] == null ? "" : isHigh[i] ? "A new 12-month high" : `${num((rs[i] / high[i]) * 100, 0)}% of its 12-month high`}</div>
      </>
    ));
  };
  return (
    <div>
      <div className="legend" style={{ marginTop: 0, marginBottom: 6 }}>
        <span><i className="line-key" style={{ borderColor: "#0f6b62" }} />Relative strength, start = 100</span>
        <span><i className="line-key" style={{ borderColor: "#6b7a72", borderTopStyle: "dashed" }} />Highest of the past 12 months</span>
        <span><i style={{ background: "#e39a1c", borderRadius: "50%" }} />New 12-month high</span>
      </div>
      <div className="chart" ref={tip.box} onMouseLeave={tip.hide}>
        {w > 0 && (
          <svg viewBox={`0 0 ${w} ${H}`} height={H} onMouseMove={onMove} role="img" aria-label="Relative strength against Nifty 50 with its 12-month high">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={pad.l + iw} y1={y(t)} y2={y(t)} stroke="var(--line-soft)" />
                <text x={pad.l - 8} y={y(t) + 4} textAnchor="end">{Math.round(t)}</text>
              </g>
            ))}
            {labels.map((l, i) => (k - 1 - i) % every === 0 && (
              <text key={l} x={x(i)} y={H - 6} textAnchor={i === k - 1 ? "end" : i === 0 ? "start" : "middle"}>{l}</text>
            ))}
            <path d={steps} fill="none" stroke="#6b7a72" strokeWidth={1.5} strokeDasharray="5 4" />
            <path d={`M${line}`} fill="none" stroke="#0f6b62" strokeWidth={2.4} strokeLinejoin="round" strokeLinecap="round" />
            {rs.map((v, i) => v != null && isHigh[i] && (
              <circle key={i} cx={x(i)} cy={y(v)} r={4.5} fill="#e39a1c" stroke="#fff" strokeWidth={1.5} />
            ))}
          </svg>
        )}
        {tip.node}
      </div>
    </div>
  );
}
