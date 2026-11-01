import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowRightIcon,
  ArrowUpIcon,
  ArrowDownIcon,
  ClockIcon,
  DatabaseIcon,
  FileTextIcon,
  MicrophoneIcon,
  PlusIcon,
  SparkleIcon,
  UploadSimpleIcon,
  WaveformIcon,
} from "@phosphor-icons/react";
import { active, api, date, Meeting, User } from "./api";
import {
  Cost,
  CostAmount,
  mediaTime,
  PercentChange,
  recordedTime,
  Spending,
  SpendingCard,
} from "./CostWidgets";

type CostMeeting = Meeting & { cost: Cost; cost_pending: boolean };
type Stats = {
  meetings: number;
  seconds: number;
  summaries: number;
  cost: Cost;
  active_jobs: number;
  weekly: {
    meetings_delta: number | null;
    seconds_delta: number | null;
    summaries_delta: number | null;
    cost_change_percent: number | null;
  };
};
type Data = {
  meetings: CostMeeting[];
  total: number;
  stats: Stats;
  spending: Spending;
};
const status = (m: Meeting) =>
  active(m)
    ? m.status === "queued"
      ? "Queued"
      : "Processing"
    : m.status === "ready"
      ? "Completed"
      : m.status === "failed"
        ? "Failed"
        : "Not processed";
function Delta({
  value,
  time = false,
}: {
  value: number | null;
  time?: boolean;
}) {
  return value === null ? (
    <span className="metric-note">No previous-week history</span>
  ) : (
    <span className={"metric-trend " + (value >= 0 ? "favorable" : "neutral")}>
      {value >= 0 ? <ArrowUpIcon /> : <ArrowDownIcon />}
      {value >= 0 ? "+" : "−"}
      {time ? recordedTime(Math.abs(value)) : Math.abs(value)}{" "}
      <span>vs previous week</span>
    </span>
  );
}

export default function DashboardView({
  user,
  history = false,
}: {
  user: User;
  history?: boolean;
}) {
  const [params, setParams] = useSearchParams(),
    [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(""),
    [q, setQ] = useState(""),
    [sort, setSort] = useState("newest"),
    [days, setDays] = useState(7),
    [offset, setOffset] = useState(0),
    [refresh, setRefresh] = useState(0);
  const onlySummaries = params.get("summaries") === "1";
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const result = await api<Data>(
          history
            ? `/meetings?q=${encodeURIComponent(q)}&sort=${sort}&offset=${offset}&summaries_only=${onlySummaries}`
            : `/dashboard?q=${encodeURIComponent(q)}&sort=${sort}&days=${days}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setData(result);
        setError("");
        if (result.stats.active_jobs) timer = setTimeout(load, 3000);
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      }
    };
    timer = setTimeout(load, 200);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [history, q, sort, days, offset, onlySummaries, refresh]);
  return (
    <div
      className={
        "page dashboard-page " + (history ? "history-page" : "overview-page")
      }
    >
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            {history ? "YOUR CONVERSATIONS, TOGETHER" : "A LITTLE MORE CLARITY"}
          </p>
          <h1>
            {history
              ? "Meeting history"
              : `Welcome back, ${user.name.split(" ")[0]}.`}
          </h1>
          <p>
            {history
              ? "Find a thought. Revisit a decision. Keep moving forward."
              : "Be present for the conversation. We'll handle the notes, summaries, and insights."}
          </p>
        </div>
        <Link className="button" to="/record">
          <PlusIcon />
          New recording
        </Link>
      </div>
      {error && (
        <div className="notice" role="alert">
          {error}
          <button
            className="button secondary"
            onClick={() => {
              setError("");
              setRefresh((v) => v + 1);
            }}
          >
            Retry
          </button>
        </div>
      )}
      {!data ? (
        !error && (
          <div aria-label="Loading" className="skeleton">
            <div />
            <div />
            <div />
          </div>
        )
      ) : (
        <>
          {!history && (
            <>
              <div className="stats overview-stats">
                <div>
                  <span className="stat-icon blue">
                    <FileTextIcon />
                  </span>
                  <div>
                    <span>Total meetings</span>
                    <strong>{data.stats.meetings}</strong>
                    <Delta value={data.stats.weekly.meetings_delta} />
                  </div>
                </div>
                <div>
                  <span className="stat-icon blue">
                    <ClockIcon />
                  </span>
                  <div>
                    <span>Time captured</span>
                    <strong className="time-total">
                      {recordedTime(data.stats.seconds)}
                    </strong>
                    <Delta value={data.stats.weekly.seconds_delta} time />
                  </div>
                </div>
                <div>
                  <span className="stat-icon purple">
                    <SparkleIcon />
                  </span>
                  <div>
                    <span>Summaries ready</span>
                    <strong>{data.stats.summaries}</strong>
                    <Delta value={data.stats.weekly.summaries_delta} />
                  </div>
                </div>
                <div>
                  <span className="stat-icon blue">
                    <DatabaseIcon />
                  </span>
                  <div>
                    <span>Total AI cost</span>
                    <strong>
                      <CostAmount
                        cost={data.stats.cost}
                        pending={data.stats.active_jobs > 0}
                      />
                    </strong>
                    <PercentChange
                      value={data.stats.weekly.cost_change_percent}
                    />
                  </div>
                </div>
              </div>
              <div className="quick-actions overview-actions">
                {[
                  {
                    to: "/record",
                    icon: <MicrophoneIcon />,
                    tone: "blue",
                    title: "Start a recording",
                    text: "Record a meeting from your microphone.",
                  },
                  {
                    to: "/upload",
                    icon: <UploadSimpleIcon />,
                    tone: "purple",
                    title: "Upload a meeting",
                    text: "Upload audio or video, including MP4.",
                  },
                  {
                    to: "/history?summaries=1",
                    icon: <FileTextIcon />,
                    tone: "green",
                    title: "View recent summaries",
                    text: "Browse and revisit your generated meeting summaries.",
                  },
                ].map((a) => (
                  <Link to={a.to} key={a.to}>
                    <span className={"quick-icon " + a.tone}>{a.icon}</span>
                    <div>
                      <h2>{a.title}</h2>
                      <p>{a.text}</p>
                    </div>
                    <ArrowRightIcon />
                  </Link>
                ))}
              </div>
            </>
          )}
          <div className={history ? "" : "dashboard-columns"}>
            <section className="surface recent-card">
              <div className="section-heading">
                <h2>{history ? "All meetings" : "Recent meetings"}</h2>
                <div className="meeting-filters">
                  <label className="search">
                    <span className="sr-only">Search meetings</span>
                    <input
                      type="search"
                      placeholder="Search meetings…"
                      value={q}
                      onChange={(e) => {
                        setQ(e.target.value);
                        setOffset(0);
                      }}
                    />
                  </label>
                  <label className="sort-select">
                    <span className="sr-only">Sort meetings by date</span>
                    <select
                      value={sort}
                      onChange={(e) => {
                        setSort(e.target.value);
                        setOffset(0);
                      }}
                    >
                      <option value="newest">Newest first</option>
                      <option value="oldest">Oldest first</option>
                    </select>
                  </label>
                </div>
              </div>
              {history && (
                <label className="check summaries-filter">
                  <input
                    type="checkbox"
                    checked={onlySummaries}
                    onChange={(e) => {
                      setParams(e.target.checked ? { summaries: "1" } : {});
                      setOffset(0);
                    }}
                  />
                  Summaries only
                </label>
              )}
              {!data.stats.meetings ? (
                <div className="dashboard-empty">
                  <WaveformIcon size={32} />
                  <h2>
                    {history
                      ? "A fresh start for your meeting history."
                      : "Your next conversation, remembered."}
                  </h2>
                  <p>
                    Your meetings, summaries and recorded AI costs will appear
                    here.
                  </p>
                  <Link className="text-link" to="/record">
                    Record a meeting <ArrowRightIcon />
                  </Link>
                </div>
              ) : !data.meetings.length ? (
                <p className="empty-search">
                  {onlySummaries && !q
                    ? "No completed summaries yet."
                    : `No meetings match “${q}”.`}
                </p>
              ) : (
                <div className="table-scroll">
                  <table className="recent-table">
                    <thead>
                      <tr>
                        <th>Meeting title</th>
                        <th>Date</th>
                        <th>Duration</th>
                        <th>Status</th>
                        <th>Total cost</th>
                        <th>
                          <span className="sr-only">Open meeting</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.meetings.map((m) => (
                        <tr key={m.id}>
                          <td>
                            <Link
                              className="meeting-title-link"
                              to={"/meetings/" + m.id}
                            >
                              <span className="meeting-glyph">
                                <FileTextIcon />
                              </span>
                              <strong>{m.title}</strong>
                            </Link>
                          </td>
                          <td>{date(m.created)}</td>
                          <td className="duration-cell">
                            <ClockIcon />
                            {mediaTime(m.duration)}
                          </td>
                          <td>
                            <span
                              className={
                                "status " +
                                (active(m) ? "processing" : m.status)
                              }
                            >
                              {status(m)}
                            </span>
                          </td>
                          <td>
                            <CostAmount
                              cost={m.cost}
                              pending={m.cost_pending}
                            />
                          </td>
                          <td>
                            <Link
                              className="icon-button"
                              aria-label={"Open " + m.title}
                              to={"/meetings/" + m.id}
                            >
                              <ArrowRightIcon />
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="pagination">
                {history && data.total > 0 ? (
                  <>
                    <button
                      className="button secondary"
                      disabled={!offset}
                      onClick={() => setOffset(Math.max(0, offset - 100))}
                    >
                      Previous
                    </button>
                    <span>
                      {offset + 1}–{Math.min(offset + 100, data.total)} of{" "}
                      {data.total}
                    </span>
                    <button
                      className="button secondary"
                      disabled={offset + 100 >= data.total}
                      onClick={() => setOffset(offset + 100)}
                    >
                      Next
                    </button>
                  </>
                ) : !history && data.total > 6 ? (
                  <Link className="text-link" to="/history">
                    View all meetings <ArrowRightIcon />
                  </Link>
                ) : null}
              </div>
            </section>
            {!history && data.spending && (
              <SpendingCard data={data.spending} days={days} onDays={setDays} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
