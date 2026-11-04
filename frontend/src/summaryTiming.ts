import type { Minutes } from "./api";
const retrieved = new WeakMap<
  Minutes,
  {
    meeting_id: string;
    mode: string;
    version?: number;
    started: number;
    received: number;
  }
>();

export function summaryRetrieved(
  content: Minutes,
  meeting_id: string,
  mode: string,
  version: number | undefined,
  started: number,
) {
  const received = performance.now();
  retrieved.set(content, { meeting_id, mode, version, started, received });
  console.info("sumx_summary_timing", {
    event: "summary_retrieved",
    meeting_id,
    mode,
    version,
    milliseconds: received - started,
  });
}

export function summaryCommitted(
  content: Minutes,
  phase: string,
  actualDuration: number | null,
  commitTime: number,
) {
  const trace = retrieved.get(content);
  if (!trace) return;
  console.info("sumx_summary_timing", {
    event: "summary_committed",
    meeting_id: trace.meeting_id,
    mode: trace.mode,
    version: trace.version,
    phase,
    render_cpu_ms: actualDuration,
    received_to_commit_ms: commitTime - trace.received,
    request_to_commit_ms: commitTime - trace.started,
  });
  if (phase === "layout")
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        console.info("sumx_summary_timing", {
          event: "summary_paint_observed",
          meeting_id: trace.meeting_id,
          mode: trace.mode,
          version: trace.version,
          commit_to_paint_ms: performance.now() - commitTime,
        });
      }),
    );
}
