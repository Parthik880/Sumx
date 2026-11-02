"""Explicit preset preview generation. Never runs on startup, selection or playback.

python -m backend.preset_previews            generate only missing previews
python -m backend.preset_previews --refresh  regenerate all previews
python -m backend.preset_previews --voice gb_jane_neutral
"""

import argparse
from sqlalchemy import select
from .config import settings
from .jobs import account
from .media import probe
from .models import DB, User
from .preset_voices import PRESETS, preview_text
from .provider import OpenRouter
from .voice_catalog import preset_id, preview_file


def ledger_owner(db):
    """Previews belong to no meeting, so their one-time cost is attributed to an administrator."""
    owner = db.scalar(select(User).order_by((User.role == "admin").desc(), User.created))
    if owner is None:
        raise ValueError("Create a SUMX account before generating previews")
    return owner.id


def generate(voice, router):
    target = preview_file(voice)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".pending.mp3")
    text = preview_text(voice)
    audio, data, response = router.speech(text, voice)
    temporary.write_bytes(audio)
    try:
        seconds = probe(temporary)[0]
        if not 1.5 <= seconds <= 20:
            raise ValueError(f"Preview duration {seconds:.1f} s is abnormal")
        with DB() as db:
            account(
                db,
                None,
                "tts",
                settings.openrouter_tts_model,
                data,
                response,
                user_id=ledger_owner(db),
                voice_type="preset",
                input_characters=len(text),
                purpose="preset_preview",
            )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--voice", action="append", help="Limit to these voice IDs")
    parser.add_argument("--refresh", action="store_true", help="Replace existing previews")
    args = parser.parse_args()
    voices = [preset_id(voice) for voice in args.voice] if args.voice else list(PRESETS)
    router = OpenRouter()
    failed = []
    try:
        data = router.json(router.request("GET", "/models?output_modalities=speech"))
        model = next((m for m in data.get("data", []) if m.get("id") == settings.openrouter_tts_model), {})
        unsupported = set(voices) - set(model.get("supported_voices", []))
        if unsupported:
            raise ValueError(f"Preview voices not supported by the configured OpenRouter model: {sorted(unsupported)}")
        for voice in voices:
            if preview_file(voice).is_file() and not args.refresh:
                print(f"{voice}: exists, skipped", flush=True)
                continue
            try:
                print(f"{voice}: generated {generate(voice, router):.2f} s", flush=True)
            except Exception as exc:
                failed.append(voice)
                print(f"{voice}: FAILED {exc}", flush=True)
    finally:
        router.close()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
