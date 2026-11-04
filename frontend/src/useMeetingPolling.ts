import { useCallback, useEffect, useRef } from "react";
import { active, api, Meeting, SummaryOverview } from "./api";

// Full content is fetched once per changed version, never on every progress tick.
function contentVersion(m: Meeting) {
  return JSON.stringify([
    m.transcript_version,
    m.summary_version,
    m.title,
    m.retain_source,
    m.job?.id,
    m.job?.state === "failed"
      ? "failed"
      : m.job?.state === "completed"
        ? "completed"
        : "active",
  ]);
}
function statusVersion(m: Meeting) {
  return JSON.stringify({
    ...m,
    job: m.job ? { ...m.job, elapsed_seconds: undefined } : undefined,
  });
}

export function useMeetingPolling(
  id: string | undefined,
  setMeeting: React.Dispatch<React.SetStateAction<Meeting | null>>,
  setOverview: React.Dispatch<React.SetStateAction<SummaryOverview | null>>,
  setError: React.Dispatch<React.SetStateAction<string>>,
) {
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    let stopped = false,
      polling = false,
      revision = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let latest: Meeting | null = null,
      loadedVersion = "",
      lastStatus = "";
    let fullPending: Promise<void> | null = null;
    setMeeting(null);
    setOverview(null);

    function schedule() {
      clearTimeout(timer);
      if (!stopped)
        timer = setTimeout(
          () => void poll(),
          latest && active(latest) ? 1500 : 10000,
        );
    }
    function full() {
      if (fullPending) return fullPending;
      const began = performance.now();
      ++revision;
      fullPending = Promise.all([
        api<Meeting>(`/meetings/${id}`, { signal: controller.signal }),
        api<SummaryOverview>(`/meetings/${id}/summaries`, {
          signal: controller.signal,
        }),
      ])
        .then(([m, overview]) => {
          if (stopped) return;
          latest = m;
          loadedVersion = contentVersion(m);
          setMeeting(m);
          setOverview(overview);
          setError("");
          console.info("sumx_summary_timing", {
            event: "detail_retrieved",
            meeting_id: id,
            milliseconds: performance.now() - began,
            summary_version: m.summary_version,
          });
        })
        .finally(() => {
          fullPending = null;
        });
      return fullPending;
    }
    async function poll() {
      if (stopped || polling) return;
      polling = true;
      try {
        if (!latest) await full();
        else {
          const readRevision = revision;
          const status = await api<Meeting>(`/meetings/${id}/status`, {
            signal: controller.signal,
          });
          if (stopped || revision !== readRevision) return;
          const fingerprint = statusVersion(status);
          if (fingerprint !== lastStatus) {
            lastStatus = fingerprint;
            latest = { ...latest, ...status };
            setMeeting((held) => (held ? { ...held, ...status } : held));
          }
          if (contentVersion(status) !== loadedVersion) await full();
        }
        if (!stopped) setError("");
      } catch (error) {
        if (!stopped && (error as Error).name !== "AbortError")
          setError((error as Error).message);
      } finally {
        polling = false;
        schedule();
      }
    }
    refreshRef.current = async () => {
      // A mutation must read a fresh result even if an older detail request was in flight.
      if (fullPending) await fullPending;
      await full();
      schedule();
    };
    const wake = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
      refreshRef.current = async () => {};
    };
  }, [id, setMeeting, setOverview, setError]);
  return refresh;
}
