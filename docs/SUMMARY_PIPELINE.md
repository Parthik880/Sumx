# Summary stages and latency diagnostics

The final note is not the final summary. Long transcripts produce bounded parallel notes, then a
DeepSeek synthesis and, when over the existing mode length target, one correction. The validated
summary and the job's completed state commit in one transaction. Only then is progress 100%.

The frontend displays Transcribing audio → Generating meeting notes → Combining meeting notes →
Finalizing summary → Saving meeting → Completed. Short transcripts go directly to finalization.
The progress bar counts work units, not an estimate of remaining time. Elapsed time updates locally.

Summary-only settings (all three modes retain their existing prompts, targets and validation):

- `SUMMARY_NOTE_CONCURRENCY=2` (1–4): independent notes, retained in transcript order.
- `SUMMARY_REQUEST_DEADLINE_SECONDS=180` (1–600): total request, including transport retries/cooldowns.
- `SUMMARY_PIPELINE_DEADLINE_SECONDS=600` (1–3600): one summary attempt; completed notes survive failure.

These settings do not change STT concurrency, transcription models, speech generation or authentication.
On Windows a summary holds a scoped system wake request and restores the prior flags afterward.
This prevents idle sleep during the request; manual sleep, lid-close policy and shutdown can still suspend it.

`Job.payload.summary_metrics` contains stage wall times, each LLM call's start/end, input characters,
prompt/completion tokens, output retries, HTTP attempt timings, response headers/body timings,
provider cooldowns, saved-at time, and the count of records saved. Concurrent call durations overlap;
do not sum them to calculate notes wall time. Job status exposes lightweight progress and timings.

Worker logs: `transcription_completed`, `summary_stage`, `summary_call_started`,
`summary_call_finished`, `summary_transport`, `summary_validation_failed`, `summary_saved`.
They contain timing and diagnostic metadata, never transcript text, summaries or credentials.
Call entries include the job ID. HTTP timing entries correlate through the job's transport events.
Database-save timing includes acquiring the final writer lock and committing the Summary.
The small metrics update after the durable save cannot turn a completed job into a failed job.

API retrieval logs use `summary_retrieval`; responses include `X-SUMX-Server-Ms`.
The browser logs `sumx_summary_timing` for detail/summary retrieval, layout commit and observed paint.
Development React Profiler entries additionally report render CPU time; production still reports
layout and paint timing. Double requestAnimationFrame observes a paint opportunity, not GPU paint duration.

The detail page polls status with one outstanding request, 1.5 seconds after the preceding active
response or 10 seconds when idle. Full content/overview refresh only when its versions change or
after an explicit mutation. Summary retrieval is deduplicated per meeting, mode and version.
Provider errors preserve successful notes; a failed save reuses the validated final result.
Duplicate deliveries cannot insert another summary for the same completed job.

Regression checks: `python -m pytest backend/tests/test_summary_progress.py` and
`npm test -- summary-progress.spec.ts` from the frontend directory. Other mode, narration,
transcription and authentication tests remain part of the full suites.
