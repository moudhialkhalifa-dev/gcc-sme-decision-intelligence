import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { compactNum, fmtDate, shortDate } from "./lib";

export type Series = { key: string; label: string; color: string; dashed?: boolean; dots?: boolean };
type Band = { key: string; label: string; color: string };

const niceMax = (v: number) => { const step = Math.pow(10, Math.max(0, Math.floor(Math.log10(v)) - 1)); return Math.ceil(v / step) * step; };

function Tip({ active, payload, label, series, band, fmt, flagKey }: any) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="tt">
      <div className="muted" style={{ marginBottom: 4 }}>{typeof label === "string" && label.length === 10 ? fmtDate(label) : label}</div>
      {series.map((s: Series) => row[s.key] != null && (
        <div key={s.key}><span className="k" style={{ background: s.color }} /><b>{fmt(row[s.key])}</b> {s.label}</div>
      ))}
      {band && row[band.key] && <div className="muted">{band.label}: {fmt(row[band.key][0])} to {fmt(row[band.key][1])}</div>}
      {flagKey && row[flagKey] != null && <div style={{ marginTop: 4 }}>Flagged as unusual</div>}
      {row.note && <div className="muted">{row.note}</div>}
    </div>
  );
}

/** Time-series line chart: one y-axis, 2px lines, hairline grid, crosshair tooltip listing every series. */
export function TimeChart({ data, series, band, flagKey, fmt, yLabel, height = 300, yMax }: {
  data: any[]; series: Series[]; band?: Band; flagKey?: string; fmt: (v: number) => string; yLabel: string; height?: number; yMax?: number;
}) {
  return (
    <div>
      {(series.length > 1 || band || flagKey) && (
        <div className="legend">
          {series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}
          {band && <span><i className="box" style={{ background: band.color }} />{band.label}</span>}
          {flagKey && <span><i style={{ background: "var(--critical)", width: 8, height: 8, borderRadius: "50%" }} />Flagged day</span>}
        </div>
      )}
      <div className="small muted" style={{ marginBottom: 2 }}>{yLabel}</div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--hairline)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 12, fill: "var(--muted)" }} stroke="var(--axis)" tickLine={false} minTickGap={48} />
          <YAxis tickFormatter={compactNum} tick={{ fontSize: 12, fill: "var(--muted)" }} stroke="none" width={48} domain={yMax ? [0, niceMax(yMax)] : undefined} allowDataOverflow={!!yMax} />
          <Tooltip content={<Tip series={series} band={band} fmt={fmt} flagKey={flagKey} />} cursor={{ stroke: "var(--axis)" }} />
          {band && <Area dataKey={band.key} stroke="none" fill={band.color} fillOpacity={0.14} isAnimationActive={false} connectNulls />}
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} stroke={s.color} strokeWidth={2} dot={false} strokeDasharray={s.dashed ? "5 4" : undefined}
              connectNulls isAnimationActive={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
          ))}
          {flagKey && <Scatter dataKey={flagKey} fill="var(--critical)" stroke="var(--surface)" strokeWidth={2} isAnimationActive={false} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

