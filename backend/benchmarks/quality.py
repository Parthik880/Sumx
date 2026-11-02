"""Independent STT and listening index for benchmark recordings, never user history."""

import argparse
import array
from decimal import Decimal
from difflib import SequenceMatcher
import html
import json
import math
from pathlib import Path
import wave
from backend.config import ROOT, settings
from backend.benchmarks.concurrency import (
    Budget,
    Router,
    write_json,
    stt_run,
    respect_cooldown,
    word_error_rate,
)
from backend.provider import OpenRouter


def boundary_metrics(output):
    from backend.media import run, probe

    parts = sorted(output.parent.glob("[0-9]*.mp3"), key=lambda p: int(p.stem))
    layouts = set()
    for path in parts:
        metadata = json.loads(
            run(
                [
                    settings.ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=sample_rate,channels",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        stream = metadata["streams"][0]
        layouts.add((stream["sample_rate"], stream["channels"]))
    raw = output.with_suffix(".boundaries.wav")
    run(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(output),
            "-ac",
            "1",
            "-ar",
            "24000",
            str(raw),
        ],
        timeout=300,
    )
    offset, rows = 0, []
    try:
        with wave.open(str(raw), "rb") as wav:
            for part in parts[:-1]:
                offset += probe(part)[0]
                center = round(offset * 24000)
                start = max(0, center - 24000)
                wav.setpos(min(start, wav.getnframes()))
                values = array.array("h", wav.readframes(48000))
                local = center - start
                jump = abs(values[local] - values[local - 1]) / 32768 if 0 < local < len(values) else None
                quiet = longest = 0
                for index in range(0, len(values), 480):
                    window = values[index : index + 480]
                    silent = math.sqrt(sum(x * x for x in window) / len(window)) / 32768 < 10 ** (-45 / 20)
                    quiet = quiet + len(window) if silent else 0
                    longest = max(longest, quiet)
                rows.append(
                    {
                        "nominal_boundary_seconds": offset,
                        "sample_jump": jump,
                        "longest_silence_in_2s_window": longest / 24000,
                    }
                )
    finally:
        raw.unlink(missing_ok=True)
    return {
        "chunk_layouts": sorted(layouts),
        "compatible_chunk_layouts": len(layouts) == 1,
        "boundaries": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--maximum-usd", type=float, default=0.1)
    parser.add_argument(
        "--reconcile-costs", action="store_true", help="Free generation-cost lookups; no inference calls"
    )
    args = parser.parse_args()
    root = args.folder.resolve()
    if not root.is_relative_to((ROOT / "data" / "benchmarks").resolve()):
        parser.error("Select a benchmark folder under data/benchmarks")
    working = root / "independent-quality"
    working.mkdir(exist_ok=True)
    settings.database_url = "sqlite:///" + (working / "control.db").as_posix()
    from backend.services.stt_pipeline import words

    settings.stt_chunk_attempts, settings.stt_job_deadline_seconds = 2, 180
    respect_cooldown(root)
    provider = OpenRouter()
    try:
        catalog = provider.request("GET", "/models?output_modalities=transcription").json()["data"]
        model = next(m for m in catalog if m["id"] == settings.openrouter_stt_model)
        rate = Decimal(model["pricing"]["prompt"])
    finally:
        provider.close()
    router = Router(Budget(working, args.maximum_usd), rate, Decimal(0))
    rows = []
    try:
        for path in sorted(root.glob("words-*.mp3")) + sorted(root.glob("*/comparison.mp3")):
            from backend.media import probe

            preset = path.name == "comparison.mp3"
            case = path.parent.name if preset else path.stem
            text = (root / "test-text.txt" if preset else path.with_suffix(".txt")).read_text(
                encoding="utf-8"
            )
            target = path.parent / "quality" if preset else working / path.stem
            if (target / "metrics.json").exists():
                metrics = json.loads((target / "metrics.json").read_text())
                actual = json.loads((target / "transcript.json").read_text())["text"]
            else:
                metrics, actual = stt_run(router, path, target, probe(path)[0], 1, False)
            rows.append(
                {
                    "case": case,
                    "word_agreement": SequenceMatcher(
                        None, words(text), words(actual), autojunk=False
                    ).ratio(),
                    "word_error_rate": word_error_rate(words(text), words(actual)),
                    "failure": metrics.get("failure"),
                    "asr_cost_usd": metrics["cost_usd"],
                    "words_expected": len(words(text)),
                    "words_recognized": len(words(actual)),
                }
            )
            write_json(root / "quality.json", rows)
            print(json.dumps(rows[-1], default=str), flush=True)
            if preset and args.reconcile_costs:
                metric_path = path.parent / "metrics.json"
                generated = json.loads(metric_path.read_text())
                costs = []
                for request in generated.get("provider_requests", []):
                    cost = request.get("provider_reported_cost")
                    if cost is None and request.get("generation_id"):
                        try:
                            cost = router.cost(request["generation_id"])
                        except Exception:
                            cost = None
                    request["provider_reported_cost"] = cost
                    if cost is not None:
                        costs.append(Decimal(str(cost)))
                generated["provider_reported_cost_usd"] = (
                    sum(costs) if len(costs) == len(generated["provider_requests"]) else None
                )
                generated["provider_costs_known"] = len(costs)
                generated["asr_word_error_rate"] = rows[-1]["word_error_rate"]
                generated.update(boundary_metrics(path))
                write_json(metric_path, generated)
    finally:
        router.close()
    pieces = [
        '<!doctype html><meta charset="utf-8"><title>SUMX benchmark listening comparisons</title><h1>SUMX benchmark comparisons</h1><p>Private generated benchmark audio. Compare intelligibility, pronunciation, speaker consistency, pitch, speed, pauses and seams. ASR scores do not certify naturalness.</p>'
    ]
    paths = sorted(root.glob("words-*.mp3")) + sorted(root.glob("*/comparison.mp3"))
    for path in paths:
        label = html.escape(str(path.relative_to(root)))
        pieces.append(f'<h2>{label}</h2><audio controls preload="none" src="{label}"></audio>')
    (root / "LISTEN.html").write_text("\n".join(pieces), encoding="utf-8")


if __name__ == "__main__":
    main()
