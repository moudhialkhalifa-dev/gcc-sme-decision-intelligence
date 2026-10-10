import { useEffect, useState, ReactNode } from "react";

/* ---------- API ---------- */
export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    const msg = typeof detail === "string" ? detail : detail?.reason || (detail?.errors || []).join(" ") || "Request failed.";
    super(msg);
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path: string, init?: RequestInit): Promise<any> {
  const key = localStorage.getItem("apiKey");
  const headers: Record<string, string> = { ...(init?.headers as any) };
  if (key) headers["X-API-Key"] = key;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "The server cannot be reached. Check that the API is running.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? `Request failed (${res.status}).`);
  return body;
}

export function useApi(path: string | null, deps: any[] = []) {
  const [state, setState] = useState<{ data: any; error: ApiError | null; loading: boolean }>({ data: null, error: null, loading: !!path });
  useEffect(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return; }
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    api(path).then((data) => live && setState({ data, error: null, loading: false }))
      .catch((error) => live && setState({ data: null, error, loading: false }));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  return state;
}

/* ---------- formatting ---------- */
export const fmtInt = (v: number | null | undefined) => (v == null ? "n/a" : Math.round(v).toLocaleString("en-GB"));
export const fmtPct = (v: number | null | undefined, digits = 1) => (v == null ? "n/a" : `${(v * 100).toFixed(digits)}%`);
export const fmtSigned = (v: number | null | undefined) => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);
export function money(cur: string, v: number | null | undefined, compact = false) {
  if (v == null) return "n/a";
  const abs = Math.abs(v);
  if (compact && abs >= 1e6) return `${cur} ${(v / 1e6).toFixed(2)}M`;
  if (compact && abs >= 1e4) return `${cur} ${(v / 1e3).toFixed(1)}K`;
  return `${cur} ${v.toLocaleString("en-GB", { maximumFractionDigits: abs < 100 ? 2 : 0 })}`;
}
export const compactNum = (v: number) => (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${Math.round(v / 1e3)}K` : `${Math.round(v)}`);
export function fmtDate(iso: string, withYear = true) {
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) });
}
export const shortDate = (iso: string) => fmtDate(iso, false);

/* ---------- shared UI ---------- */
export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="panel state" role="status"><div className="spinner" />{what}…</div>;
}
export function ErrorState({ error, retry }: { error: ApiError; retry?: () => void }) {
  return (
    <div className="panel state error" role="alert">
      <h3>This could not be loaded</h3>
      <p>{error.message}</p>
      {retry && <button className="btn ghost" onClick={retry}>Try again</button>}
    </div>
  );
}
export function Unsupported({ title, reason }: { title: string; reason: string }) {
  return (
    <div className="panel state">
      <h3>{title} is not available for this dataset</h3>
      <p>{reason}</p>
      <p className="small">Nothing is estimated or filled in when the data cannot support a result.</p>
    </div>
  );
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="panel state"><h3>{title}</h3>{children}</div>;
}
export function Delta({ v, goodUp = true, label }: { v: number | null | undefined; goodUp?: boolean; label?: string }) {
  if (v == null) return <span className="delta flat">No comparison{label ? ` ${label}` : ""}</span>;
  const dir = Math.abs(v) < 0.0005 ? "flat" : (v > 0) === goodUp ? "up" : "down";
  const arrow = v > 0 ? "▲" : v < 0 ? "▼" : "■";
  return <span className={`delta ${dir}`}>{arrow} {fmtSigned(v)}{label ? <span className="muted" style={{ fontWeight: 400 }}> {label}</span> : null}</span>;
}
export function Tile({ label, value, delta, hero, goodUp, hint }: { label: string; value: string; delta?: number | null; hero?: boolean; goodUp?: boolean; hint?: string }) {
  return (
    <div className={`panel tile ${hero ? "hero" : ""}`} style={{ marginBottom: 0 }}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta !== undefined && <Delta v={delta} goodUp={goodUp} label="vs previous period" />}
      {hint && <div className="small muted">{hint}</div>}
    </div>
  );
}
const SEV: Record<string, [string, string]> = { high: ["var(--critical)", "High"], medium: ["var(--serious)", "Medium"], low: ["var(--warning)", "Low"] };
export function Severity({ level }: { level: string }) {
  const [c, t] = SEV[level] || ["var(--muted)", level];
  return <span className="badge"><i style={{ background: c }} />{t}</span>;
}
export function Priority({ level }: { level: string }) {
  const [c, t] = SEV[level] || ["var(--muted)", level];
  return <span className="badge"><i style={{ background: c }} />{t} priority</span>;
}
export function Caveats({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return <ul className="caveats">{items.map((c, i) => <li key={i}>{c}</li>)}</ul>;
}
export function Tabs<T extends string | number>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string; disabled?: boolean; title?: string }[] }) {
  return (
    <div className="tabs" role="group">
      {options.map((o) => (
        <button key={String(o.value)} aria-pressed={o.value === value} disabled={o.disabled} title={o.title} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}
export function capOf(ds: any, key: string): { enabled: boolean; reason: string } {
  const c = (ds?.capabilities || []).find((x: any) => x.key === key);
  return c ? { enabled: c.enabled, reason: c.reason } : { enabled: false, reason: "Not available." };
}

/** Signed horizontal bars around a shared zero line (diverging: blue = up, red = down).
 *  Rows marked noBar (e.g. "all other groups") are listed with their value but not drawn, so that
 *  one large remainder does not flatten the bars it is being compared with. */
export function SignedBars({ rows, cur }: { rows: { label: string; value: number; sub?: string; noBar?: boolean }[]; cur: string }) {
  const max = Math.max(...rows.filter((r) => !r.noBar).map((r) => Math.abs(r.value)), 1);
  return (
    <div>
      {rows.map((r, i) => {
        const w = (Math.abs(r.value) / max) * 50;
        return (
          <div className="hbar" key={i} title={`${r.label}: ${money(cur, r.value)}`}>
            <div className="lab">{r.label}{r.sub && <div className="small muted">{r.sub}</div>}</div>
            <div className="track">
              {!r.noBar && <><div className="zero" />
                <div className={`bar ${r.value < 0 ? "neg" : "pos"}`} style={r.value < 0 ? { right: "50%", width: `${w}%` } : { left: "50%", width: `${w}%` }} /></>}
              {r.noBar && <span className="small muted">combined, not drawn to scale</span>}
            </div>
            <div className="num" style={{ textAlign: "right" }}>{r.value >= 0 ? "+" : "−"}{money(cur, Math.abs(r.value), true)}</div>
          </div>
        );
      })}
    </div>
  );
}

/** Magnitude bars with full-length labels (one hue). */
export function ShareBars({ rows }: { rows: { label: string; share: number }[] }) {
  const max = Math.max(...rows.map((r) => r.share), 0.0001);
  return (
    <div>
      {rows.map((r, i) => (
        <div className="hbar" key={i} title={`${r.label}: ${fmtPct(r.share)}`}>
          <div>{r.label}</div>
          <div className="sharebar" style={{ background: "transparent" }}><div style={{ width: `${(r.share / max) * 100}%`, borderRadius: "0 4px 4px 0" }} /></div>
          <div className="num" style={{ textAlign: "right" }}>{fmtPct(r.share)}</div>
        </div>
      ))}
    </div>
  );
}
