"""Concise, Standard and Detailed: distinct prompts, targets derived from the transcript, and
one bounded correction. Nothing here pads a summary that has too little to say."""

import asyncio
import json
import logging
import time
from contextlib import nullcontext
from .config import settings
from .media import text_chunks
from .provider import ProviderError
from .schema import spoken_text
from .summary_power import summary_awake

# Bump when prompts change so earlier summaries are not reused for a new request.
PROMPT_VERSION = "modes-v5"
STANDARD_SHARE = 0.095
MODE_SCALE = {"concise": 0.25, "standard": 1.0, "detailed": 1.75}
# A result this far over target gets one shortening pass.
OVERRUN = 1.25
# Notes are far smaller than the transcript, so one call can merge a long meeting.
SYNTHESIS_CHARS = 120000
log = logging.getLogger("sumx.summary")


def target_words(mode, transcript_words):
    standard = min(1100, max(150, round(transcript_words * STANDARD_SHARE)))
    return max(40, round(standard * MODE_SCALE[mode]))


def summary_words(content):
    return len(spoken_text(content).split())


def prompt_identity():
    return f"{PROMPT_VERSION}:{settings.openrouter_summary_model}"


def note_groups(parts):
    """Whole notes, never split mid-object, in groups the final call can read at once."""
    groups, current, size = [], [], 0
    for part in parts:
        text = json.dumps(part, ensure_ascii=False, separators=(",", ":"))
        if current and size + len(text) > SYNTHESIS_CHARS:
            groups.append("\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text)
    groups.append("\n".join(current))
    return groups


def build_summary(router, text, mode, on_call, *, transcript_words=None, parts=None, on_part=None,
                  metrics=None, on_metrics=None, on_stage=None):
    with summary_awake():
        return asyncio.run(_build_summary(router, text, mode, on_call, transcript_words=transcript_words,
                                         parts=parts, on_part=on_part, metrics=metrics,
                                         on_metrics=on_metrics, on_stage=on_stage))


async def _build_summary(router, text, mode, on_call, *, transcript_words=None, parts=None,
                         on_part=None, metrics=None, on_metrics=None, on_stage=None):
    """Summarize `text` in `mode`. Long transcripts become notes first, then one final summary."""
    target = target_words(mode, transcript_words or len(text.split()))
    chunks = text_chunks(text, settings.summary_chunk_chars)
    parts = list(parts or [])
    metrics = dict(metrics or {})
    metrics.update(mode=mode, transcript_chunks=len(chunks), target_words=target, records_saved=0)
    metrics["timings"] = dict(metrics.get("timings") or {})
    metrics["call_timings"] = list(metrics.get("call_timings") or [])
    metrics["transport_events"] = list(metrics.get("transport_events") or [])
    metrics["note_concurrency"] = settings.summary_note_concurrency
    note_count = len(chunks) if len(chunks) > 1 else 0
    total_units = note_count + 3  # Final synthesis/answer, final validation/correction, durable save.
    metrics["notes_completed"] = sum(part is not None for part in parts)
    for key in ("llm_calls", "note_calls", "synthesis_calls", "correction_calls", "direct_calls"):
        metrics.setdefault(key, 0)

    def publish():
        if on_metrics:
            on_metrics(metrics)

    def stage(name, done):
        metrics["phase"] = name
        metrics["work_completed"], metrics["work_total"] = done, total_units
        if on_stage:
            on_stage(name, done, total_units)

    def timing(name, seconds):
        metrics["timings"][name] = metrics["timings"].get(name, 0) + seconds

    async def ask(body, synthesis=False, **options):
        category = ("correction_calls" if options.get("correction") else "note_calls"
                    if options.get("notes") else "synthesis_calls" if synthesis else "direct_calls")

        for output_attempt in (1, 2):
            metrics["llm_calls"] += 1
            metrics[category] += 1
            call_id = metrics["llm_calls"]
            started = time.perf_counter()
            entry = {"call": call_id, "category": category, "job_id": metrics.get("job_id"), "started_at": time.time(),
                     "input_chars": len(body), "output_attempt": output_attempt,
                     "target_words": options.get("target_words"), "state": "running"}
            metrics["call_timings"].append(entry)
            log.info("summary_call_started %s", json.dumps({"mode": mode, **entry}))
            publish()

            def transport(event):
                item = {"call": call_id, "category": category, **event}
                metrics["transport_events"].append(item)
                if event["event"] == "retry_wait":
                    metrics["retry_until"] = max(metrics.get("retry_until", 0), event["retry_until"])
                if event["event"] == "retry_wait_finished":
                    timing("provider_cooldown_seconds", event["wait_seconds"])
                publish()

            attempt_options = {**options, **({"token_scale": 2} if output_attempt == 2 else {})}
            try:
                if getattr(router, "summary_telemetry", False):
                    result, data, response = await router.summarize_async(
                        body, mode, synthesis, client=client, on_event=transport, **attempt_options
                    )
                else:
                    result, data, response = await asyncio.to_thread(
                        router.summarize, body, mode, synthesis, **attempt_options
                    )
                entry["provider_seconds"] = time.perf_counter() - started
                units = data.get("usage") or {}
                entry.update(state="completed", prompt_tokens=units.get("prompt_tokens"),
                             completion_tokens=units.get("completion_tokens"))
                accounted = time.perf_counter()
                on_call(data, response)
                timing("accounting_seconds", time.perf_counter() - accounted)
                return result
            except ProviderError as exc:
                entry.update(state="failed", error_type=type(exc).__name__)
                if exc.response is not None:
                    try:
                        failed_data = exc.response.json()
                    except (ValueError, TypeError):
                        failed_data = {}
                    if not isinstance(failed_data, dict):
                        failed_data = {}
                    on_call({**failed_data, "_sumx_invalid_response": True}, exc.response)
                    exc.summary_accounted = True
                if not exc.retryable or output_attempt == 2 or options.get("token_scale"):
                    raise
            finally:
                entry["seconds"] = time.perf_counter() - started
                log.info("summary_call_finished %s", json.dumps({"mode": mode, **entry}))
                publish()

    began = time.perf_counter()
    session = router.summary_session() if getattr(router, "summary_telemetry", False) else nullcontext(None)
    try:
        async with asyncio.timeout(settings.summary_pipeline_deadline_seconds), session as client:
            if len(chunks) == 1:
                stage("summary_finalizing", 0)
                started = time.perf_counter()
                content = parts[0] if parts and parts[0] is not None else await ask(chunks[0], target_words=target)
                timing("direct_seconds", time.perf_counter() - started)
            else:
                notes = max(200, round(target_words("detailed", len(text.split())) * 1.2 / len(chunks)))
                stage("summary_notes", metrics["notes_completed"])
                started = time.perf_counter()
                semaphore = asyncio.Semaphore(settings.summary_note_concurrency)
                failures = []

                async def note(index):
                    async with semaphore:
                        if failures:
                            return
                        try:
                            result = await ask(chunks[index], target_words=notes, notes=True)
                            while len(parts) <= index:
                                parts.append(None)
                            parts[index] = result
                            metrics["notes_completed"] = sum(part is not None for part in parts)
                            if on_part:
                                on_part(list(parts), metrics["notes_completed"], len(chunks))
                            stage("summary_notes", metrics["notes_completed"])
                        except Exception as exc:
                            failures.append((index, exc))

                await asyncio.gather(*(note(index) for index in range(len(chunks))
                                       if index >= len(parts) or parts[index] is None))
                timing("notes_seconds", time.perf_counter() - started)
                if failures:
                    raise min(failures, key=lambda pair: pair[0])[1]
                stage("summary_synthesis", note_count)
                started = time.perf_counter()
                while True:
                    groups = note_groups(parts)
                    if len(groups) == 1:
                        content = await ask(groups[0], True, target_words=target)
                        break
                    reduced = [await ask(group, True, target_words=notes, notes=True) for group in groups]
                    if len(reduced) >= len(parts):
                        raise ProviderError("Summary synthesis could not reduce the meeting notes")
                    parts = reduced
                timing("synthesis_seconds", time.perf_counter() - started)
            stage("summary_finalizing", note_count + 1)
            started = time.perf_counter()
            words = summary_words(content)
            corrected = False
            if words > target * OVERRUN:
                try:
                    shorter = await ask(json.dumps(content, ensure_ascii=False, separators=(",", ":")), True,
                                        target_words=target, correction=True)
                except ProviderError:
                    shorter = None
                if shorter is not None and summary_words(shorter) < words:
                    content, corrected = shorter, True
            timing("correction_seconds", time.perf_counter() - started)
            metrics.update(summary_words=summary_words(content), corrected=corrected)
            stage("summary_saving", note_count + 2)
    except TimeoutError as exc:
        raise ProviderError("Summary processing reached its deadline. Completed notes are saved; retry the failed stage.") from exc
    finally:
        timing("summary_seconds", time.perf_counter() - began)
        publish()
    return content, metrics
