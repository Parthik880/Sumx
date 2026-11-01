"""Owner-only reference recordings. A saved profile reuses audio; it trains no model."""

import array
import shutil
import time
import wave
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import settings
from .jobs import ACTIVE
from .media import MIMES, probe, run, safe_path, signature_ok
from .models import ClonedVoice, Job, LocalTTSGeneration, Meeting, User, get_db, uid
from .provider import OpenRouter
from . import local_tts
from .qwen_runtime import LANGUAGES, LocalTTSError
from .security import current_user, rate_limit

router = APIRouter(prefix="/api/voices")
PREVIEW_TEXT = "Hello! This is my new voice in SUMX. I can now narrate my meeting summaries."


def owned(db, voice_id, user):
    voice = db.scalar(select(ClonedVoice).where(ClonedVoice.id == voice_id, ClonedVoice.user_id == user.id))
    if not voice:
        raise HTTPException(404, "Voice not found")
    return voice


def info(v):
    preview_stage, local_running = db_preview_stage(v.id)
    return {
        k: getattr(v, k)
        for k in (
            "id",
            "name",
            "description",
            "reference_text",
            "duration",
            "language",
            "model",
            "consent_at",
            "saved",
            "preview_error",
            "created",
            "updated",
        )
    } | {
        "has_preview": bool(v.preview_path),
        "preview_running": local_running or v.preview_until > time.time(),
        "preview_current": bool(v.preview_path)
        and v.model == settings.qwen_tts_model
        and bool(v.reference_text.strip()),
        "preview_stage": preview_stage,
        "usable": v.saved
        and bool(v.reference_text.strip())
        and bool(v.consent_at)
        and v.language in LANGUAGES,
    }


def lock_user(db, user_id):
    # Serialize preview/create/delete/enqueue checks across API processes and databases.
    db.execute(update(User).where(User.id == user_id).values(name=User.name))


def db_preview_stage(voice_id):
    from .models import DB

    with DB() as db:
        task = db.scalar(
            select(LocalTTSGeneration)
            .where(LocalTTSGeneration.voice_id == voice_id, LocalTTSGeneration.purpose == "voice_preview")
            .order_by(LocalTTSGeneration.created.desc())
        )
        return (task.stage, task.state in ACTIVE) if task else (None, False)


def no_voice_work(db, user_id, voice_id=None):
    now = time.time()
    local = select(LocalTTSGeneration.id).where(
        LocalTTSGeneration.user_id == user_id, LocalTTSGeneration.state.in_(ACTIVE)
    )
    if voice_id:
        local = local.where(LocalTTSGeneration.voice_id == voice_id)
    if db.scalar(local):
        raise HTTPException(409, "Local voice generation is queued or running. Please wait for it to finish.")
    previews = select(ClonedVoice.id).where(ClonedVoice.user_id == user_id, ClonedVoice.preview_until > now)
    if voice_id:
        previews = previews.where(ClonedVoice.id == voice_id)
    if db.scalar(previews):
        raise HTTPException(409, "A voice preview is still running. Try again when it finishes.")
    jobs = db.scalars(
        select(Job)
        .join(Meeting)
        .where(Meeting.user_id == user_id, Job.state.in_(ACTIVE), Job.kind == "speech")
    )
    for job in jobs:
        if job.payload.get("voice_type") == "cloned" and (
            not voice_id or job.payload.get("cloned_voice_id") == voice_id
        ):
            raise HTTPException(409, "Cloned narration is queued or running. Try again when it finishes.")


@router.get("/presets")
def presets(user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(db, "voices:" + user.id, 30, 3600)
    provider = OpenRouter()
    try:
        return {"voices": provider.voices(), "default": settings.openrouter_tts_voice}
    finally:
        provider.close()


@router.get("/capabilities")
def capabilities(user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(db, "voice-capabilities:" + user.id, 1800, 3600)
    return local_tts.status(db)


@router.get("")
def list_voices(
    q: str = Query("", max_length=100), user=Depends(current_user), db: Session = Depends(get_db)
):
    return [
        info(v)
        for v in db.scalars(
            select(ClonedVoice)
            .where(
                ClonedVoice.user_id == user.id,
                ClonedVoice.name.ilike("%" + q.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"),
            )
            .order_by(ClonedVoice.created.desc())
        )
    ]


@router.post("", status_code=201)
def create_voice(
    name: str = Form(..., min_length=1, max_length=100),
    description: str = Form("", max_length=500),
    language: str = Form("en", max_length=8),
    consent: bool = Form(False),
    reference_text: str = Form(..., min_length=1, max_length=5000),
    file: UploadFile = File(...),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if not consent:
        raise HTTPException(422, "Explicit permission to create and use this voice is required")
    if not name.strip() or language not in LANGUAGES or not reference_text.strip():
        raise HTTPException(422, "Enter a voice name and supported language")
    rate_limit(db, "voice-create:" + user.id, 20, 3600)
    ext = Path(file.filename or "").suffix.lower()
    if (
        ext not in {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".mp4"}
        or (file.content_type or "").split(";")[0] not in MIMES
    ):
        raise HTTPException(415, "Record a WAV, MP3, WebM, Ogg or M4A audio sample")
    voice_id = uid()
    folder = settings.storage_dir / "voices" / voice_id
    folder.mkdir(parents=True)
    source, target = folder / ("upload" + ext), folder / "reference.wav"
    try:
        size = 0
        with source.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > 10 * 1024 * 1024:
                    raise HTTPException(413, "Voice samples must be no larger than 10 MiB")
                out.write(chunk)
        if not signature_ok(source):
            raise ValueError("The recording is not a supported audio file")
        duration, video = probe(source)
        if video or not 3 <= duration <= 30.5:
            raise ValueError("Record 3–30 seconds of audio only; 15–30 seconds is recommended")
        run(
            [
                settings.ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-t",
                "30",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(target),
            ]
        )
        with wave.open(str(target), "rb") as recording:
            samples = array.array("h", recording.readframes(recording.getnframes()))
        if not samples or sum(s * s for s in samples) / len(samples) < 10000:
            raise ValueError(
                "The sample is silent or too quiet. Move closer to your microphone and record again."
            )
        source.unlink()
        lock_user(db, user.id)
        no_voice_work(db, user.id)
        voice = ClonedVoice(
            id=voice_id,
            user_id=user.id,
            name=name.strip(),
            description=description.strip(),
            language=language,
            reference_path=str(target),
            reference_text=reference_text.strip(),
            duration=min(duration, 30),
            model=settings.qwen_tts_model,
            consent_at=time.time(),
        )
        db.add(voice)
        db.commit()
        return info(voice)
    except Exception as exc:
        db.rollback()
        shutil.rmtree(folder)
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise
    finally:
        file.file.close()


@router.get("/{voice_id}")
def get_voice(voice_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return info(owned(db, voice_id, user))


class VoiceEdit(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    saved: Literal[True] | None = None
    reference_text: str | None = Field(None, min_length=1, max_length=5000)
    language: str | None = Field(None, max_length=8)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Enter a voice name")
        return value.strip() if value else value


@router.patch("/{voice_id}")
def edit_voice(voice_id: str, body: VoiceEdit, user=Depends(current_user), db: Session = Depends(get_db)):
    lock_user(db, user.id)
    v = owned(db, voice_id, user)
    if body.language is not None and body.language not in LANGUAGES:
        raise HTTPException(422, "Choose a supported Qwen3-TTS language")
    if body.reference_text is not None and not body.reference_text.strip():
        raise HTTPException(422, "Enter the actual words spoken in your recording")
    if (body.reference_text is not None and body.reference_text != v.reference_text) or (
        body.language is not None and body.language != v.language
    ):
        no_voice_work(db, user.id, v.id)
        v.saved, v.model = False, ""
    if body.saved and (not v.preview_path or v.model != settings.qwen_tts_model):
        raise HTTPException(
            409,
            "Generate and review a real preview before saving this voice. The reference remains a private draft.",
        )
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(v, key, value)
    v.updated = time.time()
    db.commit()
    return info(v)


@router.delete("/{voice_id}")
def delete_voice(voice_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    lock_user(db, user.id)
    v = owned(db, voice_id, user)
    no_voice_work(db, user.id, v.id)
    folder = safe_path(v.reference_path).parent
    if folder != (settings.storage_dir / "voices" / v.id).resolve():
        raise HTTPException(409, "Invalid voice storage directory")
    # Remove files first: a filesystem failure keeps the profile available for a retry.
    shutil.rmtree(folder, ignore_errors=False)
    for task in db.scalars(select(LocalTTSGeneration).where(LocalTTSGeneration.voice_id == v.id)):
        task.text, task.reference_text, task.output_path = "", "", None
    db.delete(v)
    db.commit()
    return {"ok": True}


@router.post("/{voice_id}/preview")
def preview(
    voice_id: str,
    key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=100),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    rate_limit(db, "voice-preview:" + user.id, 12, 3600)
    lock_user(db, user.id)
    v = owned(db, voice_id, user)
    if v.preview_path and v.model == settings.qwen_tts_model and v.reference_text.strip():
        return info(v)
    # A repeated click resumes the same queued/running private task.
    running = db.scalar(
        select(LocalTTSGeneration).where(
            LocalTTSGeneration.voice_id == v.id,
            LocalTTSGeneration.purpose == "voice_preview",
            LocalTTSGeneration.state.in_(ACTIVE),
        )
    )
    if running:
        return info(v)
    no_voice_work(db, user.id)
    try:
        task = local_tts.submit(db, v, PREVIEW_TEXT, "preview:" + v.id)
    except LocalTTSError as exc:
        raise HTTPException(503, str(exc)) from exc
    db.refresh(v)
    return {**info(v), "preview_job_id": task.id}


@router.get("/{voice_id}/{kind}")
def voice_audio(
    voice_id: str,
    kind: Literal["preview", "reference"],
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    v = owned(db, voice_id, user)
    path = v.preview_path if kind == "preview" else v.reference_path
    if not path or not safe_path(path).is_file():
        raise HTTPException(404, "Voice audio is not available")
    return FileResponse(safe_path(path), media_type="audio/mpeg" if kind == "preview" else "audio/wav")
