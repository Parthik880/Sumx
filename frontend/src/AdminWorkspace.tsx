import { useEffect, useState } from "react";
import {
  Link,
  NavLink,
  Outlet,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ChartBarIcon,
  DownloadSimpleIcon,
  MagnifyingGlassIcon,
  PlusIcon,
  ShieldCheckIcon,
  SignOutIcon,
  UsersIcon,
  WaveformIcon,
} from "@phosphor-icons/react";
import { api, ApiError, date, duration, post, User } from "./api";
import "./admin.css";

type Money = {
  known_usd: string | null;
  status: string;
  requests: number;
  pending_requests: number;
  estimated_requests: number;
  unrecorded_meetings: number;
  currency: string;
};
type Costs = Record<"total" | "llm" | "stt" | "tts", Money>;
type Person = {
  id: string;
  name: string | null;
  email: string;
  picture: string | null;
  registered: boolean;
  status: string;
  created: number | null;
  last_login: number | null;
  approved_at: number | null;
  meetings: number;
  costs: Costs;
};
type MeetingRow = {
  id: string | null;
  title: string;
  created: number | null;
  duration: number | null;
  status: string;
  costs: Costs;
};
type Overview = {
  registered_users: number;
  accounts: number;
  meetings: number;
  costs: Costs;
};
type Approval = {
  id: string;
  email: string;
  active: boolean;
  approved_at: number | null;
  registered: boolean;
};
const labels = {
  total: "Total AI cost",
  llm: "LLM cost",
  stt: "Speech-to-text cost",
  tts: "Text-to-speech cost",
};

function ErrorNotice({ error }: { error: string }) {
  return error ? (
    <div className="notice" role="alert">
      {error}
    </div>
  ) : null;
}
function Loader() {
  return (
    <div className="skeleton" aria-label="Loading administration">
      <div />
      <div />
      <div />
    </div>
  );
}
function useData<T>(path: string, revision = 0) {
  const [state, setState] = useState<{
    data: T | null;
    error: string;
    loading: boolean;
  }>({ data: null, error: "", loading: true });
  useEffect(() => {
    const controller = new AbortController();
    setState({ data: null, error: "", loading: true });
    api<T>(path, { signal: controller.signal })
      .then((data) => setState({ data, error: "", loading: false }))
      .catch((error) => {
        if (!controller.signal.aborted)
          setState({ data: null, error: error.message, loading: false });
      });
    return () => controller.abort();
  }, [path, revision]);
  return state;
}
function MoneyValue({
  value,
  detail = false,
}: {
  value: Money;
  detail?: boolean;
}) {
  const amount =
    value.known_usd === null
      ? value.status === "no_activity"
        ? "No usage"
        : "Unavailable"
      : new Intl.NumberFormat(undefined, {
          style: "currency",
          currency: value.currency,
          minimumFractionDigits: 2,
          maximumFractionDigits: 6,
        }).format(Number(value.known_usd));
  return (
    <span className="admin-money">
      <span>{amount}</span>
      {value.status === "partial" || value.status === "estimated" ? (
        <small>
          {value.status === "partial"
            ? "Partial · known costs"
            : "Includes estimates"}
        </small>
      ) : null}
      {detail && (
        <small>
          {value.requests} recorded operations
          {value.pending_requests > 0
            ? ` · ${value.pending_requests} unknown`
            : ""}
        </small>
      )}
    </span>
  );
}
function Status({ text }: { text: string }) {
  return (
    <span
      className={`admin-status ${text.startsWith("Approved") ? "approved" : text === "Revoked" ? "revoked" : ""}`}
    >
      {text}
    </span>
  );
}
function Pagination({
  offset,
  total,
  limit = 25,
  onChange,
}: {
  offset: number;
  total: number;
  limit?: number;
  onChange: (n: number) => void;
}) {
  return (
    <div className="admin-pagination">
      <span>
        {total
          ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`
          : "0 results"}
      </span>
      <div>
        <button
          className="button secondary"
          disabled={!offset}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </button>
        <button
          className="button secondary"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
function Range({
  value,
  change,
}: {
  value: string;
  change: (value: string) => void;
}) {
  return (
    <label className="admin-range">
      Date range
      <select
        aria-label="Admin date range"
        value={value}
        onChange={(e) => change(e.target.value)}
      >
        <option value="0">All time</option>
        <option value="7">Last 7 days</option>
        <option value="30">Last 30 days</option>
        <option value="90">Last 90 days</option>
      </select>
    </label>
  );
}
function CostCards({ costs }: { costs: Costs }) {
  return (
    <>
      {(["total", "llm", "stt", "tts"] as const).map((op) => (
        <div className="admin-stat" key={op}>
          <span>{labels[op]}</span>
          <strong>
            <MoneyValue value={costs[op]} />
          </strong>
          <small>
            {costs[op].status === "actual"
              ? "Recorded external API charges · USD"
              : costs[op].status === "no_activity"
                ? "No recorded operations"
                : "Unrecorded costs excluded"}
          </small>
        </div>
      ))}
    </>
  );
}
function CostNote() {
  return (
    <p className="admin-accounting-note">
      Totals use persisted USD charges, including voice previews. Unknown costs
      are excluded and labeled; estimates remain marked. Local Qwen inference
      has no external API charge. Hardware, hosting and Modal charges are not
      allocated. Dates use UTC.
    </p>
  );
}

export function AdminLogin({
  user,
  reload,
}: {
  user: User | null;
  reload: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [retryAt, setRetryAt] = useState(0),
    [remaining, setRemaining] = useState(0);
  useEffect(() => {
    if (!retryAt) return;
    const update = () => {
      const seconds = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
      setRemaining(seconds);
      if (!seconds) {
        setRetryAt(0);
        setError("");
      }
    };
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [retryAt]);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || retryAt > Date.now()) return;
    const values = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await post("/admin/login", {
        admin_id: values.get("admin_id"),
        password: values.get("password"),
      });
      await reload();
      navigate("/admin/overview", { replace: true });
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && e.status === 429) {
        const seconds = Math.max(1, e.retryAfter || 900);
        setRemaining(seconds);
        setRetryAt(Date.now() + seconds * 1000);
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-page admin-login">
      <Link className="brand" to="/">
        <span className="brand-mark">
          <WaveformIcon size={23} />
        </span>
        sumx<span className="brand-period">.</span>
      </Link>
      <section className="login-card">
        <span className="empty-icon">
          <ShieldCheckIcon size={30} />
        </span>
        <p className="eyebrow">SUMX ADMINISTRATION</p>
        <h1>Your workspace, in view.</h1>
        <p>
          Sign in with your Admin ID and password to manage access and review
          usage.
        </p>
        {user && (
          <p className="small">
            Your regular SUMX session does not grant administrator access.
          </p>
        )}
        <ErrorNotice
          error={
            remaining
              ? `Too many sign-in attempts. Try again in ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}.`
              : error
          }
        />
        <form onSubmit={submit}>
          <label>
            Admin ID
            <input
              name="admin_id"
              required
              maxLength={100}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
            />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              required
              maxLength={1024}
              autoComplete="current-password"
            />
          </label>
          <button
            className="button"
            type="submit"
            disabled={busy || remaining > 0}
          >
            {remaining
              ? "Please wait to try again"
              : busy
                ? "Signing in…"
                : "Sign in to administration"}
            <ArrowRightIcon />
          </button>
        </form>
        <Link className="text-link" to="/">
          Back to SUMX
        </Link>
      </section>
    </main>
  );
}

export function AdminLayout({
  user,
  reload,
}: {
  user: User;
  reload: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  useEffect(() => {
    const check = () => {
      void api("/admin/session").catch(() => reload());
    };
    const timer = setInterval(check, 60000);
    window.addEventListener("focus", check);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", check);
    };
  }, [reload]);
  async function logout() {
    try {
      await post("/admin/logout");
      await reload();
      navigate("/admin", { replace: true });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="app-shell admin-shell">
      <a href="#admin-main" className="skip">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link className="brand" to="/admin/overview">
          <span className="brand-mark">
            <WaveformIcon weight="bold" size={23} />
          </span>
          sumx<span className="brand-period">.</span>
        </Link>
        <div className="workspace-label">ADMINISTRATION</div>
        <nav aria-label="Administration">
          <NavLink to="/admin/overview">
            <ChartBarIcon />
            Overview
          </NavLink>
          <NavLink to="/admin/users/add">
            <UsersIcon />
            Add / manage users
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="admin-security">
            <ShieldCheckIcon />
            <span>Password-protected administration</span>
          </div>
          <div className="account">
            <span className="avatar">
              <ShieldCheckIcon />
            </span>
            <div>
              <strong>{user.admin_id || user.name}</strong>
              <small>Administrator</small>
            </div>
          </div>
          <button className="button secondary admin-logout" onClick={logout}>
            <SignOutIcon />
            Logout
          </button>
        </div>
      </aside>
      <div className="app-body">
        <header className="topbar">
          <span>Workspace administration</span>
          <div className="topbar-end">
            <ShieldCheckIcon />
            <span>Administrator access</span>
          </div>
        </header>
        <main id="admin-main" tabIndex={-1}>
          <ErrorNotice error={error} />
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function AdminOverview() {
  const [params, setParams] = useSearchParams();
  const days = params.get("days") || "0",
    q = params.get("q") || "",
    sort = params.get("sort") || "name",
    direction = params.get("direction") || "asc";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const [revision, setRevision] = useState(0),
    [actionError, setActionError] = useState(""),
    [reconciling, setReconciling] = useState(false);
  const overview = useData<Overview>(`/admin/overview?days=${days}`, revision);
  const query = new URLSearchParams({ days, q, sort, direction });
  const directory = useData<{ users: Person[]; total: number }>(
    `/admin/users?${query}&offset=${offset}`,
    revision,
  );
  function filter(key: string, value: string) {
    const next = new URLSearchParams(params);
    next.set(key, value);
    if (key !== "offset") next.delete("offset");
    setParams(next, { replace: true });
  }
  function order(key: string) {
    const next = new URLSearchParams(params);
    next.set("sort", key);
    next.set("direction", sort === key && direction === "asc" ? "desc" : "asc");
    next.delete("offset");
    setParams(next, { replace: true });
  }
  async function reconcile() {
    setReconciling(true);
    setActionError("");
    try {
      await post("/admin/usage/reconcile");
      setRevision((n) => n + 1);
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setReconciling(false);
    }
  }
  return (
    <div className="page admin-workspace">
      <div className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE OVERVIEW</p>
          <h1>A clear view of your workspace.</h1>
          <p>People, meetings and AI spending, all in one place.</p>
        </div>
        <Link className="button" to="/admin/users/add">
          <PlusIcon />
          Add user
        </Link>
      </div>
      <div className="admin-toolbar">
        <Range value={days} change={(v) => filter("days", v)} />
        <button
          className="button secondary"
          disabled={reconciling}
          onClick={reconcile}
        >
          {reconciling ? "Verifying…" : "Refresh verified costs"}
        </button>
        <Link className="text-link" to="/admin/analytics">
          Detailed model analytics
          <ArrowRightIcon />
        </Link>
      </div>
      <ErrorNotice error={overview.error || directory.error || actionError} />
      {overview.loading ? (
        <Loader />
      ) : (
        overview.data && (
          <div className="admin-stats">
            <div className="admin-stat">
              <span>Total registered users</span>
              <strong>{overview.data.registered_users}</strong>
              <small>
                {overview.data.accounts} accounts including approvals
              </small>
            </div>
            <div className="admin-stat">
              <span>Total meetings</span>
              <strong>{overview.data.meetings}</strong>
              <small>Across all accounts in this period</small>
            </div>
            <CostCards costs={overview.data.costs} />
          </div>
        )
      )}
      <CostNote />
      <section
        className="surface admin-directory"
        aria-busy={directory.loading}
      >
        <div className="section-heading">
          <div>
            <h2>Users &amp; usage</h2>
            <p className="small">
              Select a person to inspect their meetings and cost records.
            </p>
          </div>
          <a
            className="button secondary"
            href={`/api/admin/users/export?${query}`}
          >
            <DownloadSimpleIcon />
            Export All Users CSV
          </a>
        </div>
        <label className="admin-search">
          <MagnifyingGlassIcon />
          <input
            aria-label="Search users"
            placeholder="Search by name or email"
            value={q}
            onChange={(e) => filter("q", e.target.value)}
          />
        </label>
        {directory.loading ? (
          <Loader />
        ) : (
          directory.data && (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      {[
                        ["name", "User name"],
                        ["email", "Email"],
                        ["meetings", "Total meetings"],
                        ["llm", "LLM cost"],
                        ["stt", "STT cost"],
                        ["tts", "TTS cost"],
                        ["total", "Total cost"],
                      ].map(([key, label]) => (
                        <th
                          key={key}
                          aria-sort={
                            sort === key
                              ? direction === "asc"
                                ? "ascending"
                                : "descending"
                              : "none"
                          }
                        >
                          <button
                            className="admin-sort"
                            onClick={() => order(key)}
                          >
                            {label}
                            {sort === key
                              ? direction === "asc"
                                ? " ↑"
                                : " ↓"
                              : " ↕"}
                          </button>
                        </th>
                      ))}
                      <th>Account status</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {directory.data.users.map((person) => (
                      <tr key={person.id}>
                        <td>
                          <strong>{person.name || "Not yet registered"}</strong>
                        </td>
                        <td>{person.email}</td>
                        <td>{person.meetings}</td>
                        {(["llm", "stt", "tts", "total"] as const).map((op) => (
                          <td key={op}>
                            <MoneyValue value={person.costs[op]} />
                          </td>
                        ))}
                        <td>
                          <Status text={person.status} />
                        </td>
                        <td>
                          <Link
                            className="text-link"
                            to={`/admin/users/${person.id}?days=${days}`}
                          >
                            View Details
                            <ArrowRightIcon />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!directory.data.total && (
                <div className="admin-empty">No users match this search.</div>
              )}
              <Pagination
                offset={offset}
                total={directory.data.total}
                onChange={(n) => filter("offset", String(n))}
              />
            </>
          )
        )}
      </section>
    </div>
  );
}

function Breakdown({ costs }: { costs: Costs }) {
  const entries = (["llm", "stt", "tts"] as const).map((op) => ({
    op,
    amount: Number(costs[op].known_usd || 0),
  }));
  const maximum = Math.max(...entries.map((e) => e.amount), 0);
  return (
    <section className="surface admin-breakdown">
      <h2>Recorded cost breakdown</h2>
      <svg
        viewBox="0 0 600 160"
        role="img"
        aria-label="Recorded LLM, speech-to-text and text-to-speech costs in USD"
      >
        {entries.map(({ op, amount }, i) => (
          <g key={op}>
            <text x="0" y={i * 50 + 27}>
              {op.toUpperCase()}
            </text>
            <rect
              className="admin-chart-track"
              x="65"
              y={i * 50 + 9}
              width="400"
              height="26"
              rx="6"
            />
            <rect
              className={`admin-chart-${op}`}
              x="65"
              y={i * 50 + 9}
              width={maximum ? (amount / maximum) * 400 : 0}
              height="26"
              rx="6"
            />
            <text x="480" y={i * 50 + 27}>
              {costs[op].known_usd === null
                ? "Unavailable"
                : `$${amount.toFixed(6)}`}
            </text>
          </g>
        ))}
      </svg>
      <p className="small">
        Bar lengths compare known recorded costs. Unavailable amounts have no
        bar.
      </p>
    </section>
  );
}
type UsageRow = {
  id: string;
  operation: string;
  provider: string;
  model: string;
  generation_id: string | null;
  request_id: string | null;
  created: number;
  cost: string | null;
  currency: string;
  cost_status: string;
  cost_source: string;
  generation_status: string;
  purpose: string;
  units: Record<string, unknown>;
};
function UsageDetails({
  userId,
  meetingId,
  days,
}: {
  userId: string;
  meetingId: string | null;
  days: string;
}) {
  const [offset, setOffset] = useState(0);
  const result = useData<{ usage: UsageRow[]; total: number }>(
    `/admin/users/${userId}/usage?days=${days}&meeting_id=${encodeURIComponent(meetingId || "unlinked")}&offset=${offset}&limit=25`,
  );
  return (
    <section className="admin-ledger" aria-label="Meeting usage records">
      <h3>Persisted usage records</h3>
      <ErrorNotice error={result.error} />
      {result.loading ? (
        <Loader />
      ) : (
        result.data && (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Service / provider</th>
                    <th>Model / request</th>
                    <th>Usage</th>
                    <th>Cost / source</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {result.data.usage.map((u) => (
                    <tr key={u.id}>
                      <td>{new Date(u.created * 1000).toLocaleString()}</td>
                      <td>
                        {u.operation.toUpperCase()}
                        <small>{u.provider}</small>
                        <small>{u.purpose}</small>
                      </td>
                      <td>
                        <code>{u.model}</code>
                        <small>
                          {u.generation_id ||
                            u.request_id ||
                            "No provider request ID"}
                        </small>
                      </td>
                      <td>
                        {Object.entries(u.units).map(([key, value]) => (
                          <small key={key}>
                            {key.replaceAll("_", " ")}:{" "}
                            {typeof value === "object"
                              ? JSON.stringify(value)
                              : String(value)}
                          </small>
                        ))}
                      </td>
                      <td>
                        {u.cost === null
                          ? "Unavailable"
                          : `${u.cost} ${u.currency}`}
                        <small>
                          {u.cost_status} · {u.cost_source}
                        </small>
                      </td>
                      <td>{u.generation_status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!result.data.total && (
              <p>No usage records are available for this meeting.</p>
            )}
            <Pagination
              offset={offset}
              total={result.data.total}
              onChange={setOffset}
            />
          </>
        )
      )}
    </section>
  );
}

export function AdminUserDetail() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams(),
    days = params.get("days") || "0";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const [selected, setSelected] = useState<string | null | undefined>(
    undefined,
  );
  const person = useData<Person>(`/admin/users/${id}?days=${days}`);
  const records = useData<{ meetings: MeetingRow[]; total: number }>(
    `/admin/users/${id}/meetings?days=${days}&offset=${offset}`,
  );
  function change(key: string, value: string) {
    const next = new URLSearchParams(params);
    next.set(key, value);
    if (key === "days") next.delete("offset");
    setSelected(undefined);
    setParams(next, { replace: true });
  }
  return (
    <div className="page admin-workspace">
      <Link
        className="text-link admin-back"
        to={`/admin/overview?days=${days}`}
      >
        <ArrowLeftIcon />
        Back to Overview
      </Link>
      <ErrorNotice error={person.error || records.error} />
      {person.loading ? (
        <Loader />
      ) : (
        person.data && (
          <>
            <div className="page-heading admin-profile">
              <div className="admin-person">
                {person.data.picture ? (
                  <img
                    src={person.data.picture}
                    alt=""
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <span className="avatar">
                    {(person.data.name || person.data.email)[0].toUpperCase()}
                  </span>
                )}
                <div>
                  <p className="eyebrow">USER DETAILS</p>
                  <h1>{person.data.name || "Not yet registered"}</h1>
                  <p>{person.data.email}</p>
                  <Status text={person.data.status} />
                </div>
              </div>
              <a
                className="button secondary"
                href={`/api/admin/users/${id}/export?days=${days}`}
              >
                <DownloadSimpleIcon />
                Export User CSV
              </a>
            </div>
            <div className="admin-profile-meta">
              <span>
                Created{" "}
                <strong>
                  {person.data.created
                    ? date(person.data.created)
                    : "Not yet registered"}
                </strong>
              </span>
              <span>
                Last login{" "}
                <strong>
                  {person.data.last_login
                    ? new Date(person.data.last_login * 1000).toLocaleString()
                    : "Not recorded"}
                </strong>
              </span>
              <span>
                Total meetings <strong>{person.data.meetings}</strong>
              </span>
              <Range value={days} change={(v) => change("days", v)} />
            </div>
            <div className="admin-stats detail-stats">
              <CostCards costs={person.data.costs} />
            </div>
            <CostNote />
            <Breakdown costs={person.data.costs} />
          </>
        )
      )}
      <section className="surface admin-meetings">
        <h2>Meetings &amp; associated usage</h2>
        <p className="small">
          Voice previews and retained costs from deleted meetings are listed
          separately so all totals reconcile.
        </p>
        {records.loading ? (
          <Loader />
        ) : (
          records.data && (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Meeting</th>
                      <th>Date &amp; time</th>
                      <th>Duration</th>
                      <th>Status</th>
                      <th>LLM cost</th>
                      <th>STT cost</th>
                      <th>TTS cost</th>
                      <th>Total cost</th>
                      <th>Usage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.data.meetings.map((m) => (
                      <tr key={m.id || "unlinked"}>
                        <td>
                          <strong>{m.title}</strong>
                          <small>
                            <code>{m.id || "No meeting · previews"}</code>
                          </small>
                        </td>
                        <td>
                          {m.created
                            ? new Date(m.created * 1000).toLocaleString()
                            : "Not recorded"}
                        </td>
                        <td>
                          {m.duration === null
                            ? "Unavailable"
                            : duration(m.duration)}
                        </td>
                        <td>
                          <span className="status">{m.status}</span>
                        </td>
                        {(["llm", "stt", "tts", "total"] as const).map((op) => (
                          <td key={op}>
                            <MoneyValue value={m.costs[op]} />
                          </td>
                        ))}
                        <td>
                          <button
                            className="text-link admin-sort"
                            onClick={() => setSelected(m.id)}
                          >
                            Inspect usage
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!records.data.total && (
                <div className="admin-empty">
                  No meetings or usage recorded in this period.
                </div>
              )}
              <Pagination
                offset={offset}
                total={records.data.total}
                onChange={(n) => change("offset", String(n))}
              />
            </>
          )
        )}
        {selected !== undefined && (
          <UsageDetails
            key={`${id}-${selected}-${days}`}
            userId={id}
            meetingId={selected}
            days={days}
          />
        )}
      </section>
    </div>
  );
}

export function AdminApprovals() {
  const [params, setParams] = useSearchParams(),
    q = params.get("q") || "";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const [email, setEmail] = useState(""),
    [revision, setRevision] = useState(0),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const result = useData<{ users: Approval[]; total: number }>(
    `/admin/approved-users?q=${encodeURIComponent(q)}&offset=${offset}`,
    revision,
  );
  async function act(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
      setRevision((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function filter(key: string, value: string) {
    const next = new URLSearchParams(params);
    next.set(key, value);
    if (key === "q") next.delete("offset");
    setParams(next, { replace: true });
  }
  return (
    <div className="page admin-workspace">
      <div className="page-heading">
        <div>
          <p className="eyebrow">ACCESS MANAGEMENT</p>
          <h1>The right people, approved.</h1>
          <p>
            Approve Google emails before their first sign-in. Keep control as
            your workspace grows.
          </p>
        </div>
      </div>
      <section className="surface admin-approval-form">
        <h2>Add an approved user</h2>
        <p className="small">
          Google verifies identity. SUMX controls access through this local
          approval list.
        </p>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              await post("/admin/approved-users", { email });
              setEmail("");
            }, "Email approved. This account can now sign in through Google.");
          }}
        >
          <label>
            Email address
            <input
              type="email"
              required
              maxLength={320}
              placeholder="person@example.edu"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <button className="button" type="submit" disabled={busy}>
            <PlusIcon />
            {busy ? "Saving…" : "Add User"}
          </button>
        </form>
        <ErrorNotice error={error} />
        {message && (
          <p className="admin-success" role="status">
            {message}
          </p>
        )}
      </section>
      <section className="surface">
        <div className="section-heading">
          <h2>Approved email addresses</h2>
          <span className="small">{result.data?.total ?? "…"} records</span>
        </div>
        <label className="admin-search">
          <MagnifyingGlassIcon />
          <input
            aria-label="Search approved emails"
            placeholder="Search email addresses"
            value={q}
            onChange={(e) => filter("q", e.target.value)}
          />
        </label>
        <ErrorNotice error={result.error} />
        {result.loading ? (
          <Loader />
        ) : (
          result.data && (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Email</th>
                      <th>Approval status</th>
                      <th>Date approved</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.data.users.map((a) => (
                      <tr key={a.id}>
                        <td>{a.email}</td>
                        <td>
                          <Status
                            text={
                              a.active
                                ? a.registered
                                  ? "Approved"
                                  : "Approved — Not Yet Registered"
                                : "Revoked"
                            }
                          />
                        </td>
                        <td>
                          {a.approved_at
                            ? new Date(a.approved_at * 1000).toLocaleString()
                            : "Not recorded · legacy approval"}
                        </td>
                        <td>
                          <button
                            className={`button secondary ${a.active ? "danger" : ""}`}
                            disabled={busy}
                            onClick={() =>
                              void act(
                                () =>
                                  a.active
                                    ? api(`/admin/approved-users/${a.id}`, {
                                        method: "DELETE",
                                      })
                                    : post("/admin/approved-users", {
                                        email: a.email,
                                      }),
                                a.active
                                  ? "Access revoked. Existing user sessions were ended; meeting history is preserved."
                                  : "Access restored.",
                              )
                            }
                          >
                            {a.active ? "Revoke Access" : "Restore Access"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!result.data.total && (
                <div className="admin-empty">
                  No approved emails match this search.
                </div>
              )}
              <Pagination
                offset={offset}
                total={result.data.total}
                onChange={(n) => filter("offset", String(n))}
              />
            </>
          )
        )}
      </section>
    </div>
  );
}
