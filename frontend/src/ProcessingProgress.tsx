import React, { useEffect, useState } from "react";
import { WaveformIcon } from "@phosphor-icons/react";
import { duration, Job } from "./api";

const stages: Record<string, string> = {
  queued: "Waiting for the processing worker",
  extracting: "Extracting compatible audio",
  transcribing: "Transcribing audio",
  summarizing: "Generating meeting notes",
  summary_notes: "Generating meeting notes",
  summary_synthesis: "Combining meeting notes",
  summary_finalizing: "Finalizing summary",
  summary_saving: "Saving meeting",
  completed: "Completed",
};

function Elapsed({ started, ended }: { started: number; ended?: number }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    if (ended) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [ended]);
  return (
    <p className="subtle">
      Elapsed {duration(Math.max(0, (ended ?? now) - started))}
    </p>
  );
}

export default React.memo(function ProcessingProgress({
  job,
  status,
}: {
  job: Job;
  status: string;
}) {
  const completed = job.state === "completed";
  const waiting =
    job.retry_after_seconds || job.summary_retry_after_seconds || 0;
  const phase = completed ? "completed" : job.stage;
  const percent = completed
    ? 100
    : Math.min(
        99,
        job.progress_percent ??
          (job.total ? (100 * job.completed) / job.total : 0),
      );
  const notes = job.summary_progress;
  return (
    <div className="processing-panel" role="status">
      <span className="processing-icon">
        <WaveformIcon size={24} />
      </span>
      <div>
        <strong>
          {waiting
            ? phase === "transcribing"
              ? "Waiting for transcription service"
              : "Waiting for summary service"
            : (stages[phase] ?? stages[status] ?? "Processing meeting")}
        </strong>
        <p>
          {completed
            ? "Your meeting minutes are saved."
            : phase === "summary_notes" && notes
              ? `${notes.notes_completed ?? 0} of ${notes.notes_total ?? 0} meeting notes ready`
              : phase === "summary_synthesis"
                ? "Combining all notes into the final summary."
                : phase === "summary_finalizing"
                  ? "Checking and finalizing the summary."
                  : phase === "summary_saving"
                    ? "Saving the validated summary to your meeting."
                    : job.total
                      ? `${job.completed} of ${job.total} parts complete`
                      : "Your progress is saved. You can leave this page and return later."}
        </p>
        {waiting > 0 && (
          <p>
            The {phase === "transcribing" ? "transcription" : "summary"} service
            is temporarily busy. Retrying in about {waiting} seconds. Your
            completed parts are saved.
          </p>
        )}
        {job.started_at && (
          <Elapsed started={job.started_at} ended={job.completed_at} />
        )}
      </div>
      {!!job.total && (
        <progress
          max={100}
          value={percent}
          aria-label="Completed processing parts"
        />
      )}
    </div>
  );
});
