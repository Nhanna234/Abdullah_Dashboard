import { Fragment, useEffect, useMemo, useState } from "react";
import { Field, Seg, Toggle, day, downloadCSV, num, pct, rankColor, retStyle, useSort } from "./ui.jsx";
import { Bump, RankHeat, RSChart } from "./charts.jsx";

const GROUPS = [
  ["Sector", "Sectors"],
  ["Theme", "Themes"],
  ["Benchmark", "Benchmarks"],
];
const bold = (s) => s.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <b key={i}>{part}</b> : part));

export default function Sectors({ args, ask, pending }) {
  const d = args.sectors;
  const dflt = args.defaults?.sectors || { scope: "major", groups: ["Sector", "Theme", "Benchmark"], weeks: 12, basis: "composite", days3m: 90 };
  const [s, setS] = useState(d?.settings || dflt);
  const busy = Boolean(pending.sectors);
  const bases = args.defaults?.bases || { composite: "Rank(1,3,6), as in your sheet", r_3m: "3M return", away_low: "Away from 52W low" };
  const d3 = args.defaults?.days3m || { true: 90, sheet: 60 };
  const maxWeeks = args.defaults?.maxWeeks || 26;

  useEffect(() => {
    if (!d && !busy) ask("sectors", s);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const change = (patch) => {
    const next = { ...s, ...patch };
    if (!next.groups.length) return;
    setS(next);
    ask("sectors", next);
  };
  const toggleGroup = (g) => change({ groups: s.groups.includes(g) ? s.groups.filter((x) => x !== g) : [...s.groups, g] });

  return (
    <>
      <div className="controls">
        <Field label="Indices">
          <Seg label="Which indices" value={s.scope || "major"} onChange={(v) => change({ scope: v })}
            options={[["major", "Major"], ["all", "All NSE"]]} />
        </Field>
        <Field label="Include">
          <div className="chips" role="group" aria-label="Index groups">
            {GROUPS.map(([g, label]) => (
              <button key={g} type="button" className="chip toggleable" aria-pressed={s.groups.includes(g)} onClick={() => toggleGroup(g)}>
                {label}
              </button>
            ))}
          </div>
        </Field>
        <Field label="Rank by">
          <select className="select" value={s.basis} onChange={(e) => change({ basis: e.target.value })}>
            {Object.entries(bases).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <Field label="3M return looks back">
          <Seg label="3M look-back" value={s.days3m} onChange={(v) => change({ days3m: v })}
            options={[[d3.true, `${d3.true} days`], [d3.sheet, `${d3.sheet} days, as in the sheet`]]} />
        </Field>
        <Field label="Rank history">
          <select className="select" value={s.weeks} onChange={(e) => change({ weeks: Number(e.target.value) })}>
            {[4, 8, 12, 16, 20, maxWeeks].map((w) => <option key={w} value={w}>{w} weeks</option>)}
          </select>
        </Field>
        {busy && <span className="working">{d ? "Updating" : "Ranking every sector and thematic index. The first load can take about a minute."}</span>}
      </div>
      {d && !d.ok && <p className="problem">{d.error}</p>}
      {d && d.ok && <SectorBody d={d} args={args} ask={ask} pending={pending} />}
      {d && d.coverage && <Coverage c={d.coverage} />}
      {d && d.ok && <Method d={d} drill={args.drill} />}
    </>
  );
}

function SectorBody({ d, args, ask, pending }) {
  const rows = d.rows;
  const n = rows.length;
  const top = rows.slice(0, 5);
  const ddef = args.defaults?.drill || { topK: 3, noLenders: true, threshold: 100, history: 6, use_ema: true, cr: 75, mr: 5 };
  const lenderSet = useMemo(() => new Set(d.lenderIndices || []), [d.lenderIndices]);
  const drillable = rows.filter((r) => r.drillable && r.group !== "Benchmark");
  const defaultPicks = (noLenders) => drillable.filter((r) => !(noLenders && lenderSet.has(r.name))).slice(0, ddef.topK).map((r) => r.name);
  const [rsSet, setRsSet] = useState(args.drill?.settings ? { ...ddef, ...args.drill.settings } : ddef);
  const loaded = new Set(rows.map((r) => r.name));
  const [picks, setPicks] = useState((args.drill?.indices || []).filter((x) => loaded.has(x)).length
    ? args.drill.indices.filter((x) => loaded.has(x)) : drillable.map((r) => r.name));   // default: every index

  // first visit: screen the default indices once the ranking is in
  useEffect(() => {
    if (!args.drill && !pending.drill && picks.length) ask("drill", { indices: picks, ...rsSet });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const togglePick = (name) => setPicks((p) => (p.includes(name) ? p.filter((x) => x !== name) : [...p, name]));
  const runDrill = () => picks.length && ask("drill", { indices: picks, ...rsSet });

  return (
    <>
      <section className="leaders" aria-label="Leadership">
        <div>
          <p className="leaders-date">Strongest indices after the close of {day(d.asOf)}, out of {n}</p>
          <ol className="podium">
            {top.map((r) => {
              const mv = r.prevPos == null ? null : r.prevPos - r.pos;
              return (
                <li key={r.name}>
                  <span className="p-rank">{r.pos}</span>
                  <span>
                    <span className="p-name">{r.name}</span>
                    <span style={{ display: "block", marginTop: 4 }}><Trail trail={r.trail} n={n} /></span>
                  </span>
                  <span className="p-side">
                    <span className="ret" style={retStyle(r.r1m, 0.12)}>{pct(r.r1m)} in 1M</span>
                    <small>{mv == null ? "" : mv === 0 ? "same place as last week" : `${mv > 0 ? "up" : "down"} ${Math.abs(mv)} since last week`}</small>
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
        <div>
          <h2 className="section-title" style={{ marginBottom: 10 }}>This week's rotation</h2>
          <ul className="notes">{d.notes.map((t, i) => <li key={i}>{bold(t)}</li>)}</ul>
        </div>
      </section>

      <Leaderboard d={d} picks={picks} togglePick={togglePick} />

      <Drill args={args} ask={ask} pending={pending} picks={picks} togglePick={togglePick} setPicks={setPicks}
        rsSet={rsSet} setRsSet={setRsSet} runDrill={runDrill} drillable={drillable} lenderSet={lenderSet}
        resetPicks={() => setPicks(defaultPicks(rsSet.noLenders))}
        selectAll={() => setPicks(drillable.map((r) => r.name))} clearPicks={() => setPicks([])} />

      <History d={d} />
    </>
  );
}

function Trail({ trail, n, size }) {
  return (
    <span className="trail" title={`Rank over the last ${trail.length} weeks, oldest first: ${trail.map((p) => p ?? "—").join(", ")}`}>
      {trail.map((p, i) => <i key={i} style={{ background: rankColor(p, n), ...(size ? { width: size, height: size * 2 } : {}) }} />)}
    </span>
  );
}

function Leaderboard({ d, picks, togglePick }) {
  const n = d.rows.length;
  const [q, setQ] = useState("");
  const { apply, th } = useSort("pos", "asc");
  const get = (r, k) => (k === "move" ? (r.prevPos == null ? null : r.prevPos - r.pos) : r[k]);
  const shown = apply(d.rows.filter((r) => r.name.toLowerCase().includes(q.toLowerCase())), get);
  const save = () =>
    downloadCSV(`sector_ranking_${d.asOf}.csv`,
      ["Rank", "Index", "Type", "Rank last week", "Close", "52W high", "52W low", "1W", "1M", "3M", "6M ex 1M", "6M", "1Y", "Away from 52W low", "Off 52W high", "Rank(1,3,6)"],
      d.rows.map((r) => [r.pos, r.name, r.group, r.prevPos, r.close, r.high52, r.low52, r.r1w, r.r1m, r.r3m, r.r6mx1m, r.r6m, r.r1y, r.awayLow, r.offHigh, r.score]));
  const RET = [["r1w", "1W"], ["r1m", "1M"], ["r3m", "3M"], ["r6mx1m", "6M ex 1M"], ["r6m", "6M"], ["r1y", "1Y"]];
  return (
    <section className="section">
      <div className="section-head">
        <h2 className="section-title">All {n} indices, ranked</h2>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <input className="search" type="search" placeholder="Find an index" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find an index" />
          <button type="button" className="btn small" onClick={save}>Download CSV</button>
        </div>
      </div>
      <p className="section-note" style={{ marginBottom: 10 }}>
        Tick an index to screen its stocks below. The coloured strip is its rank over the last {d.weeks.length} weeks, oldest on the left; green is near the top.
      </p>
      <div className="table-wrap" style={{ maxHeight: 640 }}>
        <table className="data">
          <thead>
            <tr>
              <th aria-label="Screen its stocks" />
              {th("pos", "Rank", { firstDir: "asc" })}
              {th("move", "Move")}
              {th("name", "Index", { firstDir: "asc" })}
              <th>Rank trail</th>
              {RET.map(([k, l]) => th(k, l, { className: "num" }))}
              {th("offHigh", "vs 52W high", { className: "num" })}
              {th("awayLow", "vs 52W low", { className: "num" })}
              {th("score", "Score", { className: "num", firstDir: "asc" })}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const mv = r.prevPos == null ? null : r.prevPos - r.pos;
              return (
                <tr key={r.name}>
                  <td>
                    {r.drillable && r.group !== "Benchmark" && (
                      <input type="checkbox" className="pick" checked={picks.includes(r.name)} onChange={() => togglePick(r.name)} aria-label={`Screen ${r.name}`} />
                    )}
                  </td>
                  <td><span className="rank-num">{r.pos}</span></td>
                  <td>{mv == null ? "" : mv === 0 ? <span className="faint">—</span> : <span className={`mv ${mv > 0 ? "up" : "down"}`}>{mv > 0 ? "▲" : "▼"} {Math.abs(mv)}</span>}</td>
                  <td><b style={{ fontWeight: 600 }}>{r.name}</b> <span className="type-tag">{r.group === "Sector" ? "" : r.group.toLowerCase()}</span></td>
                  <td><Trail trail={r.trail} n={n} /></td>
                  {RET.map(([k]) => <td key={k} className="num"><span className="ret" style={retStyle(r[k], k === "r1y" ? 0.4 : k === "r1w" ? 0.05 : 0.18)}>{pct(r[k])}</span></td>)}
                  <td className="num">{pct(r.offHigh)}</td>
                  <td className="num">{pct(r.awayLow)}</td>
                  <td className="num">{num(r.score, 2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Drill({ args, ask, pending, picks, togglePick, setPicks, rsSet, setRsSet, runDrill, drillable, lenderSet, resetPicks, selectAll, clearPicks }) {
  const r = args.drill;
  const busy = Boolean(pending.drill);
  const [adding, setAdding] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [currentOnly, setCurrentOnly] = useState(true);   // default: only stocks at or above the threshold this month
  const [openRow, setOpenRow] = useState(null);
  const [chartMonths, setChartMonths] = useState(24);
  const lastLabel = r?.labels?.[r.labels.length - 1] || "the latest month";
  const stocks = r?.stocks ? (currentOnly ? r.stocks.filter((x) => x.cells[x.cells.length - 1]?.[1]) : r.stocks) : [];
  const chipsShown = showAll || picks.length <= 6 ? picks : picks.slice(0, 6);
  const nameList = (list) => (list.length > 4 ? `${list.length} indices` : list.join(", "));
  const set = (patch) => setRsSet((x) => ({ ...x, ...patch }));
  const changed = !r || JSON.stringify([...(r.indices || [])].sort()) !== JSON.stringify([...picks].sort()) ||
    ["threshold", "history", "use_ema", "cr", "mr", "noLenders"].some((k) => r.settings?.[k] !== rsSet[k]);
  const multi = (s) => s.indices.length > 1;
  const save = () =>
    downloadCSV(`strong_stocks_${r.lastMonth || ""}.csv`,
      ["Symbol", "Company", "Industry", "In strong indices", ...r.labels, "Run", "Close in range", "Month return", "vs 12M EMA", "Price"],
      stocks.map((s) => [s.symbol, s.company, s.industry, s.indices.join("; "), ...s.cells.map((c) => c[0]), s.run, s.cr, s.mr, s.emaGap, s.price]));
  return (
    <section className="section" aria-label="Strong stocks">
      <div className="section-head">
        <h2 className="section-title">Strong stocks inside the leading indices</h2>
        <span className="section-note">Screened with the Abu Abdullah RS rules: RS at or near a 12-month high against Nifty 50.</span>
      </div>
      <div className="panel">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
          <div className="chips">
            {chipsShown.map((p) => (
              <span key={p} className="chip">{p}{lenderSet.has(p) && rsSet.noLenders ? " (lenders only)" : ""}
                <button type="button" aria-label={`Remove ${p}`} onClick={() => togglePick(p)}>×</button>
              </span>
            ))}
            {picks.length > 6 && (
              <button type="button" className="chip toggleable" onClick={() => setShowAll((x) => !x)}>
                {showAll ? "Show fewer" : `+${picks.length - 6} more`}
              </button>
            )}
            {!picks.length && <span className="muted">No index chosen yet.</span>}
          </div>
          <select className="select" value={adding} aria-label="Add an index"
            onChange={(e) => { if (e.target.value) togglePick(e.target.value); setAdding(""); }}>
            <option value="">Add an index</option>
            {drillable.filter((x) => !picks.includes(x.name)).map((x) => <option key={x.name} value={x.name}>{x.pos}. {x.name}</option>)}
          </select>
          <button type="button" className="linkish" onClick={resetPicks}>Top-ranked</button>
          <button type="button" className="linkish" onClick={selectAll}>Select all {drillable.length}</button>
          {picks.length > 0 && <button type="button" className="linkish" onClick={clearPicks}>Clear</button>}
        </div>
        <div className="controls" style={{ margin: "14px 0 0" }}>
          <Field label="RS threshold"><input className="numin" type="number" min={50} max={100} value={rsSet.threshold} onChange={(e) => set({ threshold: Number(e.target.value) })} /></Field>
          <Field label="Months shown"><input className="numin" type="number" min={3} max={12} value={rsSet.history} onChange={(e) => set({ history: Number(e.target.value) })} /></Field>
          <Field label="Min close in range %"><input className="numin" type="number" min={0} max={100} step={5} value={rsSet.cr} onChange={(e) => set({ cr: Number(e.target.value) })} /></Field>
          <Field label="Min month return %"><input className="numin" type="number" min={-50} max={100} value={rsSet.mr} onChange={(e) => set({ mr: Number(e.target.value) })} /></Field>
          <Toggle checked={rsSet.use_ema} onChange={(v) => set({ use_ema: v })}>Above 12-month EMA</Toggle>
          <Toggle checked={rsSet.noLenders} onChange={(v) => set({ noLenders: v })}>Leave out lenders</Toggle>
          <button type="button" className="btn primary" disabled={busy || !picks.length || !changed} onClick={runDrill}>
            {busy ? "Screening…" : changed ? "Screen these indices" : "Screened"}
          </button>
        </div>
      </div>

      {busy && <p className="working">Screening the stocks of {picks.length} {picks.length === 1 ? "index" : "indices"}</p>}
      {r && !busy && (
        <div style={{ marginTop: 14 }}>
          {r.error && <p className="problem">{r.error}</p>}
          {r.errors?.length > 0 && <p className="notice">No stock list for: {r.errors.join("; ")}</p>}
          {r.ok && (
            <>
              <div className="section-head">
                <p>
                  <b>{r.stocks.length}</b> of {r.members} stocks in {nameList(r.indices)} pass
                  {r.excluded?.length ? ` (${r.excluded.length} lending companies left out)` : ""}.
                  {r.provisional && <span className="muted"> {r.labels[r.labels.length - 1]} is still running, so the last month is provisional.</span>}
                </p>
                {r.stocks.length > 0 && <button type="button" className="btn small" onClick={save}>Download CSV</button>}
              </div>
              {r.stocks.length > 0 && (
                <div style={{ margin: "-2px 0 10px" }}>
                  <Toggle checked={currentOnly} onChange={setCurrentOnly}>
                    Only stocks at or above the RS threshold in {lastLabel}
                    {currentOnly ? ` (${stocks.length} of ${r.stocks.length})` : ""}
                  </Toggle>
                </div>
              )}
              {stocks.length > 0 ? (
                <div className="table-wrap" style={{ maxHeight: 620 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th aria-label="Show chart" /><th className="sticky-col">Stock</th><th>Industry</th><th>In leading indices</th>
                        {r.labels.map((l) => <th key={l} className="num">{l}</th>)}
                        <th className="num">Run</th><th className="num">Close in range</th><th className="num">Month return</th><th className="num">vs 12M EMA</th><th className="num">Price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stocks.map((s) => (
                        <Fragment key={s.symbol}>
                        <tr className={openRow === s.symbol ? "is-open" : undefined}>
                          <td>
                            <button type="button" className="expander" aria-expanded={openRow === s.symbol}
                              aria-label={`${openRow === s.symbol ? "Hide" : "Show"} the RS chart of ${s.symbol}`}
                              onClick={() => setOpenRow((o) => (o === s.symbol ? null : s.symbol))}>
                              {openRow === s.symbol ? "▾" : "▸"}
                            </button>
                          </td>
                          <td className="sticky-col"><b>{s.symbol}</b><div className="faint" style={{ fontSize: 12, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>{s.company}</div></td>
                          <td>{s.industry}</td>
                          <td className={multi(s) ? "multi" : ""}>{s.indices.join(", ")}</td>
                          {s.cells.map((c, i) => (
                            <td key={i} className="num">
                              <span className={`rs ${c[2] ? "high" : c[1] ? "mark" : "no"}`}>{c[0] == null ? "—" : c[0] >= 100 ? "100" : Math.floor(c[0])}</span>
                            </td>
                          ))}
                          <td className="num">{s.run}</td>
                          <td className="num">{num(s.cr * 100, 0)}%</td>
                          <td className="num">{pct(s.mr)}</td>
                          <td className="num">{pct(s.emaGap)}</td>
                          <td className="num">{num(s.price, 2)}</td>
                        </tr>
                        {openRow === s.symbol && (
                          <tr className="detail-row">
                            <td colSpan={9 + r.labels.length}>
                              <div className="rs-detail">
                                <div className="rs-detail-head">
                                  <b>{s.symbol}</b> <span className="muted">relative strength against Nifty 50, month-end closes</span>
                                  <Seg label="Months shown in the chart" value={chartMonths} onChange={setChartMonths}
                                    options={[[6, "6 months"], [12, "12 months"], [24, "24 months"]]} />
                                </div>
                                {s.trail?.rs?.length > 1 ? <RSChart trail={s.trail} months={chartMonths} /> : <p className="muted">Not enough monthly history for a chart.</p>}
                                <p className="section-note" style={{ marginTop: 6 }}>
                                  RS = the stock's month-end close divided by Nifty 50's, rescaled so the first month shown is 100.
                                  The dashed steps are its highest RS of the 12 months up to each month; a dot is a new 12-month high (the
                                  dark 100 cells in the table). {r.provisional ? `${r.labels[r.labels.length - 1]} is still running.` : ""}{" "}
                                  <a href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`NSE:${s.symbol}`)}`} target="_blank" rel="noopener noreferrer">Open the price chart on TradingView</a>
                                </p>
                              </div>
                            </td>
                          </tr>
                        )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted">{r.stocks.length > 0
                  ? `None of these stocks is at or above the RS threshold in ${lastLabel}. Switch the filter off to see all ${r.stocks.length}.`
                  : "No stock in these indices meets the RS rules right now. Try more indices, or a lower RS threshold."}</p>
              )}
              <p className="section-note" style={{ marginTop: 8 }}>
                Month cells show RS as a percentage of its 12-month high. Dark: a new 12-month high. Light: at or above the threshold ({r.settings.threshold}%). Run: marked months in a row up to the latest. Bold: the stock is in more than one leading index.
              </p>
              {r.excluded?.length > 0 && (
                <details className="more" style={{ marginTop: 14 }}>
                  <summary>Lending companies left out ({r.excluded.length})</summary>
                  <div className="body">
                    <ul>{r.excluded.map((x) => <li key={x.symbol}><b>{x.symbol}</b> {x.name}: {x.label} <span className="faint">({x.source})</span></li>)}</ul>
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function History({ d }) {
  const n = d.rows.length;
  const [chosen, setChosen] = useState(d.rows.slice(0, 5).map((r) => r.name));
  const [adding, setAdding] = useState("");
  const bumpRows = chosen.map((name) => d.rows.find((r) => r.name === name)).filter(Boolean);
  return (
    <section className="section">
      <div className="section-head">
        <h2 className="section-title">Rank history</h2>
        <span className="section-note">The last trading day of each week; the newest column is provisional until Friday.</span>
      </div>
      <div className="panel">
        <h3 style={{ marginBottom: 10 }}>How the leaders moved</h3>
        <div className="chips" style={{ marginBottom: 6 }}>
          {chosen.map((c) => (
            <span key={c} className="chip">{c}<button type="button" aria-label={`Remove ${c}`} onClick={() => setChosen((x) => x.filter((y) => y !== c))}>×</button></span>
          ))}
          <select className="select" value={adding} aria-label="Add an index to the chart"
            onChange={(e) => { if (e.target.value && chosen.length < 8) setChosen((x) => [...x, e.target.value]); setAdding(""); }}>
            <option value="">Add an index</option>
            {d.rows.filter((r) => !chosen.includes(r.name)).map((r) => <option key={r.name} value={r.name}>{r.pos}. {r.name}</option>)}
          </select>
        </div>
        {bumpRows.length > 0 && <Bump rows={bumpRows} weeks={d.weeks} n={n} />}
      </div>
      <div className="panel" style={{ marginTop: 14 }}>
        <h3 style={{ marginBottom: 10 }}>Every index, week by week</h3>
        <RankHeat rows={d.rows} weeks={d.weeks} n={n} />
      </div>
    </section>
  );
}

function Coverage({ c }) {
  const ok = c.status.filter((s) => s.source && s.source !== "FAILED" && s.source !== "not requested").length;
  return (
    <details className="more">
      <summary>Which indices are included, and where the data came from ({ok} of {c.status.length} loaded{ok < c.status.length ? "; the rest are left out" : ""})</summary>
      <div className="body" style={{ maxWidth: "none" }}>
        <p>{c.scope === "all" ? "Every NSE sectoral and thematic index" : "The major NSE sector and thematic indices (switch to All NSE above for the full list)"}, plus Nifty 50, 200 and 500 as benchmarks. An index that does not load is left out of the ranking and the screen; it appears only in the table below. Checked against niftyindices.com on {c.checked}; new NSE indices are added automatically ({c.discoveryNote}).</p>
        {c.newFound.length > 0 && <p style={{ marginTop: 6 }}><b>Added since then:</b> {c.newFound.map((x) => `${x.name} (${x.group.toLowerCase()})`).join(", ")}.</p>}
        <h4>Left out on purpose</h4>
        <ul>{c.excluded.map((x) => <li key={x.name}><b>{x.name}</b>: {x.why}</li>)}</ul>
        <h4>BSE indices</h4>
        <p>Every BSE sectoral and thematic index either matches an NSE index already here or mixes sectors already covered, except: {c.bse.map((x) => `${x.name}, ${x.why}`).join("; ")}.</p>
        <h4>Data status</h4>
        <div className="table-wrap" style={{ maxHeight: 420, marginTop: 6 }}>
          <table className="data">
            <thead><tr><th>Index</th><th>Group</th><th>Source</th><th>Name used</th><th className="num">Days</th><th>Last close</th><th>Note</th></tr></thead>
            <tbody>
              {c.status.map((s) => (
                <tr key={s.name}>
                  <td>{s.name}</td><td>{s.group}</td>
                  <td style={{ color: s.source === "FAILED" ? "var(--down)" : undefined, fontWeight: s.source === "FAILED" ? 650 : 400 }}>{s.source}</td>
                  <td>{s.api}</td><td className="num">{s.rows || ""}</td><td>{s.last ? day(s.last) : ""}</td>
                  <td className="wrap" style={{ minWidth: 200 }}>{s.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}

function Method({ d, drill }) {
  const days = d.cfg.days;
  return (
    <details className="more">
      <summary>How the numbers are worked out</summary>
      <div className="body">
        <h4>Ranking (your Google Sheet's method)</h4>
        <ul>
          <li>Returns: the latest close divided by the first close on or after the date N calendar days earlier, minus 1. N is {days["1w"]} for 1W, {days["1m"]} for 1M, {d.settings.days3m} for 3M, {days["6m"]} for 6M and {days["1y"]} for 1Y.</li>
          <li>6M ex 1M: the close a month ago against the close six months ago (your sheet subtracts the two percentages instead).</li>
          <li>52-week high and low: the highest daily high and lowest daily low of the last 365 days.</li>
          <li>Rank(1,3,6) = {d.cfg.w_1m} × rank of 1M + {d.cfg.w_3m} × rank of 3M + {d.cfg.w_6m} × rank of 6M, where rank 1 is the best return and ties share a rank. Lower is better; ties go to the better 1M return.</li>
          <li>Rank history repeats the same calculation as of the last trading day of each week.</li>
        </ul>
        <h4>Strong stocks (the Abu Abdullah screener, same defaults)</h4>
        <ul>
          <li>RS is the stock's month-end close divided by Nifty 50's. The RS ratio is this month's RS as a share of its highest month-end RS in the last 12 months; 100% is a new 12-month high.</li>
          <li>A stock is listed when, in the latest month, it closes at or above its 12-month EMA, its close sits in the top part of the month's range and the month's return clears the minimum, and its RS ratio reached the threshold in at least one of the months shown.</li>
          <li>Banks, NBFCs, housing finance companies and government lenders are left out by default, with the same rule as the Abu Abdullah app.</li>
          <li>Stock lists come from each index's page on niftyindices.com; prices from Yahoo Finance (monthly, adjusted for splits and dividends).</li>
        </ul>
        <p className="faint" style={{ marginTop: 10 }}>Rankings and screens are rule outputs, not investment advice. Updated {d.rendered}{drill?.lastMonth ? `; stock prices to ${day(drill.lastMonth)}` : ""}.</p>
      </div>
    </details>
  );
}
