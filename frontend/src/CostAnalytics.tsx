import { useEffect, useState } from "react";
import {
  CaretDownIcon,
  CaretRightIcon,
  DownloadSimpleIcon,
  ChartBarIcon,
  CheckCircleIcon,
  UsersIcon,
  DatabaseIcon,
} from "@phosphor-icons/react";
import { api, post } from "./api";
import {
  Cost,
  CostAmount,
  dollars,
  recordedTime,
  Spending,
  SpendingCard,
} from "./CostWidgets";

type Participant = {
  id: string;
  name: string;
  email: string;
  meetings: number;
  seconds: number;
  cost: Cost;
  average_usd: string | null;
};
type Row = {
  id: string;
  title: string;
  duration: number | null;
  status: string;
  cost: Cost;
};
type Analytics = {
  spending: Spending;
  totals: Cost;
  meetings: number;
  completed_meetings: number;
  active_participants: number;
  average_usd: string | null;
  participants: Participant[];
  participant_count: number;
  operations: Record<string, Cost>;
  models: { model: string; cost: Cost }[];
  preview_cost: Cost;
  local_tts: {
    generations: number;
    completed: number;
    failed: number;
    characters: number;
    audio_seconds: number;
    processing_seconds: number;
    peak_vram_mb: number;
    models: string[];
    external_api_cost_usd: string;
  };
};
function Breakdown({ operations }: { operations: Record<string, Cost> }) {
  const entries = [
      ["stt", "Speech to text", "stt"],
      ["llm", "LLM summarization", "llm"],
      ["tts", "Text to speech", "tts"],
    ],
    total = entries.reduce(
      (n, [op]) => n + Number(operations[op].known_usd),
      0,
    );
  return (
    <div className="cost-breakdown">
      <h3>Cost breakdown</h3>
      <div
        className="breakdown-track"
        role="img"
        aria-label="Recorded cost by AI service"
      >
        {entries.map(([op, label, tone]) => (
          <span
            key={op}
            className={tone}
            style={{
              width: `${total ? (Number(operations[op].known_usd) / total) * 100 : 0}%`,
            }}
            title={label + ": " + dollars(operations[op].known_usd)}
          />
        ))}
      </div>
      <div className="breakdown-legend">
        {entries.map(([op, label, tone]) => (
          <div key={op}>
            <span className={"legend-dot " + tone} />
            <span>{label}</span>
            <CostAmount cost={operations[op]} />
            <small>
              {total
                ? ((Number(operations[op].known_usd) / total) * 100).toFixed(1)
                : "0"}
              %
            </small>
          </div>
        ))}
      </div>
      <p className="accounting-note">
        Percentages apply to recorded amounts. Unconfirmed charges remain
        pending.
      </p>
    </div>
  );
}
function ParticipantRows({
  person,
  days,
}: {
  person: Participant;
  days: number;
}) {
  const [open, setOpen] = useState(false),
    [rows, setRows] = useState<Row[] | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (!open || rows) return;
    const controller = new AbortController();
    api<Row[]>(`/admin/analytics/users/${person.id}/meetings?days=${days}`, {
      signal: controller.signal,
    })
      .then(setRows)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [open, rows, person.id, days]);
  return (
    <>
      <tr>
        <td>
          <button
            className="participant-toggle"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-label={"View meetings for " + person.name}
          >
            {open ? <CaretDownIcon /> : <CaretRightIcon />}
            <span>
              <strong>{person.name}</strong>
              <small>{person.email}</small>
            </span>
          </button>
        </td>
        <td>{person.meetings}</td>
        <td>{recordedTime(person.seconds)}</td>
        <td>
          <CostAmount cost={person.cost} />
        </td>
        <td>
          {person.average_usd === null
            ? "Not available"
            : dollars(person.average_usd)}
          {person.cost.pending_requests > 0 && (
            <small className="cell-note">Partial</small>
          )}
        </td>
      </tr>
      {open && (
        <tr className="expanded-row">
          <td colSpan={5}>
            {error ? (
              <p role="alert">{error}</p>
            ) : !rows ? (
              <p role="status">Loading meeting costs…</p>
            ) : !rows.length ? (
              <p>No meetings or recorded usage in this period.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Meeting</th>
                    <th>Duration</th>
                    <th>Status</th>
                    <th>Cost in selected period</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>{r.title}</td>
                      <td>
                        {r.duration === null
                          ? "Not retained"
                          : recordedTime(r.duration)}
                      </td>
                      <td>{r.status.replaceAll("_", " ")}</td>
                      <td>
                        <CostAmount cost={r.cost} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function CostAnalytics() {
  const [days, setDays] = useState(30),
    [q, setQ] = useState(""),
    [offset, setOffset] = useState(0),
    [data, setData] = useState<Analytics | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [version, setVersion] = useState(0),
    [reconciling, setReconciling] = useState(false);
  useEffect(() => {
    setLoading(true);
    const controller = new AbortController();
    const timer = setTimeout(
      () =>
        api<Analytics>(
          `/admin/analytics/costs?days=${days}&q=${encodeURIComponent(q)}&offset=${offset}`,
          { signal: controller.signal },
        )
          .then((d) => {
            setData(d);
            setLoading(false);
            setError("");
          })
          .catch((e) => {
            if (!controller.signal.aborted) {
              setError(e.message);
              setLoading(false);
            }
          }),
      200,
    );
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [days, q, offset, version]);
  async function reconcile() {
    setReconciling(true);
    try {
      await post("/admin/usage/reconcile");
      setVersion((v) => v + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setReconciling(false);
    }
  }
  return (
    <div className="page cost-analytics-page" aria-busy={loading}>
      <div className="page-heading">
        <div>
          <p className="eyebrow">ADMINISTRATOR ANALYTICS</p>
          <h1>A clear view of AI spending.</h1>
          <p>
            Verified costs, shared usage, and the details behind every meeting.
          </p>
        </div>
        <div className="button-row">
          <button
            className="button secondary"
            disabled={reconciling}
            onClick={reconcile}
          >
            {reconciling ? "Checking costs…" : "Reconcile provider costs"}
          </button>
          <a
            className="button"
            href={`/api/admin/analytics/export?days=${days}`}
          >
            <DownloadSimpleIcon />
            Export ledger CSV
          </a>
        </div>
      </div>
      {error && (
        <div className="notice" role="alert">
          {error}
          <button
            className="button secondary"
            onClick={() => {
              setError("");
              setVersion((v) => v + 1);
            }}
          >
            Retry
          </button>
        </div>
      )}
      {data && loading && (
        <p className="analytics-update" role="status">
          Updating analytics…
        </p>
      )}
      {!data ? (
        !error && (
          <div className="skeleton" aria-label="Loading">
            <div />
            <div />
            <div />
          </div>
        )
      ) : (
        <>
          <div className="stats overview-stats admin-cost-stats">
            {[
              {
                label: "Platform AI cost",
                value: <CostAmount cost={data.totals} />,
                icon: <DatabaseIcon />,
              },
              {
                label: "Completed meetings",
                value: data.completed_meetings,
                icon: <CheckCircleIcon />,
              },
              {
                label: "Average per meeting",
                value:
                  data.average_usd === null
                    ? "Not available"
                    : dollars(data.average_usd),
                icon: <ChartBarIcon />,
              },
              {
                label: "Active participants",
                value: data.active_participants,
                icon: <UsersIcon />,
              },
            ].map((s) => (
              <div key={s.label}>
                <span className="stat-icon blue">{s.icon}</span>
                <div>
                  <span>{s.label}</span>
                  <strong>{s.value}</strong>
                  <small className="metric-note">
                    Selected {data.spending.days}-day period
                    {data.totals.pending_requests &&
                    s.label === "Average per meeting"
                      ? " · partial"
                      : ""}
                  </small>
                </div>
              </div>
            ))}
          </div>
          <div className="admin-accounting-note">
            Recorded costs include all matching requests, including repeated
            summaries and narration.{" "}
            {data.totals.pending_requests > 0
              ? `${data.totals.pending_requests} charges are awaiting provider confirmation. `
              : ""}
            {data.totals.estimated_requests > 0
              ? "Estimated amounts are labeled. "
              : ""}
            Meeting counts include meetings created or charged in this period;
            deleted content retains its accounting.
          </div>
          <div className="analytics-columns">
            <section className="surface participant-card">
              <div className="section-heading">
                <h2>Participant costs</h2>
                <label className="search">
                  <span className="sr-only">Search participants</span>
                  <input
                    type="search"
                    placeholder="Search name or email…"
                    value={q}
                    onChange={(e) => {
                      setQ(e.target.value);
                      setOffset(0);
                    }}
                  />
                </label>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Participant</th>
                      <th>Meetings</th>
                      <th>Duration</th>
                      <th>Total cost</th>
                      <th>Average / meeting</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.participants.map((p) => (
                      <ParticipantRows
                        key={p.id + "-" + days}
                        person={p}
                        days={days}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              {!data.participants.length && (
                <p className="empty-search">No matching participants.</p>
              )}
              <div className="pagination">
                <button
                  className="button secondary"
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 100))}
                >
                  Previous
                </button>
                <span>
                  {data.participant_count
                    ? `${offset + 1}–${Math.min(offset + 100, data.participant_count)} of ${data.participant_count}`
                    : "0 participants"}
                </span>
                <button
                  className="button secondary"
                  disabled={offset + 100 >= data.participant_count}
                  onClick={() => setOffset(offset + 100)}
                >
                  Next
                </button>
              </div>
            </section>
            <SpendingCard
              data={data.spending}
              days={days}
              onDays={(v) => {
                setDays(v);
                setOffset(0);
              }}
            >
              <Breakdown operations={data.operations} />
              <div className="voice-preview-cost">
                <div>
                  <strong>Standalone voice previews</strong>
                  <p>
                    <CostAmount cost={data.preview_cost} /> · Included in
                    platform TTS spending, separately from meeting costs.
                  </p>
                </div>
              </div>
            </SpendingCard>
          </div>
          <section className="surface model-cost-card">
            <div className="section-heading">
              <h2>Modal GPU narration</h2>
              <span className="subtle small">Separate from API spending</span>
            </div>
            <p>
              {data.local_tts.completed} completed · {data.local_tts.failed}{" "}
              failed · {data.local_tts.characters.toLocaleString()} characters ·{" "}
              {Math.round(data.local_tts.audio_seconds)} seconds of audio
            </p>
            <p className="subtle small">
              {Math.round(data.local_tts.processing_seconds)} seconds processing
              · {Math.round(data.local_tts.peak_vram_mb)} MB peak GPU memory · $
              {data.local_tts.external_api_cost_usd} estimated from published
              GPU rates. Modal invoices may differ.
            </p>
            <p className="subtle small">
              {data.local_tts.models.join(", ") ||
                "No GPU generations in this period."}
            </p>
          </section>
          <section className="surface model-cost-card">
            <div className="section-heading">
              <h2>Model-level costs</h2>
              <span className="subtle small">
                Last {data.spending.days} days
              </span>
            </div>
            {data.models.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Requests</th>
                      <th>Actual USD</th>
                      <th>Estimated USD</th>
                      <th>Pending</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.models.map((m) => (
                      <tr key={m.model}>
                        <td>
                          <code>{m.model}</code>
                        </td>
                        <td>{m.cost.requests}</td>
                        <td>{dollars(m.cost.actual_usd)}</td>
                        <td>{dollars(m.cost.estimated_usd)}</td>
                        <td>{m.cost.pending_requests}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="empty-search">
                No provider usage recorded in this period.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
