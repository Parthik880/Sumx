import time
import uuid
from decimal import Decimal
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings
from .money import DecimalSum, USD


class Base(DeclarativeBase):
    pass


def uid():
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default="user")
    password_hash: Mapped[str | None] = mapped_column(Text)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    admin_id: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    picture: Mapped[str | None] = mapped_column(Text)
    last_login: Mapped[float | None] = mapped_column(Float)
    tts_preset_voice: Mapped[str] = mapped_column(String(64), default="en_paul_neutral")


class AllowedEmail(Base):
    __tablename__ = "allowed_emails"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    id: Mapped[str] = mapped_column(String(32), default=uid, unique=True)
    approved_at: Mapped[float | None] = mapped_column(Float)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[float | None] = mapped_column(Float)


class AdminAudit(Base):
    __tablename__ = "admin_audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(320))
    created: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[float] = mapped_column(Float)
    auth_method: Mapped[str] = mapped_column(String(24), default="google")
    revoked_at: Mapped[float | None] = mapped_column(Float)


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (UniqueConstraint("user_id", "upload_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    upload_key: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200), default="Untitled meeting")
    created: Mapped[float] = mapped_column(Float, default=time.time)
    duration: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(32), default="empty")
    error: Mapped[str | None] = mapped_column(Text)
    original_path: Mapped[str | None] = mapped_column(Text)
    original_name: Mapped[str | None] = mapped_column(String(250))
    media_type: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(8))
    retain_source: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(String(16), default="standard")
    transcript_version: Mapped[int] = mapped_column(Integer, default=0)
    summary_version: Mapped[int] = mapped_column(Integer, default=0)


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (UniqueConstraint("meeting_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str | None] = mapped_column(String(32))


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (
        UniqueConstraint("meeting_id", "version"),
        Index("ix_summaries_meeting_detail_version", "meeting_id", "detail", "version"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    transcript_version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    # Blank for older and hand-edited summaries, which are never reused automatically.
    detail: Mapped[str | None] = mapped_column(String(16))
    prompt_version: Mapped[str | None] = mapped_column(String(240))
    created: Mapped[float] = mapped_column(Float, default=time.time)


class AudioOutput(Base):
    __tablename__ = "audio_outputs"
    __table_args__ = (UniqueConstraint("meeting_id", "summary_version", "model", "voice"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    summary_version: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(200))
    voice: Mapped[str] = mapped_column(String(200))
    voice_type: Mapped[str] = mapped_column(String(16), default="preset")
    voice_name: Mapped[str | None] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("meeting_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    request_key: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    completed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    heartbeat: Mapped[float] = mapped_column(Float, default=time.time)
    user_id: Mapped[str | None] = mapped_column(String(32))
    voice_id: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(200))
    narration_stage: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[float | None] = mapped_column(Float)
    completed_at: Mapped[float | None] = mapped_column(Float)
    output_path: Mapped[str | None] = mapped_column(Text)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0)


class SttChunk(Base):
    """One finished part of a transcription job. Text is cleared once the transcript is saved."""

    __tablename__ = "stt_chunks"
    __table_args__ = (UniqueConstraint("job_id", "chunk_index"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    offset_seconds: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 9), nullable=True)
    cost_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)


class SttLease(Base):
    __tablename__ = "stt_leases"
    slot: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    owner: Mapped[str] = mapped_column(String(32))
    expires: Mapped[float] = mapped_column(Float)


class SttState(Base):
    __tablename__ = "stt_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    level: Mapped[int] = mapped_column(Integer)
    updated: Mapped[float] = mapped_column(Float)
    control: Mapped[dict] = mapped_column(JSON, default=dict)


class Usage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (
        CheckConstraint("currency = 'USD'", name="ck_api_usage_usd"),
        Index("ix_api_usage_created", "created"),
        Index("ix_api_usage_user_created", "user_id", "created"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    meeting_id: Mapped[str | None] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    operation: Mapped[str] = mapped_column(String(12))
    provider: Mapped[str] = mapped_column(String(40), default="openrouter")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    model: Mapped[str] = mapped_column(String(200))
    generation_id: Mapped[str | None] = mapped_column(String(200))
    request_id: Mapped[str | None] = mapped_column(String(200))
    cost: Mapped[Decimal | None] = mapped_column(USD())
    cost_source: Mapped[str] = mapped_column(String(32), default="unavailable")
    cost_status: Mapped[str] = mapped_column(String(16), default="pending")
    billing_key: Mapped[str | None] = mapped_column(String(250), unique=True, index=True)
    excluded_as_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    units: Mapped[dict] = mapped_column(JSON, default=dict)
    voice_type: Mapped[str | None] = mapped_column(String(16))
    input_characters: Mapped[int | None] = mapped_column(Integer)
    generation_status: Mapped[str] = mapped_column(String(32), default="succeeded")
    purpose: Mapped[str] = mapped_column(String(24), default="meeting")
    created: Mapped[float] = mapped_column(Float, default=time.time)


class ClonedVoice(Base):
    __tablename__ = "cloned_voices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    reference_path: Mapped[str] = mapped_column(Text)
    reference_text: Mapped[str] = mapped_column(Text, default="")
    reference_verified_at: Mapped[float | None] = mapped_column(Float)
    reference_quality: Mapped[dict] = mapped_column(JSON, default=dict)
    preview_version: Mapped[str | None] = mapped_column(String(80))
    duration: Mapped[float] = mapped_column(Float)
    language: Mapped[str] = mapped_column(String(8), default="en")
    model: Mapped[str] = mapped_column(String(200))
    preview_path: Mapped[str | None] = mapped_column(Text)
    consent_at: Mapped[float] = mapped_column(Float)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    preview_until: Mapped[float] = mapped_column(Float, default=0)
    preview_error: Mapped[str | None] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class LocalTTSGeneration(Base):
    __tablename__ = "local_tts_generations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    request_key: Mapped[str] = mapped_column(String(200), unique=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    voice_id: Mapped[str] = mapped_column(String(32), index=True)
    meeting_id: Mapped[str | None] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(24), default="modal_qwen3")
    remote_call_id: Mapped[str | None] = mapped_column(String(80))
    voice_type: Mapped[str] = mapped_column(String(16), default="cloned")
    preset_voice: Mapped[str | None] = mapped_column(String(64))
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    reference_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    input_characters: Mapped[int] = mapped_column(Integer)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0)
    processing_seconds: Mapped[float] = mapped_column(Float, default=0)
    peak_vram_mb: Mapped[float] = mapped_column(Float, default=0)
    external_api_cost: Mapped[Decimal] = mapped_column(USD(), default=Decimal("0"))
    output_path: Mapped[str | None] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    heartbeat: Mapped[float] = mapped_column(Float, default=time.time)
    parent_job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    segment_index: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[float | None] = mapped_column(Float)
    completed_at: Mapped[float | None] = mapped_column(Float)
    first_audio_at: Mapped[float | None] = mapped_column(Float)
    stream_events: Mapped[int] = mapped_column(Integer, default=0)


class LocalTTSWorker(Base):
    __tablename__ = "local_tts_worker"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(200))
    gpu_name: Mapped[str | None] = mapped_column(String(200))
    vram_mb: Mapped[float] = mapped_column(Float, default=0)
    peak_vram_mb: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    heartbeat: Mapped[float] = mapped_column(Float, default=time.time)
    engine: Mapped[str] = mapped_column(String(40), default="modal_faster_qwen3")
    cuda_graphs: Mapped[bool] = mapped_column(Boolean, default=False)
    load_seconds: Mapped[float] = mapped_column(Float, default=0)
    warmup_seconds: Mapped[float] = mapped_column(Float, default=0)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    expires: Mapped[float] = mapped_column(Float)


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30}
    if settings.database_url.startswith("sqlite")
    else {},
)
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def sqlite_options(connection, _):
        connection.create_aggregate("decimal_sum", 1, DecimalSum)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")


DB = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with DB() as db:
        yield db
