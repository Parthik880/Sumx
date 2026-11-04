export type User = {
  id: string;
  name: string;
  email: string;
  role: string;
  csrf: string;
  auth_method: string;
  admin_id: string | null;
};
export type Action = {
  task: string;
  owner: string | null;
  deadline: string | null;
  timestamp: number | null;
};
export type Minutes = {
  title: string;
  executive_summary: string;
  discussion_points: string[];
  decisions: string[];
  action_items: Action[];
  unresolved_issues: string[];
  follow_up: string[];
};
export type Job = {
  id: string;
  kind: string;
  state: string;
  stage: string;
  completed: number;
  total: number;
  error: string | null;
  retry_after_seconds?: number;
  summary_retry_after_seconds?: number;
  started_at?: number;
  completed_at?: number;
  elapsed_seconds?: number;
  progress_percent?: number;
  summary_progress?: {
    stage?: string;
    stage_started_at?: number;
    notes_completed?: number;
    notes_total?: number;
    mode?: SummaryMode;
    timings?: Record<string, number>;
    saved_at?: number;
  };
};
export type SummaryMode = "concise" | "standard" | "detailed";
export type SummaryInfo = {
  mode: SummaryMode;
  exists: boolean;
  status: "not_generated" | "generating" | "generated" | "failed";
  error: string | null;
  version?: number;
  transcript_version?: number;
  audio_count?: number;
};
export type SummaryRecord = SummaryInfo & {
  content?: Minutes;
  audio?: NonNullable<Meeting["audio"]>;
};
export type SummaryOverview = {
  modes: Record<SummaryMode, SummaryInfo>;
  latest_mode: SummaryMode | null;
  narration_options: SummaryMode[];
  legacy: { id: string; version: number; content: Minutes } | null;
};
export type Meeting = {
  id: string;
  title: string;
  created: number;
  duration: number;
  status: string;
  error: string | null;
  transcript_version: number;
  summary_version: number;
  retain_source: boolean;
  detail: SummaryMode;
  source_available?: boolean;
  media_type?: string;
  transcript?: {
    text: string;
    segments: { text: string; start: number; end: number; speaker?: string }[];
    version: number;
    language?: string;
  };
  summary?: { content: Minutes; version: number; transcript_version: number };
  audio?: {
    id: string;
    voice: string;
    voice_type?: string;
    voice_name?: string;
    summary_version: number;
  }[];
  job?: Job;
};
export type Config = {
  max_upload_mb: number;
  max_duration_seconds: number;
  ai_configured: boolean;
  default_voice: string;
  models: { stt: string; summary: string; tts: string; cloning: string };
};
let csrf = "";
type AuthFailure = "access_revoked" | "session_required";
let authFailureHandler: ((reason: AuthFailure) => void) | null = null;
export function setAuthFailureHandler(handler: typeof authFailureHandler) {
  authFailureHandler = handler;
}
function sessionFailure(code: string | null, requestCsrf: string) {
  // An old request must not sign out a newer session after an account switch.
  if (
    requestCsrf === csrf &&
    (code === "access_revoked" || code === "session_required")
  ) {
    authFailureHandler?.(code);
  }
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public retryAfter?: number,
  ) {
    super(message);
  }
}
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const requestCsrf = csrf;
  const response = await fetch("/api" + path, {
    ...options,
    credentials: "include",
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      "X-CSRF-Token": csrf,
      ...options.headers,
    },
  });
  if (!response.ok) {
    sessionFailure(response.headers.get("X-SUMX-Auth-Error"), requestCsrf);
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      typeof body.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`,
      response.status,
      Number(response.headers.get("Retry-After")) || undefined,
    );
  }
  return response.json();
}
export function post<T = any>(path: string, data: unknown = {}, key: string = crypto.randomUUID()) {
  return api<T>(path, {
    method: "POST",
    body: JSON.stringify(data),
    headers: { "Idempotency-Key": key },
  });
}
export function upload(
  data: FormData,
  key: string,
  recording: boolean,
  progress: (percent: number) => void,
): Promise<Meeting> {
  return new Promise((resolve, reject) => {
    const requestCsrf = csrf;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/meetings/" + (recording ? "recordings" : "upload"));
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.setRequestHeader("Idempotency-Key", key);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onerror = () =>
      reject(
        new Error(
          "Upload connection failed. Your recording is still here; retry when connected.",
        ),
      );
    xhr.onload = () => {
      let body;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error("Upload response could not be read"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else {
        sessionFailure(xhr.getResponseHeader("X-SUMX-Auth-Error"), requestCsrf);
        reject(
          new Error(
            typeof body.detail === "string" ? body.detail : "Upload failed",
          ),
        );
      }
    };
    xhr.send(data);
  });
}
export function duration(seconds: number) {
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
}
export function date(seconds: number) {
  return new Date(seconds * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
export const active = (m: Meeting) =>
  [
    "queued",
    "extracting",
    "transcribing",
    "summarizing",
    "generating_speech",
  ].includes(m.status);
