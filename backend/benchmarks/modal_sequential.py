"""Call the existing deployed single-container narration service; never deploy or override it."""

import argparse
import json
from pathlib import Path
import time
import uuid
from sqlalchemy import select
from backend.config import ROOT, settings
from backend.models import DB, ClonedVoice, User, AllowedEmail
from backend.clone_speech import split_clone_narration
from backend.services.modal_tts_client import ModalSpeechClient
from backend.tts_worker import clone_options
from backend.benchmarks.concurrency import TEXT, write_json, inspect_audio
from modal_service.config import MODAL_GPU_USD_PER_SECOND, MODAL_MAX_CONTAINERS, MODAL_GPU


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-gpu-usd", type=float, default=1)
    args = parser.parse_args()
    folder = args.output.resolve()
    if not folder.is_relative_to((ROOT / "data" / "benchmarks").resolve()):
        parser.error("Benchmark output must stay under data/benchmarks")
    folder.mkdir(parents=True, exist_ok=True)
    assert MODAL_MAX_CONTAINERS == 1
    with DB() as db:
        reference = None
        for voice in db.scalars(
            select(ClonedVoice).where(ClonedVoice.saved, ClonedVoice.reference_verified_at.is_not(None))
        ):
            user = db.get(User, voice.user_id)
            approved = db.get(AllowedEmail, user.email)
            if (
                voice.consent_at
                and (user.role == "admin" or approved and approved.active)
                and Path(voice.reference_path).is_file()
            ):
                reference = {
                    "reference_wav": Path(voice.reference_path).read_bytes(),
                    "reference_text": voice.reference_text,
                    "user_id": user.id,
                    "profile_id": voice.id,
                }
                break
    if reference is None:
        parser.error("No saved, verified, consented reference belonging to an approved user is available")
    settings.qwen_tts_timeout = 300  # This benchmark process only: cancel an over-budget remote call.
    client = ModalSpeechClient()
    rows = json.loads((folder / "metrics.json").read_text()) if (folder / "metrics.json").exists() else []
    write_json(
        folder / "manifest.json",
        {
            "benchmark": "sumx-modal-sequential-v1",
            "created_at": time.time(),
            "expires_at": time.time() + 7 * 86400,
            "max_containers": MODAL_MAX_CONTAINERS,
            "gpu_preference": MODAL_GPU,
            "maximum_gpu_execution_estimate_usd": args.maximum_gpu_usd,
            "profile_id": reference["profile_id"],
            "production_changed": False,
        },
    )
    maximum_rate = max(float(v) for v in MODAL_GPU_USD_PER_SECOND.values())
    for count in (150, 600):
        words = TEXT.split()
        text = " ".join((words * (count // len(words) + 1))[:count])
        chunks = split_clone_narration(text, settings.qwen_clone_chunk_words)
        for repeat in range(2):
            name = f"words-{count}-repeat-{repeat}"
            if any(row["case"] == name and row.get("ok") for row in rows):
                continue
            spent = sum(row.get("gpu_cost_estimate_usd", 0) for row in rows)
            if spent + maximum_rate * 300 > args.maximum_gpu_usd:
                print("Stopped before another call: benchmark GPU execution budget", flush=True)
                return
            payload = {
                "voice_type": "cloned",
                "chunks": chunks,
                "total_chunks": len(chunks),
                "prior_wavs": {},
                "text": text,
                "language": "en",
                "speaker": None,
                "preview": False,
                "reference_wav": reference["reference_wav"],
                "reference_text": reference["reference_text"],
                "gap_ms": settings.qwen_clone_gap_ms,
                "request_id": uuid.uuid4().hex,
                "clone_context": {
                    "user_id": reference["user_id"],
                    "profile_id": reference["profile_id"],
                    "job_id": "benchmark-" + name,
                },
                "clone_options": {**clone_options(), "seed": 7000 + repeat, "debug": False},
            }
            started = time.perf_counter()
            try:
                result = client.narrate(payload)
                metrics = result.get("metrics", {})
                row = {
                    "case": name,
                    "ok": result.get("ok"),
                    "wall_seconds": time.perf_counter() - started,
                    **metrics,
                }
                row["gpu_cost_estimate_usd"] = float(
                    MODAL_GPU_USD_PER_SECOND.get(metrics.get("modal_gpu"), maximum_rate)
                ) * metrics.get("billable_seconds", row["wall_seconds"])
                if result.get("ok"):
                    path = folder / (name + ".mp3")
                    path.write_bytes(result["mp3"])
                    (folder / (name + ".txt")).write_text(text, encoding="utf-8")
                    row.update(inspect_audio(path))
                else:
                    row["error"] = result.get("error")
            except Exception as exc:
                row = {
                    "case": name,
                    "ok": False,
                    "wall_seconds": time.perf_counter() - started,
                    "error": type(exc).__name__ + ": " + str(exc)[:240],
                }
                row["gpu_cost_estimate_usd"] = maximum_rate * row["wall_seconds"]
            rows.append(row)
            write_json(folder / "metrics.json", rows)
            print(json.dumps({k: v for k, v in row.items() if k != "chunks"}, default=str), flush=True)


if __name__ == "__main__":
    main()
