import { useEffect, useMemo, useState } from "react";
import { CHECK, POSTURE, STATE, Field, Pill, Toggle, day, downloadCSV, num, pctPts, signed } from "./ui.jsx";
import { LineChart, Ribbon, ScoreBars, Spark } from "./charts.jsx";

const KEYS = [
  ["n50", "Nifty 50"],
  ["mid", "Nifty Midcap 150"],
  ["sml", "Nifty Smallcap 250"],
];
const WINDOWS = [30, 60, 90, 120, 150, 180];

export default function Breadth({ args, ask, pending }) {
  const d = args.breadth;
  const defaults = args.defaults?.breadth || { window: 90, dtMode: "latch", useBreadth: true };
  const [s, setS] = useState(d?.settings || defaults);
  const busy = Boolean(pending.breadth);

  // first visit in this session: ask for the data
  useEffect(() => {
    if (!d && !busy) ask("breadth", s);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const change = (patch) => {
    const next = { ...s, ...patch };
    setS(next);
    ask("breadth", next);
  };

  return (
    <>
      <div className="controls">
        <Field label="Show the last">
          <select className="select" value={s.window} onChange={(e) => change({ window: Number(e.target.value) })}>
            {WINDOWS.map((w) => <option key={w} value={w}>{w} days</option>)}
          </select>
        </Field>
        <Field label="A downtrend needs">
          <select className="select" value={s.dtMode} onChange={(e) => change({ dtMode: e.target.value })}>
            {Object.entries(args.defaults?.dtModes || { latch: "Fresh 3-month low within the last 10 sessions" }).map(([k, v]) => (
              <option key={k} value={k}>{v}{k === "latch" ? " (recommended)" : ""}</option>
            ))}
          </select>
        </Field>
        <Toggle checked={s.useBreadth} onChange={(v) => change({ useBreadth: v })}>Nifty 500 participation</Toggle>
        {busy && <span className="working">{d ? "Updating" : "Working out market breadth. The first load of the day can take a minute or two."}</span>}
      </div>
      {d && !d.ok && <p className="problem">{d.error}</p>}
      {d && d.ok && <BreadthBody d={d} />}
    </>
  );
}

function BreadthBody({ d }) {
  const rows = d.rows;
  const last = rows[rows.length - 1];
  const cfg = d.cfg;
  const F = cfg.ema_fast, S = cfg.ema_slow;
  const dates = rows.map((r) => r.date);
  const postureColor = POSTURE[last.posture] || POSTURE.Selective;
  const remarkParts = useMemo(() => last.remark.split(/(?<=\.)\s+(?=[A-Z])/).filter(Boolean), [last.remark]);
  const hasBreadth = last.breadth_pct != null;
  const hasCross = last.ema_n != null && last.ema_n > 0;
  const crossBack = rows.length > cfg.breadth_delta_n ? rows[rows.length - 1 - cfg.breadth_delta_n] : null;

  return (
    <>
      <section className="regime" aria-label="Market regime">
        <div>
          <p className="regime-date">Market regime after the close of {day(d.asOf)}</p>
          <h1 className="regime-word">{last.regime}.</h1>
          <div className="regime-bar" style={{ background: postureColor.bg }} />
          <div className="regime-facts">
            <div className="fact"><b><Pill map={POSTURE} value={last.posture} /></b><span>Posture</span></div>
            <div className="fact"><b>{last.streak}</b><span>sessions in this regime</span></div>
            <div className="fact"><b>{signed(last.score)}</b><span>breadth score (−6 to +6)</span></div>
            {hasBreadth && <div className="fact"><b>{num(last.breadth_pct, 0)}%</b><span>of Nifty 500 above {cfg.dma} DMA</span></div>}
            {hasCross && <div className="fact"><b>{num(last.ema_above, 0)}</b><span>stocks with {cfg.cross_fast} EMA above {cfg.cross_slow} EMA</span></div>}
          </div>
        </div>
        <div className="remark">
          {remarkParts.map((p, i) => <p key={i}>{p}</p>)}
        </div>
      </section>

      <section className="section" aria-label="Daily trend states">
        <div className="section-head">
          <h2 className="section-title">Trend state, session by session</h2>
          <span className="section-note">Hover a day for its details.</span>
        </div>
        <div className="panel">
          <Ribbon rows={rows} />
          <div className="legend">
            {Object.entries(STATE).map(([k, v]) => <span key={k}><i style={{ background: v.hex }} />{k} {v.name.toLowerCase()}</span>)}
          </div>
        </div>
      </section>

      <section className="section grid-4" aria-label="The three indices and participation">
        {KEYS.map(([k, name]) => {
          const st = last[`${k}_state`];
          const prev = d.prevClose[k];
          const chg = prev ? last[k] / prev - 1 : null;
          const tail = rows.slice(-60);
          return (
            <article key={k} className="panel index-card" style={{ borderTopColor: STATE[st].hex }}>
              <div className="card-head"><h3>{name}</h3><Pill map={STATE} value={st} title={STATE[st].name} /></div>
              <p className="big-num">{num(last[k], 2)}</p>
              {chg != null && <span className={`delta ${chg >= 0 ? "up" : "down"}`}>{pctPts(chg * 100, 2)} on the day</span>}
              <div style={{ marginTop: 8 }}>
                <Spark values={tail.map((r) => r[k])} ema1={tail.map((r) => r[`${k}_ema_f`])} ema2={tail.map((r) => r[`${k}_ema_s`])} />
              </div>
              <dl className="kv">
                <dt>vs {F} EMA</dt><dd>{pctPts(last[`${k}_vs_f`], 2)}</dd>
                <dt>vs {S} EMA</dt><dd>{pctPts(last[`${k}_vs_s`], 2)}</dd>
                <dt>{F} EMA vs {S} EMA</dt><dd>{pctPts(last[`${k}_fs`], 2)}</dd>
                <dt>Above 3-month low</dt><dd>{pctPts(last[`${k}_off_low`], 2)}</dd>
                <dt>Sessions in this state</dt><dd>{last[`${k}_days`]}</dd>
              </dl>
            </article>
          );
        })}
        <article className="panel index-card" style={{ borderTopColor: hasBreadth ? "var(--accent)" : "var(--line)" }}>
          <div className="card-head"><h3>Nifty 500 participation</h3>{hasBreadth && <Pill map={CHECK} value={last.breadth_check} />}</div>
          {hasBreadth ? (
            <>
              <p className="big-num">{num(last.breadth_pct, 1)}%</p>
              <span className="muted" style={{ fontSize: 13 }}>above their own {cfg.dma}-day average</span>
              <dl className="kv" style={{ marginTop: 18 }}>
                <dt>Zone</dt><dd>{last.breadth_zone}</dd>
                <dt>Change in {cfg.breadth_delta_n} sessions</dt><dd>{last.breadth_delta == null ? "—" : `${signed(last.breadth_delta, 1)} pts`}</dd>
                <dt>Against the regime</dt><dd>{last.breadth_check}</dd>
                <dt>Stocks counted</dt><dd>{num(last.breadth_n, 0)}</dd>
                {hasCross && <><dt>{cfg.cross_fast} EMA above {cfg.cross_slow} EMA</dt><dd>{num(last.ema_above, 0)} of {num(last.ema_n, 0)}</dd></>}
              </dl>
            </>
          ) : (
            <p className="muted" style={{ marginTop: 10 }}>{d.breadthNote || "Switched off above."}</p>
          )}
        </article>
      </section>

      <section className="section grid-2">
        <div className="panel">
          <h3>Breadth score</h3>
          <p className="section-note" style={{ marginBottom: 8 }}>Sum of the three indices: S-UT +2, UT +1, TZ 0, DT −1, S-DT −2.</p>
          <ScoreBars rows={rows} />
        </div>
        <div className="panel">
          <h3>Share of Nifty 500 above its {cfg.dma}-day average</h3>
          <p className="section-note" style={{ marginBottom: 8 }}>Shaded by zone; a dot marks a breadth thrust.</p>
          {hasBreadth ? (
            <LineChart
              dates={dates} height={186} yFormat={(v) => `${num(v, 0)}%`}
              series={[{ name: "Above 50 DMA", values: rows.map((r) => r.breadth_pct), color: "var(--accent)", width: 2.2 }]}
              bands={[
                { label: "washed", from: 0, to: 20, color: "rgba(163,34,47,.10)" },
                { label: "weak", from: 20, to: 40, color: "rgba(236,122,104,.10)" },
                { label: "neutral", from: 40, to: 60, color: "rgba(235,192,63,.10)" },
                { label: "healthy", from: 60, to: 80, color: "rgba(93,190,126,.12)" },
                { label: "hot", from: 80, to: 100, color: "rgba(14,107,58,.12)" },
              ]}
              markers={rows.map((r, i) => (r.thrust ? { i, v: r.breadth_pct } : null)).filter(Boolean)}
            />
          ) : <p className="muted">{d.breadthNote || "Switched off above."}</p>}
        </div>
      </section>

      {hasCross && (
        <section className="section">
          <div className="panel">
            <div className="section-head" style={{ marginBottom: 4 }}>
              <h3>Nifty 500 stocks with their {cfg.cross_fast}-day EMA above their {cfg.cross_slow}-day EMA</h3>
              <span className="section-note">
                <b style={{ color: "var(--ink)" }}>{num(last.ema_above, 0)} of {num(last.ema_n, 0)}</b> ({num(last.ema_pct, 1)}%) on {day(d.asOf)}
                {crossBack?.ema_above != null && `, ${signed(last.ema_above - crossBack.ema_above)} in ${cfg.breadth_delta_n} sessions`}
              </span>
            </div>
            <p className="section-note" style={{ marginBottom: 8 }}>
              The longer-term trend under the surface: a stock whose {cfg.cross_fast} EMA is above its {cfg.cross_slow} EMA is in a medium-term uptrend. The dashed line marks half the index.
            </p>
            <LineChart
              dates={dates} height={200} yFormat={(v) => `${num(v, 0)}%`}
              series={[{ name: `${cfg.cross_fast} EMA above ${cfg.cross_slow} EMA`, values: rows.map((r) => r.ema_pct), color: "#0e6b3a", width: 2.2 }]}
              bands={[{ label: "lower", from: 0, to: 50, color: "rgba(236,122,104,.07)" }, { label: "upper", from: 50, to: 100, color: "rgba(93,190,126,.08)" }]}
              refLines={[50]}
              extra={(i) => rows[i].ema_above == null ? null : <div>{num(rows[i].ema_above, 0)} of {num(rows[i].ema_n, 0)} stocks</div>}
            />
          </div>
        </section>
      )}

      <section className="section grid-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 380px), 1fr))" }}>
        {KEYS.map(([k, name]) => (
          <div className="panel" key={k}>
            <h3>{name} against its averages</h3>
            <LineChart
              dates={dates} height={200}
              series={[
                { name: "Close", values: rows.map((r) => r[k]), color: "var(--ink)", width: 2 },
                { name: `${F} EMA`, values: rows.map((r) => r[`${k}_ema_f`]), color: "#4a63c9", width: 1.4 },
                { name: `${S} EMA`, values: rows.map((r) => r[`${k}_ema_s`]), color: "#d99a2b", width: 1.4, dash: "5 4" },
              ]}
              strip={rows.map((r) => r[`${k}_state`])}
            />
            <div className="legend">
              <span><i className="line-key" style={{ borderColor: "var(--ink)" }} />Close</span>
              <span><i className="line-key" style={{ borderColor: "#4a63c9" }} />{F} EMA</span>
              <span><i className="line-key" style={{ borderColor: "#d99a2b", borderTopStyle: "dashed" }} />{S} EMA</span>
              <span>strip = daily state</span>
            </div>
          </div>
        ))}
      </section>

      <DailyLog d={d} />
      <Rules cfg={cfg} zones={d.zones} />
      <p className="footer">
        Index closes: {Object.entries(d.sources).map(([k, v]) => `${k} from ${v}`).join("; ")}.
        {d.settings.useBreadth && ` Participation: Yahoo Finance adjusted closes of the Nifty 500 members listed by NSE Indices. ${d.breadthNote}`}
        {" "}A day's closes are used from 5:00 pm IST. Updated {d.rendered}.
      </p>
    </>
  );
}

function DailyLog({ d }) {
  const rows = [...d.rows].reverse();
  const save = () =>
    downloadCSV(`market_breadth_${d.asOf}.csv`,
      ["Date", "Nifty 50", "Midcap 150", "Smallcap 250", "Score", "Score change", "% above 50 DMA", "Breadth check",
        "Stocks with 50 EMA above 200 EMA", "Stocks counted", "% with 50 EMA above 200 EMA", "Regime", "Posture", "Remark"],
      rows.map((r) => [r.date, r.n50_state, r.mid_state, r.sml_state, r.score, r.d_score, r.breadth_pct?.toFixed(1), r.breadth_check,
        r.ema_above, r.ema_n, r.ema_pct?.toFixed(1), r.regime, r.posture, r.remark]));
  return (
    <section className="section">
      <div className="section-head">
        <h2 className="section-title">Daily log</h2>
        <button type="button" className="btn small" onClick={save}>Download CSV</button>
      </div>
      <div className="table-wrap" style={{ maxHeight: 560 }}>
        <table className="data">
          <thead>
            <tr>
              <th className="sticky-col">Date</th><th>Nifty 50</th><th>Midcap 150</th><th>Smallcap 250</th>
              <th className="num">Score</th><th className="num">Change</th><th className="num">&gt; 50 DMA</th><th>Breadth</th><th className="num">50 EMA &gt; 200 EMA</th>
              <th>Regime</th><th>Posture</th><th>Remark</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.date}>
                <td className="sticky-col">{day(r.date)}</td>
                <td><Pill map={STATE} value={r.n50_state} /></td>
                <td><Pill map={STATE} value={r.mid_state} /></td>
                <td><Pill map={STATE} value={r.sml_state} /></td>
                <td className="num">{signed(r.score)}</td>
                <td className="num">{r.d_score == null ? "" : signed(r.d_score)}</td>
                <td className="num">{r.breadth_pct == null ? "" : `${num(r.breadth_pct, 1)}%`}</td>
                <td>{r.breadth_check !== "n/a" && <Pill map={CHECK} value={r.breadth_check} />}</td>
                <td className="num">{r.ema_above == null ? "" : `${num(r.ema_above, 0)} (${num(r.ema_pct, 0)}%)`}</td>
                <td>{r.regime}</td>
                <td><Pill map={POSTURE} value={r.posture} /></td>
                <td className="wrap">{r.remark}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Rules({ cfg, zones }) {
  const F = cfg.ema_fast, S = cfg.ema_slow;
  return (
    <details className="more">
      <summary>How the regime is decided</summary>
      <div className="body">
        <h4>Trend state of each index (daily closes)</h4>
        <ul>
          <li><b>S-UT</b>: close above the {F} EMA and the {S} EMA, and the {F} EMA above the {S} EMA.</li>
          <li><b>UT</b>: close above the {S} EMA but not S-UT (a pullback to the {F} EMA, or the {F} EMA not yet above the {S} EMA).</li>
          <li><b>DT</b>: close at or below the {S} EMA and at a 3-month low (lowest close of {cfg.lookback} sessions; by default a fresh low within the last {cfg.latch} sessions).</li>
          <li><b>S-DT</b>: {cfg.sdt_needs_3m_low ? "DT, and also " : ""}close below both EMAs with the {F} EMA below the {S} EMA.</li>
          <li><b>TZ</b>: everything else.</li>
        </ul>
        <h4>Breadth score</h4>
        <p>S-UT +2, UT +1, TZ 0, DT −1, S-DT −2, added up for the three indices, each weighted equally.</p>
        <h4>Regime and posture (the first rule that fits)</h4>
        <ol>
          <li>All three in an uptrend: Broad uptrend (strong if all S-UT), Risk-on.</li>
          <li>All three in a downtrend: Broad downtrend (strong if all S-DT), Risk-off.</li>
          <li>At least one up and one down: Divergent, Cautious.</li>
          <li>Only Nifty 50 up: Narrow, large-caps only, Cautious.</li>
          <li>Nifty 50 and one broader index up: Breadth narrowing, Selective.</li>
          <li>Midcap and Smallcap up, Nifty 50 not: Broader market leading, Constructive.</li>
          <li>Only one of Midcap and Smallcap up: Mixed, Selective.</li>
          <li>None up and none down: Correction or no trend, Defensive.</li>
          <li>None up, one or two down: Weakening, Defensive.</li>
        </ol>
        <h4>Nifty 500 participation</h4>
        <p>
          Share of today's Nifty 500 members closing above their own {cfg.dma}-day simple average. Zones: {zones.map(([lo, lab]) => `${lab} from ${lo}%`).join(", ")}.
          In Risk-on and Constructive regimes {cfg.breadth_confirm_up}% or more confirms and under {cfg.breadth_diverge_up}% diverges; in Defensive and Risk-off regimes {cfg.breadth_confirm_dn}% or less confirms and {cfg.breadth_diverge_dn}% or more diverges.
          A breadth thrust is a move from below {cfg.thrust_from}% to above {cfg.thrust_to}% within {cfg.thrust_n} sessions. Participation is reported in the remark; it does not change the regime.
        </p>
        <h4>{cfg.cross_fast} EMA above {cfg.cross_slow} EMA</h4>
        <p>
          For each of today's Nifty 500 members: is its {cfg.cross_fast}-day EMA of daily closes above its {cfg.cross_slow}-day EMA?
          The count is out of the stocks that traded that day and have at least {cfg.cross_slow} sessions of history (newly listed stocks join once they do).
          Each EMA starts from the average of the stock's first closes, the same method as the index EMAs, on about two years of Yahoo Finance prices adjusted for splits and bonuses.
          It is shown for information and does not change the regime.
        </p>
        <h4>Remark</h4>
        <p>What the regime means, the strongest and weakest index against its {S} EMA, what that says about risk appetite, the day's events (EMA crosses, fresh 3-month lows), participation, and the score's change over {cfg.traj_n} sessions when it moves by {cfg.traj_thresh} or more.</p>
        <p className="faint" style={{ marginTop: 10 }}>The posture is the output of these rules, not investment advice.</p>
      </div>
    </details>
  );
}
