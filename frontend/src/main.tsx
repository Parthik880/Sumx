import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useLocation,
  useParams,
} from "react-router-dom";
import {
  ArrowRightIcon,
  ChartBarIcon,
  UploadSimpleIcon,
  CheckIcon,
  CopyIcon,
  DownloadSimpleIcon,
  FileTextIcon,
  GearSixIcon,
  HouseIcon,
  ListBulletsIcon,
  MicrophoneIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RecordIcon,
  ShieldCheckIcon,
  SignOutIcon,
  SparkleIcon,
  StopIcon,
  SunIcon,
  TrashIcon,
  WaveformIcon,
} from "@phosphor-icons/react";
import {
  active,
  api,
  ApiError,
  Config,
  date,
  duration,
  Meeting,
  Minutes,
  post,
  setCsrf,
  setAuthFailureHandler,
  SummaryMode,
  SummaryOverview,
  SummaryRecord,
  upload,
  User,
} from "./api";
import "./style.css";
import "./analytics.css";
import DashboardView from "./Dashboard";
import CostAnalytics from "./CostAnalytics";
import { GoogleSignIn, disableGoogleAutoSelect } from "./GoogleSignIn";
import { VoiceStudio, VoiceCreator, NarrationPicker } from "./VoiceStudio";
import NarrationProgress from "./NarrationProgress";
import "./voices.css";
import {
  AdminLogin,
  AdminLayout,
  AdminOverview,
  AdminUserDetail,
  AdminApprovals,
} from "./AdminWorkspace";

type Context = {
  user: User | null;
  notify: (message: string) => void;
  reload: () => Promise<void>;
};
const AppContext = createContext<Context>(null!);
function useApp() {
  return useContext(AppContext);
}
function Brand() {
  return (
    <Link className="brand" to="/">
      <span className="brand-mark">
        <WaveformIcon weight="bold" size={23} />
      </span>
      sumx<span className="brand-period">.</span>
    </Link>
  );
}
function Button({
  children,
  onClick,
  secondary = false,
  disabled = false,
  type = "button",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  secondary?: boolean;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      className={"button " + (secondary ? "secondary" : "")}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="notice" role="status">
      {children}
    </div>
  );
}
function Loading() {
  return (
    <div aria-label="Loading" className="skeleton">
      <div />
      <div />
      <div />
    </div>
  );
}
function AccessRevoked({ onConfirm }: { onConfirm: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return (
    <main className="login-page">
      <Brand />
      <dialog
        ref={dialog}
        className="confirm"
        aria-labelledby="access-revoked-title"
        aria-describedby="access-revoked-description"
        onCancel={(event) => event.preventDefault()}
      >
        <h2 id="access-revoked-title">You are not approved</h2>
        <p id="access-revoked-description">
          Your access to SUMX has been revoked or is not approved. Contact your
          administrator for access.
        </p>
        <div className="button-row">
          <Button onClick={onConfirm}>OK</Button>
        </div>
      </dialog>
    </main>
  );
}
function App() {
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [toast, setToast] = useState(""),
    [accessRevoked, setAccessRevoked] = useState(false),
    [revocationAcknowledged, setRevocationAcknowledged] = useState(false);
  const userRef = useRef<User | null>(null),
    authEpoch = useRef(0),
    revokedNotice = useRef(false);
  const navigate = useNavigate(),
    location = useLocation();
  const userId = user?.id,
    userRole = user?.role;
  const reload = useCallback(async () => {
    const epoch = authEpoch.current;
    try {
      const u = await api<User>("/auth/me");
      if (epoch !== authEpoch.current) return;
      setCsrf(u.csrf);
      userRef.current = u;
      revokedNotice.current = false;
      setUser(u);
    } catch (error) {
      if (
        error instanceof ApiError &&
        (error.status === 401 || error.status === 403)
      ) {
        userRef.current = null;
        setUser(null);
        setCsrf("");
      }
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    setAuthFailureHandler((reason) => {
      if (reason !== "access_revoked" && !userRef.current) return;
      authEpoch.current += 1;
      userRef.current = null;
      setUser(null);
      setCsrf("");
      setToast("");
      disableGoogleAutoSelect();
      if (reason === "access_revoked" && !revokedNotice.current) {
        revokedNotice.current = true;
        setRevocationAcknowledged(false);
        setAccessRevoked(true);
      }
    });
    void reload();
    return () => setAuthFailureHandler(null);
  }, [reload]);
  useEffect(() => {
    if (!userId || userRole === "admin") return;
    let pending = false,
      disposed = false;
    const check = async () => {
      if (pending || disposed) return;
      pending = true;
      try {
        await reload();
      } finally {
        pending = false;
      }
    };
    const visible = () => {
      if (document.visibilityState === "visible") void check();
    };
    void check();
    const timer = setInterval(() => void check(), 5000);
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      disposed = true;
      clearInterval(timer);
      window.removeEventListener("focus", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [userId, userRole, location.pathname, reload]);
  useEffect(() => {
    if (revocationAcknowledged && location.pathname === "/") {
      setAccessRevoked(false);
      setRevocationAcknowledged(false);
    }
  }, [revocationAcknowledged, location.pathname]);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(""), 5000);
    return () => clearTimeout(id);
  }, [toast]);
  useEffect(() => {
    document.documentElement.dataset.theme = "light";
    localStorage.removeItem("sumx-theme");
  }, []);
  if (accessRevoked)
    return (
      <AccessRevoked
        onConfirm={() => {
          setRevocationAcknowledged(true);
          navigate("/", { replace: true });
        }}
      />
    );
  if (loading)
    return (
      <main className="boot">
        <Brand />
        <Loading />
      </main>
    );
  const isAdmin =
    user?.role === "admin" && user.auth_method === "admin_password";
  return (
    <AppContext.Provider value={{ user, reload, notify: setToast }}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/admin"
          element={
            isAdmin ? (
              <Navigate to="/admin/overview" replace />
            ) : (
              <AdminLogin user={user} reload={reload} />
            )
          }
        />
        <Route path="/admin/login" element={<Navigate to="/admin" replace />} />
        <Route
          element={
            isAdmin ? (
              <AdminLayout user={user!} reload={reload} />
            ) : (
              <Navigate to="/admin" replace />
            )
          }
        >
          <Route path="/admin/overview" element={<AdminOverview />} />
          <Route path="/admin/users/add" element={<AdminApprovals />} />
          <Route path="/admin/users/:id" element={<AdminUserDetail />} />
          <Route path="/admin/analytics" element={<CostAnalytics />} />
        </Route>
        <Route element={user ? <Shell /> : <Navigate to="/login" replace />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/history" element={<Dashboard history />} />
          <Route path="/record" element={<Capture recording />} />
          <Route path="/upload" element={<Capture />} />
          <Route path="/voices" element={<VoiceStudio />} />
          <Route path="/voices/new" element={<VoiceCreator key="new" />} />
          <Route path="/voices/:voiceId" element={<VoiceCreator />} />
          <Route path="/meetings/:id" element={<Detail />} />
          <Route path="/settings" element={<Settings />} />
          <Route
            path="/analytics"
            element={
              <Navigate
                to={isAdmin ? "/admin/analytics" : "/dashboard"}
                replace
              />
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      {toast && (
        <div className="toast" role="status">
          {toast}
          <button
            aria-label="Dismiss notification"
            onClick={() => setToast("")}
          >
            ×
          </button>
        </div>
      )}
    </AppContext.Provider>
  );
}

function Landing() {
  const { user } = useApp();
  const message = new URLSearchParams(location.search).get("message");
  return (
    <div className="landing">
      <header className="public-nav">
        <Brand />
        <span className="nav-note">Made for conversations that matter.</span>
        <Link
          className="button secondary"
          to={
            user
              ? user.role === "admin"
                ? "/admin/overview"
                : "/dashboard"
              : "/login"
          }
        >
          {user ? "Open workspace" : "Sign in"} <ArrowRightIcon />
        </Link>
      </header>
      <main>
        {message && <Notice>{message}</Notice>}
        <section className="hero">
          <div className="hero-copy">
            <div className="eyebrow">MORE PRESENT. LESS NOTE-TAKING.</div>
            <h1>
              Good conversations.
              <br />
              <span>Clearer next steps.</span>
            </h1>
            <p>
              A thoughtful home for your college meetings. Turn recordings into
              searchable transcripts, useful minutes and summaries you can
              listen to.
            </p>
            <Link
              className="button large"
              to={
                user
                  ? user.role === "admin"
                    ? "/admin/overview"
                    : "/dashboard"
                  : "/login"
              }
            >
              Make room for clarity <ArrowRightIcon />
            </Link>
            <p className="hero-foot">
              Your meetings stay private to your account.
            </p>
          </div>
          <div
            className="hero-art"
            aria-label="Record, transcribe, summarize and listen"
          >
            <div className="orb">
              <MicrophoneIcon size={72} weight="light" />
            </div>
            <div className="process-strip">
              {" "}
              <span>
                <RecordIcon /> Capture
              </span>
              <i />
              <span>
                <FileTextIcon /> Understand
              </span>
              <i />
              <span>
                <CheckIcon /> Follow through
              </span>
            </div>
          </div>
        </section>
        <section className="landing-details">
          <div>
            <span className="number">01</span>
            <h2>Be in the room.</h2>
            <p>
              Record with your microphone or bring an audio or video file. SUMX
              takes care of extracting the audio.
            </p>
          </div>
          <div>
            <span className="number">02</span>
            <h2>Keep the meaning.</h2>
            <p>
              Find the decisions, discussion and action items. Edit your
              transcript and refine the minutes any time.
            </p>
          </div>
          <div>
            <span className="number">03</span>
            <h2>Take it with you.</h2>
            <p>
              Search your meeting history, export your notes or listen to a
              spoken version of your summary.
            </p>
          </div>
        </section>
        <section className="privacy-note">
          <ShieldCheckIcon size={28} />
          <div>
            <h2>Clear about your data.</h2>
            <p>
              Audio, transcripts and summaries are processed by OpenRouter and
              its downstream AI providers. Record only with participant consent.
              Choose whether to retain the original audio, and delete a meeting
              and its files at any time.
            </p>
          </div>
        </section>
      </main>
      <footer>
        <Brand />
        <span>Less to remember. More to take forward.</span>
        <Link to="/admin/login">Administrator</Link>
      </footer>
    </div>
  );
}

function Login() {
  const { user, reload } = useApp(),
    navigate = useNavigate();
  if (user)
    return (
      <Navigate
        to={user.role === "admin" ? "/admin/overview" : "/dashboard"}
        replace
      />
    );
  return (
    <main className="login-page">
      <Brand />
      <section className="login-card">
        <span className="empty-icon">
          <WaveformIcon size={30} />
        </span>
        <h1>A little more clarity.</h1>
        <p>Sign in with your approved college Google account.</p>
        <GoogleSignIn
          onDenied={(message) =>
            navigate("/?message=" + encodeURIComponent(message), {
              replace: true,
            })
          }
          onSuccess={async () => {
            await reload();
            navigate("/dashboard", { replace: true });
          }}
        />
        <p className="small">
          Access is limited to emails approved by your administrator. Your
          meetings stay private to your account.
        </p>
        <Link className="text-link" to="/">
          Back to SUMX
        </Link>
      </section>
    </main>
  );
}

function Shell() {
  const { user, reload, notify } = useApp();
  async function logout() {
    try {
      await post("/auth/logout");
      disableGoogleAutoSelect();
      await reload();
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <div className="app-shell">
      <a href="#main" className="skip">
        Skip to content
      </a>
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">YOUR WORKSPACE</div>
        <nav>
          <NavLink to="/dashboard">
            <HouseIcon />
            Overview
          </NavLink>
          <NavLink to="/history">
            <ListBulletsIcon />
            Meeting history
          </NavLink>
          <NavLink to="/record">
            <MicrophoneIcon />
            Record meeting
          </NavLink>
          <NavLink to="/upload">
            <UploadSimpleIcon />
            Upload meeting
          </NavLink>
          <NavLink to="/voices">
            <WaveformIcon />
            Voice Cloning
          </NavLink>
          {user?.role === "admin" && (
            <NavLink to="/analytics">
              <ChartBarIcon />
              Analytics
            </NavLink>
          )}
          {user?.role === "admin" && (
            <NavLink to="/admin">
              <ShieldCheckIcon />
              Administration
            </NavLink>
          )}
        </nav>
        <div className="sidebar-bottom">
          <NavLink to="/settings">
            <GearSixIcon />
            Settings
          </NavLink>
          <div className="account">
            <span className="avatar">{user?.name[0]?.toUpperCase()}</span>
            <div>
              <strong>{user?.name}</strong>
              <small>
                {user?.role === "admin" ? "Administrator" : "College workspace"}
              </small>
            </div>
            <button
              className="icon-button"
              aria-label="Sign out"
              onClick={logout}
            >
              <SignOutIcon />
            </button>
          </div>
        </div>
      </aside>
      <div className="app-body">
        <header className="topbar">
          <span>Room for a clearer mind.</span>
          <div className="topbar-end">
            <ShieldCheckIcon />
            <span>Private workspace</span>
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Dashboard({ history = false }: { history?: boolean }) {
  const { user } = useApp();
  return (
    <DashboardView
      key={history ? "history" : "overview"}
      user={user!}
      history={history}
    />
  );
}

function Recorder({
  onReady,
  onActive,
  maxSeconds,
}: {
  onReady: (file: File) => void;
  onActive: (active: boolean) => void;
  maxSeconds: number;
}) {
  const [state, setState] = useState<
      "idle" | "recording" | "paused" | "stopped"
    >("idle"),
    [seconds, setSeconds] = useState(0),
    [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null),
    stream = useRef<MediaStream | null>(null),
    context = useRef<AudioContext | null>(null),
    canvas = useRef<HTMLCanvasElement>(null),
    animation = useRef(0),
    elapsed = useRef(0),
    started = useRef(0),
    chunks = useRef<Blob[]>([]),
    timer = useRef<ReturnType<typeof setInterval> | null>(null),
    disposed = useRef(false);
  const cleanup = useCallback(() => {
    cancelAnimationFrame(animation.current);
    if (timer.current) clearInterval(timer.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    void context.current?.close();
    context.current = null;
  }, []);
  useEffect(() => {
    disposed.current = false;
    return () => {
      disposed.current = true;
      if (recorder.current?.state !== "inactive") recorder.current?.stop();
      cleanup();
    };
  }, [cleanup]);
  useEffect(() => {
    onActive(state === "recording" || state === "paused");
  }, [state, onActive]);
  useEffect(() => {
    if (state !== "recording" && state !== "paused") return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", guard);
    const navigationGuard = (event: MouseEvent) => {
      if (
        (event.target as Element).closest("a[href]") &&
        !window.confirm(
          "Leave this recording? Stop and save it first to keep the captured audio.",
        )
      ) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    document.addEventListener("click", navigationGuard, true);
    return () => {
      window.removeEventListener("beforeunload", guard);
      document.removeEventListener("click", navigationGuard, true);
    };
  }, [state]);
  function stop() {
    if (recorder.current && recorder.current.state !== "inactive")
      recorder.current.stop();
  }
  async function start() {
    setError("");
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError(
        "Microphone recording requires a supported browser on HTTPS or localhost.",
      );
      return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (disposed.current) {
        s.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = s;
      const type = [
        "audio/webm;codecs=opus",
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ].find((t) => MediaRecorder.isTypeSupported(t));
      const r = new MediaRecorder(s, type ? { mimeType: type } : undefined);
      recorder.current = r;
      chunks.current = [];
      elapsed.current = 0;
      started.current = performance.now();
      setSeconds(0);
      r.ondataavailable = (e) => {
        if (e.data.size) chunks.current.push(e.data);
      };
      r.onerror = () => {
        setError(
          "Recording was interrupted. Any captured audio will be kept for preview.",
        );
        stop();
      };
      r.onstop = () => {
        cleanup();
        if (!disposed.current) {
          setState("stopped");
          const mime = r.mimeType.split(";")[0],
            ext = mime.includes("mp4")
              ? "m4a"
              : mime.includes("ogg")
                ? "ogg"
                : "webm";
          onReady(
            new File(chunks.current, "Meeting recording." + ext, {
              type: mime,
            }),
          );
        }
      };
      s.getAudioTracks()[0].onended = () => {
        setError(
          "Microphone disconnected. Review the captured recording before uploading.",
        );
        stop();
      };
      r.start(1000);
      setState("recording");
      timer.current = setInterval(() => {
        const value =
          elapsed.current +
          (r.state === "recording" ? performance.now() - started.current : 0);
        setSeconds(Math.floor(value / 1000));
        if (value / 1000 >= maxSeconds) stop();
      }, 250);
      context.current = new AudioContext();
      const source = context.current.createMediaStreamSource(s),
        analyser = context.current.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      const values = new Uint8Array(analyser.frequencyBinCount);
      const draw = () => {
        const c = canvas.current,
          ctx = c?.getContext("2d");
        if (ctx && c) {
          analyser.getByteTimeDomainData(values);
          ctx.clearRect(0, 0, c.width, c.height);
          ctx.strokeStyle = getComputedStyle(c)
            .getPropertyValue(r.state === "paused" ? "--subtle" : "--accent")
            .trim();
          ctx.lineWidth = 3;
          ctx.beginPath();
          values.forEach((v, i) => {
            const x = (i / (values.length - 1)) * c.width,
              y = (v / 255) * c.height;
            if (!i) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          });
          ctx.stroke();
        }
        animation.current = requestAnimationFrame(draw);
      };
      draw();
    } catch (e) {
      cleanup();
      setError(
        (e as Error).name === "NotAllowedError"
          ? "Microphone access was denied. Allow access in your browser settings, then try again."
          : "The microphone could not be started. Check that it is connected and available.",
      );
    }
  }
  function pause() {
    const r = recorder.current;
    if (!r) return;
    if (r.state === "recording") {
      elapsed.current += performance.now() - started.current;
      r.pause();
      setState("paused");
    } else if (r.state === "paused") {
      started.current = performance.now();
      r.resume();
      setState("recording");
    }
  }
  return (
    <div className="recorder">
      {error && <Notice>{error}</Notice>}
      <div className={"mic-circle " + state}>
        <MicrophoneIcon size={44} weight="light" />
      </div>
      <div className="record-time">{duration(seconds)}</div>
      <p>
        {state === "recording"
          ? "Recording your microphone"
          : state === "paused"
            ? "Recording paused"
            : state === "stopped"
              ? "Recording saved for preview"
              : "Ready when you are."}
      </p>
      <canvas
        ref={canvas}
        width={600}
        height={100}
        aria-label="Live microphone waveform"
      />
      <div className="button-row">
        {state === "idle" || state === "stopped" ? (
          <Button onClick={start}>
            <RecordIcon />
            {state === "stopped" ? "Record again" : "Start recording"}
          </Button>
        ) : (
          <>
            <Button secondary onClick={pause}>
              {state === "paused" ? <PlayIcon /> : <PauseIcon />}
              {state === "paused" ? "Resume" : "Pause"}
            </Button>
            <Button onClick={stop}>
              <StopIcon />
              Stop recording
            </Button>
          </>
        )}
      </div>
      <p className="small">
        Transcription starts after you upload. Keep this page open while
        recording.
      </p>
    </div>
  );
}

function Capture({ recording = false }: { recording?: boolean }) {
  const navigate = useNavigate(),
    { notify } = useApp();
  const [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState(""),
    [consent, setConsent] = useState(false),
    [retain, setRetain] = useState(true),
    [title, setTitle] = useState(""),
    [language, setLanguage] = useState(""),
    [detail, setDetail] = useState("standard"),
    [busy, setBusy] = useState(false),
    [percent, setPercent] = useState(0),
    [error, setError] = useState(""),
    [config, setConfig] = useState<Config | null>(null),
    [drag, setDrag] = useState(false);
  const [recorderActive, setRecorderActive] = useState(false);
  const key = useRef(crypto.randomUUID());
  useEffect(() => {
    api<Config>("/config")
      .then(setConfig)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  function select(f: File) {
    setError("");
    if (
      config &&
      config.max_upload_mb > 0 &&
      f.size > config.max_upload_mb * 1024 * 1024
    ) {
      setError(`Choose a file smaller than ${config.max_upload_mb} MB.`);
      return;
    }
    setFile(f);
    key.current = crypto.randomUUID();
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !consent) return;
    setBusy(true);
    setError("");
    const data = new FormData();
    data.set("file", file);
    data.set("title", title || "Untitled meeting");
    data.set("language", language);
    data.set("detail", detail);
    data.set("consent", String(consent));
    data.set("retain_source", String(retain));
    try {
      const m = await upload(data, key.current, recording, setPercent);
      notify("Recording received. Processing is queued.");
      navigate("/meetings/" + m.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page capture-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">MAKE SPACE FOR THE CONVERSATION</p>
          <h1>{recording ? "Record a meeting." : "Bring your recording."}</h1>
          <p>
            {recording
              ? "A clear record, without losing your place in the room."
              : "From an existing file to a clearer understanding."}
          </p>
        </div>
      </div>
      {error && <Notice>{error}</Notice>}
      {config && !config.ai_configured && (
        <Notice>
          OpenRouter is not configured yet. An administrator must add the server
          API key before processing can finish.
        </Notice>
      )}
      <div className="capture-grid">
        <section className="surface">
          {recording ? (
            <Recorder
              onReady={select}
              onActive={setRecorderActive}
              maxSeconds={config?.max_duration_seconds || 14400}
            />
          ) : (
            <label
              className={"dropzone " + (drag ? "dragging" : "")}
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                if (!busy && e.dataTransfer.files[0])
                  select(e.dataTransfer.files[0]);
              }}
            >
              <UploadSimpleIcon size={42} weight="light" />
              <h2>Drop a meeting here.</h2>
              <p>Or choose a file from your device</p>
              <span className="button secondary">Choose a file</span>
              <input
                aria-label="Choose a meeting file"
                type="file"
                disabled={busy}
                accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.webm,.mp4,.mov"
                onChange={(e) => {
                  if (e.target.files?.[0]) select(e.target.files[0]);
                }}
              />
              <small>
                MP3, WAV, M4A, AAC, FLAC, OGG, WebM, MP4, MOV
                <br />
                {config && config.max_upload_mb > 0
                  ? `Up to ${config.max_upload_mb} MB`
                  : "No file size limit"}{" "}
                · {Math.round((config?.max_duration_seconds || 14400) / 3600)}{" "}
                hours
              </small>
            </label>
          )}
          {file && (
            <div className="file-preview">
              <strong>{file.name}</strong>
              <span>{(file.size / 1048576).toFixed(1)} MB</span>
              {file.type.startsWith("video") ? (
                <video src={preview} controls />
              ) : (
                <audio src={preview} controls />
              )}
              <a href={preview} download={file.name} className="text-link">
                <DownloadSimpleIcon />
                Save a local copy
              </a>
            </div>
          )}
        </section>
        <form className="surface capture-options" onSubmit={submit}>
          <h2>The meeting details.</h2>
          <label>
            Meeting title
            <input
              value={title}
              maxLength={200}
              placeholder="Untitled meeting"
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            Spoken language
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              <option value="">Detect automatically</option>
              {[
                ["en", "English"],
                ["hi", "Hindi"],
                ["es", "Spanish"],
                ["fr", "French"],
                ["de", "German"],
                ["ja", "Japanese"],
                ["ta", "Tamil"],
                ["te", "Telugu"],
              ].map(([v, l]) => (
                <option value={v} key={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            Summary detail
            <select value={detail} onChange={(e) => setDetail(e.target.value)}>
              <option value="concise">Concise</option>
              <option value="standard">Standard</option>
              <option value="detailed">Detailed</option>
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={retain}
              onChange={(e) => setRetain(e.target.checked)}
            />
            <span>
              Keep the original recording
              <small>
                Allows playback and retranscription. Otherwise it is deleted
                after transcription succeeds.
              </small>
            </span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={consent}
              required
              onChange={(e) => setConsent(e.target.checked)}
            />
            <span>
              I have participant consent to process this meeting through
              OpenRouter and its downstream providers.
            </span>
          </label>
          <Button
            type="submit"
            disabled={!file || !consent || busy || recorderActive}
          >
            {busy
              ? percent < 100
                ? `Uploading ${percent}%`
                : "Validating media…"
              : "Create meeting minutes"}
            <ArrowRightIcon />
          </Button>
          {busy && (
            <progress value={percent} max={100} aria-label="Upload progress" />
          )}
          <p className="small">
            Audio extraction, transcription and summarization begin after
            upload. Generated speech is optional.
          </p>
        </form>
      </div>
    </div>
  );
}

function SummaryView({ content }: { content: Minutes }) {
  return (
    <div className="minutes">
      <div className="executive">
        <SparkleIcon size={24} />
        <h2>At a glance.</h2>
        <p>{content.executive_summary}</p>
      </div>
      {(
        [
          ["Discussion points", content.discussion_points],
          ["Decisions", content.decisions],
          ["Unresolved issues", content.unresolved_issues],
          ["Follow-up", content.follow_up],
        ] as [string, string[]][]
      ).map(([label, items]) => (
        <section key={label}>
          <h3>{label}</h3>
          {items.length ? (
            <ul>
              {items.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          ) : (
            <p className="subtle">None explicitly recorded.</p>
          )}
        </section>
      ))}
    </div>
  );
}
function SummaryEditor({
  content,
  onSave,
  onCancel,
}: {
  content: Minutes;
  onSave: (c: Minutes) => Promise<void>;
  onCancel: () => void;
}) {
  const [value, setValue] = useState<Minutes>(structuredClone(content)),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <form
      className="summary-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await onSave(value);
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      {error && <Notice>{error}</Notice>}
      <label>
        Summary title
        <input
          value={value.title}
          maxLength={200}
          required
          onChange={(e) => setValue({ ...value, title: e.target.value })}
        />
      </label>
      <label>
        Executive summary
        <textarea
          value={value.executive_summary}
          required
          rows={6}
          onChange={(e) =>
            setValue({ ...value, executive_summary: e.target.value })
          }
        />
      </label>
      {(
        [
          "discussion_points",
          "decisions",
          "unresolved_issues",
          "follow_up",
        ] as const
      ).map((key) => (
        <label key={key}>
          {key.replaceAll("_", " ")}
          <textarea
            rows={4}
            value={value[key].join("\n")}
            onChange={(e) =>
              setValue({ ...value, [key]: e.target.value.split("\n") })
            }
          />
          <small>One point per line. Empty lines are removed when saved.</small>
        </label>
      ))}
      <h3>Action items</h3>
      {value.action_items.map((a, i) => (
        <div className="action-edit" key={i}>
          <label>
            Task
            <input
              value={a.task}
              required
              onChange={(e) =>
                setValue({
                  ...value,
                  action_items: value.action_items.map((v, j) =>
                    i === j ? { ...v, task: e.target.value } : v,
                  ),
                })
              }
            />
          </label>
          {(["owner", "deadline"] as const).map((key) => (
            <label key={key}>
              {key}
              <input
                value={a[key] || ""}
                placeholder="Not specified"
                onChange={(e) =>
                  setValue({
                    ...value,
                    action_items: value.action_items.map((v, j) =>
                      i === j ? { ...v, [key]: e.target.value || null } : v,
                    ),
                  })
                }
              />
            </label>
          ))}
          <button
            className="text-link danger"
            type="button"
            onClick={() =>
              setValue({
                ...value,
                action_items: value.action_items.filter((_, j) => i !== j),
              })
            }
          >
            Remove action
          </button>
        </div>
      ))}
      <Button
        secondary
        onClick={() =>
          setValue({
            ...value,
            action_items: [
              ...value.action_items,
              { task: "", owner: null, deadline: null, timestamp: null },
            ],
          })
        }
      >
        <PlusIcon />
        Add action item
      </Button>
      <div className="button-row">
        <Button type="submit" disabled={busy}>
          Save summary
        </Button>
        <Button secondary onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Detail() {
  const { id } = useParams(),
    { notify } = useApp(),
    navigate = useNavigate();
  const [meeting, setMeeting] = useState<Meeting | null>(null),
    [error, setError] = useState(""),
    [tab, setTab] = useState("summary"),
    [query, setQuery] = useState(""),
    [editing, setEditing] = useState(""),
    [transcript, setTranscript] = useState(""),
    [title, setTitle] = useState(""),
    [detail, setDetail] = useState<SummaryMode>("standard"),
    [busy, setBusy] = useState(false),
    [confirmDelete, setConfirmDelete] = useState(false);
  // The database is the source of truth; records are held per meeting and mode.
  const [overview, setOverview] = useState<SummaryOverview | null>(null),
    [records, setRecords] = useState<Record<string, SummaryRecord>>({}),
    [narrationChoice, setNarrationChoice] = useState<SummaryMode | "">(""),
    modeChosen = useRef(false);
  const [narrationJobId, setNarrationJobId] = useState<string | undefined>(),
    [narrationPending, setNarrationPending] = useState("");
  useEffect(() => {
    setNarrationJobId(undefined);
    setNarrationPending("");
  }, [id, meeting?.summary_version]);
  const refresh = useCallback(async () => {
    const [m, o] = await Promise.all([
      api<Meeting>("/meetings/" + id),
      api<SummaryOverview>(`/meetings/${id}/summaries`),
    ]);
    setMeeting(m);
    setOverview(o);
  }, [id]);
  useEffect(() => {
    let cancelled = false;
    modeChosen.current = false;
    setOverview(null);
    const load = () =>
      Promise.all([
        api<Meeting>("/meetings/" + id),
        api<SummaryOverview>(`/meetings/${id}/summaries`),
      ])
        .then(([m, o]) => {
          if (!cancelled) {
            setMeeting(m);
            setOverview(o);
            setError("");
          }
        })
        .catch((e) => {
          if (!cancelled) setError(e.message);
        });
    void load();
    const interval = setInterval(load, 2500);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [id]);
  useEffect(() => {
    if (!overview || modeChosen.current) return;
    modeChosen.current = true;
    if (overview.latest_mode) setDetail(overview.latest_mode);
  }, [overview]);
  const options = overview?.narration_options ?? [];
  const narrationMode =
    narrationChoice && options.includes(narrationChoice)
      ? narrationChoice
      : options.includes(detail)
        ? detail
        : options[0];
  useEffect(() => {
    if (!overview) return;
    for (const mode of new Set([detail, narrationMode])) {
      if (!mode) continue;
      const info = overview.modes[mode],
        key = `${id}:${mode}`,
        held = records[key];
      if (
        !info.exists ||
        (held?.version === info.version &&
          held.audio?.length === info.audio_count)
      )
        continue;
      void api<SummaryRecord>(`/meetings/${id}/summaries/${mode}`)
        .then((record) =>
          // Filed under its own mode, so a late reply can never reach another mode.
          setRecords((current) =>
            (current[key]?.version ?? 0) > (record.version ?? 0)
              ? current
              : { ...current, [key]: record },
          ),
        )
        .catch((e) => setError(e.message));
    }
  }, [overview, detail, narrationMode, id, records]);
  async function perform(operation: () => Promise<unknown>, message?: string) {
    setBusy(true);
    setError("");
    try {
      await operation();
      await refresh();
      if (message) notify(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      notify("Copied to clipboard");
    } catch {
      notify("Clipboard access failed. Use the export download.");
    }
  }
  if (!meeting)
    return (
      <div className="page">
        {error ? <Notice>{error}</Notice> : <Loading />}
      </div>
    );
  const m = meeting,
    processing = active(m),
    disabled = processing || busy,
    info = overview?.modes[detail],
    held = records[`${id}:${detail}`],
    selected = held && held.version === info?.version ? held : undefined,
    content = info?.exists ? selected?.content : undefined,
    narrationRecord = narrationMode
      ? records[`${id}:${narrationMode}`]
      : undefined,
    narrationAudio =
      narrationMode &&
      narrationRecord?.version === overview?.modes[narrationMode].version
        ? narrationRecord?.audio
        : undefined;
  return (
    <div className="page detail-page">
      <Link className="text-link back-link" to="/history">
        ← Meeting history
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            {date(m.created)} · {duration(m.duration)}
          </p>
          {editing === "title" ? (
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                void perform(async () => {
                  await api("/meetings/" + id, {
                    method: "PATCH",
                    body: JSON.stringify({ title }),
                  });
                  setEditing("");
                });
              }}
            >
              <input
                aria-label="Meeting title"
                required
                maxLength={200}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Button type="submit">Save</Button>
              <Button secondary onClick={() => setEditing("")}>
                Cancel
              </Button>
            </form>
          ) : (
            <h1>{m.title}</h1>
          )}
          <p>Every thought, decision and next step in one place.</p>
        </div>
        <div className="button-row">
          <Button
            secondary
            disabled={disabled}
            onClick={() => {
              setTitle(m.title);
              setEditing("title");
            }}
          >
            Rename
          </Button>
          <button
            aria-label="Delete meeting"
            className="icon-button danger"
            disabled={disabled}
            onClick={() => setConfirmDelete(true)}
          >
            <TrashIcon />
          </button>
        </div>
      </div>
      {error && <Notice>{error}</Notice>}
      {m.error && (m.job?.kind !== "speech" || tab !== "audio") && (
        <Notice>
          {m.error}
          {m.job?.state === "failed" && (
            <Button
              secondary
              disabled={busy}
              onClick={() =>
                perform(() => post(`/meetings/${id}/jobs/${m.job!.id}/retry`))
              }
            >
              Retry failed stage
            </Button>
          )}
        </Notice>
      )}
      {processing && m.job?.kind !== "speech" && (
        <div className="processing-panel" role="status">
          <span className="processing-icon">
            <WaveformIcon size={24} />
          </span>
          <div>
            <strong>
              {
                (
                  {
                    queued: "Waiting for the processing worker",
                    extracting: "Extracting compatible audio",
                    transcribing: "Transcribing your recording",
                    summarizing: "Writing your meeting minutes",
                    generating_speech: "Generating summary narration",
                  } as Record<string, string>
                )[m.status]
              }
            </strong>
            <p>
              {m.job?.total
                ? `${m.job.completed} of ${m.job.total} parts complete`
                : "Your progress is saved. You can leave this page and return later."}
            </p>
          </div>
          {!!m.job?.total && (
            <progress
              max={m.job.total}
              value={m.job.completed}
              aria-label="Completed processing parts"
            />
          )}
        </div>
      )}
      <div className="segmented" role="tablist" aria-label="Meeting content">
        {["summary", "transcript", "actions", "audio"].map((t) => (
          <button
            role="tab"
            aria-selected={tab === t}
            aria-controls={"panel-" + t}
            id={"tab-" + t}
            key={t}
            onClick={() => {
              setTab(t);
              setEditing("");
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                e.preventDefault();
                const tabs = ["summary", "transcript", "actions", "audio"];
                const next =
                  tabs[
                    (tabs.indexOf(tab) + (e.key === "ArrowRight" ? 1 : 3)) % 4
                  ];
                setTab(next);
                document.getElementById("tab-" + next)?.focus();
              }
            }}
            tabIndex={tab === t ? 0 : -1}
          >
            {t === "audio"
              ? "Recording & narration"
              : t === "actions"
                ? "Action items"
                : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      <section
        className="surface detail-content"
        role="tabpanel"
        id={"panel-" + tab}
        aria-labelledby={"tab-" + tab}
      >
        {tab === "summary" && (
          <>
            {selected &&
              selected.transcript_version !== m.transcript_version && (
                <Notice>
                  The transcript has changed. Regenerate the summary to include
                  your edits.
                </Notice>
              )}
            <div className="toolbar">
              <h2>Meeting minutes</h2>
              <div className="button-row">
                <select
                  aria-label="Summary detail"
                  value={detail}
                  onChange={(e) => {
                    setDetail(e.target.value as SummaryMode);
                    setEditing("");
                  }}
                >
                  <option value="concise">Concise</option>
                  <option value="standard">Standard</option>
                  <option value="detailed">Detailed</option>
                </select>
                <Button
                  secondary
                  disabled={disabled || !m.transcript}
                  onClick={() =>
                    perform(() =>
                      post(`/meetings/${id}/summaries/${detail}/generate`),
                    )
                  }
                >
                  {info?.exists ? "Regenerate" : "Generate summary"}
                </Button>
                {content && (
                  <>
                    <Button
                      secondary
                      disabled={disabled}
                      onClick={() => setEditing("summary")}
                    >
                      Edit
                    </Button>
                    <button
                      className="icon-button"
                      aria-label="Copy summary"
                      onClick={() => copy(JSON.stringify(content, null, 2))}
                    >
                      <CopyIcon />
                    </button>
                    <a
                      className="icon-button"
                      aria-label="Export summary"
                      href={`/api/meetings/${id}/export?kind=summary&mode=${detail}`}
                    >
                      <DownloadSimpleIcon />
                    </a>
                  </>
                )}
              </div>
            </div>
            {content ? (
              editing === "summary" ? (
                <SummaryEditor
                  content={content}
                  onCancel={() => setEditing("")}
                  onSave={async (c) => {
                    for (const key of [
                      "discussion_points",
                      "decisions",
                      "unresolved_issues",
                      "follow_up",
                    ] as const)
                      c[key] = c[key].filter((s) => s.trim());
                    await api("/meetings/" + id + "/summary", {
                      method: "PUT",
                      body: JSON.stringify({
                        content: c,
                        version: selected!.version,
                      }),
                    });
                    setEditing("");
                    await refresh();
                    notify(
                      "Summary saved. Generate narration for this new version.",
                    );
                  }}
                />
              ) : (
                <SummaryView content={content} />
              )
            ) : (
              <>
                <div className="empty-search">
                  {info?.exists
                    ? "Loading summary…"
                    : info?.status === "generating"
                      ? `Generating ${detail} summary…`
                      : info?.status === "failed"
                        ? `The ${detail} summary could not be generated.`
                        : !overview
                          ? "Loading summary…"
                          : `No ${detail} summary generated yet.`}
                </div>
                {overview?.legacy && (
                  <details>
                    <summary>Earlier summary, mode not recorded</summary>
                    <SummaryView content={overview.legacy.content} />
                  </details>
                )}
              </>
            )}
          </>
        )}
        {tab === "transcript" && (
          <>
            <div className="toolbar">
              <h2>Full transcript</h2>
              <div className="button-row">
                <Button
                  secondary
                  disabled={disabled || !m.source_available}
                  onClick={() =>
                    perform(() => post("/meetings/" + id + "/transcribe"))
                  }
                >
                  Retranscribe
                </Button>
                {m.transcript && (
                  <>
                    <Button
                      secondary
                      disabled={disabled}
                      onClick={() => {
                        setTranscript(m.transcript!.text);
                        setEditing("transcript");
                      }}
                    >
                      Edit transcript
                    </Button>
                    <button
                      className="icon-button"
                      aria-label="Copy transcript"
                      onClick={() => copy(m.transcript!.text)}
                    >
                      <CopyIcon />
                    </button>
                    <a
                      className="icon-button"
                      aria-label="Export transcript"
                      href={"/api/meetings/" + id + "/export?kind=transcript"}
                    >
                      <DownloadSimpleIcon />
                    </a>
                  </>
                )}
              </div>
            </div>
            {m.transcript ? (
              editing === "transcript" ? (
                <>
                  <Notice>
                    Editing the full text clears segment timestamps. Regenerate
                    your summary after saving.
                  </Notice>
                  <textarea
                    className="transcript-editor"
                    aria-label="Transcript text"
                    value={transcript}
                    onChange={(e) => setTranscript(e.target.value)}
                  />
                  <div className="button-row">
                    <Button
                      disabled={busy || !transcript.trim()}
                      onClick={() =>
                        perform(async () => {
                          await api("/meetings/" + id + "/transcript", {
                            method: "PUT",
                            body: JSON.stringify({
                              text: transcript,
                              version: m.transcript!.version,
                            }),
                          });
                          setEditing("");
                        }, "Transcript saved")
                      }
                    >
                      Save transcript
                    </Button>
                    <Button secondary onClick={() => setEditing("")}>
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <label className="search">
                    <span className="sr-only">Search transcript</span>
                    <input
                      type="search"
                      placeholder="Find in transcript…"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                  <p className="small">
                    {m.transcript.language
                      ? `Language: ${m.transcript.language}. `
                      : ""}
                    {m.transcript.segments.length
                      ? "Timestamps returned by the transcription provider. Speaker labels, when present, apply within each clip."
                      : "Segment timestamps were not supplied, or were cleared by an edit."}
                  </p>
                  <div className="transcript">
                    {m.transcript.segments.length
                      ? m.transcript.segments
                          .filter((s) =>
                            s.text.toLowerCase().includes(query.toLowerCase()),
                          )
                          .map((s, i) => (
                            <div className="transcript-segment" key={i}>
                              <time>{duration(s.start)}</time>
                              <p>
                                {s.speaker && (
                                  <strong>
                                    {s.speaker}
                                    <br />
                                  </strong>
                                )}
                                {s.text}
                              </p>
                            </div>
                          ))
                      : m.transcript.text
                          .split("\n")
                          .filter((p) =>
                            p.toLowerCase().includes(query.toLowerCase()),
                          )
                          .map((p, i) => <p key={i}>{p}</p>)}
                  </div>
                </>
              )
            ) : (
              <p className="empty-search">
                Your transcript will appear once speech-to-text is complete.
              </p>
            )}
          </>
        )}
        {tab === "actions" && (
          <>
            <div className="toolbar">
              <h2>What happens next.</h2>
              {content && (
                <Button
                  secondary
                  disabled={disabled}
                  onClick={() => {
                    setTab("summary");
                    setEditing("summary");
                  }}
                >
                  Edit action items
                </Button>
              )}
            </div>
            {content?.action_items.length ? (
              <div className="action-list">
                {content.action_items.map((a, i) => (
                  <article key={i}>
                    <span className="action-number">{i + 1}</span>
                    <div>
                      <h3>{a.task}</h3>
                      <p>
                        <strong>Owner</strong> {a.owner || "Not specified"}{" "}
                        <span>·</span> <strong>Deadline</strong>{" "}
                        {a.deadline || "Not specified"}
                        {a.timestamp !== null && (
                          <span> · {duration(a.timestamp)}</span>
                        )}
                      </p>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="empty-search">
                No explicit action items have been recorded.
              </p>
            )}
            <p className="small">
              Owners and deadlines remain unspecified unless stated in the
              transcript. Review AI-generated minutes before sharing.
            </p>
          </>
        )}
        {tab === "audio" && (
          <>
            <h2>Listen again.</h2>
            <section className="audio-section">
              <h3>Original recording</h3>
              {m.source_available ? (
                <>
                  {m.media_type?.startsWith("video") ? (
                    <video controls src={"/api/meetings/" + id + "/media"} />
                  ) : (
                    <audio controls src={"/api/meetings/" + id + "/media"} />
                  )}
                  <Button
                    secondary
                    disabled={disabled}
                    onClick={() =>
                      perform(
                        () =>
                          api("/meetings/" + id, {
                            method: "PATCH",
                            body: JSON.stringify({ retain_source: false }),
                          }),
                        "Original recording removed",
                      )
                    }
                  >
                    Remove original recording
                  </Button>
                </>
              ) : (
                <p className="subtle">
                  The original recording is not retained.
                </p>
              )}
            </section>
            <section className="audio-section">
              <div className="toolbar">
                <h3>Summary narration</h3>
                <select
                  aria-label="Narration summary"
                  disabled={!options.length}
                  value={narrationMode ?? ""}
                  onChange={(e) =>
                    setNarrationChoice(e.target.value as SummaryMode)
                  }
                >
                  {options.length ? (
                    options.map((mode) => (
                      <option key={mode} value={mode}>
                        {mode[0].toUpperCase() + mode.slice(1)}
                      </option>
                    ))
                  ) : (
                    <option value="">No generated summaries available.</option>
                  )}
                </select>
              </div>
              <p className="subtle">
                Generated speech is tied to this exact summary version.
              </p>
              <NarrationPicker
                disabled={disabled || !narrationMode}
                generate={async (selection) => {
                  setNarrationPending(
                    selection.voice_type === "cloned"
                      ? "Faster Qwen3-TTS"
                      : "OpenRouter preset",
                  );
                  try {
                    await perform(async () => {
                      const job = await post<{ id: string }>(
                        `/meetings/${id}/narrations`,
                        { ...selection, summary_mode: narrationMode },
                      );
                      setNarrationJobId(job.id);
                    });
                  } finally {
                    setNarrationPending("");
                  }
                }}
              />
              <NarrationProgress
                jobId={
                  narrationJobId ||
                  (m.job?.kind === "speech" &&
                  (m.job.state !== "completed" || !!narrationAudio?.length)
                    ? m.job.id
                    : undefined)
                }
                pending={narrationPending}
                onFinished={refresh}
              />
              {narrationAudio?.map((a) => (
                <div className="narration" key={a.id}>
                  <strong>
                    {a.voice_name || a.voice.replaceAll("_", " ")}
                  </strong>
                  <audio controls src={`/api/meetings/${id}/audio/${a.id}`} />
                  <a
                    className="text-link"
                    href={`/api/meetings/${id}/audio/${a.id}?download=true`}
                  >
                    <DownloadSimpleIcon />
                    Download MP3
                  </a>
                </div>
              ))}
            </section>
          </>
        )}
      </section>
      {confirmDelete && (
        <Confirm
          title="Delete this meeting?"
          text="The recording, transcripts, summaries and narration will be permanently removed. Usage totals are retained for accounting."
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() =>
            perform(async () => {
              await api("/meetings/" + id, { method: "DELETE" });
              navigate("/history");
            }, "Meeting deleted")
          }
        />
      )}
    </div>
  );
}

function Confirm({
  title,
  text,
  onCancel,
  onConfirm,
}: {
  title: string;
  text: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    const el = ref.current;
    return () => el?.close();
  }, []);
  return (
    <dialog ref={ref} onCancel={onCancel} className="confirm">
      <h2>{title}</h2>
      <p>{text}</p>
      <div className="button-row">
        <Button secondary onClick={onCancel}>
          Keep meeting
        </Button>
        <button className="button destructive" onClick={onConfirm}>
          Delete permanently
        </button>
      </div>
    </dialog>
  );
}

function Settings() {
  const { user, reload, notify } = useApp(),
    [config, setConfig] = useState<Config | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    api<Config>("/config")
      .then(setConfig)
      .catch((e) => setError(e.message));
  }, []);
  async function switchAccount() {
    try {
      await post("/auth/logout");
      disableGoogleAutoSelect();
      await reload();
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <div className="page settings-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">MAKE YOURSELF AT HOME</p>
          <h1>Settings.</h1>
          <p>Your account, your preferences, your data.</p>
        </div>
      </div>
      {error && <Notice>{error}</Notice>}
      <section className="surface">
        <h2>Your account</h2>
        <dl>
          <dt>Name</dt>
          <dd>{user?.name}</dd>
          <dt>Email</dt>
          <dd>{user?.email}</dd>
          <dt>Access</dt>
          <dd>
            {user?.role === "admin"
              ? "Administrator"
              : "Approved college account"}
          </dd>
        </dl>
        {user?.role !== "admin" && (
          <Button secondary onClick={switchAccount}>
            Switch Google account
          </Button>
        )}
      </section>
      <section className="surface">
        <h2>Appearance</h2>
        <div className="appearance-light">
          <SunIcon size={22} />
          <div>
            <strong>Light</strong>
            <p>A clear, consistent workspace on every device.</p>
          </div>
          <CheckIcon size={20} />
        </div>
      </section>
      <section className="surface">
        <h2>Processing & privacy</h2>
        <p>
          Audio is sent to OpenRouter for transcription. Transcript text is sent
          through OpenRouter for minutes, and summary text for optional
          narration. OpenRouter routes requests to downstream AI providers,
          whose retention policies may vary.
        </p>
        <p>
          Each upload lets you choose whether to retain the original recording.
          Deleting a meeting removes its local media and text artifacts.
          Previously completed provider requests and database backups may have
          separate retention policies.
        </p>
        {config && (
          <dl>
            <dt>Transcription</dt>
            <dd>{config.models.stt}</dd>
            <dt>Summarization</dt>
            <dd>{config.models.summary}</dd>
            <dt>Narration</dt>
            <dd>{config.models.tts}</dd>
          </dl>
        )}
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
