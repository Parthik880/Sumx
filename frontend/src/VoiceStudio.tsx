import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  MicrophoneIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  StopIcon,
  WaveformIcon,
  ShieldCheckIcon,
  ArrowLeftIcon,
  TrashIcon,
  PencilSimpleIcon,
} from "@phosphor-icons/react";
import { api, date, duration, post } from "./api";

type Voice = {
  id: string;
  name: string;
  description: string;
  language: string;
  duration: number;
  created: number;
  updated: number;
  saved: boolean;
  has_preview: boolean;
  preview_current: boolean;
  preview_error: string | null;
  preview_running: boolean;
  preview_stage?: string;
  reference_text: string;
  usable: boolean;
};
type Capability = {
  model: string;
  supported: boolean;
  message: string;
  state?: string;
  gpu_available?: boolean;
  gpu_name?: string;
  accepting?: boolean;
};
type Selection =
  | { voice_type: "preset"; preset_voice_id: string | null }
  | { voice_type: "cloned"; cloned_voice_id: string };
const PARAGRAPH =
  "Hello, and welcome to my personal voice studio. Today, I am creating a voice that can help narrate meeting summaries clearly and naturally. Each conversation brings new ideas, important decisions, and meaningful discussions. My goal is to make these summaries easier to understand, remember, and revisit whenever they are needed.";
const CONSENT =
  "I confirm this is my own voice, or I have the speaker's explicit permission to create and use this voice.";

function CapabilityNotice({
  capability,
  refresh,
}: {
  capability: Capability | null;
  refresh: () => void;
}) {
  return (
    <div className="voice-availability" role="status">
      <ShieldCheckIcon size={22} />
      <div>
        <strong>
          {capability?.supported
            ? "Local voice studio · GPU ready"
            : capability?.state === "loading"
              ? "Loading your local voice model…"
              : capability?.state === "warming_up"
                ? "Preparing GPU acceleration…"
                : "Local voice studio · GPU unavailable"}
        </strong>
        <p>
          {capability?.message || "Checking your private local voice worker."}
        </p>
        {capability?.model && (
          <small>
            {capability.gpu_name || "NVIDIA worker"} ·{" "}
            {capability.model.split("/").at(-1)}
          </small>
        )}
        {capability && !capability.supported && (
          <button className="text-link" onClick={refresh}>
            Check availability again
          </button>
        )}
      </div>
    </div>
  );
}
function useCapability() {
  const [capability, setCapability] = useState<Capability | null>(null);
  const refresh = useCallback(() => {
    void api<Capability>("/voices/capabilities")
      .then(setCapability)
      .catch((e) =>
        setCapability({ supported: false, model: "", message: e.message }),
      );
  }, []);
  useEffect(refresh, [refresh]);
  useEffect(() => {
    const timer = setInterval(refresh, capability?.supported ? 15000 : 5000);
    return () => clearInterval(timer);
  }, [capability?.supported, refresh]);
  return { capability, refresh };
}

export function VoiceStudio() {
  const [voices, setVoices] = useState<Voice[]>([]),
    [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Voice | null>(null),
    [deleting, setDeleting] = useState<Voice | null>(null),
    [name, setName] = useState(""),
    [busy, setBusy] = useState(false);
  const { capability, refresh } = useCapability();
  const load = useCallback(async () => {
    try {
      setVoices(await api<Voice[]>("/voices"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      setEditing(null);
      setDeleting(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const filtered = voices.filter((v) =>
    v.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  return (
    <div className="page voice-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">YOUR PERSONAL VOICE STUDIO</p>
          <h1>Your Voices</h1>
          <p>Create a voice once. Bring it to every summary.</p>
        </div>
        <Link className="button" to="/voices/new">
          <PlusIcon /> Create New Voice
        </Link>
      </div>
      <CapabilityNotice capability={capability} refresh={refresh} />
      {error && (
        <div role="alert" className="notice">
          {error}
        </div>
      )}
      <div className="voice-library-toolbar">
        <label className="sr-only" htmlFor="voice-search">
          Search your voices
        </label>
        <input
          id="voice-search"
          type="search"
          placeholder="Search your voices..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="subtle">
          {voices.length} private {voices.length === 1 ? "voice" : "voices"}
        </span>
      </div>
      {loading ? (
        <p role="status">Loading your voices…</p>
      ) : voices.length === 0 ? (
        <section className="voice-empty">
          <span className="voice-symbol">
            <WaveformIcon size={34} />
          </span>
          <h2>A voice that feels like you.</h2>
          <p>
            No voices yet. Create your first voice to personalize your meeting
            summaries.
          </p>
          <Link to="/voices/new" className="button">
            <PlusIcon /> Create New Voice
          </Link>
          <small>
            <ShieldCheckIcon /> Private to your account. Always with permission.
          </small>
        </section>
      ) : (
        <div className="voice-library">
          {filtered.map((v) => (
            <article className="voice-card" key={v.id}>
              <div className="voice-card-top">
                <span className="voice-symbol">
                  <WaveformIcon size={26} />
                </span>
                <span
                  className={
                    "voice-state " +
                    (v.saved && v.preview_current ? "saved" : "")
                  }
                >
                  {v.saved && v.preview_current
                    ? "Ready to narrate"
                    : "Private draft"}
                </span>
              </div>
              <h2>{v.name}</h2>
              <p>
                {v.description ||
                  "A personal voice for your meeting summaries."}
              </p>
              <small>
                {date(v.created)} · {duration(v.duration)} sample ·{" "}
                {v.language.toUpperCase()}
              </small>
              {v.has_preview && (
                <audio
                  aria-label={v.name + " preview"}
                  controls
                  preload="none"
                  src={`/api/voices/${v.id}/preview`}
                />
              )}
              <div className="voice-card-actions">
                {(!v.saved || !v.preview_current) && (
                  <Link className="text-link" to={"/voices/" + v.id}>
                    Finish voice <PlayIcon />
                  </Link>
                )}
                <button
                  className="text-link"
                  onClick={() => {
                    setEditing(v);
                    setName(v.name);
                  }}
                >
                  <PencilSimpleIcon /> Rename
                </button>
                <button
                  className="icon-button danger"
                  aria-label={"Delete " + v.name}
                  onClick={() => setDeleting(v)}
                >
                  <TrashIcon />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {!loading && voices.length > 0 && filtered.length === 0 && (
        <p>No voices match your search.</p>
      )}
      {(editing || deleting) && (
        <VoiceDialog
          title={editing ? "Rename voice" : "Permanently delete this voice?"}
          close={() => {
            if (!busy) {
              setEditing(null);
              setDeleting(null);
            }
          }}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void mutate(() =>
                editing
                  ? api(`/voices/${editing.id}`, {
                      method: "PATCH",
                      body: JSON.stringify({ name }),
                    })
                  : api(`/voices/${deleting!.id}`, { method: "DELETE" }),
              );
            }}
          >
            {editing ? (
              <label>
                Voice name
                <input
                  autoFocus
                  required
                  maxLength={100}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
            ) : (
              <p>
                The reference recording and preview for “{deleting?.name}” will
                be permanently removed. Existing meeting narrations remain
                available.
              </p>
            )}
            {error && <p role="alert">{error}</p>}
            <div className="button-row">
              <button
                className={"button " + (deleting ? "danger" : "")}
                disabled={busy || (!!editing && !name.trim())}
                type="submit"
              >
                {busy
                  ? "Saving…"
                  : editing
                    ? "Save name"
                    : "Delete voice permanently"}
              </button>
              <button
                className="button secondary"
                disabled={busy}
                type="button"
                onClick={() => {
                  setEditing(null);
                  setDeleting(null);
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        </VoiceDialog>
      )}
    </div>
  );
}

function VoiceDialog({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      className="voice-dialog"
      ref={ref}
      aria-labelledby="voice-dialog-title"
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
    >
      <h2 id="voice-dialog-title">{title}</h2>
      {children}
    </dialog>
  );
}

function useRecorder() {
  const [state, setState] = useState<
    "idle" | "requesting" | "recording" | "paused" | "review"
  >("idle");
  const [blob, setBlob] = useState<Blob | null>(null),
    [seconds, setSeconds] = useState(0),
    [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null),
    stream = useRef<MediaStream | null>(null),
    context = useRef<AudioContext | null>(null),
    frame = useRef(0),
    level = useRef<HTMLMeterElement>(null),
    mounted = useRef(true);
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!blob) {
      setUrl("");
      return;
    }
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob]);
  const release = useCallback(() => {
    cancelAnimationFrame(frame.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    if (context.current?.state !== "closed") void context.current?.close();
    context.current = null;
  }, []);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (recorder.current && recorder.current.state !== "inactive")
        recorder.current.stop();
      release();
    };
  }, [release]);
  useEffect(() => {
    if (state !== "recording" && state !== "paused") return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [state]);
  function stop() {
    if (recorder.current && recorder.current.state !== "inactive")
      recorder.current.stop();
  }
  async function start() {
    setError("");
    setState("requesting");
    try {
      if (
        !navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === "undefined"
      )
        throw new Error(
          "Microphone recording requires a supported browser on HTTPS or localhost.",
        );
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current) {
        s.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = s;
      const mime = [
        "audio/webm;codecs=opus",
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ].find((t) => MediaRecorder.isTypeSupported(t));
      const r = new MediaRecorder(s, mime ? { mimeType: mime } : undefined);
      recorder.current = r;
      const parts: Blob[] = [];
      let elapsed = 0,
        previous = performance.now();
      r.ondataavailable = (e) => {
        if (e.data.size) parts.push(e.data);
      };
      r.onerror = () => {
        if (mounted.current)
          setError(
            "Recording was interrupted. Review the captured sample or record again.",
          );
        stop();
      };
      r.onstop = () => {
        release();
        if (mounted.current) {
          setBlob(new Blob(parts, { type: r.mimeType }));
          setState("review");
        }
      };
      s.getAudioTracks().forEach((t) => {
        t.onended = stop;
      });
      const ac = new AudioContext();
      context.current = ac;
      await ac.resume();
      const analyser = ac.createAnalyser();
      analyser.fftSize = 256;
      ac.createMediaStreamSource(s).connect(analyser);
      const values = new Uint8Array(analyser.fftSize);
      r.start(250);
      setBlob(null);
      setSeconds(0);
      setState("recording");
      function tick() {
        const now = performance.now();
        if (r.state === "recording") elapsed += (now - previous) / 1000;
        previous = now;
        if (mounted.current) setSeconds(Math.min(elapsed, 30));
        analyser.getByteTimeDomainData(values);
        if (level.current)
          level.current.value =
            r.state === "recording"
              ? Math.min(
                  1,
                  Math.sqrt(
                    values.reduce((sum, n) => sum + (n - 128) ** 2, 0) /
                      values.length,
                  ) / 35,
                )
              : 0;
        if (elapsed >= 30) {
          stop();
          return;
        }
        frame.current = requestAnimationFrame(tick);
      }
      frame.current = requestAnimationFrame(tick);
    } catch (e) {
      release();
      if (mounted.current) {
        setState("idle");
        setError(
          (e as Error).name === "NotAllowedError"
            ? "Microphone permission was denied. Allow microphone access in your browser and try again."
            : (e as Error).name === "NotFoundError"
              ? "No microphone was found. Connect one and try again."
              : (e as Error).message,
        );
      }
    }
  }
  function togglePause() {
    const r = recorder.current;
    if (r?.state === "recording") {
      r.pause();
      setState("paused");
    } else if (r?.state === "paused") {
      r.resume();
      setState("recording");
    }
  }
  return {
    state,
    blob,
    seconds,
    error,
    url,
    level,
    start,
    stop,
    togglePause,
    reset: () => {
      setBlob(null);
      setSeconds(0);
      setState("idle");
    },
  };
}

export function VoiceCreator() {
  const { voiceId } = useParams(),
    navigate = useNavigate();
  const [profile, setProfile] = useState<Voice | null>(null),
    [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [language, setLanguage] = useState("en"),
    [refText, setRefText] = useState(PARAGRAPH),
    [consent, setConsent] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const { capability, refresh } = useCapability(),
    recording = useRecorder();
  useEffect(() => {
    if (!voiceId) return;
    let current = true;
    void api<Voice>("/voices/" + voiceId)
      .then((v) => {
        if (current) {
          setProfile(v);
          setName(v.name);
          setDescription(v.description);
          setLanguage(v.language);
          setRefText(v.reference_text || "");
          setConsent(true);
        }
      })
      .catch((e) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
    };
  }, [voiceId]);
  useEffect(() => {
    if (!profile?.preview_running) return;
    const timer = setInterval(() => {
      void api<Voice>(`/voices/${profile.id}`)
        .then(setProfile)
        .catch((e) => setError(e.message));
    }, 1500);
    return () => clearInterval(timer);
  }, [profile?.id, profile?.preview_running]);
  const recordingActive = ["requesting", "recording", "paused"].includes(
    recording.state,
  );
  async function persist() {
    if (profile) {
      if (profile.reference_text !== refText || profile.language !== language) {
        const updated = await api<Voice>(`/voices/${profile.id}`, {
          method: "PATCH",
          body: JSON.stringify({ reference_text: refText, language }),
        });
        setProfile(updated);
        return updated;
      }
      return profile;
    }
    if (!recording.blob || !consent || !name.trim())
      throw new Error(
        "Add a voice name, recording and explicit permission first.",
      );
    const form = new FormData();
    form.set("name", name.trim());
    form.set("description", description);
    form.set("language", language);
    form.set("reference_text", refText);
    form.set("consent", "true");
    const type = recording.blob.type,
      ext = type.includes("mp4")
        ? "m4a"
        : type.includes("ogg")
          ? "ogg"
          : "webm";
    form.set("file", recording.blob, "my-voice." + ext);
    const v = await api<Voice>("/voices", { method: "POST", body: form });
    setProfile(v);
    return v;
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const sample = profile
    ? `/api/voices/${profile.id}/reference`
    : recording.url;
  return (
    <div className="page voice-page voice-creator">
      <Link className="text-link back-link" to="/voices">
        <ArrowLeftIcon /> Your Voices
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">A LITTLE MORE YOU</p>
          <h1>Create your personal voice.</h1>
          <p>
            A short recording. A familiar voice. Every summary, easier to
            revisit.
          </p>
        </div>
        <span className="voice-symbol large">
          <WaveformIcon size={36} />
        </span>
      </div>
      <CapabilityNotice capability={capability} refresh={refresh} />
      {(error || recording.error) && (
        <div className="notice" role="alert">
          {error || recording.error}
        </div>
      )}
      <div className="voice-create-grid">
        <div className="voice-create-main">
          <section className="voice-panel">
            <div className="voice-step">
              <span>1</span>
              <div>
                <h2>Give your voice a name</h2>
                <p>Make it easy to find in your personal library.</p>
              </div>
            </div>
            <div className="voice-fields">
              <label>
                Voice name
                <input
                  required
                  maxLength={100}
                  placeholder="My Personal Voice"
                  value={name}
                  disabled={!!profile || busy}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label>
                Language
                <select
                  value={language}
                  disabled={busy || profile?.preview_running}
                  onChange={(e) => setLanguage(e.target.value)}
                >
                  {[
                    ["en", "English"],
                    ["fr", "French"],
                    ["de", "German"],
                    ["es", "Spanish"],
                    ["it", "Italian"],
                    ["pt", "Portuguese"],
                    ["zh", "Chinese"],
                    ["ja", "Japanese"],
                    ["ko", "Korean"],
                    ["ru", "Russian"],
                  ].map(([id, label]) => (
                    <option value={id} key={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="voice-description">
                Description <span className="subtle">(optional)</span>
                <textarea
                  rows={2}
                  maxLength={500}
                  value={description}
                  placeholder="Warm and clear, for my weekly summaries."
                  disabled={!!profile || busy}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
            </div>
          </section>
          <section className="voice-panel">
            <div className="voice-step">
              <span>2</span>
              <div>
                <h2>Let your voice do the talking</h2>
                <p>
                  Read this paragraph naturally in a quiet space. Aim for 15–30
                  seconds.
                </p>
              </div>
            </div>
            <blockquote className="voice-reading">{PARAGRAPH}</blockquote>
            <label className="voice-transcript">
              Exact reference transcript
              <textarea
                rows={5}
                maxLength={5000}
                value={refText}
                disabled={busy || profile?.preview_running}
                onChange={(e) => setRefText(e.target.value)}
              />
              <small>
                Edit this to match the words you actually recorded. Existing
                voices need a confirmed transcript before local generation.
              </small>
            </label>
            <div className="voice-recorder">
              <span
                className={
                  "voice-record-dot " +
                  (recording.state === "recording" ? "on" : "")
                }
              />
              <span className="voice-timer">
                {duration(profile?.duration || recording.seconds)}
              </span>
              <label className="voice-level">
                Microphone level
                <meter min={0} max={1} value={0} ref={recording.level} />
              </label>
              <span className="subtle">
                {recording.state === "paused"
                  ? "Paused"
                  : recording.state === "recording"
                    ? "Recording"
                    : "3–30 seconds"}
              </span>
            </div>
            <div className="button-row">
              {!sample && !recordingActive && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void recording.start()}
                >
                  <MicrophoneIcon /> Start Recording
                </button>
              )}
              {recording.state === "requesting" && (
                <p role="status">Waiting for microphone permission…</p>
              )}
              {["recording", "paused"].includes(recording.state) && (
                <>
                  <button
                    className="button secondary"
                    onClick={recording.togglePause}
                  >
                    {recording.state === "paused" ? (
                      <PlayIcon />
                    ) : (
                      <PauseIcon />
                    )}
                    {recording.state === "paused" ? "Resume" : "Pause"}
                  </button>
                  <button className="button" onClick={recording.stop}>
                    <StopIcon /> Stop Recording
                  </button>
                </>
              )}
              {sample && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => {
                    if (profile) {
                      if (
                        !window.confirm(
                          "Delete this reference and preview to record again? Existing meeting narrations will remain.",
                        )
                      )
                        return;
                      void perform(async () => {
                        await api("/voices/" + profile.id, {
                          method: "DELETE",
                        });
                        setProfile(null);
                        setConsent(false);
                        recording.reset();
                        navigate("/voices/new", { replace: true });
                      });
                    } else recording.reset();
                  }}
                >
                  Record Again
                </button>
              )}
            </div>
            {sample && (
              <div className="voice-sample">
                <label>Review your reference recording</label>
                <audio
                  aria-label="Your reference recording"
                  controls
                  src={sample}
                />
              </div>
            )}
          </section>
        </div>
        <aside className="voice-create-side">
          <section className="voice-panel">
            <div className="voice-step">
              <span>3</span>
              <div>
                <h2>Your voice. Your permission.</h2>
              </div>
            </div>
            <label className="voice-consent">
              <input
                type="checkbox"
                checked={consent}
                disabled={!!profile || busy}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <span>{CONSENT}</span>
            </label>
            <p className="voice-privacy">
              <ShieldCheckIcon /> Your reference stays on this computer and is
              private to your account. Qwen3-TTS generates cloned speech locally
              on the NVIDIA GPU. You can permanently delete your voice and its
              reference recordings at any time.
            </p>
          </section>
          <section className="voice-panel">
            <div className="voice-step">
              <span>4</span>
              <div>
                <h2>Hear it for yourself</h2>
                <p>Review a real generated preview before saving.</p>
              </div>
            </div>
            <p className="voice-preview-quote">
              “Hello! This is my new voice in SUMX. I can now narrate my meeting
              summaries.”
            </p>
            {profile?.has_preview &&
            profile.preview_current &&
            profile.reference_text === refText &&
            profile.language === language ? (
              <>
                <audio
                  aria-label="Generated voice preview"
                  controls
                  src={`/api/voices/${profile.id}/preview`}
                />
                <p className="voice-success">
                  Preview generated. Listen and decide if it feels right.
                </p>
                <button
                  className="button"
                  disabled={busy || profile.saved}
                  onClick={() =>
                    void perform(async () => {
                      await api(`/voices/${profile.id}`, {
                        method: "PATCH",
                        body: JSON.stringify({ saved: true }),
                      });
                      navigate("/voices");
                    })
                  }
                >
                  {profile.saved ? "Voice saved" : "Save Voice"}
                </button>
              </>
            ) : (
              <>
                <button
                  className="button"
                  disabled={
                    busy ||
                    profile?.preview_running ||
                    recordingActive ||
                    !sample ||
                    !name.trim() ||
                    !consent ||
                    !refText.trim() ||
                    !capability?.supported
                  }
                  onClick={() =>
                    void perform(async () => {
                      const v = await persist();
                      setProfile(await post<Voice>(`/voices/${v.id}/preview`));
                    })
                  }
                >
                  <PlayIcon />
                  {profile?.preview_running
                    ? "Generating locally…"
                    : busy
                      ? "Processing…"
                      : profile
                        ? "Retry Preview"
                        : "Generate Voice Preview"}
                </button>
                <button
                  className="button secondary"
                  disabled={
                    busy ||
                    recordingActive ||
                    !sample ||
                    !name.trim() ||
                    !consent
                  }
                  onClick={() =>
                    void perform(async () => {
                      await persist();
                      navigate("/voices");
                    })
                  }
                >
                  Keep private draft
                </button>
              </>
            )}
            {profile?.preview_running && (
              <p role="status">
                {(
                  {
                    queued: "Waiting for the GPU…",
                    preparing_voice: "Preparing your voice…",
                    generating_speech: "Generating speech on your GPU…",
                    generating_audio: "Streaming speech on your GPU…",
                    encoding_audio: "Preparing playback…",
                  } as Record<string, string>
                )[profile.preview_stage || "queued"] || "Generating locally…"}
              </p>
            )}
            {profile?.preview_error && (
              <p role="alert">{profile.preview_error}</p>
            )}
            <small>
              Uses your saved recording each time. No custom model is trained. A
              draft has no generated voice yet.
            </small>
          </section>
        </aside>
      </div>
    </div>
  );
}

export function NarrationPicker({
  disabled,
  generate,
}: {
  disabled: boolean;
  generate: (selection: Selection) => Promise<void>;
}) {
  const [type, setType] = useState<"preset" | "cloned">("preset"),
    [presets, setPresets] = useState<string[]>([]),
    [preset, setPreset] = useState(""),
    [voices, setVoices] = useState<Voice[]>([]),
    [selected, setSelected] = useState(""),
    [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const [capability, setCapability] = useState<Capability | null>(null);
  useEffect(() => {
    if (type !== "cloned") return;
    let current = true;
    setLoading(true);
    setError("");
    void Promise.all([
      api<Voice[]>("/voices"),
      api<Capability>("/voices/capabilities"),
    ])
      .then(([v, c]) => {
        if (current) {
          setVoices(v.filter((v) => v.usable));
          setCapability(c);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (current) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => {
      current = false;
    };
  }, [type]);
  async function loadPresets() {
    setLoading(true);
    setError("");
    try {
      const d = await api<{ voices: string[]; default: string }>(
        "/voices/presets",
      );
      setPresets(d.voices);
      setPreset(d.default);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  const filtered = voices.filter((v) =>
    v.name.toLowerCase().includes(query.toLowerCase()),
  );
  const chosen = voices.find((v) => v.id === selected);
  return (
    <div className="narration-picker">
      <div
        className="voice-segment"
        role="group"
        aria-label="Narration voice category"
      >
        {(["preset", "cloned"] as const).map((t) => (
          <button
            aria-pressed={type === t}
            className={type === t ? "selected" : ""}
            key={t}
            onClick={() => setType(t)}
          >
            {t === "preset" ? "Preset Voices" : "Cloned Voices"}
          </button>
        ))}
      </div>
      {error && <p role="alert">{error}</p>}
      {type === "preset" ? (
        <div className="button-row">
          <button
            className="button secondary"
            disabled={loading}
            onClick={() => void loadPresets()}
          >
            Load available voices
          </button>
          {presets.length > 0 && (
            <select
              aria-label="Narration voice"
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
            >
              {presets.map((v) => (
                <option key={v} value={v}>
                  {v.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          )}
          <button
            className="button"
            disabled={disabled || loading}
            onClick={() =>
              void generate({
                voice_type: "preset",
                preset_voice_id: preset || null,
              })
            }
          >
            <PlayIcon /> Generate narration
          </button>
        </div>
      ) : (
        <div className="voice-cloned-picker">
          {loading ? (
            <p role="status">Loading your saved voices…</p>
          ) : (
            <>
              {capability && !capability.supported && (
                <p className="voice-availability">{capability.message}</p>
              )}
              {voices.length === 0 ? (
                <p>
                  You haven't created a voice yet.{" "}
                  <Link className="text-link" to="/voices">
                    Create a voice
                  </Link>
                </p>
              ) : (
                <>
                  <label>
                    Search cloned voices
                    <input
                      type="search"
                      placeholder="Search by voice name"
                      value={query}
                      onChange={(e) => {
                        setQuery(e.target.value);
                        setSelected("");
                      }}
                    />
                  </label>
                  <label>
                    Select a cloned voice
                    <select
                      value={selected}
                      onChange={(e) => setSelected(e.target.value)}
                    >
                      <option value="">Choose your voice</option>
                      {filtered.map((v) => (
                        <option value={v.id} key={v.id}>
                          {v.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {filtered.length === 0 && <p>No matching voices.</p>}
                  {chosen?.has_preview && (
                    <audio
                      controls
                      aria-label="Selected cloned voice preview"
                      src={`/api/voices/${chosen.id}/preview`}
                    />
                  )}
                  <button
                    className="button"
                    disabled={
                      disabled ||
                      !chosen ||
                      !(capability?.supported || capability?.accepting)
                    }
                    onClick={() =>
                      void generate({
                        voice_type: "cloned",
                        cloned_voice_id: selected,
                      })
                    }
                  >
                    <PlayIcon /> Generate narration
                  </button>
                  <Link to="/voices" className="text-link">
                    Manage your voices
                  </Link>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
