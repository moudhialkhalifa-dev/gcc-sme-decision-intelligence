import { useCallback, useEffect, useState } from "react";
import { DataPage } from "./DataPage";
import { api, ApiError, Empty, ErrorState, Loading } from "./lib";
import { Anomalies, Customers, Diagnostics, Forecast, Overview, Recommendations } from "./pages";

const PAGES: [string, string][] = [
  ["data", "Data and quality"], ["overview", "Overview"], ["forecast", "Forecasting"], ["customers", "Customers"],
  ["anomalies", "Anomaly center"], ["diagnostics", "Diagnostics"], ["recommendations", "Recommendations"],
];

export default function App() {
  const [page, setPage] = useState(() => location.hash.slice(1) || "data");
  const [list, setList] = useState<any[] | null>(null);
  const [meta, setMeta] = useState<any>(null);
  const [id, setId] = useState<string | null>(() => localStorage.getItem("datasetId"));
  const [ds, setDs] = useState<any>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);

  const go = useCallback((p: string) => { location.hash = p; setPage(p); window.scrollTo(0, 0); }, []);
  const select = useCallback((next: string | null) => {
    if (next) localStorage.setItem("datasetId", next); else localStorage.removeItem("datasetId");
    setDs(null); setId(next); setTick((t) => t + 1); go("data");
  }, [go]);

  useEffect(() => {
    const onHash = () => setPage(location.hash.slice(1) || "data");
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    setError(null);
    Promise.all([api("/meta"), api("/datasets")]).then(([m, l]) => { setMeta(m); setList(l); }).catch(setError);
  }, [tick]);
  useEffect(() => {
    if (!id) { setDs(null); return; }
    api(`/datasets/${id}`).then(setDs).catch((e: ApiError) => {
      if (e.status === 404) { localStorage.removeItem("datasetId"); setId(null); } else setError(e);
    });
  }, [id, tick]);

  const ready = ds?.status === "ready";
  let body;
  if (error) {
    body = error.status === 401 ? (
      <Empty title="This server needs an access key">
        <p>Enter the key you were given. It is stored in this browser only.</p>
        <form onSubmit={(e) => { e.preventDefault(); localStorage.setItem("apiKey", (e.currentTarget.elements.namedItem("k") as HTMLInputElement).value); setTick((t) => t + 1); }}>
          <input name="k" type="password" aria-label="Access key" style={{ padding: 8, border: "1px solid var(--axis)", borderRadius: 6 }} /> <button className="btn">Save key</button>
        </form>
      </Empty>
    ) : <ErrorState error={error} retry={() => setTick((t) => t + 1)} />;
  } else if (!list || (id && !ds)) body = <Loading />;
  else if (page === "data" || !ds) body = <DataPage ds={ds} meta={meta} onSelect={select} onChanged={() => setTick((t) => t + 1)} />;
  else if (!ready) body = <Empty title="This dataset is not ready yet"><p>Finish the steps on the data page first.</p><button className="btn" onClick={() => go("data")}>Go to data</button></Empty>;
  else {
    const P = { overview: Overview, forecast: Forecast, customers: Customers, anomalies: Anomalies, diagnostics: Diagnostics, recommendations: Recommendations }[page] || Overview;
    body = <P ds={ds} go={go} key={ds.id + page} />;
  }

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">SME Decision Intelligence<small>From sales records to decisions</small></div>
        {list && list.length > 0 && (
          <div>
            <label htmlFor="ds">Dataset</label>
            <select id="ds" value={id || ""} onChange={(e) => select(e.target.value || null)}>
              <option value="">Upload a new file…</option>
              {list.map((d) => <option key={d.id} value={d.id}>{d.name}{d.status !== "ready" ? ` (${d.status})` : ""}</option>)}
            </select>
          </div>
        )}
        <nav aria-label="Pages">
          {PAGES.map(([k, label]) => <button key={k} aria-current={page === k ? "page" : undefined} disabled={k !== "data" && !ready} onClick={() => go(k)}>{label}</button>)}
        </nav>
        <div className="foot">{ds?.currency ? `Amounts in ${ds.currency}, as supplied. ` : ""}Single-company demonstration.</div>
      </aside>
      <main className="main">{body}</main>
    </div>
  );
}
