"""One backend voice catalog. Nothing here can reach an inference provider."""

from sqlalchemy import select
from .config import settings
from .models import ClonedVoice
from .preset_voices import PRESETS, PREVIEW_VERSION
from .speech_contract import PIPELINE_VERSION, LocalTTSError


def preset_id(value):
    if value not in PRESETS:
        raise LocalTTSError("Choose one of the available preset voices")
    return value


def default_preset(user):
    """Accounts saved before the Voxtral catalog fall back to the current default."""
    return user.tts_preset_voice if user.tts_preset_voice in PRESETS else settings.tts_default_voice


def preview_file(voice):
    return settings.storage_dir / "preset-previews" / voice / f"{PREVIEW_VERSION}.mp3"


def preset_entry(voice):
    ready = preview_file(voice).is_file()
    return {
        "id": voice,
        "name": PRESETS[voice]["name"],
        "description": PRESETS[voice]["description"],
        "language": PRESETS[voice]["language"],
        "group": PRESETS[voice]["group"],
        "gender": PRESETS[voice]["gender"],
        "voice_type": "preset",
        "provider": "openrouter",
        "provider_voice_id": voice,
        "model": settings.openrouter_tts_model,
        "preview_version": PREVIEW_VERSION,
        "preview_available": ready,
        "preview_url": f"/api/voices/presets/{voice}/preview?v={PREVIEW_VERSION}" if ready else None,
        "user_id": None,
    }


def cloned_entry(voice):
    ready = bool(voice.preview_path) and voice.preview_version == PIPELINE_VERSION
    return {
        "id": voice.id,
        "name": voice.name,
        "description": voice.description,
        "language": voice.language,
        "voice_type": "cloned",
        "provider": "modal",
        "provider_voice_id": None,
        "model": voice.model,
        "preview_version": voice.preview_version,
        "preview_available": ready,
        "preview_url": f"/api/voices/{voice.id}/preview" if ready else None,
        "user_id": voice.user_id,
    }


def catalog(db, user):
    cloned = db.scalars(
        select(ClonedVoice).where(ClonedVoice.user_id == user.id).order_by(ClonedVoice.created.desc())
    )
    return [preset_entry(voice) for voice in PRESETS] + [cloned_entry(voice) for voice in cloned]
