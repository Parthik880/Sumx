"""Bounded async transcription. Chunks finish in any order and are merged chronologically."""

import asyncio
import logging
import math
import re
import time
from ..config import settings
from ..media import extract

log = logging.getLogger("sumx.stt")


def chunk_plan(duration):
    """Each chunk owns [offset, offset + length); the overlap only protects boundary words."""
    length = settings.audio_chunk_seconds
    overlap = settings.stt_overlap_seconds
    return [
        {"index": i, "offset": i * length, "length": length, "extract_seconds": length + overlap}
        for i in range(math.ceil(duration / length))
    ]


async def transcribe_chunks(router, source, folder, duration, language, stored, save, stats):
    """Transcribe every chunk missing from `stored`. `save` runs once per finished chunk.

    At most `stt_concurrency` chunks exist on disk at once, and audio streams from disk, so
    memory stays flat. After a failure no new chunk starts, but running ones finish and are
    saved because they have already been paid for.
    """
    started = time.perf_counter()
    plan = [chunk for chunk in chunk_plan(duration) if chunk["index"] not in stored]
    gate = asyncio.Semaphore(settings.stt_concurrency)
    failures = []
    rows = []

    async def one(client, chunk):
        async with gate:
            if failures:
                return
            began = time.perf_counter()
            target = folder / f"chunk-{chunk['index']}.mp3"
            try:
                await asyncio.to_thread(extract, source, target, chunk["offset"], chunk["extract_seconds"])
                prepared = time.perf_counter()
                part, data, response, timing = await router.transcribe_async(target, language, client)
                seconds = min(chunk["extract_seconds"], max(0, duration - chunk["offset"]))
                row = {
                    "index": chunk["index"],
                    "prep_seconds": prepared - began,
                    "bytes": target.stat().st_size,
                    "latency_seconds": time.perf_counter() - began,
                    **timing,
                }
                rows.append(row)
                for segment in part["segments"]:
                    segment["start"] += chunk["offset"]
                    segment["end"] += chunk["offset"]
                    if "speaker" in segment:
                        segment["speaker"] = f"Clip {chunk['index'] + 1}, speaker {segment['speaker']}"
                save(chunk, {**part, "offset": chunk["offset"]}, data, response, seconds)
            except Exception as exc:
                failures.append((chunk["index"], exc))
            finally:
                target.unlink(missing_ok=True)

    try:
        async with router.async_client() as client:
            await asyncio.gather(*(one(client, chunk) for chunk in plan))
    finally:
        rows.sort(key=lambda row: row["index"])
        stats.update(
            concurrency=settings.stt_concurrency,
            media_seconds=duration,
            total_chunks=len(chunk_plan(duration)),
            resumed_chunks=len(stored) - len(rows),
            total_seconds=time.perf_counter() - started,
            prep_seconds=sum(row["prep_seconds"] for row in rows),
            upload_seconds=sum(row["upload_seconds"] for row in rows),
            provider_seconds=sum(row["provider_seconds"] for row in rows),
            backoff_seconds=sum(row["backoff_seconds"] for row in rows),
            retries=sum(row["retries"] for row in rows),
            failed_chunks=sorted(index for index, _ in failures),
            chunks=rows,
        )
        log.info("STT job: %s", {k: v for k, v in stats.items() if k != "chunks"})
    if failures:
        raise min(failures, key=lambda item: item[0])[1]


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
