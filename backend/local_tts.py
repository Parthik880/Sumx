"""Private database IPC. This module imports no ML libraries and opens no network port."""

import hashlib
import time
from sqlalchemy import select
from .config import settings
from .media import safe_path
from .models import ClonedVoice, DB, LocalTTSGeneration, LocalTTSWorker
from .qwen_runtime import LANGUAGES, LocalTTSError


def status(db):
    worker = db.get(LocalTTSWorker, "qwen3")
    live = worker is not None and worker.heartbeat > time.time() - 40
    state = worker.state if live else "offline"
    ready = bool(
        settings.local_voice_cloning_enabled
        and settings.local_tts_provider == "faster_qwen3"
        and live
        and worker.model == settings.qwen_tts_model
        and state in ("ready", "busy")
        and worker.cuda_graphs
    )
    return {
        "model": settings.qwen_tts_model,
        "provider": "local_qwen3",
        "engine": "faster_qwen3",
        "cuda_graphs": bool(live and worker.cuda_graphs),
        "accepting": bool(
            live
            and settings.local_voice_cloning_enabled
            and worker.model == settings.qwen_tts_model
            and state in ("ready", "busy", "loading", "warming_up")
        ),
        "supported": ready,
        "state": state,
        "gpu_available": bool(live and worker.gpu_name),
        "gpu_name": worker.gpu_name if live else None,
        "vram_mb": worker.vram_mb if live else 0,
        "message": "Voice cloning runs privately on this computer's NVIDIA GPU."
        if ready
        else "The local voice model is loading. Please wait."
        if state in ("loading", "warming_up")
        else (
            worker.error
            if live and worker.error
            else "The local voice worker is unavailable. Start it and check the GPU setup."
        ),
    }


def submit(db, voice, text, request_key, meeting_id=None):
    profile = db.get(ClonedVoice, voice.id)
    if not profile or profile.user_id != voice.user_id:
        raise LocalTTSError("The selected voice is no longer available")
    if voice.language not in LANGUAGES:
        raise LocalTTSError("Choose a language supported by local Qwen3-TTS")
    if not voice.reference_text.strip():
        raise LocalTTSError("Add the exact words spoken in your reference recording first")
    if not voice.consent_at or not safe_path(voice.reference_path).is_file():
        raise LocalTTSError("The consented reference recording is unavailable")
    if not status(db)["accepting"]:
        raise LocalTTSError(status(db)["message"])
    if len(text) > 600 or not text.strip():
        raise LocalTTSError("Split local narration into chunks of at most 600 characters")
    fingerprint = hashlib.sha256((voice.reference_text + settings.qwen_tts_model).encode()).hexdigest()[:24]
    key = request_key + ":" + fingerprint
    task = db.scalar(select(LocalTTSGeneration).where(LocalTTSGeneration.request_key == key))
    if task:
        if task.user_id != voice.user_id or task.voice_id != voice.id:
            raise LocalTTSError("The local generation does not belong to this voice")
        if task.state == "failed":
            task.state, task.stage, task.error = "queued", "queued", None
            task.text, task.reference_text = text, voice.reference_text
            if not meeting_id:
                profile.preview_until, profile.preview_error = time.time() + 600, None
            db.commit()
        return task
    task = LocalTTSGeneration(
        request_key=key,
        user_id=voice.user_id,
        voice_id=voice.id,
        meeting_id=meeting_id,
        model=settings.qwen_tts_model,
        purpose="meeting" if meeting_id else "voice_preview",
        text=text,
        reference_text=voice.reference_text,
        language=voice.language,
        input_characters=len(text),
        parent_job_id=request_key.split(":")[1] if request_key.startswith("meeting:") else None,
        segment_index=int(request_key.rsplit(":", 1)[1]) if request_key.startswith("meeting:") else None,
    )
    db.add(task)
    if not meeting_id:
        profile.preview_until, profile.preview_error = time.time() + 600, None
    db.commit()
    return task


def generate(voice, text, request_key, meeting_id=None, timeout=1800, progress=None):
    with DB() as db:
        task_id = submit(db, voice, text, request_key, meeting_id).id
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with DB() as db:
            task = db.get(LocalTTSGeneration, task_id)
            if progress:
                progress(task)
            if task.state == "completed":
                output = safe_path(task.output_path or "")
                if not output.is_file():
                    raise LocalTTSError("The generated audio is missing. Generate a new narration.")
                return output.read_bytes()
            if task.state == "failed":
                raise LocalTTSError(task.error or "Local generation failed; retry the failed stage.")
            if not status(db)["accepting"]:
                raise LocalTTSError("The local GPU worker stopped. Restart it, then retry the failed stage.")
        time.sleep(0.5)
    raise LocalTTSError("Local generation is taking too long. Check the worker before retrying.")
