"""Reference quality/transcript checks, separate from synthesis text."""

import re
import time
from difflib import SequenceMatcher
from .config import settings
from .media import safe_path
from .provider import OpenRouter
from .speech_contract import LocalTTSError, pcm_metrics


def words(text):
    return re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]|[^\W_]+", text.casefold())


def verify_reference(db, voice):
    content = safe_path(voice.reference_path).read_bytes()
    quality = pcm_metrics(content, reference=True)
    if voice.reference_verified_at:
        return quality
    router = OpenRouter()
    try:
        part, data, response = router.transcribe(safe_path(voice.reference_path), voice.language)
        from .jobs import account

        account(
            db,
            None,
            "stt",
            settings.openrouter_stt_model,
            data,
            response,
            user_id=voice.user_id,
            purpose="voice_reference",
            audio_seconds=quality["audio_seconds"],
        )
    finally:
        router.close()
    actual = str(part.get("text") or "").strip()
    if len(words(actual)) < 2:
        raise LocalTTSError(
            "No clear reference speech was transcribed. Record a clean single-speaker sample."
        )
    speakers = {s["speaker"] for s in part.get("segments", []) if s.get("speaker") is not None}
    if len(speakers) > 1:
        raise LocalTTSError(
            "Multiple speakers were detected. Record only the voice you have permission to use."
        )
    supplied = voice.reference_text.strip()
    # Agreement check preserves a correct human transcript rather than blindly replacing it with ASR.
    similarity = SequenceMatcher(None, words(supplied), words(actual)).ratio()
    if supplied and similarity < 0.65:
        raise LocalTTSError(
            "The reference transcript does not match the recording. Enter the actual spoken words and generate a new preview."
        )
    if not supplied:
        voice.reference_text = actual
    voice.reference_verified_at = time.time()
    voice.reference_quality = {
        **quality,
        "transcript_check": "asr_agreement",
        "transcript_similarity": similarity,
        "speaker_check": "provider_labels" if speakers else "single_speaker_required_not_diarized",
        "noise_check": "conservative_broadband_screen",
    }
    db.commit()
    return quality
