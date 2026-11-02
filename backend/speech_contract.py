"""Backend view of the speech contract shared with the Modal workers."""

from modal_service.speech_contract import (
    BASE_MODEL,
    LANGUAGES,
    PIPELINE_VERSION,
    PRESET_MODEL,
    PREVIEW_TEXT,
    SAMPLE_RATE,
    SPEAKERS,
    LocalTTSError,
    assemble_wav,
    clone_duration_limit,
    duration_budget,
    pcm_metrics,
    silence_metrics,
    speaker_id,
    split_narration,
    validate_duration,
)

__all__ = [
    "BASE_MODEL",
    "LANGUAGES",
    "PIPELINE_VERSION",
    "PRESET_MODEL",
    "PREVIEW_TEXT",
    "SAMPLE_RATE",
    "SPEAKERS",
    "LocalTTSError",
    "assemble_wav",
    "clone_duration_limit",
    "duration_budget",
    "pcm_metrics",
    "silence_metrics",
    "speaker_id",
    "split_narration",
    "validate_duration",
]
