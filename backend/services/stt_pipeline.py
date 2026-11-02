"""Bounded async transcription. Chunks finish in any order and are merged chronologically."""

import asyncio
import logging
import math
import random
import re
import time
from collections import Counter
from ..config import settings
from ..media import extract
from ..provider import ProviderError
from .stt_control import AdaptiveLimit, NoBudget, p95

log = logging.getLogger("sumx.stt")


def chunk_plan(duration):
    """Each chunk owns [offset, offset + length); the overlap only protects boundary words."""
    length = settings.audio_chunk_seconds
    overlap = settings.stt_overlap_seconds
    return [
        {"index": i, "offset": i * length, "length": length, "extract_seconds": length + overlap}
        for i in range(math.ceil(duration / length))
    ]


def backoff(attempt, wait):
    """Exponential with jitter; a provider's Retry-After wins when it asks for longer."""
    delay = min(30.0, 2.0**attempt) + random.uniform(0, 1)
    return max(delay, wait or 0)


async def transcribe_chunks(
    router, source, folder, duration, language, stored, save, stats, *, budget=None, start=None
):
    """Transcribe every chunk missing from `stored`. `save` runs once per finished chunk.

    A temporary provider failure retries only that chunk, after giving up its slot, and the job
    carries on. A permanent failure stops new work; running chunks finish and are saved because
    they have already been paid for. Audio streams from disk, so memory stays flat.
    """
    started = time.perf_counter()
    resumed_chunks = len(stored)
    deadline = started + settings.stt_job_deadline_seconds
    plan = [chunk for chunk in chunk_plan(duration) if chunk["index"] not in stored]
    limit = AdaptiveLimit(
        [
            level
            for level in settings.stt_concurrency_levels
            if settings.stt_min_concurrency <= level <= settings.stt_max_concurrency
        ],
        start or settings.stt_initial_concurrency,
        settings.stt_adaptive,
    )
    budget = budget or NoBudget()
    extracting = asyncio.Semaphore(settings.stt_extract_concurrency)
    statuses, http_statuses, rows, requests = Counter(), Counter(), [], []
    fatal, exhausted = [], []
    ambiguous = [0]
    retry_count = [0]
    pending = iter(plan)

    async def attempt(client, chunk, target, retry_number):
        epoch = await limit.acquire()
        slot, began = None, None
        try:
            if not target.exists():
                async with extracting:
                    extraction_slot = await budget.take_extract()
                    try:
                        await asyncio.to_thread(
                            extract, source, target, chunk["offset"], chunk["extract_seconds"]
                        )
                    finally:
                        if extraction_slot is not None:
                            await asyncio.shield(budget.give(extraction_slot))
            slot = await budget.take()
            if fatal:
                return None, 0
            began = time.perf_counter()
            try:
                async with asyncio.timeout(120):
                    result = await router.transcribe_async(target, language, client)
            except TimeoutError as exc:
                raise ProviderError(
                    "OpenRouter exceeded the total request timeout; retry may incur another charge",
                    kind="ambiguous",
                    retryable=True,
                ) from exc
            seconds = time.perf_counter() - began
            limit.success(epoch, seconds)
            await budget.result(slot, seconds=seconds)
            statuses["200"] += 1
            http_statuses["200"] += 1
            requests.append(seconds)
            return result, began
        except ProviderError as exc:
            exc.epoch = epoch
            exc.backoff_seconds = backoff(retry_number, exc.retry_after)
            await budget.result(slot, error=exc)
            if began:
                requests.append(time.perf_counter() - began)
            if exc.response is not None:
                http_statuses[str(exc.response.status_code)] += 1
            raise
        finally:
            limit.release()
            if slot is not None:
                await asyncio.shield(budget.give(slot))

    async def one(client, chunk):
        target = folder / f"chunk-{chunk['index']}.mp3"
        began = time.perf_counter()
        retries, waited, unsure = 0, 0.0, 0
        try:
            while True:
                if fatal:
                    return
                try:
                    result, sent = await attempt(client, chunk, target, retries)
                    break
                except ProviderError as exc:
                    statuses[exc.kind or "invalid"] += 1
                    if not exc.retryable:
                        fatal.append((chunk["index"], exc))
                        return
                    if exc.kind == "ambiguous":
                        ambiguous[0] += 1
                        unsure += 1
                        log.warning(
                            "STT chunk %s timed out after upload; a retry may be charged", chunk["index"]
                        )
                    delay = exc.backoff_seconds
                    if exc.kind in ("rate_limit", "in_flight"):
                        limit.throttled(exc.epoch, delay)
                    elif exc.kind in ("server", "ambiguous", "network"):
                        limit.problem(exc.epoch)
                    retries += 1
                    if (
                        retries >= settings.stt_chunk_attempts
                        or unsure > settings.stt_ambiguous_retries
                        or time.perf_counter() + delay > deadline
                    ):
                        exhausted.append((chunk["index"], exc))
                        return
                    waited += delay
                    retry_count[0] += 1
                    await asyncio.sleep(delay)
            if result is None:
                return
            part, data, response, timing = result
            seconds = min(chunk["extract_seconds"], max(0, duration - chunk["offset"]))
            rows.append(
                {
                    "index": chunk["index"],
                    "prep_seconds": sent - began - waited,
                    "bytes": target.stat().st_size,
                    "latency_seconds": time.perf_counter() - began,
                    "request_seconds": time.perf_counter() - sent,
                    "retries": retries,
                    "backoff_seconds": waited,
                    **timing,
                }
            )
            for segment in part["segments"]:
                segment["start"] += chunk["offset"]
                segment["end"] += chunk["offset"]
                if "speaker" in segment:
                    segment["speaker"] = f"Clip {chunk['index'] + 1}, speaker {segment['speaker']}"
            save(chunk, {**part, "offset": chunk["offset"]}, data, response, seconds)
        except ProviderError as exc:
            fatal.append((chunk["index"], exc))
        except Exception as exc:
            fatal.append(
                (
                    chunk["index"],
                    ProviderError(
                        "A transcription chunk could not be prepared or saved. Retry from its saved checkpoints."
                    ),
                )
            )
            log.error("STT chunk %s failed (%s)", chunk["index"], type(exc).__name__)
        finally:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                log.warning("STT temporary chunk %s awaits cleanup", chunk["index"])

    async def worker(client):
        while not fatal:
            chunk = next(pending, None)
            if chunk is None:
                return
            await one(client, chunk)

    try:
        async with router.async_client(capacity=settings.stt_max_concurrency) as client:
            try:
                async with asyncio.timeout(settings.stt_job_deadline_seconds):
                    await asyncio.gather(*(worker(client) for _ in range(min(len(plan), max(limit.levels)))))
            except TimeoutError:
                exhausted.extend(
                    (chunk["index"], ProviderError("Transcription exceeded its total job timeout"))
                    for chunk in plan
                    if chunk["index"] not in stored
                )
    finally:
        rows.sort(key=lambda row: row["index"])
        latencies = requests
        stats.update(
            adaptive=settings.stt_adaptive,
            concurrency=start or settings.stt_initial_concurrency,
            final_concurrency=limit.stable_level,
            peak_concurrency=limit.peak,
            concurrency_changes=limit.changes,
            media_seconds=duration,
            total_chunks=len(chunk_plan(duration)),
            resumed_chunks=resumed_chunks,
            completed_chunks=len(stored),
            total_seconds=time.perf_counter() - started,
            prep_seconds=sum(row["prep_seconds"] for row in rows),
            upload_seconds=sum(row["upload_seconds"] for row in rows),
            provider_seconds=sum(row["provider_seconds"] for row in rows),
            backoff_seconds=sum(row["backoff_seconds"] for row in rows),
            retries=retry_count[0],
            average_seconds=sum(latencies) / len(latencies) if latencies else 0.0,
            p95_seconds=p95(latencies),
            responses=dict(statuses),
            http_statuses=dict(http_statuses),
            ambiguous_timeouts=ambiguous[0],
            failed_chunks=sorted(index for index, _ in fatal + exhausted),
            chunks=rows,
        )
        log.info("STT job: %s", {k: v for k, v in stats.items() if k != "chunks"})
    if fatal:
        raise min(fatal, key=lambda item: item[0])[1]
    if exhausted:
        finished, total = len(stored), stats["total_chunks"]
        raise ProviderError(
            f"OpenRouter stayed unavailable. {finished} of {total} parts are saved; "
            "resume transcription to finish the rest. " + str(exhausted[0][1])
        ) from exhausted[0][1]


def words(text):
    return re.findall(r"[\w']+", text.lower())


def strip_repeat(previous, text, limit=30, minimum=3):
    """Remove words at the start of `text` that repeat the end of `previous`."""
    before, pieces = words(previous)[-limit:], text.split()
    current = [(words(piece) or [""])[0] for piece in pieces[:limit]]
    for size in range(min(len(before), len(current)), minimum - 1, -1):
        if before[-size:] == current[:size]:
            return " ".join(pieces[size:])
    return text


def merge_parts(parts):
    """Chronological text and segments. Each segment belongs to the chunk holding its midpoint."""
    length = settings.audio_chunk_seconds
    ordered = [parts[index] for index in sorted(parts)]
    texts, segments = [], []
    for position, part in enumerate(ordered):
        final = position == len(ordered) - 1
        limit = part.get("offset", 0) + length
        kept = [
            dict(segment)
            for segment in part.get("segments") or []
            if final or (segment["start"] + segment["end"]) / 2 < limit
        ]
        if position:
            kept = [
                segment
                for segment in kept
                if (segment["start"] + segment["end"]) / 2 >= part.get("offset", 0)
            ]
        previous = texts[-1] if texts else ""
        if part.get("segments"):
            # Repeats are only duplicates when both sides touch the shared boundary audio.
            overlap = settings.stt_overlap_seconds
            start = part.get("offset", 0)
            while (
                kept
                and previous
                and segments
                and segments[-1]["end"] >= start - overlap
                and kept[0]["start"] <= start + overlap
            ):
                kept[0]["text"] = strip_repeat(previous, kept[0]["text"])
                if kept[0]["text"].strip():
                    break
                kept.pop(0)
            text = " ".join(segment["text"].strip() for segment in kept)
        else:
            text = strip_repeat(previous, part["text"]) if previous else part["text"]
        segments.extend(kept)
        if text.strip():
            texts.append(text)
    return "\n".join(texts), sorted(segments, key=lambda s: s["start"])
