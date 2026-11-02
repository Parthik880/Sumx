import { useEffect, useState } from "react";
import {
  CheckCircleIcon,
  WaveformIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import { api, duration, post } from "./api";

type Status = {
  id: string;
  meeting_id: string;
  state: string;
  stage: string;
  model: string | null;
  engine: string;
  gpu_name: string | null;
  completed_segments: number;
  total_segments: number;
  percent: number | null;
  elapsed_seconds: number;
  audio_seconds: number;
  segment_audio_seconds: number;
  stream_events: number;
  error: string | null;
  audio_ready: boolean;
};
const labels: Record<string, string> = {
  QUEUED: "Waiting for the worker",
  LOADING_MODEL: "Loading model and capturing CUDA graphs",
  PREPARING_VOICE: "Preparing your saved voice",
  GENERATING_AUDIO: "Generating audio",
  MERGING_AUDIO: "Merging audio segments",
  FINALIZING: "Validating and saving the MP3",
  COMPLETED: "Narration ready",
  FAILED: "Narration failed",
};

export default function NarrationProgress({
  jobId,
  pending,
  onFinished,
}: {
  jobId?: string;
  pending?: string;
  onFinished: () => Promise<void>;
}) {
  const [data, setData] = useState<Status | null>(null),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0),
    [retrying, setRetrying] = useState(false);
  useEffect(() => {
    if (!jobId || pending) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setData(null);
    setError("");
    async function poll() {
      try {
        const next = await api<Status>(`/narration/jobs/${jobId}/status`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setData(next);
        setError("");
        if (next.state === "completed" || next.state === "failed") {
          void onFinished();
          return;
        }
        timer = setTimeout(() => void poll(), 1000);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError((e as Error).message);
          timer = setTimeout(() => void poll(), 3000);
        }
      }
    }
    void poll();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId, pending, revision, onFinished]);
  if (!pending && !jobId) return null;
  const current = pending ? null : data,
    stage = current?.stage || "QUEUED",
    done = current?.state === "completed",
    failed = current?.state === "failed";
  const percent = current?.percent ?? null;
  async function retry() {
    if (!current) return;
    setRetrying(true);
    try {
      await post(`/meetings/${current.meeting_id}/jobs/${current.id}/retry`);
      setRevision((v) => v + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRetrying(false);
    }
  }
  return (
    <section
      className={"narration-progress " + (failed ? "failed" : "")}
      aria-label="Narration generation progress"
      aria-busy={!done && !failed}
    >
      <div className="narration-progress-heading">
        <span className="voice-symbol">
          {done ? (
            <CheckCircleIcon size={25} />
          ) : failed ? (
            <WarningCircleIcon size={25} />
          ) : (
            <WaveformIcon size={25} />
          )}
        </span>
        <div>
          <h4>
            {done
              ? "Narration ready"
              : failed
                ? "Narration failed"
                : "Generating narration"}
          </h4>
          <p>
            {current?.engine || pending || "GPU narration"}
            {current?.gpu_name ? " · " + current.gpu_name : ""}
          </p>
        </div>
        <strong>
          {percent !== null ? `${Math.round(percent)}%` : done ? "100%" : ""}
        </strong>
      </div>
      <div
        className={
          "narration-progress-track " +
          (percent === null && !failed ? "indeterminate" : "")
        }
        role="progressbar"
        aria-label="Completed narration segments"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-valuetext={
          percent === null
            ? labels[stage]
            : `${Math.round(percent)}%, ${labels[stage]}`
        }
      >
        <span style={percent !== null ? { width: `${percent}%` } : undefined} />
      </div>
      <div className="narration-progress-detail">
        <span>
          {labels[stage] || stage}
          {stage === "GENERATING_AUDIO" && current?.total_segments
            ? ` · segment ${Math.min(current.completed_segments + 1, current.total_segments)} of ${current.total_segments}`
            : ""}
        </span>
        <span>Elapsed: {duration(current?.elapsed_seconds || 0)}</span>
      </div>
      {!!current?.total_segments && (
        <p className="narration-progress-count">
          {current.completed_segments} of {current.total_segments} text segments
          complete
        </p>
      )}
      {!done && !failed && current?.segment_audio_seconds ? (
        <p className="narration-stream-activity">
          <i aria-hidden="true" />
          Receiving audio · {current.segment_audio_seconds.toFixed(1)} seconds
          generated in this segment
        </p>
      ) : null}
      {current?.error && <p role="alert">{current.error}</p>}
      {error && <p role="alert">{error}</p>}
      {failed && (
        <button
          className="button secondary"
          disabled={retrying}
          onClick={() => void retry()}
        >
          {retrying ? "Retrying…" : "Retry narration"}
        </button>
      )}
      {done && (
        <p className="narration-progress-count">
          Your narration is ready to play and download below.
        </p>
      )}
    </section>
  );
}
