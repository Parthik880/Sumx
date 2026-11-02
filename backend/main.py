import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from google.auth.exceptions import GoogleAuthError
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from starlette.middleware.sessions import SessionMiddleware
from .config import settings
from .google_identity import verify_google_token
from .analytics import router as analytics_router, user_meetings, user_stats
from .admin import (
    router as admin_router,
    AdminLogin,
    login as password_login,
    approve,
    overview as admin_overview,
)
from .money import usd
from . import summaries
from .jobs import ACTIVE, enqueue, latest
from .media import EXTENSIONS, MIMES, probe, safe_path, signature_ok
from .limits import BodyLimitMiddleware
from .models import AllowedEmail, AudioOutput, Job, Meeting, Session, Summary, Transcript, Usage, User, get_db
from .provider import OpenRouter, ProviderError
from .schema import Minutes, SpeechRequest, SummaryRequest, spoken_text
from .voices import router as voices_router, owned as owned_voice, lock_user
from .security import (
    admin_user,
    logout_session,
    current_user,
    digest,
    origin_check,
    owns,
    rate_limit,
    start_session,
)


@asynccontextmanager
async def lifespan(app):
    if len(settings.session_secret) < 32:
        raise RuntimeError("Set SESSION_SECRET to at least 32 random characters before starting SUMX")
    if settings.openrouter_base_url.rstrip("/") != "https://openrouter.ai/api/v1":
        raise RuntimeError("OPENROUTER_BASE_URL must use the official OpenRouter HTTPS endpoint")
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="SUMX", lifespan=lifespan)
app.include_router(analytics_router)
app.include_router(voices_router)
app.include_router(admin_router)
app.add_middleware(
    BodyLimitMiddleware, limit=(settings.max_upload_mb + 1) * 1024 * 1024 if settings.max_upload_mb else None
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or secrets.token_hex(32),
    session_cookie="sumx_oauth",
    https_only=settings.cookie_secure,
    max_age=600,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    if (
        settings.max_upload_mb > 0
        and request.headers.get("content-length", "").isdigit()
        and int(request.headers["content-length"]) > (settings.max_upload_mb + 1) * 1024 * 1024
    ):
        return JSONResponse({"detail": "Upload exceeds the server limit"}, status_code=413)
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(self)",
        }
    )
    # Only versioned preset previews opt in to caching; everything else stays uncached.
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/me")
def me(request: Request, user=Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "csrf": request.state.session.csrf,
        "auth_method": request.state.session.auth_method,
        "admin_id": user.admin_id,
    }


@app.get("/api/auth/google/config")
def google_config(request: Request, db: DBSession = Depends(get_db)):
    rate_limit(db, "google-config:" + request.client.host, 60, 300)
    if not settings.google_client_id:
        raise HTTPException(503, "Google sign-in is not configured. Contact your administrator.")
    nonce = secrets.token_urlsafe(32)
    request.session["google_nonce"] = nonce
    request.session["google_nonce_issued"] = time.time()
    return {"client_id": settings.google_client_id, "nonce": nonce}


class GoogleCredential(BaseModel):
    credential: str = Field(min_length=1, max_length=16000)


@app.post("/api/auth/google")
def google_login(body: GoogleCredential, request: Request, db: DBSession = Depends(get_db)):
    origin_check(request)
    rate_limit(db, "google-login:" + request.client.host, 20, 300)
    if not settings.google_client_id:
        raise HTTPException(503, "Google sign-in is not configured. Contact your administrator.")
    nonce = request.session.pop("google_nonce", None)
    issued = request.session.pop("google_nonce_issued", 0)
    if not nonce or time.time() - issued > 300:
        raise HTTPException(403, "Your sign-in request expired. Reload the page and try again.")
    try:
        info = verify_google_token(body.credential)
    except (ValueError, GoogleAuthError, KeyError, TypeError):
        raise HTTPException(401, "Google identity could not be verified. Choose an account and try again.")
    if not isinstance(info.get("nonce"), str) or not secrets.compare_digest(info["nonce"], nonce):
        raise HTTPException(401, "Google sign-in does not match this browser request. Reload and try again.")
    if info.get("email_verified") is not True:
        raise HTTPException(403, "A verified Google email is required.")
    email, subject = info.get("email"), info.get("sub")
    if (
        not isinstance(email, str)
        or len(email) > 320
        or not isinstance(subject, str)
        or not 0 < len(subject) <= 255
    ):
        raise HTTPException(401, "Google identity is missing its email or subject identifier.")
    email = email.lower()
    allowed = db.get(AllowedEmail, email)
    if not allowed or not allowed.active:
        raise HTTPException(
            403,
            "Access denied. Your email is not on the active college access list. Contact your administrator or choose another Google account.",
        )
    user = db.scalar(select(User).where(User.google_sub == subject))
    by_email = db.scalar(select(User).where(User.email == email))
    if (user and by_email and user.id != by_email.id) or (
        by_email and by_email.google_sub and by_email.google_sub != subject
    ):
        raise HTTPException(
            403, "This email is already linked to another Google identity. Contact your administrator."
        )
    user = user or by_email
    if user and user.role == "admin":
        raise HTTPException(403, "Administrators must use the separate admin login.")
    if not user:
        user = User(id=allowed.id, email=email, name=str(info.get("name") or email)[:200])
        db.add(user)
    user.email, user.google_sub = email, subject
    user.name = str(info.get("name") or email)[:200]
    picture = info.get("picture")
    user.picture = (
        picture
        if isinstance(picture, str) and picture.startswith("https://") and len(picture) <= 2048
        else None
    )
    user.last_login = time.time()
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This identity was updated by another sign-in. Reload and try again.")
    old = db.get(Session, digest(request.cookies.get("sumx_session", "")))
    if old:
        db.delete(old)
    response = JSONResponse({"ok": True})
    start_session(db, user, response, "google")
    request.session.clear()
    return response


class Login(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@app.post("/api/auth/admin/login")
def admin_login(body: Login, request: Request, db: DBSession = Depends(get_db)):
    return password_login(AdminLogin(admin_id=body.email, password=body.password), request, db)


@app.post("/api/auth/logout")
def logout(request: Request, session=Depends(logout_session), db: DBSession = Depends(get_db)):
    db.delete(session)
    db.commit()
    request.session.clear()
    response = JSONResponse({"ok": True})
    response.delete_cookie("sumx_session", path="/")
    return response


@app.get("/api/config")
def config(user=Depends(current_user)):
    return {
        "max_upload_mb": settings.max_upload_mb,
        "max_duration_seconds": settings.max_duration_seconds,
        "models": {
            "stt": settings.openrouter_stt_model,
            "summary": settings.openrouter_summary_model,
            "tts": settings.openrouter_tts_model,
            "cloning": settings.qwen_tts_model,
        },
        "default_voice": settings.tts_default_voice,
        "ai_configured": bool(settings.openrouter_api_key),
    }


def brief(m):
    return {
        k: getattr(m, k)
        for k in [
            "id",
            "title",
            "created",
            "duration",
            "status",
            "error",
            "transcript_version",
            "summary_version",
            "retain_source",
        ]
    }


def job_info(job):
    return (
        {
            k: getattr(job, k)
            for k in ["id", "kind", "state", "stage", "completed", "total", "error", "created"]
        }
        if job
        else None
    )


def idle(db, meeting_id):
    db.execute(update(Meeting).where(Meeting.id == meeting_id).values(status=Meeting.status))
    db.expire_all()
    if db.scalar(select(Job.id).where(Job.meeting_id == meeting_id, Job.state.in_(ACTIVE))):
        raise HTTPException(409, "Wait for the current processing job to finish")


@app.get("/api/meetings")
def meetings(
    q: str = "",
    offset: int = 0,
    sort: str = "newest",
    summaries_only: bool = False,
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    rows, total = user_meetings(db, user, q[:200], max(0, offset), sort, summaries_only)
    return {"meetings": rows, "total": total, "stats": user_stats(db, user)}


class NewMeeting(BaseModel):
    title: str = Field(default="Untitled meeting", min_length=1, max_length=200)


@app.post("/api/meetings")
def create_meeting(body: NewMeeting, user=Depends(current_user), db: DBSession = Depends(get_db)):
    rate_limit(db, "create:" + user.id, 30, 3600)
    m = Meeting(user_id=user.id, title=body.title)
    db.add(m)
    db.commit()
    return brief(m)


@app.post("/api/meetings/upload")
@app.post("/api/meetings/recordings")
def upload(
    file: UploadFile = File(...),
    title: str = Form("Untitled meeting"),
    language: str = Form(""),
    detail: str = Form("standard"),
    consent: bool = Form(False),
    retain_source: bool = Form(True),
    key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", key):
        raise HTTPException(422, "Invalid upload retry key")
    existing = db.scalar(select(Meeting).where(Meeting.user_id == user.id, Meeting.upload_key == key))
    if existing:
        return brief(existing)
    rate_limit(db, "upload:" + user.id, 20, 3600)
    if not consent:
        raise HTTPException(422, "Confirm participant consent before uploading")
    if detail not in ("concise", "standard", "detailed") or (
        language and not re.fullmatch("[a-z]{2}", language)
    ):
        raise HTTPException(422, "Invalid detail or language selection")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in EXTENSIONS or (file.content_type or "").split(";")[0] not in MIMES:
        raise HTTPException(415, "Unsupported audio or video format")
    m = Meeting(
        user_id=user.id,
        upload_key=key,
        title=title.strip()[:200] or "Untitled meeting",
        language=language or None,
        detail=detail,
        retain_source=retain_source,
        original_name=Path(file.filename or "Recording").name[:250],
        media_type=file.content_type,
    )
    from .models import uid

    m.id = uid()
    folder = settings.storage_dir / m.id
    folder.mkdir(parents=True)
    path = folder / ("original" + ext)
    try:
        size = 0
        with path.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if settings.max_upload_mb and size > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, "File exceeds the server upload limit")
                target.write(chunk)
        if not signature_ok(path):
            raise ValueError("File signature does not match a supported media container")
        m.duration, _ = probe(path)
        m.original_path = str(path)
        db.add(m)
        db.flush()
        enqueue(db, m, "pipeline", "upload", {"detail": detail})
        return brief(m)
    except IntegrityError:
        db.rollback()
        shutil.rmtree(folder)
        existing = db.scalar(select(Meeting).where(Meeting.user_id == user.id, Meeting.upload_key == key))
        if existing:
            return brief(existing)
        raise HTTPException(409, "An upload is already being submitted")
    except (ValueError, HTTPException) as exc:
        db.rollback()
        shutil.rmtree(folder)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, str(exc))
    finally:
        file.file.close()


@app.get("/api/meetings/{meeting_id}")
def meeting_detail(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    m = owns(db, Meeting, meeting_id, user)
    t, s = latest(db, Transcript, m.id), latest(db, Summary, m.id)
    audio = db.scalars(
        select(AudioOutput).where(
            AudioOutput.meeting_id == m.id, AudioOutput.summary_version == m.summary_version
        )
    ).all()
    job = db.scalar(select(Job).where(Job.meeting_id == m.id).order_by(Job.created.desc()))
    return {
        **brief(m),
        "source_available": bool(m.original_path),
        "media_type": m.media_type,
        "transcript": {"text": t.text, "segments": t.segments, "language": t.language, "version": t.version}
        if t
        else None,
        "summary": {"content": s.content, "version": s.version, "transcript_version": s.transcript_version}
        if s
        else None,
        "audio": [
            {
                "id": a.id,
                "voice": a.voice,
                "voice_type": a.voice_type,
                "voice_name": a.voice_name,
                "summary_version": a.summary_version,
            }
            for a in audio
        ],
        "job": job_info(job),
    }


@app.get("/api/meetings/{meeting_id}/status")
def meeting_status(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    m = owns(db, Meeting, meeting_id, user)
    job = db.scalar(select(Job).where(Job.meeting_id == m.id).order_by(Job.created.desc()))
    return {**brief(m), "job": job_info(job)}


class MeetingEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    retain_source: bool | None = None


@app.patch("/api/meetings/{meeting_id}")
def edit_meeting(
    meeting_id: str, body: MeetingEdit, user=Depends(current_user), db: DBSession = Depends(get_db)
):
    m = owns(db, Meeting, meeting_id, user)
    idle(db, m.id)
    if body.title is not None:
        m.title = body.title
    if body.retain_source is not None:
        m.retain_source = body.retain_source
        if not body.retain_source and m.original_path:
            safe_path(m.original_path).unlink(missing_ok=True)
            m.original_path = None
    db.commit()
    return brief(m)


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    m = owns(db, Meeting, meeting_id, user)
    idle(db, m.id)
    for cls in (AudioOutput, Summary, Transcript, Job):
        db.execute(delete(cls).where(cls.meeting_id == m.id))
    db.delete(m)
    db.commit()
    folder = safe_path(str(settings.storage_dir / meeting_id))
    if folder.exists():
        shutil.rmtree(folder)
    return {"ok": True}


def queue_operation(db, user, meeting_id, kind, key, payload):
    m = owns(db, Meeting, meeting_id, user)
    rate_limit(db, "ai:" + user.id, 30, 3600)
    if len(key) < 16 or len(key) > 100:
        raise HTTPException(422, "Invalid retry key")
    if kind == "transcribe" and not m.original_path:
        raise HTTPException(409, "The original recording is no longer retained")
    if kind == "summarize" and not m.transcript_version:
        raise HTTPException(409, "Create a transcript first")
    if kind == "speech" and not m.summary_version:
        raise HTTPException(409, "Create a summary first")
    return job_info(enqueue(db, m, kind, key, payload))


@app.post("/api/meetings/{meeting_id}/transcribe")
def transcribe(
    meeting_id: str,
    key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    return queue_operation(db, user, meeting_id, "transcribe", key, {})


SummaryMode = Literal["concise", "standard", "detailed"]


@app.get("/api/meetings/{meeting_id}/summaries")
def list_summaries(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    owns(db, Meeting, meeting_id, user)
    return summaries.overview(db, meeting_id)


@app.get("/api/meetings/{meeting_id}/summaries/{mode}")
def mode_summary(
    meeting_id: str, mode: SummaryMode, user=Depends(current_user), db: DBSession = Depends(get_db)
):
    owns(db, Meeting, meeting_id, user)
    return summaries.full_record(db, meeting_id, mode)


@app.post("/api/meetings/{meeting_id}/summaries/{mode}/generate")
def generate_summary(
    meeting_id: str,
    mode: SummaryMode,
    key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    owns(db, Meeting, meeting_id, user)
    running = db.scalar(select(Job).where(Job.meeting_id == meeting_id, Job.state.in_(ACTIVE)))
    if running and not (running.kind == "summarize" and running.payload.get("detail") == mode):
        raise HTTPException(409, "This meeting is busy. Try again when the current task finishes.")
    return queue_operation(db, user, meeting_id, "summarize", key, {"detail": mode})


@app.get("/api/meetings/{meeting_id}/narration-options")
def narration_options(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    owns(db, Meeting, meeting_id, user)
    return {"options": summaries.overview(db, meeting_id)["narration_options"]}


@app.post("/api/meetings/{meeting_id}/summarize")
def summarize(
    meeting_id: str,
    body: SummaryRequest,
    key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    return queue_operation(db, user, meeting_id, "summarize", key, body.model_dump())


@app.post("/api/meetings/{meeting_id}/narrations")
@app.post("/api/meetings/{meeting_id}/speech")
def speech(
    meeting_id: str,
    body: SpeechRequest,
    key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    meeting = owns(db, Meeting, meeting_id, user)
    from .speech_contract import PIPELINE_VERSION, LocalTTSError
    from .voice_catalog import default_preset, preset_id

    if body.voice_type == "cloned":
        rate_limit(db, "ai:" + user.id, 30, 3600)
        if not 16 <= len(key) <= 100:
            raise HTTPException(422, "Invalid retry key")
        lock_user(db, user.id)
        voice = owned_voice(db, body.cloned_voice_id, user)
        if (
            not voice.saved
            or not voice.preview_path
            or not voice.consent_at
            or voice.preview_version != PIPELINE_VERSION
            or not voice.reference_verified_at
        ):
            raise HTTPException(409, "Generate a preview and save this voice before using it")
        if not voice.reference_text.strip():
            raise HTTPException(409, "This voice needs its matching reference transcript")
        # An exact enqueue retry remains idempotent while other clone work is blocked.
        previous = db.scalar(select(Job).where(Job.meeting_id == meeting.id, Job.request_key == key))
        if previous:
            return job_info(previous)
        if not meeting.summary_version:
            raise HTTPException(409, "Create a summary first")
    payload = body.model_dump()
    if body.voice_type == "preset":
        try:
            payload["preset_voice_id"] = preset_id(body.preset_voice_id or body.voice or default_preset(user))
        except LocalTTSError as exc:
            raise HTTPException(422, str(exc)) from exc
    # A named mode narrates that saved summary; older clients get the newest one.
    payload["summary_version"] = meeting.summary_version
    if body.summary_mode:
        chosen = summaries.active_summary(db, meeting.id, body.summary_mode)
        if not chosen:
            raise HTTPException(409, f"No {body.summary_mode} summary has been generated")
        payload["summary_version"] = chosen.version
    if body.voice_type == "cloned":
        return job_info(enqueue(db, meeting, "speech", key, payload))
    return queue_operation(db, user, meeting_id, "speech", key, payload)


@app.post("/api/meetings/{meeting_id}/jobs/{job_id}/retry")
def retry(meeting_id: str, job_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    m = owns(db, Meeting, meeting_id, user)
    rate_limit(db, "ai:" + user.id, 30, 3600)
    job = db.get(Job, job_id)
    if job and job.meeting_id == m.id and job.kind == "speech" and job.payload.get("voice_type") == "cloned":
        lock_user(db, user.id)
    idle(db, m.id)
    if not job or job.meeting_id != m.id or job.state != "failed":
        raise HTTPException(409, "Only a failed job can be retried")
    # An edit supersedes failed work and its checkpoints.
    newest = db.scalar(select(Job.id).where(Job.meeting_id == m.id).order_by(Job.created.desc()))
    if newest != job.id:
        raise HTTPException(409, "This job has been superseded")
    if job.kind == "speech" and job.payload.get("voice_type") == "cloned":
        voice = owned_voice(db, job.payload.get("cloned_voice_id"), user)
        if not voice.saved:
            raise HTTPException(409, "The selected voice is not saved")
    job.state, job.error, m.status, m.error = "queued", None, "queued", None
    if job.kind == "speech":
        job.narration_stage = "QUEUED"
    job.completed_at = None
    job.started_at = None
    job.heartbeat = time.time()
    db.commit()
    return job_info(job)


@app.get("/api/narration/jobs/{job_id}/status")
def narration_status(job_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    from .models import LocalTTSGeneration, LocalTTSWorker

    job = db.get(Job, job_id)
    if not job or job.kind != "speech":
        raise HTTPException(404, "Narration job not found")
    meeting = owns(db, Meeting, job.meeting_id, user)
    worker = db.get(LocalTTSWorker, "qwen3")
    cloned = job.payload.get("voice_type") == "cloned"
    local = db.scalar(
        select(LocalTTSGeneration)
        .where(LocalTTSGeneration.parent_job_id == job.id)
        .order_by(LocalTTSGeneration.created.desc())
    )
    stage = job.narration_stage or (
        "COMPLETED" if job.state == "completed" else "FAILED" if job.state == "failed" else "QUEUED"
    )
    if (
        job.state in ACTIVE
        and job.payload.get("voice_type") == "cloned"
        and worker
        and worker.state in ("loading", "warming_up")
    ):
        stage = "LOADING_MODEL"
    percent = (
        100
        if job.state == "completed"
        else (job.completed / job.total * 100 if job.total and job.completed < job.total else None)
    )
    return {
        "id": job.id,
        "meeting_id": meeting.id,
        "voice_id": job.voice_id or job.payload.get("cloned_voice_id"),
        "model": job.model_name,
        "state": job.state,
        "stage": stage,
        "completed_segments": job.completed,
        "total_segments": job.total,
        "percent": percent,
        "audio_seconds": job.audio_seconds,
        "stream_events": local.stream_events if local else 0,
        "segment_audio_seconds": local.audio_seconds if local and local.state == "running" else 0,
        "elapsed_seconds": max(0, (job.completed_at or time.time()) - (job.started_at or job.heartbeat)),
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error": job.error,
        "engine": "Faster Qwen3-TTS" if cloned else "Voxtral TTS 3",
        "gpu_name": worker.gpu_name if worker and cloned else None,
        "cuda_graphs": bool(worker and cloned and worker.cuda_graphs),
        "audio_ready": job.state == "completed" and bool(job.output_path),
    }


@app.get("/api/meetings/{meeting_id}/transcript")
def get_transcript(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    owns(db, Meeting, meeting_id, user)
    t = latest(db, Transcript, meeting_id)
    if not t:
        raise HTTPException(404, "Transcript is not available yet")
    return {"text": t.text, "segments": t.segments, "version": t.version}


class TranscriptEdit(BaseModel):
    text: str = Field(min_length=1, max_length=2000000)
    version: int


@app.put("/api/meetings/{meeting_id}/transcript")
def edit_transcript(
    meeting_id: str, body: TranscriptEdit, user=Depends(current_user), db: DBSession = Depends(get_db)
):
    m = owns(db, Meeting, meeting_id, user)
    idle(db, m.id)
    if m.transcript_version != body.version:
        raise HTTPException(409, "Transcript changed. Reload before editing.")
    m.transcript_version += 1
    db.add(Transcript(meeting_id=m.id, version=m.transcript_version, text=body.text, segments=[]))
    # Text edits invalidate original segment timing and failed-job checkpoints.
    db.execute(delete(Job).where(Job.meeting_id == m.id, Job.state == "failed"))
    db.commit()
    return {"version": m.transcript_version}


@app.get("/api/meetings/{meeting_id}/summary")
def get_summary(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    owns(db, Meeting, meeting_id, user)
    summary = latest(db, Summary, meeting_id)
    if not summary:
        raise HTTPException(404, "Summary is not available yet")
    return {"content": summary.content, "version": summary.version}


class SummaryEdit(BaseModel):
    content: Minutes
    version: int


@app.put("/api/meetings/{meeting_id}/summary")
def edit_summary(
    meeting_id: str, body: SummaryEdit, user=Depends(current_user), db: DBSession = Depends(get_db)
):
    m = owns(db, Meeting, meeting_id, user)
    idle(db, m.id)
    previous = db.scalar(select(Summary).where(Summary.meeting_id == m.id, Summary.version == body.version))
    # Only the active summary of its own mode can be edited, whatever other modes have done.
    if not previous or body.version not in summaries.active_versions(db, m.id):
        raise HTTPException(409, "Summary changed. Reload before editing.")
    m.summary_version += 1
    db.add(
        Summary(
            meeting_id=m.id,
            version=m.summary_version,
            transcript_version=previous.transcript_version,
            content=body.content.model_dump(),
            detail=previous.detail,
        )
    )
    db.execute(delete(Job).where(Job.meeting_id == m.id, Job.state == "failed"))
    db.commit()
    return {"version": m.summary_version}


@app.get("/api/meetings/{meeting_id}/export")
def export(
    meeting_id: str,
    kind: str = "summary",
    mode: SummaryMode | None = None,
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    owns(db, Meeting, meeting_id, user)
    if kind == "summary" and mode:
        obj = summaries.active_summary(db, meeting_id, mode)
    else:
        obj = latest(db, Transcript if kind == "transcript" else Summary, meeting_id)
    if not obj:
        raise HTTPException(404, "Requested content is not available")
    content = obj.text if kind == "transcript" else spoken_text(obj.content)
    return Response(
        content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{kind if kind == "transcript" else "summary"}.txt"'
        },
    )


@app.get("/api/meetings/{meeting_id}/media")
def original(meeting_id: str, user=Depends(current_user), db: DBSession = Depends(get_db)):
    m = owns(db, Meeting, meeting_id, user)
    if not m.original_path:
        raise HTTPException(404, "Source media is not retained")
    return FileResponse(
        safe_path(m.original_path),
        media_type=m.media_type,
        filename=m.original_name,
        content_disposition_type="inline",
    )


@app.get("/api/meetings/{meeting_id}/audio/{audio_id}")
def audio(
    meeting_id: str,
    audio_id: str,
    download: bool = False,
    user=Depends(current_user),
    db: DBSession = Depends(get_db),
):
    m = owns(db, Meeting, meeting_id, user)
    a = db.get(AudioOutput, audio_id)
    if not a or a.meeting_id != m.id or a.summary_version not in summaries.active_versions(db, m.id):
        raise HTTPException(404, "Narration for the current summary is not available")
    return FileResponse(
        safe_path(a.path),
        media_type="audio/mpeg",
        filename="summary.mp3",
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/api/preset-voices")
def voices(user=Depends(current_user), db: DBSession = Depends(get_db)):
    rate_limit(db, "voices:" + user.id, 30, 3600)
    from .preset_voices import PRESETS
    from .voice_catalog import default_preset

    return {"voices": list(PRESETS), "default": default_preset(user)}


@app.get("/api/admin/allowed-users", deprecated=True)
def allowed_users(user=Depends(admin_user), db: DBSession = Depends(get_db)):
    return [
        {"email": a.email, "active": a.active}
        for a in db.scalars(select(AllowedEmail).order_by(AllowedEmail.email))
    ]


class AllowedEdit(BaseModel):
    email: str = Field(max_length=320)
    active: bool = True


@app.post("/api/admin/users")
def allow_user(body: AllowedEdit, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    result = approve(db, user, body.email, body.active)
    return {"email": result["email"], "active": result["active"]}


@app.delete("/api/admin/users/{email}")
def remove_user(email: str, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    approve(db, user, email, active=False)
    return {"ok": True}


@app.get("/api/admin/analytics", deprecated=True)
def analytics(user=Depends(admin_user), db: DBSession = Depends(get_db)):
    return admin_overview(days=0, user=user, db=db)


@app.post("/api/admin/usage/reconcile")
def reconcile(user=Depends(admin_user), db: DBSession = Depends(get_db)):
    rate_limit(db, "reconcile", 6, 3600)
    provider = OpenRouter()
    updated = 0
    try:
        for u in db.scalars(
            select(Usage)
            .where(
                Usage.cost_status != "actual",
                Usage.excluded_as_duplicate.is_(False),
                Usage.generation_id.is_not(None),
            )
            .limit(30)
        ):
            try:
                cost = provider.cost(u.generation_id)
                amount = usd(cost)
                if amount is not None:
                    u.cost, u.cost_source, u.cost_status = amount, "generation_api", "actual"
                    updated += 1
                    db.commit()
            except ProviderError:
                continue
        return {"updated": updated}
    finally:
        provider.close()


@app.get("/api/admin/credits")
def credits(user=Depends(admin_user)):
    provider = OpenRouter()
    try:
        return provider.json(provider.request("GET", "/credits")).get("data", {})
    finally:
        provider.close()
