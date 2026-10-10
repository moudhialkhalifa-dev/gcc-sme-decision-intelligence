import { useEffect, useRef, useState } from "react";
import { api, ApiError, ErrorState, fmtDate, fmtInt, fmtPct, Loading, money } from "./lib";

const FIELD_LABELS: Record<string, string> = {
  timestamp: "Date / time", transaction_id: "Transaction ID", customer_id: "Customer ID", product_id: "Product ID",
  product_name: "Product name", quantity: "Quantity", unit_price: "Unit price", amount: "Line amount",
  category: "Category", location: "Location", channel: "Channel",
};
const ZONES = ["Asia/Bahrain", "Asia/Riyadh", "Asia/Dubai", "Asia/Qatar", "Asia/Kuwait", "Asia/Muscat", "Europe/London", "UTC"];

export function DataPage({ ds, meta, onSelect, onChanged }: { ds: any; meta: any; onSelect: (id: string | null) => void; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function upload(file: File) {
    setErr(null);
    if (meta && file.size > meta.max_upload_mb * 1024 * 1024) { setErr(`This file is larger than the ${meta.max_upload_mb} MB limit.`); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api("/datasets", { method: "POST", body: fd });
      onSelect(res.dataset.id);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function loadDemo() {
    setBusy(true); setErr(null);
    try { const res = await api("/demo", { method: "POST" }); onSelect(res.dataset.id); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  const uploader = (
    <div className="panel">
      <h2>Upload sales data</h2>
      <p className="sub">One row per line item of a transaction (or one row per transaction). You choose which column is which in the next step.</p>
      <div className={`drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); }}>
        <p>Drop a CSV file here{meta ? `, up to ${meta.max_upload_mb} MB` : ""}</p>
        <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <button className="btn" disabled={busy} onClick={() => input.current?.click()}>{busy ? "Uploading…" : "Choose a CSV file"}</button>
        {meta?.demo_available && <> <button className="btn ghost" disabled={busy} onClick={loadDemo}>Load the public case study</button></>}
      </div>
      {meta?.demo_available && <p className="small" style={{ marginTop: 10 }}>The case study is the UCI Online Retail II dataset: a UK online retailer, 2009 to 2011, in GBP. It is a development example, not GCC data.</p>}
      {err && <p role="alert" style={{ color: "var(--down-text)", marginTop: 10 }}>{err}</p>}
    </div>
  );

  if (!ds) return <><Head title="Data" sub="Upload a file, map its columns and check its quality before any analysis is shown." />{uploader}</>;
  return (
    <>
      <Head title="Data" sub={`${ds.name} (${ds.filename})`} />
      {ds.status === "uploaded" && <Mapping ds={ds} onDone={onChanged} />}
      {ds.status === "processing" && <Processing ds={ds} onDone={onChanged} />}
      {ds.status === "failed" && (
        <>
          <div className="panel state error" role="alert"><h3>Processing failed</h3><p>{ds.job?.error || "Unknown error."}</p><p className="small">Adjust the column mapping below and try again.</p></div>
          <Mapping ds={ds} onDone={onChanged} />
        </>
      )}
      {ds.status === "ready" && <Quality ds={ds} />}
      {ds.status !== "processing" && (
        <div className="panel">
          <h2>Manage</h2>
          <button className="btn ghost" onClick={() => onSelect(null)}>Upload another file</button>{" "}
          <button className="btn danger" onClick={async () => {
            if (!confirm("Delete this dataset and everything computed from it?")) return;
            try { await api(`/datasets/${ds.id}`, { method: "DELETE" }); onSelect(null); } catch (e: any) { alert(e.message); }
          }}>Delete this dataset</button>
        </div>
      )}
    </>
  );
}

function Head({ title, sub }: { title: string; sub: string }) {
  return <div className="page-head"><div><h1>{title}</h1><div className="sub">{sub}</div></div></div>;
}

function Mapping({ ds, onDone }: { ds: any; onDone: () => void }) {
  const [pv, setPv] = useState<any>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [map, setMap] = useState<Record<string, string>>({});
  const [currency, setCurrency] = useState(ds.settings?.currency || "");
  const [tz, setTz] = useState(ds.settings?.timezone || "Asia/Bahrain");
  const [dayfirst, setDayfirst] = useState(!!ds.settings?.dayfirst);
  const [prefixes, setPrefixes] = useState((ds.settings?.return_prefixes || []).join(","));
  const [pattern, setPattern] = useState(ds.settings?.non_product_pattern || "");
  const [dedupe, setDedupe] = useState(ds.settings?.drop_exact_duplicates ?? true);
  const [errs, setErrs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api(`/datasets/${ds.id}/preview`).then((p) => { setPv(p); setMap(ds.settings?.mapping || p.suggested_mapping); }).catch(setError);
  }, [ds.id]);
  if (error) return <ErrorState error={error} />;
  if (!pv) return <Loading what="Reading the file" />;

  async function submit() {
    setBusy(true); setErrs([]);
    try {
      await api(`/datasets/${ds.id}/process`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mapping: map, currency: currency.toUpperCase(), timezone: tz, dayfirst,
          return_prefixes: prefixes.split(",").map((s: string) => s.trim()).filter(Boolean),
          non_product_pattern: pattern || null, drop_exact_duplicates: dedupe,
        }),
      });
      onDone();
    } catch (e: any) {
      setErrs(e.detail?.errors || [e.message]);
    } finally { setBusy(false); }
  }
  const hasValue = map.amount || (map.quantity && map.unit_price);
  return (
    <>
      <div className="panel">
        <h2>Map the columns</h2>
        <p className="sub">Suggestions come from the column names. Date and a value (amount, or quantity and unit price) are required; everything else unlocks more analysis.</p>
        {Object.keys(FIELD_LABELS).map((f) => (
          <div className="maprow" key={f}>
            <label htmlFor={`m-${f}`}>{FIELD_LABELS[f]}{f === "timestamp" ? " (required)" : ""}</label>
            <select id={`m-${f}`} value={map[f] || ""} onChange={(e) => { const m = { ...map }; if (e.target.value) m[f] = e.target.value; else delete m[f]; setMap(m); }}>
              <option value="">Not in this file</option>
              {pv.columns.map((c: string) => <option key={c} value={c}>{c}</option>)}
            </select>
            <span className="small muted">{/* description from API meta would go here */}{map[f] && pv.preview[0] ? `e.g. ${String(pv.preview[0][map[f]]).slice(0, 40)}` : ""}</span>
          </div>
        ))}
        <div className="filters" style={{ marginTop: 16 }}>
          <label className="field">Currency of the amounts (ISO code)
            <input value={currency} maxLength={3} placeholder="e.g. BHD" onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
          </label>
          <label className="field">Time zone of the timestamps
            <select value={tz} onChange={(e) => setTz(e.target.value)}>{ZONES.map((z) => <option key={z}>{z}</option>)}</select>
          </label>
          <label className="field">Return / cancellation ID prefixes
            <input value={prefixes} placeholder="e.g. C" onChange={(e) => setPrefixes(e.target.value)} />
          </label>
        </div>
        <label className="small"><input type="checkbox" checked={dayfirst} onChange={(e) => setDayfirst(e.target.checked)} /> Dates are written day first (31/12/2024)</label><br />
        <label className="small"><input type="checkbox" checked={dedupe} onChange={(e) => setDedupe(e.target.checked)} /> Remove exact duplicate rows</label>
        <details style={{ marginTop: 10 }}>
          <summary>Exclude non-product lines (postage, fees)</summary>
          <label className="field" style={{ marginTop: 8 }}>Product IDs matching this pattern are left out of revenue
            <input value={pattern} placeholder="regular expression, e.g. ^(POST|FEE)$" onChange={(e) => setPattern(e.target.value)} style={{ minWidth: 420 }} />
          </label>
        </details>
        <p className="small" style={{ marginTop: 12 }}>Amounts are never converted: they are shown in the currency you enter here.</p>
        {errs.length > 0 && <div role="alert" style={{ color: "var(--down-text)", margin: "8px 0" }}>{errs.map((e, i) => <div key={i}>{e}</div>)}</div>}
        <button className="btn" disabled={busy || !map.timestamp || !hasValue || currency.length !== 3} onClick={submit}>{busy ? "Starting…" : "Process data"}</button>
        {(!map.timestamp || !hasValue || currency.length !== 3) && <span className="small muted"> Map a date, a value and enter the currency to continue.</span>}
      </div>
      <div className="panel">
        <h2>First rows of the file</h2>
        <div className="scroll-x"><table>
          <thead><tr>{pv.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{pv.preview.map((r: any, i: number) => <tr key={i}>{pv.columns.map((c: string) => <td key={c}>{String(r[c])}</td>)}</tr>)}</tbody>
        </table></div>
      </div>
    </>
  );
}

function Processing({ ds, onDone }: { ds: any; onDone: () => void }) {
  const [job, setJob] = useState<any>(ds.job);
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const d = await api(`/datasets/${ds.id}`);
        setJob(d.job);
        if (d.status !== "processing") { clearInterval(t); onDone(); }
      } catch { /* keep polling; a transient error should not lose the job */ }
    }, 2000);
    return () => clearInterval(t);
  }, [ds.id]);
  return (
    <div className="panel" role="status">
      <h2>Processing</h2>
      <p>{job?.stage || "Queued"}</p>
      <div className="progress"><div style={{ width: `${job?.progress || 3}%` }} /></div>
      <p className="small">Cleaning, model back-testing and customer clustering run once per dataset. A large file takes about a minute.</p>
    </div>
  );
}

function Quality({ ds }: { ds: any }) {
  const q = ds.quality, cal = q.calendar, cur = q.currency;
  const st = cal.days_by_status;
  return (
    <>
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Stat label="Rows kept for analysis" value={fmtInt(q.rows_clean)} hint={`of ${fmtInt(q.rows_raw)} in the file`} />
        <Stat label="Period covered" value={`${cal.days_total} days`} hint={`${fmtDate(q.date_min.slice(0, 10))} to ${fmtDate(q.date_max.slice(0, 10))}`} />
        <Stat label="Sale transactions" value={fmtInt(q.transactions)} hint={`${q.granularity} data, ${q.lines_per_transaction.toFixed(1)} lines each`} />
        <Stat label="Identified customers" value={fmtInt(q.customers_identified)} hint={`${fmtPct(q.customer_id_coverage_revenue)} of revenue has a customer ID`} />
      </div>
      {q.warnings.map((w: string, i: number) => <div className="note" key={i}>{w}</div>)}
      <div className="grid g2">
        <div className="panel">
          <h2>What was removed, and why</h2>
          <table>
            <thead><tr><th>Check</th><th className="r">Rows</th><th className="r">Share of file</th></tr></thead>
            <tbody>
              {q.steps.map((s: any) => <tr key={s.key}><td>{s.label}</td><td className="r">{fmtInt(s.rows)}</td><td className="r">{fmtPct(s.share, 2)}</td></tr>)}
              <tr><td>Sales lines kept</td><td className="r">{fmtInt(q.rows_sales)}</td><td className="r">{fmtPct(q.rows_sales / q.rows_raw)}</td></tr>
              <tr><td>Return and cancellation lines kept</td><td className="r">{fmtInt(q.rows_returns)}</td><td className="r">{fmtPct(q.rows_returns / q.rows_raw, 2)}</td></tr>
            </tbody>
          </table>
          <p className="small" style={{ marginTop: 10 }}>Gross sales {money(cur, q.gross_sales)}; returns {money(cur, q.returns_value)}; non-product lines excluded {money(cur, q.non_product_value_excluded)}.</p>
        </div>
        <div className="panel">
          <h2>Calendar check</h2>
          <table><tbody>
            <tr><td>Days with sales</td><td className="r">{st.observed || 0}</td></tr>
            <tr><td>Regular closed weekdays{cal.closed_weekdays.length ? ` (${cal.closed_weekdays.join(", ")})` : ""}</td><td className="r">{st.closed || 0}</td></tr>
            <tr><td>Zero-sales days on a normal trading weekday</td><td className="r">{st.zero || 0}</td></tr>
            <tr><td>Days inside gaps of 3 or more trading days (closure or missing data)</td><td className="r">{st.gap || 0}</td></tr>
            <tr><td>Incomplete final day</td><td className="r">{cal.partial_last_day ? "Yes, excluded" : "No"}</td></tr>
          </tbody></table>
          {cal.gap_periods.length > 0 && <p className="small" style={{ marginTop: 10 }}>Gaps: {cal.gap_periods.map((g: string[]) => `${fmtDate(g[0])} to ${fmtDate(g[1])}`).join("; ")}. These days are left out of models and comparisons instead of being counted as zero demand.</p>}
          <p className="small">Analysis runs up to the last complete day, {fmtDate(cal.last_complete_day)}.</p>
        </div>
      </div>
      <div className="grid g2">
        <div className="panel">
          <h2>Missing values in mapped columns</h2>
          <table><tbody>{Object.entries(q.missing_by_field).map(([k, v]: any) => <tr key={k}><td>{FIELD_LABELS[k] || k}</td><td className="r">{fmtPct(v, 2)}</td></tr>)}</tbody></table>
        </div>
        <div className="panel">
          <h2>Assumptions</h2>
          <ul className="caveats" style={{ fontSize: 14 }}>{q.assumptions.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul>
        </div>
      </div>
      <div className="panel">
        <h2>What this dataset supports</h2>
        <div className="caplist">
          {ds.capabilities.map((c: any) => (
            <div className="cap" key={c.key}>
              <span className={`mark ${c.enabled ? "on" : "off"}`} aria-hidden>{c.enabled ? "✓" : "–"}</span>
              <div><div>{c.label} <span className="small muted">{c.enabled ? "available" : c.kind === "roadmap" ? "not built yet" : "not available"}</span></div>{!c.enabled && <div className="small">{c.reason}</div>}</div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return <div className="panel tile" style={{ marginBottom: 0 }}><div className="label">{label}</div><div className="value">{value}</div><div className="small muted">{hint}</div></div>;
}
