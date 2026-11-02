"""Concise, Standard and Detailed are independent: each mode's active summary is its newest row."""

import hashlib
import json
from sqlalchemy import func, select
from .jobs import ACTIVE
from .models import AudioOutput, Job, Meeting, Summary

MODES = ("concise", "standard", "detailed")


def active_summary(db, meeting_id, mode):
    return db.scalar(
        select(Summary)
        .where(Summary.meeting_id == meeting_id, Summary.detail == mode)
        .order_by(Summary.version.desc())
    )


def active_versions(db, meeting_id):
    """Versions currently in use: one per mode, plus an older summary of unknown mode."""
    rows = db.execute(
        select(Summary.detail, func.max(Summary.version))
        .where(Summary.meeting_id == meeting_id)
        .group_by(Summary.detail)
    ).all()
    return {version for _, version in rows}


def content_hash(summary):
    text = json.dumps(summary.content, sort_keys=True).encode()
    return hashlib.sha256(text).hexdigest()[:16]


def audio_for(db, meeting_id, version):
    return db.scalars(
        select(AudioOutput).where(
            AudioOutput.meeting_id == meeting_id, AudioOutput.summary_version == version
        )
    ).all()


def audio_info(audio):
    return {
        "id": audio.id,
        "voice": audio.voice,
        "voice_type": audio.voice_type,
        "voice_name": audio.voice_name,
        "summary_version": audio.summary_version,
    }


def mode_status(db, meeting_id, mode, summary):
    job = db.scalar(
        select(Job)
        .where(
            Job.meeting_id == meeting_id,
            Job.kind.in_(("pipeline", "summarize")),
            func.coalesce(Job.payload["detail"].as_string(), db.get(Meeting, meeting_id).detail) == mode,
        )
        .order_by(Job.created.desc())
    )
    current = bool(job and job.payload.get("detail", db.get(Meeting, meeting_id).detail) == mode)
    if current and job.state in ACTIVE:
        return "generating", None
    if current and job.state == "failed":
        return "failed", job.error
    return ("generated" if summary else "not_generated"), None


def describe(db, meeting_id, mode):
    summary = active_summary(db, meeting_id, mode)
    status, error = mode_status(db, meeting_id, mode, summary)
    info = {"mode": mode, "exists": bool(summary), "status": status, "error": error}
    if summary:
        info.update(
            id=summary.id,
            version=summary.version,
            revision=db.scalar(
                select(func.count())
                .select_from(Summary)
                .where(Summary.meeting_id == meeting_id, Summary.detail == mode)
            ),
            transcript_version=summary.transcript_version,
            content_hash=content_hash(summary),
            model_prompt=summary.prompt_version,
            created=summary.created,
            audio_count=len(audio_for(db, meeting_id, summary.version)),
        )
    return info


def overview(db, meeting_id):
    modes = {mode: describe(db, meeting_id, mode) for mode in MODES}
    legacy = db.scalar(
        select(Summary)
        .where(Summary.meeting_id == meeting_id, Summary.detail.is_(None))
        .order_by(Summary.version.desc())
    )
    newest = max(
        (info for info in modes.values() if info["exists"]),
        key=lambda info: info["version"],
        default=None,
    )
    return {
        "modes": modes,
        "latest_mode": newest["mode"] if newest else None,
        "narration_options": [mode for mode in MODES if modes[mode]["exists"]],
        # Kept visible, but its mode is unknown, so it is never presented as any one mode.
        "legacy": {"id": legacy.id, "version": legacy.version, "content": legacy.content} if legacy else None,
    }


def full_record(db, meeting_id, mode):
    info = describe(db, meeting_id, mode)
    summary = active_summary(db, meeting_id, mode)
    if summary:
        info["content"] = summary.content
        info["audio"] = [audio_info(a) for a in audio_for(db, meeting_id, summary.version)]
    return info
