"""Private database IPC. This module imports no ML libraries and opens no network port."""

import hashlib
import json
import time
from sqlalchemy import select
from .config import settings
from .media import safe_path
from .models import ClonedVoice, DB, LocalTTSGeneration, LocalTTSWorker
from .speech_contract import LANGUAGES, LocalTTSError
from .tts_usage import record_tts_usage


def status(db):
    from .speech_contract import PREVIEW_TEXT, PIPELINE_VERSION

    worker = db.get(LocalTTSWorker, "qwen3")
    live = worker is not None and worker.heartbeat > time.time() - 40
    ready = bool(
        settings.tts_enabled
        and live
        and worker.state in ("ready", "busy")
        and worker.model == settings.qwen_tts_model
    )
    return {
        "model": settings.qwen_tts_model,
        "preset_model": settings.openrouter_tts_model,
        "provider": "modal_qwen3",
        "engine": "modal_faster_qwen3",
        "cuda_graphs": bool(live and worker.cuda_graphs),
        "preview_text": PREVIEW_TEXT,
        "pipeline_version": PIPELINE_VERSION,
        "accepting": ready,
        "supported": ready,
        "state": worker.state if live else "offline",
        "gpu_available": ready,
        "gpu_name": worker.gpu_name if ready else None,
        "vram_mb": worker.vram_mb if live else 0,
        "message": "Cloned voices are generated on private Modal GPUs. The first request after a quiet period takes longer."
        if ready
        else "Cloned-voice generation is unavailable. Check the Modal deployment and the speech worker.",
    }


def submit(db, voice, text, request_key, meeting_id=None, *, purpose=None, language=None):
    from .speech_contract import PREVIEW_TEXT, PIPELINE_VERSION

    if not status(db)["accepting"]:
        raise LocalTTSError(status(db)["message"])
    if len(text) > 600 or not text.strip():
        raise LocalTTSError("Split narration into segments of at most 600 characters")
    profile = db.get(ClonedVoice, voice.id)
    if not profile or profile.user_id != voice.user_id or not profile.reference_verified_at:
        raise LocalTTSError("Validate the consented voice reference and its transcript first")
    if not voice.consent_at or not safe_path(voice.reference_path).is_file():
        raise LocalTTSError("The consented reference recording is unavailable")
    if not profile.reference_text.strip():
        raise LocalTTSError("An accurate reference transcript is required before generation")
    user_id, voice_id, model = voice.user_id, voice.id, settings.qwen_tts_model
    reference_text = voice.reference_text
    language = language or voice.language
    voice_type, preset_voice = "cloned", None
    fingerprint = hashlib.sha256(safe_path(voice.reference_path).read_bytes()).hexdigest()
    purpose = purpose or ("meeting" if meeting_id else "voice_preview")
    if purpose == "voice_preview":
        text, language = PREVIEW_TEXT, "en"
    if language not in LANGUAGES:
        raise LocalTTSError("Choose a language supported by Qwen3-TTS")
    digest = hashlib.sha256(
        (fingerprint + reference_text + model + text + PIPELINE_VERSION + language).encode()
    ).hexdigest()[:32]
    key = request_key + ":" + digest
    task = db.scalar(select(LocalTTSGeneration).where(LocalTTSGeneration.request_key == key))
    if task:
        if task.user_id != user_id or task.voice_id != voice_id:
            raise LocalTTSError("This generation belongs to another account")
        if task.state == "failed":
            task.state, task.stage, task.error = "queued", "queued", None
            task.provider, task.remote_call_id = "modal_qwen3", None
            task.text, task.reference_text = text, reference_text
            if voice and purpose == "voice_preview":
                profile.preview_until, profile.preview_error = (
                    time.time() + settings.qwen_tts_timeout + 60,
                    None,
                )
            record_tts_usage(db, task)
            db.commit()
        return task
    task = LocalTTSGeneration(
        request_key=key,
        user_id=user_id,
        voice_id=voice_id,
        meeting_id=meeting_id,
        provider="modal_qwen3",
        model=model,
        voice_type=voice_type,
        preset_voice=preset_voice,
        purpose=purpose,
        text=text,
        reference_text=reference_text,
        language=language,
        input_characters=len(text),
        parent_job_id=request_key.split(":")[1] if request_key.startswith("meeting:") else None,
        segment_index=int(request_key.rsplit(":", 1)[1]) if request_key.startswith("meeting:") else None,
    )
    db.add(task)
    db.flush()
    record_tts_usage(db, task)
    if voice and purpose == "voice_preview":
        profile.preview_until, profile.preview_error = time.time() + settings.qwen_tts_timeout + 60, None
    db.commit()
    return task


def submit_narration(db, voice, chunks, job_id, meeting_id, folder, *, done=0, language=None):
    """One task, and so one Modal invocation, for all chunks the job has not finished."""
    from .speech_contract import PIPELINE_VERSION

    if not status(db)["accepting"]:
        raise LocalTTSError(status(db)["message"])
    remaining = chunks[done:]
    if not remaining or any(len(chunk) > 600 or not chunk.strip() for chunk in remaining):
        raise LocalTTSError("Split narration into segments of at most 600 characters")
    profile = db.get(ClonedVoice, voice.id)
    path = safe_path(voice.reference_path)
    if not profile or profile.user_id != voice.user_id or not profile.reference_verified_at:
        raise LocalTTSError("Validate the consented voice reference and its transcript first")
    if not voice.consent_at or not path.is_file() or not profile.reference_text.strip():
        raise LocalTTSError("The consented reference recording is unavailable")
    user_id, voice_id, model, voice_type = voice.user_id, voice.id, settings.qwen_tts_model, "cloned"
    language, reference_text = language or voice.language, voice.reference_text
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    preset_voice = None
    if language not in LANGUAGES:
        raise LocalTTSError("Choose a language supported by Qwen3-TTS")
    text = json.dumps(remaining, ensure_ascii=False)
    digest = hashlib.sha256(
        (fingerprint + reference_text + model + text + PIPELINE_VERSION + language).encode()
    ).hexdigest()[:32]
    key = f"narration:{job_id}:{done}:{digest}"
    task = db.scalar(select(LocalTTSGeneration).where(LocalTTSGeneration.request_key == key))
    plan = {"folder": str(folder), "done": done, "total": len(chunks)}
    if task:
        if task.user_id != user_id or task.voice_id != voice_id:
            raise LocalTTSError("This generation belongs to another account")
        if task.state == "failed":
            task.state, task.stage, task.error, task.remote_call_id = "queued", "queued", None, None
            task.text, task.reference_text, task.diagnostics = text, reference_text, plan
            record_tts_usage(db, task)
            db.commit()
        return task
    task = LocalTTSGeneration(
        request_key=key,
        user_id=user_id,
        voice_id=voice_id,
        meeting_id=meeting_id,
        provider="modal_qwen3",
        model=model,
        voice_type=voice_type,
        preset_voice=preset_voice,
        purpose="narration",
        text=text,
        reference_text=reference_text,
        language=language,
        input_characters=sum(len(chunk) for chunk in remaining),
        parent_job_id=job_id,
        diagnostics=plan,
    )
    db.add(task)
    db.flush()
    record_tts_usage(db, task)
    db.commit()
    return task


def wait_for(task_id, timeout, progress=None):
    deadline = time.monotonic() + (timeout or settings.qwen_tts_timeout + 120)
    while time.monotonic() < deadline:
        with DB() as db:
            task = db.get(LocalTTSGeneration, task_id)
            if progress:
                progress(task)
            if task.state == "completed":
                return task
            if task.state == "failed":
                raise LocalTTSError(task.error or "Speech generation failed; retry the failed stage.")
            if not status(db)["accepting"]:
                raise LocalTTSError("The speech worker stopped. Restart it, then retry the failed stage.")
        time.sleep(0.5)
    raise LocalTTSError("Speech generation is taking too long. Check the Modal deployment before retrying.")


def generate_narration(voice, chunks, job_id, meeting_id, folder, *, timeout=None, progress=None, **options):
    with DB() as db:
        task_id = submit_narration(db, voice, chunks, job_id, meeting_id, folder, **options).id
    task = wait_for(task_id, timeout, progress)
    output = safe_path(task.output_path or "")
    if not output.is_file():
        raise LocalTTSError("The generated audio is missing. Generate a new narration.")
    return output


def generate(
    voice, text, request_key, meeting_id=None, timeout=None, progress=None, raw_wav=False, **options
):
    with DB() as db:
        task_id = submit(db, voice, text, request_key, meeting_id, **options).id
    output = safe_path(wait_for(task_id, timeout, progress).output_path or "")
    if raw_wav:
        output = output.with_suffix(".wav")
    if not output.is_file():
        raise LocalTTSError("The generated audio is missing. Generate a new narration.")
    return output.read_bytes()
