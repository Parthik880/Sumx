"""Operator-only Modal benchmark. Calls deployed GPU workers directly and reports timings.

Reports contain generated audio and timings, never reference transcripts or recordings.
"""

import argparse
import json
import time
from pathlib import Path
from sqlalchemy import select
from .config import ROOT
from .models import AllowedEmail, ClonedVoice, DB, User
from .services.modal_tts_client import ModalSpeechClient
from .speech_contract import PREVIEW_TEXT
from .tts_worker import clone_options


def references(limit):
    rows = []
    with DB() as db:
        for voice in db.scalars(select(ClonedVoice).order_by(ClonedVoice.created)):
            user = db.get(User, voice.user_id)
            allowed = db.get(AllowedEmail, user.email) if user else None
            if (
                user
                and (user.role == "admin" or allowed and allowed.active)
                and voice.consent_at
                and voice.reference_verified_at
                and Path(voice.reference_path).is_file()
            ):
                rows.append(
                    (voice.id, user.id, Path(voice.reference_path).read_bytes(), voice.reference_text)
                )
    return rows[:limit]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--voices", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "modal-benchmark")
    parser.add_argument(
        "--gpu",
        help="Test-only override for this run. The application always uses modal_service/config.py.",
    )
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    client = ModalSpeechClient()
    if args.gpu:
        client.workers = {k: v.with_options(gpu=args.gpu) for k, v in client.workers.items()}
    rows = []

    def call(label, payload):
        started = time.monotonic()
        result = client.synthesize(payload)
        metrics = result.get("metrics", {})
        row = {
            "label": label,
            "ok": result.get("ok"),
            "error": result.get("error"),
            "total_seconds": time.monotonic() - started,
            **{
                k: metrics.get(k)
                for k in (
                    "modal_gpu",
                    "gpu_name",
                    "cold_start",
                    "cold_start_seconds",
                    "load_seconds",
                    "warmup_seconds",
                    "gpu_execution_seconds",
                    "audio_seconds",
                    "attempts",
                    "peak_vram_mb",
                    "cuda_graphs",
                    "max_internal_silence_seconds",
                )
            },
        }
        if result.get("ok"):
            path = folder / f"{label}.wav"
            path.write_bytes(result["wav"])
            row["path"] = str(path)
        rows.append(row)
        (folder / "report.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(json.dumps({k: row[k] for k in list(row)[:8]}), flush=True)

    base = {"text": PREVIEW_TEXT, "language": "en", "preview": True, "reference_text": ""}
    for voice_id, user_id, reference, transcript in references(args.voices):
        for repeat in range(args.repeats):
            call(
                f"clone-{voice_id[:8]}-{repeat}",
                {
                    **base,
                    "voice_type": "cloned",
                    "speaker": None,
                    "reference_wav": reference,
                    "reference_text": transcript,
                    "clone_options": {**clone_options(), "seed": 7000 + repeat},
                    "clone_context": {
                        "user_id": user_id,
                        "profile_id": voice_id,
                        "job_id": f"benchmark-{voice_id}",
                        "request_id": f"benchmark-{repeat}",
                    },
                },
            )
    print("Report:", folder / "report.json", flush=True)


if __name__ == "__main__":
    main()
