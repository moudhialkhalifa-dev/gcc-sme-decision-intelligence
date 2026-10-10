import { useState } from "react";
import { TimeChart } from "./charts";
import { capOf, Caveats, Delta, Empty, ErrorState, fmtDate, fmtInt, fmtPct, Loading, money, Priority, Severity, ShareBars, SignedBars, Tabs, Tile, Unsupported, useApi } from "./lib";

type P = { ds: any; go: (page: string) => void };
const Head = ({ title, sub, right }: { title: string; sub: string; right?: any }) => (
  <div className="page-head"><div><h1>{title}</h1><div className="sub">{sub}</div></div>{right}</div>
);

/* ------------------------------------------------------------------ Overview */
export function Overview({ ds, go }: P) {
  const { data, error, loading } = useApi(`/datasets/${ds.id}/overview`);
  if (loading) return <Loading what="Loading the overview" />;
  if (error) return <ErrorState error={error} />;
  const cur = data.currency, c = data.comparison, k = c.current, g = c.growth;
  const weekly = data.weekly.map((w: any) => ({ date: w.date, revenue: w.net_revenue, note: w.days_gap ? `${w.days_gap} day(s) without data this week` : undefined }));
  return (
    <>
      <Head title="Overview" sub={`Last 28 complete days, ${fmtDate(c.current_period.start)} to ${fmtDate(c.current_period.end)}, compared with the 28 days before.`} />
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Tile hero label="Net revenue" value={money(cur, k.net_revenue, true)} delta={g.net_revenue} />
        <Tile label="Transactions" value={fmtInt(k.transactions)} delta={g.transactions} />
        <Tile label="Average transaction value" value={money(cur, k.avg_transaction_value)} delta={g.avg_transaction_value} />
        <Tile label="Active customers (with an ID)" value={fmtInt(k.active_customers)} delta={g.active_customers} />
      </div>
      {c.notes.map((n: string, i: number) => <div className="note" key={i}>{n}</div>)}
      <div className="panel">
        <h2>Weekly net revenue</h2>
        <TimeChart data={weekly} series={[{ key: "revenue", label: "Net revenue", color: "var(--series-1)" }]} fmt={(v) => money(cur, v)} yLabel={`${cur} per week (week ending on the date shown)`} height={260} />
      </div>
      <div className="grid g2">
        <div className="panel">
          <h2>Forecast</h2>
          {data.forecast ? (
            <>
              <div className="tile"><div className="label">Expected net revenue, next {data.forecast.horizon} days</div><div className="value">{money(cur, data.forecast.total, true)}</div></div>
              <p className="small">{fmtDate(data.forecast.from)} to {fmtDate(data.forecast.to)}, from {data.forecast.model}. In back-testing on unseen days its daily error (WMAPE) was {fmtPct(data.forecast.test_wmape, 0)}.</p>
              <button className="btn ghost" onClick={() => go("forecast")}>Open forecasting</button>
            </>
          ) : <p className="sub">No forecast horizon passed validation for this dataset. The forecasting page explains why.</p>}
        </div>
        <div className="panel">
          <h2>Unusual days in the last 60 days</h2>
          {!data.anomalies_available ? <p className="sub">Anomaly detection needs more history than this dataset has.</p> :
            data.anomalies.length === 0 ? <p className="sub">Nothing unusual was flagged.</p> : (
              <table><tbody>
                {data.anomalies.map((a: any, i: number) => (
                  <tr key={i}><td>{fmtDate(a.date)}</td><td>{a.metric_label}</td><td className="r">{a.deviation_pct >= 0 ? "+" : ""}{fmtPct(a.deviation_pct, 0)} vs expected</td><td><Severity level={a.severity} /></td></tr>
                ))}
              </tbody></table>
            )}
          {data.anomalies_available && <button className="btn ghost" style={{ marginTop: 12 }} onClick={() => go("anomalies")}>Open anomaly center</button>}
        </div>
      </div>
      <h2 style={{ marginTop: 8 }}>Actions to consider</h2>
      {data.recommendations.length === 0 ? <Empty title="No rule was triggered"><p>None of the recommendation rules found enough evidence in this period.</p></Empty> :
        data.recommendations.map((r: any) => <Rec key={r.id} r={r} compact />)}
      {data.recommendations.length > 0 && <button className="btn ghost" onClick={() => go("recommendations")}>See all recommendations</button>}
    </>
  );
}

/* ------------------------------------------------------------------ Forecast */
export function Forecast({ ds }: P) {
  const { data, error, loading } = useApi(`/datasets/${ds.id}/forecast`);
  const [h, setH] = useState<number | null>(null);
  if (loading) return <Loading what="Loading forecasts" />;
  if (error) return <ErrorState error={error} />;
  const cur = data.currency, hz = data.horizons || {};
  const enabled = [7, 30, 90].filter((x) => hz[x]?.enabled);
  const sel = h ?? enabled[enabled.length > 1 ? 1 : 0] ?? null;
  const tabs = <Tabs value={sel as number} onChange={setH} options={[7, 30, 90].map((x) => ({ value: x, label: `${x} days`, disabled: !hz[x]?.enabled, title: hz[x]?.reason }))} />;
  const disabledNotes = [7, 30, 90].filter((x) => !hz[x]?.enabled).map((x) => <div className="note" key={x}>The {x}-day forecast is switched off. {hz[x]?.reason}</div>);
  if (sel == null) return <><Head title="Forecasting" sub="Daily net revenue" right={tabs} />{disabledNotes}<Empty title="No forecast is shown"><p>No horizon passed validation, so nothing is displayed.</p></Empty></>;
  const e = hz[sel];
  const hist = data.history.slice(-120).map((d: any) => ({ date: d.date, actual: d.status === "observed" || d.status === "zero" ? d.net_revenue : null }));
  const fc = e.forecast.filter((p: any) => !p.closed).map((p: any) => ({ date: p.date, forecast: p.yhat, band: p.lo != null ? [p.lo, p.hi] : null }));
  const chart = [...hist, ...fc];
  const bt = e.test_backtest.filter((r: any) => r.y != null).map((r: any) => ({ date: r.date, actual: r.y, predicted: r.yhat }));
  const cov = e.interval?.test_coverage;
  return (
    <>
      <Head title="Forecasting" sub={`Target: ${data.target}. Forecast made from ${fmtDate(e.origin)}.`} right={tabs} />
      {disabledNotes}
      <div className="grid g3" style={{ marginBottom: 16 }}>
        <Tile label={`Expected total, next ${sel} days`} value={money(cur, e.forecast_total, true)} hint={`${fmtDate(e.forecast[0].date)} to ${fmtDate(e.forecast[e.forecast.length - 1].date)}`} />
        <Tile label="Model in use" value={e.selected_label.split(" (")[0]} hint={e.selection_rule} />
        <Tile label="Daily error on unseen test days (WMAPE)" value={fmtPct(e.comparison.find((c: any) => c.model === e.selected_model).test.wmape, 0)} hint="Sum of absolute errors as a share of actual revenue" />
      </div>
      <div className="panel">
        <h2>Last 120 days and the next {sel}</h2>
        <TimeChart data={chart} series={[{ key: "actual", label: "Actual", color: "var(--series-1)" }, { key: "forecast", label: "Forecast", color: "var(--series-2)", dashed: true }]}
          band={fc.some((p: any) => p.band) ? { key: "band", label: `${fmtPct(e.interval.level, 0)} range from past forecast errors`, color: "var(--series-2)" } : undefined}
          fmt={(v) => money(cur, v)} yLabel={`${cur} per trading day (closed weekdays and data gaps not drawn)`} height={320} />
        <p className="small" style={{ marginTop: 8 }}>
          {e.interval.method}{" "}
          {cov ? `On the test window, ${fmtPct(cov.empirical, 0)} of ${cov.n} actual days fell inside a range calibrated beforehand (target ${fmtPct(cov.nominal, 0)}).` : "Coverage could not be checked."}
        </p>
      </div>
      <div className="grid g2">
        <div className="panel">
          <h2>Model comparison for {sel} days ahead</h2>
          <table>
            <thead><tr><th>Model</th><th className="r">Validation WMAPE</th><th className="r">Test WMAPE</th><th className="r">Test MAE</th><th className="r">Test RMSE</th></tr></thead>
            <tbody>{e.comparison.map((c: any) => (
              <tr key={c.model} className={c.model === e.selected_model ? "sel" : ""}>
                <td>{c.label}{c.model === e.selected_model ? " (in use)" : ""}</td>
                <td className="r">{fmtPct(c.validation.wmape)}</td><td className="r">{fmtPct(c.test.wmape)}</td>
                <td className="r">{fmtInt(c.test.mae)}</td><td className="r">{fmtInt(c.test.rmse)}</td>
              </tr>))}</tbody>
          </table>
          <p className="small" style={{ marginTop: 10 }}>
            The model is chosen on validation only ({data.plan.validation_origins.length} rolling start dates up to {fmtDate(data.plan.train_validation_end)}). The test window ({fmtDate(data.plan.test_start)} to {fmtDate(data.plan.test_end)}) is used only to report. If another model looks better on test, that is shown here and not hidden. MAE and RMSE are in {cur} per trading day.
          </p>
        </div>
        <div className="panel">
          <h2>How it would have done on the test window</h2>
          <TimeChart data={bt} series={[{ key: "actual", label: "Actual", color: "var(--series-1)" }, { key: "predicted", label: `Predicted ${sel === 90 ? "" : "(re-forecast every " + sel + " days)"}`, color: "var(--series-2)", dashed: true }]}
            fmt={(v) => money(cur, v)} yLabel={`${cur} per trading day`} height={230} />
        </div>
      </div>
      {data.explainability && (
        <div className="panel">
          <h2>What the XGBoost model relies on</h2>
          {e.selected_model !== "xgboost" && <div className="note">This horizon uses {e.selected_label}, not XGBoost. The chart below describes the XGBoost model only.</div>}
          <p className="small">Share of the model's total attribution, by input.</p>
          <ShareBars rows={data.explainability.features.slice(0, 8).map((f: any) => ({ label: f.description, share: f.share }))} />
          <p className="small">{data.explainability.method}. {data.explainability.note}</p>
        </div>
      )}
      <div className="panel"><h2>Limits of these forecasts</h2><Caveats items={data.limitations} /></div>
    </>
  );
}

/* ------------------------------------------------------------------ Customers */
export function Customers({ ds }: P) {
  const cap = capOf(ds, "customer_rfm");
  const { data, error, loading } = useApi(cap.enabled ? `/datasets/${ds.id}/customers` : null);
  if (!cap.enabled) return <><Head title="Customers" sub="RFM segments and clusters" /><Unsupported title="Customer analysis" reason={cap.reason} /></>;
  if (loading) return <Loading what="Loading customer segments" />;
  if (error) return <ErrorState error={error} />;
  const cur = data.currency, oot = data.out_of_time, km = data.kmeans;
  const rate = (s: string) => oot?.by_segment.find((x: any) => x.segment === s)?.repurchase_rate;
  const SegTable = ({ rows, withRule }: { rows: any[]; withRule?: boolean }) => (
    <div className="scroll-x"><table>
      <thead><tr><th>Segment</th><th className="r">Customers</th><th>Share of customers</th><th>Share of revenue</th><th className="r">Last purchase</th><th className="r">Purchases</th><th className="r">Spend</th></tr></thead>
      <tbody>{rows.map((s: any) => (
        <tr key={s.segment}>
          <td><b style={{ fontWeight: 600 }}>{s.segment}</b>{withRule && <div className="small">{s.rule}</div>}</td>
          <td className="r">{fmtInt(s.customers)}</td>
          <td><Share v={s.customer_share} /></td><td><Share v={s.revenue_share} /></td>
          <td className="r">{fmtInt(s.median_recency_days)} days ago</td><td className="r">{fmtInt(s.median_frequency)}</td><td className="r">{money(cur, s.median_monetary)}</td>
        </tr>))}</tbody>
    </table></div>
  );
  return (
    <>
      <Head title="Customers" sub={`${fmtInt(data.customers)} customers with an ID, measured up to ${fmtDate(data.cutoff)}. Last purchase, purchases and spend are medians per segment.`} />
      <div className="panel">
        <h2>RFM segments</h2>
        <SegTable rows={data.segments} withRule />
        <p className="small" style={{ marginTop: 10 }}>Recency: {data.definitions.recency_days} Frequency: {data.definitions.frequency} Spend: {data.definitions.monetary}</p>
        <Caveats items={data.caveats} />
      </div>
      {oot && (
        <div className="panel">
          <h2>Do the labels mean anything? A check on later behaviour</h2>
          <p className="sub">Customers were labelled using data up to {fmtDate(oot.as_of)} only. The table shows what each group then did in the following {oot.window_days} days.</p>
          <table>
            <thead><tr><th>Segment on {fmtDate(oot.as_of)}</th><th className="r">Customers</th><th>Bought again within {oot.window_days} days</th><th className="r">Average spend in those days</th></tr></thead>
            <tbody>{oot.by_segment.map((s: any) => <tr key={s.segment}><td>{s.segment}</td><td className="r">{fmtInt(s.customers)}</td><td><Share v={s.repurchase_rate} /></td><td className="r">{money(cur, s.mean_next_spend)}</td></tr>)}</tbody>
          </table>
          <p className="small" style={{ marginTop: 10 }}>
            {rate("At-risk") != null && rate("Occasional") != null && Math.abs(rate("At-risk") - rate("Occasional")) < 0.05
              ? "At-risk and Occasional customers came back at a similar rate here, so the At-risk label says more about past value than about the chance of returning."
              : "Read differences between groups as a description of this period, not a prediction."}{" "}
            This is a check of rule-based labels, not a churn model.
          </p>
        </div>
      )}
      {km ? (
        <div className="panel">
          <h2>K-Means comparison ({km.k} clusters)</h2>
          <p className="sub">An alternative grouping found by the algorithm from the same three measures. {km.preprocessing} Names describe where each cluster's typical customer sits in the whole base.</p>
          <SegTable rows={km.clusters} />
          <div className="grid g2" style={{ marginTop: 16 }}>
            <div>
              <h3>How the number of clusters was chosen</h3>
              <table>
                <thead><tr><th>k</th><th className="r">Silhouette</th><th className="r">Stability (ARI)</th></tr></thead>
                <tbody>{km.diagnostics.map((d: any) => <tr key={d.k} className={d.k === km.k ? "sel" : ""}><td>{d.k}{d.k === km.k ? " (chosen)" : ""}</td><td className="r">{d.silhouette.toFixed(3)}</td><td className="r">{d.stability_ari.toFixed(3)}</td></tr>)}</tbody>
              </table>
              <p className="small" style={{ marginTop: 8 }}>{km.selection_rule} Stability is the average agreement between the clustering and re-fits on resampled customers (1 = identical).</p>
            </div>
            <div>
              <h3>Overlap with the RFM segments</h3>
              <div className="scroll-x"><table>
                <thead><tr><th></th>{km.crosstab.cols.map((c: string) => <th key={c} className="r">{c}</th>)}</tr></thead>
                <tbody>{km.crosstab.rows.map((r: string, i: number) => <tr key={r}><td>{r}</td>{km.crosstab.values[i].map((v: number, j: number) => <td key={j} className="r">{fmtInt(v)}</td>)}</tr>)}</tbody>
              </table></div>
              <p className="small" style={{ marginTop: 8 }}>Agreement (adjusted Rand index): {km.agreement_with_rfm_ari.toFixed(2)}. The two methods overlap but are not the same grouping; the RFM rules are used elsewhere in the product because each label has a stated definition.</p>
            </div>
          </div>
        </div>
      ) : <Unsupported title="K-Means clustering" reason={capOf(ds, "customer_kmeans").reason} />}
    </>
  );
}
const Share = ({ v }: { v: number }) => (
  <div style={{ display: "flex", gap: 8, alignItems: "center" }}><div className="sharebar" style={{ flex: 1 }}><div style={{ width: `${Math.min(100, v * 100)}%` }} /></div><span className="num" style={{ width: 48, textAlign: "right" }}>{fmtPct(v)}</span></div>
);

/* ------------------------------------------------------------------ Anomalies */
export function Anomalies({ ds }: P) {
  const cap = capOf(ds, "anomalies");
  const { data, error, loading } = useApi(cap.enabled ? `/datasets/${ds.id}/anomalies` : null);
  const [metric, setMetric] = useState("net_revenue");
  const [sev, setSev] = useState("all");
  const [open, setOpen] = useState<string | null>(null);
  if (!cap.enabled) return <><Head title="Anomaly center" sub="Unusual days" /><Unsupported title="Anomaly detection" reason={cap.reason} /></>;
  if (loading) return <Loading what="Loading alerts" />;
  if (error) return <ErrorState error={error} />;
  const cur = data.currency;
  const labels: Record<string, string> = { net_revenue: "Net revenue", transactions: "Transactions", customers: "Active customers" };
  const isMoney = metric === "net_revenue";
  const f = (v: number) => (isMoney ? money(cur, v) : fmtInt(v));
  const flagged = new Set(data.alerts.filter((a: any) => a.metric === metric).map((a: any) => a.date));
  const series = data.series[metric].slice(-180).filter((r: any) => r.actual != null).map((r: any) => ({
    date: r.date, actual: r.actual, expected: r.expected, band: r.lo != null ? [r.lo, r.hi] : null, flag: flagged.has(r.date) ? r.actual : null,
  }));
  const order: Record<string, number> = { high: 0, medium: 1, low: 2 };
  const alerts = data.alerts.filter((a: any) => a.metric === metric && (sev === "all" || a.severity === sev))
    .sort((a: any, b: any) => order[a.severity] - order[b.severity] || Math.abs(b.z) - Math.abs(a.z));
  const mc = data.method_comparison;
  return (
    <>
      <Head title="Anomaly center" sub="Days that differ strongly from the same weekday in the previous eight weeks." />
      <div className="filters">
        <Tabs value={metric} onChange={(m) => { setMetric(m); setOpen(null); }} options={data.metrics.map((m: string) => ({ value: m, label: labels[m] }))} />
        <Tabs value={sev} onChange={setSev} options={[{ value: "all", label: "All severities" }, { value: "high", label: "High" }, { value: "medium", label: "Medium" }, { value: "low", label: "Low" }]} />
      </div>
      <div className="panel">
        <h2>{labels[metric]}: actual against expected, last 180 days</h2>
        <TimeChart data={series} series={[{ key: "actual", label: "Actual", color: "var(--series-1)" }, { key: "expected", label: "Expected (same-weekday median)", color: "var(--series-3)", dashed: true }]}
          band={{ key: "band", label: "Expected range", color: "var(--series-3)" }} flagKey="flag" fmt={f} yLabel={isMoney ? `${cur} per trading day` : "Count per trading day"} height={300}
          yMax={Math.max(...series.map((r: any) => Math.max(r.actual, r.expected || 0))) * 1.15} />
        <p className="small" style={{ marginTop: 8 }}>The expected range is wide because daily values in this dataset vary a lot from week to week; where it extends above the chart it is cut off. Only days outside the range are flagged.</p>
      </div>
      <div className="panel">
        <h2>{alerts.length} alert{alerts.length === 1 ? "" : "s"}, most severe first</h2>
        {alerts.length === 0 ? <p className="sub">No days match this filter.</p> : (
          <div className="scroll-x"><table>
            <thead><tr><th>Date</th><th>Severity</th><th className="r">Actual</th><th className="r">Expected</th><th className="r">Expected range</th><th className="r">Difference</th><th>Also flagged by Isolation Forest</th><th></th></tr></thead>
            <tbody>{alerts.map((a: any) => {
              const id = a.metric + a.date;
              return [
                <tr key={id}>
                  <td>{fmtDate(a.date)}</td><td><Severity level={a.severity} /></td>
                  <td className="r">{f(a.actual)}</td><td className="r">{f(a.expected)}</td><td className="r">{f(a.expected_lo)} to {f(a.expected_hi)}</td>
                  <td className="r">{a.deviation >= 0 ? "+" : "−"}{f(Math.abs(a.deviation))} ({a.deviation_pct >= 0 ? "+" : ""}{fmtPct(a.deviation_pct, 0)})</td>
                  <td>{a.isolation_forest_flag == null ? "Not scored" : a.isolation_forest_flag ? "Yes" : "No"}</td>
                  <td>{a.context && <button className="btn ghost" style={{ padding: "3px 10px" }} aria-expanded={open === id} onClick={() => setOpen(open === id ? null : id)}>{open === id ? "Hide" : "Evidence"}</button>}</td>
                </tr>,
                open === id && a.context && <tr key={id + "x"}><td colSpan={8} style={{ background: "#f6f8f7" }}><AlertContext a={a} cur={cur} /></td></tr>,
              ];
            })}</tbody>
          </table></div>
        )}
        <p className="small" style={{ marginTop: 10 }}>Method: {data.alerts[0]?.method || "Seasonal robust z-score"}. {data.thresholds.explanation} Alerts start at |z| of {data.thresholds.alert}; medium from {data.thresholds.medium}; high from {data.thresholds.high}. Evidence is computed for the most recent alert days.</p>
      </div>
      <div className="panel">
        <h2>Second opinion and limits</h2>
        <p>Across all metrics the statistical detector flagged {mc.statistical_days} days and Isolation Forest flagged {mc.isolation_forest_days}; {mc.both} days were flagged by both{mc.jaccard != null ? ` (overlap ${fmtPct(mc.jaccard, 0)})` : ""}. {mc.note}</p>
        <Caveats items={data.caveats} />
      </div>
    </>
  );
}
function AlertContext({ a, cur }: { a: any; cur: string }) {
  const c = a.context;
  const dimName: Record<string, string> = { location: "Locations", product_id: "Products", category: "Categories" };
  return (
    <div style={{ padding: "6px 4px" }}>
      {a.note && <p>{a.note}</p>}
      <p className="small">Compared with the average of {c.baseline_days.length} earlier days on the same weekday ({fmtDate(c.baseline_days[0])} to {fmtDate(c.baseline_days[c.baseline_days.length - 1])}).</p>
      <div className="grid g3">
        <div><h3>Volume and basket</h3>
          <div className="small">Transactions: <b>{fmtInt(c.transactions.actual)}</b> against a typical {fmtInt(c.transactions.expected)}</div>
          <div className="small">Average transaction value: <b>{money(cur, c.avg_transaction_value.actual)}</b> against {money(cur, c.avg_transaction_value.expected)}</div>
          <div className="small">Returns: <b>{money(cur, c.returns_value.actual)}</b> against {money(cur, c.returns_value.expected)}</div>
          {c.largest_transaction && <div className="small">Largest single transaction: <b>{money(cur, c.largest_transaction.value)}</b>, {fmtPct(c.largest_transaction.share_of_day_sales, 0)} of the day's sales</div>}
        </div>
        {Object.entries(c.dimensions).map(([dim, rows]: any) => (
          <div key={dim}><h3>{dimName[dim] || dim} that differed most</h3>
            {rows.map((r: any) => <div className="small" key={r.key}>{r.label}: <b>{r.difference >= 0 ? "+" : "−"}{money(cur, Math.abs(r.difference))}</b></div>)}
          </div>
        ))}
      </div>
      <p className="small muted" style={{ marginTop: 8 }}>These are differences in the numbers on that day. They do not establish a cause.</p>
    </div>
  );
}

/* ------------------------------------------------------------------ Diagnostics */
export function Diagnostics({ ds }: P) {
  const cap = capOf(ds, "diagnostics");
  const [q, setQ] = useState<string>("?days=28");
  const [form, setForm] = useState<any>(null);
  const [dim, setDim] = useState<string | null>(null);
  const { data, error, loading } = useApi(cap.enabled ? `/datasets/${ds.id}/diagnostics${q}` : null);
  const f = form || (data && { a_start: data.baseline_period.start, a_end: data.baseline_period.end, b_start: data.comparison_period.start, b_end: data.comparison_period.end });
  if (!cap.enabled) return <><Head title="Diagnostics" sub="Why did revenue change?" /><Unsupported title="Diagnostics" reason={cap.reason} /></>;
  const head = <Head title="Diagnostics" sub="Split a change in net revenue into the measurable parts that add up to it." />;
  const controls = f && (
    <div className="filters">
      <div className="field">Quick choice
        <Tabs value={q} onChange={(v) => { setQ(v); setForm(null); }} options={[7, 28, 90].map((d) => ({ value: `?days=${d}`, label: `Last ${d} days vs previous ${d}` }))} />
      </div>
      {(["a_start", "a_end", "b_start", "b_end"] as const).map((k) => (
        <label className="field" key={k}>{{ a_start: "Baseline from", a_end: "Baseline to", b_start: "Compare from", b_end: "Compare to" }[k]}
          <input type="date" value={f[k]} min={data?.data_range.start} max={data?.data_range.end} onChange={(e) => setForm({ ...f, [k]: e.target.value })} style={{ minWidth: 0 }} />
        </label>
      ))}
      <button className="btn" disabled={!form} onClick={() => setQ(`?a_start=${f.a_start}&a_end=${f.a_end}&b_start=${f.b_start}&b_end=${f.b_end}`)}>Compare these dates</button>
    </div>
  );
  if (loading && !data) return <>{head}<Loading what="Calculating" /></>;
  if (error) return <>{head}{controls}<ErrorState error={error} retry={() => { setQ("?days=28"); setForm(null); }} /></>;
  const cur = data.currency, dims = data.dimensions;
  const dimLabels: Record<string, string> = { product_id: "Product", category: "Category", location: "Location", channel: "Channel", customer_segment: "Customer segment", weekday: "Day of week" };
  const d = dim && dims[dim] ? dim : Object.keys(dims)[0];
  const dd = dims[d];
  const fell = data.total_change < 0;
  const Effects = ({ block }: { block: any }) => (
    <>
      <p className="small">{block.identity}</p>
      <SignedBars cur={cur} rows={block.effects.map((e: any) => ({ label: e.factor, value: e.effect, sub: `${fmtNum(e.before, e.factor, cur)} to ${fmtNum(e.after, e.factor, cur)}` }))} />
      <p className="small muted" style={{ marginTop: 6 }}>These effects add up to {money(cur, block.effects.reduce((s: number, e: any) => s + e.effect, 0))}; the total change is {money(cur, data.total_change)} (difference {Math.abs(block.reconciliation_error).toFixed(2)}).</p>
    </>
  );
  return (
    <>
      {head}{controls}
      <div className="grid g3" style={{ marginBottom: 16 }}>
        <Tile label={`Baseline: ${fmtDate(data.baseline_period.start)} to ${fmtDate(data.baseline_period.end)}`} value={money(cur, data.baseline.net_revenue, true)} hint={`${data.baseline_period.days} days`} />
        <Tile label={`Comparison: ${fmtDate(data.comparison_period.start)} to ${fmtDate(data.comparison_period.end)}`} value={money(cur, data.comparison.net_revenue, true)} hint={`${data.comparison_period.days} days`} />
        <div className="panel tile" style={{ marginBottom: 0 }}><div className="label">Net revenue {fell ? "fell" : "rose"} by</div><div className="value">{money(cur, Math.abs(data.total_change), true)}</div><Delta v={data.total_change_pct} /></div>
      </div>
      <div className="grid g2">
        <div className="panel"><h2>Volume or basket size?</h2><Effects block={data.two_factor} /></div>
        <div className="panel"><h2>Customers, frequency or value?</h2>
          {data.three_factor ? <Effects block={data.three_factor} /> : <p className="sub">This split needs customer IDs in both periods.</p>}
        </div>
      </div>
      <div className="panel">
        <div className="page-head" style={{ marginBottom: 10 }}>
          <h2 style={{ margin: 0 }}>Where the change shows up</h2>
          <Tabs value={d} onChange={setDim} options={Object.keys(dims).map((k) => ({ value: k, label: dimLabels[k] || k }))} />
        </div>
        {d === "weekday" ? (
          <SignedBars cur={cur} rows={dd.rows.map((r: any) => ({ label: r.label, value: r.change }))} />
        ) : (
          <>
            <p className="small">Across {fmtInt(dd.groups)} groups: falls total {money(cur, dd.sum_negative)}, rises total +{money(cur, dd.sum_positive)}, net {money(cur, dd.total_change)}. Largest movers shown; the rest are combined.</p>
            <SignedBars cur={cur} rows={[
              ...dd.top_negative.map((r: any) => ({ label: r.label, value: r.change, sub: r.status !== "continuing" ? r.status : undefined })),
              ...dd.top_positive.slice().reverse().map((r: any) => ({ label: r.label, value: r.change, sub: r.status !== "continuing" ? r.status : undefined })),
              { label: `All other groups (${fmtInt(dd.other.groups)})`, value: dd.other.change, noBar: true },
            ]} />
          </>
        )}
        <Caveats items={data.caveats} />
      </div>
    </>
  );
}
function fmtNum(v: number, factor: string, cur: string) {
  if (/value|Revenue/.test(factor)) return money(cur, v);
  if (/per customer/.test(factor)) return v.toFixed(2);
  return fmtInt(v);
}

/* ------------------------------------------------------------------ Recommendations */
export function Rec({ r, compact }: { r: any; compact?: boolean }) {
  return (
    <article className={`rec ${r.priority}`}>
      <div className="claim">
        <Priority level={r.priority} />
        <h3>{r.title}</h3>
        <p>{r.action}</p>
        {!compact && <><h4>Uncertainty</h4><p className="small">{r.uncertainty}</p></>}
      </div>
      <div className="ledger">
        <h4>Evidence</h4>
        <table><tbody>{r.evidence.map((e: any, i: number) => <tr key={i}><td>{e.label}</td><td className="r">{e.value}</td></tr>)}</tbody></table>
        {!compact && r.assumptions.length > 0 && <><h4 style={{ marginTop: 12 }}>Assumes</h4><ul>{r.assumptions.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul></>}
        {!compact && r.missing_information.length > 0 && <><h4 style={{ marginTop: 12 }}>Would sharpen this</h4><ul>{r.missing_information.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul></>}
      </div>
    </article>
  );
}
export function Recommendations({ ds }: P) {
  const { data, error, loading } = useApi(`/datasets/${ds.id}/recommendations`);
  if (loading) return <Loading what="Loading recommendations" />;
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <Head title="Recommendations" sub={`Actions to consider as of ${data.as_of ? fmtDate(data.as_of) : "the latest data"}. Each one is produced by a fixed rule and shows the numbers that triggered it.`} />
      {data.items.length === 0 ? <Empty title="No rule was triggered"><p>None of the rules found enough evidence in the current period. That is a result, not an error.</p></Empty> :
        data.items.map((r: any) => <Rec key={r.id} r={r} />)}
      <p className="small">{data.method} These are suggestions to weigh against what you know about the business. They are not guaranteed to improve results.</p>
    </>
  );
}

