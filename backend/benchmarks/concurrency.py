"""Paid, bounded benchmarks isolated from meeting history and production deployment.

python -m backend.benchmarks.concurrency stt --source <audio> --output <run-folder>
python -m backend.benchmarks.concurrency voxtral --output <run-folder>
Requires the optional measurement tool: pip install psutil.
"""

import argparse
import asyncio
import array
from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import uuid
import wave
import httpx
from backend.config import ROOT, settings
from backend.provider import OpenRouter, ProviderError, retry_after

TEXT = (
    "The project team reviewed the revised schedule and confirmed that the design review will take place on Thursday. "
    "Dr. Patel will review twelve reports, including the Q3 figures and the 3.5 percent growth estimate. "
    "Aiden and Serena will verify the API integration, document the remaining issues, and share their findings by 10:30 a.m. on Friday. "
    "Action items: update the budget, check the release notes, confirm the meeting room, and send the approved agenda to all participants. "
    "The team discussed a longer proposal, considered the costs and delivery risks, and agreed to revisit unresolved questions after the next testing session. "
)


def write_json(path, value):
    temporary = path.with_suffix(".pending.json")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def word_error_rate(expected, actual):
    """Two-row Levenshtein distance; repeated paragraphs cannot fool greedy alignment."""
    row = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        following = [i]
        for j, other in enumerate(actual, 1):
            following.append(min(following[-1] + 1, row[j] + 1, row[j - 1] + (word != other)))
        row = following
    return row[-1] / len(expected) if expected else float(bool(actual))


def respect_cooldown(folder):
    path = folder.parent / "provider-cooldown.json"
    if path.exists():
        until = json.loads(path.read_text()).get("until", 0)
        while time.time() < until:
            time.sleep(min(5, until - time.time()))


class Budget:
    def __init__(self, folder, maximum):
        self.path, self.maximum, self.lock = folder / "budget.json", Decimal(str(maximum)), threading.Lock()
        self.data = (
            json.loads(self.path.read_text()) if self.path.exists() else {"reserved_usd": "0", "attempts": 0}
        )

    def reserve(self, usd):
        with self.lock:
            amount = Decimal(self.data["reserved_usd"]) + usd
            if amount > self.maximum:
                raise ProviderError("Benchmark spending ceiling reached; no further paid request was sent")
            self.data.update(
                reserved_usd=str(amount), attempts=self.data["attempts"] + 1, maximum_usd=str(self.maximum)
            )
            write_json(self.path, self.data)


class Memory:
    def __enter__(self):
        import psutil

        self.process, self.peak, self.stop = psutil.Process(), 0, threading.Event()

        def sample():
            while not self.stop.wait(0.05):
                try:
                    processes = [self.process] + self.process.children(recursive=True)
                    self.peak = max(self.peak, sum(p.memory_info().rss for p in processes if p.is_running()))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()


class Router(OpenRouter):
    def __init__(self, budget, stt_rate, speech_rate):
        super().__init__()
        self.budget, self.stt_rate, self._speech_rate = budget, stt_rate, speech_rate
        self.statuses, self.sent = Counter(), 0
        self.telemetry = []

    async def transcribe_async(self, path, language, client):
        from backend.media import probe

        self.budget.reserve(self.stt_rate * Decimal(str(probe(path)[0])))
        return await super().transcribe_async(path, language, client)

    def async_client(self, *, capacity=None):
        async def request(req):
            if req.url.path.endswith("/audio/speech"):
                self.budget.reserve(self._speech_rate * len(json.loads(req.content)["input"]))
                self.sent += 1

        async def response(res):
            delay = retry_after(res)
            self.telemetry.append(
                {
                    "at": time.time(),
                    "status": res.status_code,
                    "retry_after_seconds": delay,
                    "endpoint": res.request.url.path,
                }
            )
            write_json(self.budget.path.parent / "http-attempts.json", self.telemetry)
            if delay and res.status_code in (402, 429, 503):
                path = self.budget.path.parent.parent / "provider-cooldown.json"
                old = json.loads(path.read_text()).get("until", 0) if path.exists() else 0
                write_json(path, {"until": max(old, time.time() + delay)})
            if res.request.url.path.endswith("/audio/speech"):
                self.statuses[str(res.status_code)] += 1

        return httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=15),
            limits=httpx.Limits(max_connections=capacity or 12, max_keepalive_connections=capacity or 12),
            event_hooks={"request": [request], "response": [response]},
        )


def charge(data, response):
    return {
        "cost": (data.get("usage") or {}).get("cost", data.get("estimated_cost")),
        "generation_id": data.get("id") or response.headers.get("x-generation-id"),
        "request_id": response.headers.get("x-request-id"),
    }


def stt_run(router, source, folder, duration, concurrency, adaptive):
    from backend.services.stt_pipeline import transcribe_chunks, merge_parts, words
    from backend.models import Base, engine, DB, SttState, SttLease
    from backend.services.stt_control import GlobalBudget
    from sqlalchemy import delete

    folder.mkdir(parents=True, exist_ok=True)
    checkpoint = folder / "parts.json"
    records = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    stored = {int(k): v["part"] for k, v in records.items()}
    settings.stt_initial_concurrency, settings.stt_adaptive = concurrency, adaptive
    Base.metadata.create_all(engine)
    with DB() as db:
        db.execute(delete(SttLease))
        db.execute(delete(SttState))
        db.commit()

    def save(chunk, part, data, response, seconds):
        stored[chunk["index"]] = part
        records[str(chunk["index"])] = {"part": part, **charge(data, response)}
        write_json(checkpoint, records)

    stats = {}
    failure = None
    with Memory() as memory:
        try:
            asyncio.run(
                transcribe_chunks(
                    router,
                    source,
                    folder,
                    duration,
                    "en",
                    stored,
                    save,
                    stats,
                    budget=GlobalBudget(uuid.uuid4().hex),
                    start=concurrency,
                )
            )
        except ProviderError as exc:
            failure = str(exc)
    text, segments = merge_parts(stored)
    write_json(folder / "transcript.json", {"text": text, "segments": segments})
    stats.update(
        failure=failure,
        peak_process_and_children_mb=memory.peak / 1024**2,
        cost_usd=sum(Decimal(str(r["cost"])) for r in records.values() if r["cost"] is not None),
        cost_unknown=sum(r["cost"] is None for r in records.values()),
        completed_chunks=len(stored),
        words=len(words(text)),
        timestamps_ordered=all(0 <= s["start"] <= s["end"] <= duration + 2 for s in segments)
        and all(a["start"] <= b["start"] for a, b in zip(segments, segments[1:])),
    )
    write_json(folder / "metrics.json", stats)
    return stats, text


def inspect_audio(path):
    from backend.media import run

    raw = path.with_suffix(".quality.wav")
    run(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            "24000",
            str(raw),
        ],
        timeout=300,
    )
    longest = current = frames = clipped = active_frames = 0
    seen = False
    with wave.open(str(raw), "rb") as wav:
        while content := wav.readframes(480):
            values = array.array("h", content)
            frames += len(values)
            clipped += sum(abs(x) >= 32700 for x in values)
            quiet = math.sqrt(sum(x * x for x in values) / len(values)) / 32768 < 10 ** (-45 / 20)
            if quiet:
                current += len(values)
            else:
                if seen:
                    longest = max(longest, current)
                seen, current = True, 0
                active_frames += len(values)
    raw.unlink()
    return {
        "audio_seconds": frames / 24000,
        "max_internal_silence_seconds": longest / 24000,
        "clipping_fraction": clipped / frames if frames else 1,
        "active_audio_fraction": active_frames / frames if frames else 0,
        "valid_pcm_decode": frames > 0,
    }


def voxtral_run(router, text, folder, size, concurrency):
    from backend.speech_contract import split_narration
    from backend.media import run
    from backend.services.stt_control import p95

    # Use the production word-safe splitter, whose preset sizes may exceed 600 characters.
    chunks = split_narration(text, size)
    folder.mkdir(parents=True, exist_ok=True)
    gate, rows, began = asyncio.Semaphore(concurrency), [], time.perf_counter()
    router.sent, router.statuses = 0, Counter()
    failures = []

    async def one(client, index, chunk):
        async with gate:
            if failures:
                return
            start = time.perf_counter()
            try:
                audio, data, response = await router.speech_async(chunk, settings.tts_default_voice, client)
                (folder / f"{index}.mp3").write_bytes(audio)
                rows.append(
                    {
                        "index": index,
                        "latency": time.perf_counter() - start,
                        "ready_at": time.perf_counter() - began,
                        **charge(data, response),
                    }
                )
            except Exception as exc:
                failures.append(type(exc).__name__ + ": " + str(exc)[:220])

    async def generate():
        async with router.async_client(capacity=concurrency) as client:
            await asyncio.gather(*(one(client, i, chunk) for i, chunk in enumerate(chunks)))

    with Memory() as memory:
        asyncio.run(generate())
    stats = {
        "chunk_chars": size,
        "concurrency": concurrency,
        "chunks": len(chunks),
        "total_seconds": time.perf_counter() - began,
        "first_audio_seconds": min((r["ready_at"] for r in rows), default=None),
        "average_latency": sum(r["latency"] for r in rows) / len(rows) if rows else None,
        "p95_latency": p95([r["latency"] for r in rows]),
        "http_statuses": dict(router.statuses),
        "retries": max(0, router.sent - len(chunks)),
        "failures": failures,
        "peak_process_and_children_mb": memory.peak / 1024**2,
        "cost_estimate_usd": sum(Decimal(str(r["cost"])) for r in rows if r["cost"] is not None),
        "provider_requests": rows,
    }
    if not failures:
        listing = folder / "concat.txt"
        listing.write_text("\n".join(f"file '{i}.mp3'" for i in range(len(chunks))), encoding="utf-8")
        output = folder / "comparison.mp3"
        run(
            [
                settings.ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(listing),
                "-c",
                "copy",
                str(output),
            ],
            timeout=300,
        )
        stats.update(inspect_audio(output))
    write_json(folder / "metrics.json", stats)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["stt", "voxtral"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--seconds", type=float, default=3000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-usd", type=float, default=1.5)
    parser.add_argument("--levels", nargs="+", default=["1", "2", "3", "4", "6", "8", "10", "12", "adaptive"])
    parser.add_argument("--job-timeout", type=int, default=600)
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.is_relative_to((ROOT / "data" / "benchmarks").resolve()) or folder.is_relative_to(
        settings.storage_dir.resolve()
    ):
        parser.error("Benchmark outputs must be under data/benchmarks, outside private user media")
    folder.mkdir(parents=True, exist_ok=True)
    # Shared controllers are exercised against an isolated database, never user history.
    settings.database_url = "sqlite:///" + (folder / "control.db").as_posix()
    budget = Budget(folder, args.maximum_usd)
    settings.stt_job_deadline_seconds = args.job_timeout
    respect_cooldown(folder)
    preflight = OpenRouter()
    rates = {}
    try:
        for modality, model in [
            ("transcription", settings.openrouter_stt_model),
            ("speech", settings.openrouter_tts_model),
        ]:
            catalog = preflight.request("GET", "/models?output_modalities=" + modality).json()["data"]
            match = next((m for m in catalog if m["id"] == model), None)
            if not match:
                parser.error(f"Configured {modality} model is unavailable in the live catalog")
            rates[modality] = Decimal(match["pricing"]["prompt"])
    finally:
        preflight.close()
    manifest = {
        "benchmark": "sumx-concurrency-v1",
        "phase": args.phase,
        "created_at": time.time(),
        "expires_at": time.time() + 7 * 86400,
        "maximum_reserved_usd": args.maximum_usd,
        "models": [settings.openrouter_stt_model, settings.openrouter_tts_model],
        "rates": rates,
        "production_tts_changed": False,
    }
    write_json(folder / "manifest.json", manifest)
    router = Router(budget, rates["transcription"], rates["speech"])
    try:
        if args.phase == "stt":
            if not args.source or not args.source.is_file():
                parser.error("STT requires --source pointing to the representative recording")
            from backend.media import extract
            from backend.services.stt_pipeline import words

            sample = folder / "sample.mp3"
            if not sample.exists():
                extract(args.source, sample, 0, args.seconds)
            manifest.update(
                source_sha256=hashlib.sha256(sample.read_bytes()).hexdigest(), media_seconds=args.seconds
            )
            write_json(folder / "manifest.json", manifest)
            baseline_path = folder / "1" / "transcript.json"
            baseline = json.loads(baseline_path.read_text())["text"] if baseline_path.exists() else None
            for level in [value if value == "adaptive" else int(value) for value in args.levels]:
                target = folder / str(level)
                if (target / "metrics.json").exists():
                    stats = json.loads((target / "metrics.json").read_text())
                    text = json.loads((target / "transcript.json").read_text())["text"]
                else:
                    stats, text = stt_run(
                        router,
                        sample,
                        target,
                        args.seconds,
                        4 if level == "adaptive" else level,
                        level == "adaptive",
                    )
                if level == 1:
                    baseline = text
                stats["word_agreement_with_sequential"] = (
                    SequenceMatcher(None, words(baseline), words(text), autojunk=False).ratio()
                    if baseline is not None
                    else None
                )
                write_json(target / "metrics.json", stats)
                print(
                    json.dumps(
                        {
                            "level": level,
                            **{
                                k: v
                                for k, v in stats.items()
                                if k
                                in [
                                    "total_seconds",
                                    "average_seconds",
                                    "p95_seconds",
                                    "retries",
                                    "http_statuses",
                                    "final_concurrency",
                                    "peak_process_and_children_mb",
                                    "cost_usd",
                                    "word_agreement_with_sequential",
                                ]
                            },
                        },
                        default=str,
                    ),
                    flush=True,
                )
                if stats.get("failure"):
                    print(
                        "Stopped matrix after a provider/deadline failure; checkpoints remain resumable.",
                        flush=True,
                    )
                    break
        else:
            from backend.services.stt_pipeline import words

            text = "\n\n".join(TEXT + f" Review item {i + 1} remains on the agenda." for i in range(20))[
                :9000
            ]
            text = text[: text.rfind(".") + 1]
            (folder / "test-text.txt").write_text(text, encoding="utf-8")
            manifest.update(
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                characters=len(text),
                voice=settings.tts_default_voice,
            )
            write_json(folder / "manifest.json", manifest)
            for size, concurrency in [(1800, 3), (1800, 4), (1200, 4), (1200, 6), (900, 6), (900, 8)]:
                respect_cooldown(folder)
                target = folder / f"{size}-{concurrency}"
                stats = (
                    json.loads((target / "metrics.json").read_text())
                    if (target / "metrics.json").exists()
                    else voxtral_run(router, text, target, size, concurrency)
                )
                if not stats["failures"] and "asr_word_agreement" not in stats:
                    duration = stats["audio_seconds"]
                    settings.stt_chunk_attempts = 2
                    quality, actual = stt_run(
                        router, target / "comparison.mp3", target / "quality", duration, 3, False
                    )
                    stats["asr_word_agreement"] = SequenceMatcher(
                        None, words(text), words(actual), autojunk=False
                    ).ratio()
                    stats["quality_stt_cost"] = quality["cost_usd"]
                    write_json(target / "metrics.json", stats)
                print(
                    json.dumps(
                        {k: v for k, v in stats.items() if k not in ["provider_requests"]}, default=str
                    ),
                    flush=True,
                )
                if stats.get("failures"):
                    print(
                        "Stopped higher TTS probes after an unsupported/provider failure; production unchanged.",
                        flush=True,
                    )
                    break
    finally:
        router.close()


if __name__ == "__main__":
    main()
