import { ArrowDownIcon, ArrowUpIcon } from "@phosphor-icons/react";
export type Cost = {
  actual_usd: string;
  estimated_usd: string;
  known_usd: string;
  pending_requests: number;
  estimated_requests: number;
  requests: number;
};
export type Spending = {
  days: number;
  timezone: string;
  totals: Cost;
  previous: Cost;
  change_percent: number | null;
  daily: ({ date: string } & Cost)[];
};
export function dollars(value: string | number) {
  const n = Number(value);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits:
      n && Math.abs(n) < 0.01 ? 12 : Math.abs(n) < 1 ? 4 : 2,
  }).format(n);
}
export function recordedTime(seconds: number) {
  const minutes = Math.floor(seconds / 60),
    hours = Math.floor(minutes / 60);
  return hours
    ? `${hours}h ${minutes % 60}m`
    : seconds > 0 && minutes === 0
      ? "<1 min"
      : `${minutes} min`;
}
export function mediaTime(seconds: number) {
  return seconds >= 3600
    ? `${Math.floor(seconds / 3600)}:${Math.floor(seconds / 60) % 60}`.replace(
        /:(\d)$/,
        ":0$1",
      ) +
        ":" +
        Math.floor(seconds % 60)
          .toString()
          .padStart(2, "0")
    : `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60)
        .toString()
        .padStart(2, "0")}`;
}
export function CostAmount({
  cost,
  pending = false,
}: {
  cost: Cost;
  pending?: boolean;
}) {
  return (
    <span className="cost-amount">
      <span>
        {cost.estimated_requests ? "≈ " : ""}
        {dollars(cost.known_usd)}
      </span>
      {(cost.pending_requests > 0 || pending) && (
        <span
          className="cost-pending"
          title={`${cost.pending_requests} recorded requests have unconfirmed cost${pending ? "; processing is still active" : ""}`}
        >
          Pending
        </span>
      )}
      {cost.estimated_requests > 0 && (
        <span className="cost-pending">Includes estimate</span>
      )}
    </span>
  );
}
export function PercentChange({ value }: { value: number | null }) {
  if (value === null)
    return <span className="metric-note">No comparable prior spending</span>;
  return (
    <span className={"metric-trend " + (value <= 0 ? "favorable" : "neutral")}>
      {value <= 0 ? <ArrowDownIcon /> : <ArrowUpIcon />}
      {Math.abs(value).toFixed(1)}% <span>vs previous period</span>
    </span>
  );
}
export function DailyChart({ data }: { data: Spending }) {
  const values = data.daily.map((d) => Number(d.known_usd)),
    max = Math.max(...values, 0),
    height = 140,
    width = 500,
    left = 53,
    top = 12,
    plotWidth = width - left - 8,
    plotHeight = height - top - 26,
    step = plotWidth / values.length;
  return (
    <>
      <div className="daily-chart">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`Daily recorded AI spending for the last ${data.days} days, UTC`}
        >
          <title>Daily AI spending, USD</title>
          <desc>
            {data.totals.pending_requests
              ? "Some costs are awaiting provider confirmation."
              : "Only recorded costs are shown."}{" "}
            No charges are inferred for empty days.
          </desc>
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line
                x1={left}
                x2={width - 5}
                y1={top + plotHeight * (1 - f)}
                y2={top + plotHeight * (1 - f)}
                stroke="var(--line)"
                strokeDasharray="3 4"
              />
              <text
                x={left - 8}
                y={top + plotHeight * (1 - f) + 4}
                textAnchor="end"
                className="chart-label"
              >
                {max ? dollars(max * f) : f === 0 ? "$0" : ""}
              </text>
            </g>
          ))}
          {data.daily.map((d, i) => {
            const h = max ? (values[i] / max) * plotHeight : 0;
            return (
              <g key={d.date}>
                <rect
                  x={left + i * step + step * 0.18}
                  y={top + plotHeight - h}
                  width={step * 0.64}
                  height={h}
                  rx={Math.min(5, step * 0.2)}
                  fill="var(--chart-blue)"
                  tabIndex={0}
                  aria-label={`${d.date}: ${dollars(d.known_usd)}${d.pending_requests ? ", pending charges" : ""}`}
                >
                  <title>
                    {d.date}: {dollars(d.known_usd)}
                    {d.pending_requests ? " (some costs pending)" : ""}
                  </title>
                </rect>
                {(data.days === 7 ||
                  i === 0 ||
                  i === data.daily.length - 1 ||
                  i % Math.ceil(data.daily.length / 5) === 0) && (
                  <text
                    x={left + (i + 0.5) * step}
                    y={height - 5}
                    textAnchor="middle"
                    className="chart-label"
                  >
                    {new Date(d.date + "T00:00:00Z").toLocaleDateString(
                      undefined,
                      { month: "short", day: "numeric", timeZone: "UTC" },
                    )}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {!max && (
          <p className="chart-empty">No recorded spending in this period.</p>
        )}
      </div>
      <details className="chart-data">
        <summary>View daily amounts</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Date (UTC)</th>
                <th>Recorded USD</th>
                <th>Pending requests</th>
              </tr>
            </thead>
            <tbody>
              {data.daily.map((d) => (
                <tr key={d.date}>
                  <td>{d.date}</td>
                  <td>{dollars(d.known_usd)}</td>
                  <td>{d.pending_requests}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
export function SpendingCard({
  data,
  days,
  onDays,
  children,
}: {
  data: Spending;
  days: number;
  onDays: (v: number) => void;
  children?: React.ReactNode;
}) {
  return (
    <section className="surface spending-card">
      <div className="section-heading">
        <h2>Spend overview</h2>
        <label className="range-select">
          <span className="sr-only">Spending date range</span>
          <select value={days} onChange={(e) => onDays(Number(e.target.value))}>
            {[7, 30, 90].map((n) => (
              <option key={n} value={n}>
                Last {n} days
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="spend-total">
        <CostAmount cost={data.totals} />
      </div>
      <PercentChange value={data.change_percent} />
      <DailyChart data={data} />
      <p className="accounting-note">
        USD · UTC calendar days
        {data.totals.pending_requests > 0
          ? " · Unconfirmed charges excluded from amounts"
          : ""}
        {data.totals.estimated_requests > 0
          ? " · Includes labeled estimates"
          : ""}
      </p>
      {children}
    </section>
  );
}
