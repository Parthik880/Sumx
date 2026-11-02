"""Scoped SQL aggregates; regular-user responses never contain service/model costs."""

import csv
import io
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BeforeValidator
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import String, case, func, or_, select, union
from sqlalchemy.orm import Session
from .models import AllowedEmail, Job, LocalTTSGeneration, Meeting, Summary, Usage, User, engine, get_db
from .money import ZERO
from .security import admin_user, current_user

router = APIRouter(prefix="/api")
DAY = 86400
Days = Annotated[Literal[7, 30, 90], BeforeValidator(int)]


def bounds(days, now=None):
    now = time.time() if now is None else now
    today = int(now // DAY) * DAY
    start = today - (days - 1) * DAY
    return start, now, start - days * DAY, now - days * DAY


def total_expr(condition=True):
    value = case((condition, Usage.cost), else_=None)
    return func.decimal_sum(value, type_=String()) if engine.dialect.name == "sqlite" else func.sum(value)


def cost_columns():
    return [
        total_expr(Usage.cost_status != "estimated").label("actual"),
        total_expr(Usage.cost_status == "estimated").label("estimated"),
        func.sum(case((Usage.cost.is_(None), 1), else_=0)).label("pending"),
        func.sum(case((Usage.cost_status == "estimated", 1), else_=0)).label("estimates"),
        func.count(Usage.id).label("requests"),
    ]


def pack(row=None):
    r = row or {}
    actual, estimated = Decimal(str(r.get("actual") or 0)), Decimal(str(r.get("estimated") or 0))
    return {
        "actual_usd": str(actual),
        "estimated_usd": str(estimated),
        "known_usd": str(actual + estimated),
        "pending_requests": int(r.get("pending") or 0),
        "estimated_requests": int(r.get("estimates") or 0),
        "requests": int(r.get("requests") or 0),
    }


def cost_query(*conditions):
    return select(*cost_columns()).where(Usage.excluded_as_duplicate.is_(False), *conditions)


def cost_total(db, *conditions):
    return pack(db.execute(cost_query(*conditions)).mappings().one())


def change(current, previous):
    if (
        current["pending_requests"]
        or previous["pending_requests"]
        or current["estimated_requests"]
        or previous["estimated_requests"]
    ):
        return None
    old = Decimal(previous["known_usd"])
    return float((Decimal(current["known_usd"]) - old) / old * 100) if old > ZERO else None


def spending(db, days, user_id=None):
    start, end, old_start, old_end = bounds(days)
    scope = [Usage.user_id == user_id] if user_id else []
    current = cost_total(db, *scope, Usage.created >= start, Usage.created <= end)
    previous = cost_total(db, *scope, Usage.created >= old_start, Usage.created < old_end)
    day = func.floor(Usage.created / DAY).label("day")
    rows = (
        db.execute(
            cost_query(*scope, Usage.created >= start, Usage.created <= end).add_columns(day).group_by(day)
        )
        .mappings()
        .all()
    )
    indexed = {int(r["day"]): pack(r) for r in rows}
    daily = [
        {
            "date": datetime.fromtimestamp(start + i * DAY, timezone.utc).date().isoformat(),
            **indexed.get(int(start // DAY) + i, pack()),
        }
        for i in range(days)
    ]
    return {
        "days": days,
        "timezone": "UTC",
        "start": start,
        "end": end,
        "totals": current,
        "previous": previous,
        "change_percent": change(current, previous),
        "daily": daily,
    }


def meeting_cost_map(db, ids):
    if not ids:
        return {}
    rows = db.execute(
        cost_query(Usage.meeting_id.in_(ids)).add_columns(Usage.meeting_id).group_by(Usage.meeting_id)
    ).mappings()
    return {r["meeting_id"]: pack(r) for r in rows}


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


def user_meetings(db, user, q="", offset=0, sort="newest", summaries_only=False, limit=100):
    scope = [Meeting.user_id == user.id]
    if q:
        scope.append(func.lower(Meeting.title).contains(q.lower(), autoescape=True))
    if summaries_only:
        scope.append(Meeting.summary_version > 0)
    rows = db.scalars(
        select(Meeting)
        .where(*scope)
        .order_by(Meeting.created.asc() if sort == "oldest" else Meeting.created.desc(), Meeting.id)
        .offset(offset)
        .limit(limit)
    ).all()
    costs = meeting_cost_map(db, [m.id for m in rows])
    result = [
        {
            **brief(m),
            "cost": costs.get(m.id, pack()),
            "cost_pending": m.status
            in ("queued", "extracting", "transcribing", "summarizing", "generating_speech"),
        }
        for m in rows
    ]
    total = db.scalar(select(func.count(Meeting.id)).where(*scope))
    return result, total


def user_stats(db, user):
    start, end, old_start, old_end = bounds(7)
    count = (
        db.execute(
            select(
                func.count(Meeting.id).label("meetings"),
                func.coalesce(func.sum(Meeting.duration), 0).label("seconds"),
                func.sum(case((Meeting.summary_version > 0, 1), else_=0)).label("summaries"),
                func.sum(case((Meeting.created >= start, 1), else_=0)).label("week_meetings"),
                func.sum(
                    case(((Meeting.created >= old_start) & (Meeting.created < old_end), 1), else_=0)
                ).label("old_meetings"),
                func.sum(case((Meeting.created >= start, Meeting.duration), else_=0)).label("week_seconds"),
                func.sum(
                    case(
                        ((Meeting.created >= old_start) & (Meeting.created < old_end), Meeting.duration),
                        else_=0,
                    )
                ).label("old_seconds"),
            ).where(Meeting.user_id == user.id)
        )
        .mappings()
        .one()
    )
    first_summary = (
        select(Summary.meeting_id, func.min(Summary.created).label("created"))
        .group_by(Summary.meeting_id)
        .subquery()
    )
    ready = db.execute(
        select(
            func.sum(case((first_summary.c.created >= start, 1), else_=0)),
            func.sum(
                case(
                    ((first_summary.c.created >= old_start) & (first_summary.c.created < old_end), 1), else_=0
                )
            ),
        )
        .select_from(first_summary)
        .join(Meeting, Meeting.id == first_summary.c.meeting_id)
        .where(Meeting.user_id == user.id)
    ).one()
    comparable = user.created <= old_start
    recent = db.scalar(
        select(Meeting)
        .where(Meeting.user_id == user.id, Meeting.summary_version > 0)
        .order_by(Meeting.created.desc())
        .limit(1)
    )
    this_week = cost_total(db, Usage.user_id == user.id, Usage.created >= start, Usage.created <= end)
    prior_week = cost_total(db, Usage.user_id == user.id, Usage.created >= old_start, Usage.created < old_end)
    return {
        "meetings": count["meetings"],
        "seconds": count["seconds"],
        "summaries": int(count["summaries"] or 0),
        "last_summary": brief(recent) if recent else None,
        "weekly": {
            "comparable": comparable,
            "meetings_delta": int(count["week_meetings"] or 0) - int(count["old_meetings"] or 0)
            if comparable
            else None,
            "seconds_delta": float(count["week_seconds"] or 0) - float(count["old_seconds"] or 0)
            if comparable
            else None,
            "summaries_delta": int(ready[0] or 0) - int(ready[1] or 0) if comparable else None,
            "cost_change_percent": change(this_week, prior_week) if comparable else None,
        },
        "cost": cost_total(db, Usage.user_id == user.id),
        "active_jobs": db.scalar(
            select(func.count(Job.id))
            .join(Meeting, Meeting.id == Job.meeting_id)
            .where(Meeting.user_id == user.id, Job.state.in_(("queued", "running")))
        ),
    }


@router.get("/dashboard")
def dashboard(
    days: Days = 7,
    q: str = Query("", max_length=200),
    sort: Literal["newest", "oldest"] = "newest",
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    rows, total = user_meetings(db, user, q, 0, sort, limit=6)
    return {
        "meetings": rows[:6],
        "total": total,
        "stats": user_stats(db, user),
        "spending": spending(db, days, user.id),
    }


@router.get("/analytics/spending")
def user_spending(days: Days = 7, user=Depends(current_user), db: Session = Depends(get_db)):
    return spending(db, days, user.id)


def period_scope(days):
    start, end, _, _ = bounds(days)
    return [Usage.created >= start, Usage.created <= end]


def grouped(db, column, *conditions):
    rows = db.execute(cost_query(*conditions).add_columns(column.label("key")).group_by(column)).mappings()
    return {r["key"]: pack(r) for r in rows}


def local_metrics(db, days):
    start, end, _, _ = bounds(days)
    q = select(
        func.count(LocalTTSGeneration.id).label("generations"),
        func.sum(case((LocalTTSGeneration.state == "completed", 1), else_=0)).label("completed"),
        func.sum(case((LocalTTSGeneration.state == "failed", 1), else_=0)).label("failed"),
        func.sum(LocalTTSGeneration.input_characters).label("characters"),
        func.sum(LocalTTSGeneration.audio_seconds).label("audio_seconds"),
        func.sum(LocalTTSGeneration.processing_seconds).label("processing_seconds"),
        func.max(LocalTTSGeneration.peak_vram_mb).label("peak_vram_mb"),
    ).where(LocalTTSGeneration.created >= start, LocalTTSGeneration.created <= end)
    data = dict(db.execute(q).mappings().one())
    return {
        **{k: v or 0 for k, v in data.items()},
        "provider": "modal_qwen3",
        "external_api_cost_usd": format(
            sum(
                db.scalars(
                    select(LocalTTSGeneration.external_api_cost).where(
                        LocalTTSGeneration.created >= start, LocalTTSGeneration.created <= end
                    )
                ),
                Decimal(0),
            ).normalize(),
            "f",
        ),
        "models": list(
            db.scalars(
                select(LocalTTSGeneration.model)
                .where(LocalTTSGeneration.created >= start, LocalTTSGeneration.created <= end)
                .distinct()
            )
        ),
    }


def scoped_ids(days):
    start, end, _, _ = bounds(days)
    return union(
        select(Meeting.user_id.label("user_id"), Meeting.id.label("meeting_id")).where(
            Meeting.created >= start, Meeting.created <= end
        ),
        select(Usage.user_id, Usage.meeting_id).where(
            Usage.excluded_as_duplicate.is_(False), *period_scope(days)
        ),
    ).subquery()


def participants(db, days, q="", offset=0, limit=100):
    ids = scoped_ids(days)
    counts = (
        select(
            ids.c.user_id,
            func.count(ids.c.meeting_id).label("meetings"),
            func.coalesce(func.sum(Meeting.duration), 0).label("seconds"),
        )
        .outerjoin(Meeting, Meeting.id == ids.c.meeting_id)
        .group_by(ids.c.user_id)
        .subquery()
    )
    costs = cost_query(*period_scope(days)).add_columns(Usage.user_id).group_by(Usage.user_id).subquery()
    meeting_costs = grouped(db, Usage.user_id, Usage.meeting_id.is_not(None), *period_scope(days))
    query = (
        select(
            User.id,
            User.name,
            User.email,
            func.coalesce(counts.c.meetings, 0).label("meetings"),
            func.coalesce(counts.c.seconds, 0).label("seconds"),
            *[costs.c[name] for name in ["actual", "estimated", "pending", "estimates", "requests"]],
        )
        .select_from(User)
        .outerjoin(counts, User.id == counts.c.user_id)
        .outerjoin(costs, User.id == costs.c.user_id)
    )
    if q:
        query = query.where(
            or_(
                func.lower(User.name).contains(q.lower(), autoescape=True),
                func.lower(User.email).contains(q.lower(), autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    query = query.order_by(User.name, User.id).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    result = []
    for r in db.execute(query).mappings():
        cost = pack(r)
        result.append(
            {
                "id": r["id"],
                "name": r["name"],
                "email": r["email"],
                "meetings": r["meetings"],
                "seconds": r["seconds"],
                "cost": cost,
                "average_usd": str(Decimal(meeting_costs.get(r["id"], pack())["known_usd"]) / r["meetings"])
                if r["meetings"]
                else None,
            }
        )
    return result, total


@router.get("/admin/analytics/costs")
def admin_costs(
    days: Days = 30,
    q: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    user=Depends(admin_user),
    db: Session = Depends(get_db),
):
    series = spending(db, days)
    people, total = participants(db, days, q, offset)
    ids = scoped_ids(days)
    meeting_count = db.scalar(select(func.count(ids.c.meeting_id)).select_from(ids))
    meeting_total = cost_total(db, Usage.meeting_id.is_not(None), *period_scope(days))
    completed = db.scalar(
        select(func.count(Meeting.id)).where(
            Meeting.status == "ready", Meeting.id.in_(select(ids.c.meeting_id))
        )
    )
    active = db.scalar(
        select(func.count(func.distinct(ids.c.user_id)))
        .select_from(ids)
        .join(User, User.id == ids.c.user_id)
        .join(AllowedEmail, AllowedEmail.email == User.email)
        .where(AllowedEmail.active.is_(True))
    )
    operation_costs = grouped(db, Usage.operation, *period_scope(days))
    return {
        "spending": series,
        "totals": series["totals"],
        "meetings": meeting_count,
        "completed_meetings": completed,
        "active_participants": active,
        "average_usd": str(Decimal(meeting_total["known_usd"]) / meeting_count) if meeting_count else None,
        "participants": people,
        "participant_count": total,
        "operations": {op: operation_costs.get(op, pack()) for op in ("stt", "llm", "tts")},
        "models": [
            {"model": model, "cost": cost}
            for model, cost in grouped(db, Usage.model, *period_scope(days)).items()
        ],
        "preview_cost": cost_total(
            db, Usage.purpose.in_(("voice_preview", "preset_preview")), *period_scope(days)
        ),
        "local_tts": local_metrics(db, days),
    }


@router.get("/admin/analytics/users/{user_id}/meetings")
def participant_meetings(
    user_id: str, days: Days = 30, user=Depends(admin_user), db: Session = Depends(get_db)
):
    ids = scoped_ids(days)
    costs = (
        cost_query(Usage.user_id == user_id, *period_scope(days))
        .add_columns(Usage.meeting_id)
        .group_by(Usage.meeting_id)
        .subquery()
    )
    rows = db.execute(
        select(
            ids.c.meeting_id,
            Meeting.title,
            Meeting.created,
            Meeting.duration,
            Meeting.status,
            *[costs.c[k] for k in ["actual", "estimated", "pending", "estimates", "requests"]],
        )
        .select_from(ids)
        .outerjoin(Meeting, Meeting.id == ids.c.meeting_id)
        .outerjoin(costs, costs.c.meeting_id == ids.c.meeting_id)
        .where(ids.c.user_id == user_id, ids.c.meeting_id.is_not(None))
        .order_by(Meeting.created.desc())
    ).mappings()
    return [
        {
            "id": r["meeting_id"],
            "title": r["title"] or "Deleted meeting",
            "created": r["created"],
            "duration": r["duration"],
            "status": r["status"] or "deleted",
            "cost": pack(r),
        }
        for r in rows
    ]


@router.get("/admin/analytics/export")
def export_costs(days: Days = 30, user=Depends(admin_user), db: Session = Depends(get_db)):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "timestamp_utc",
            "user_name",
            "user_email",
            "meeting_id",
            "meeting_title",
            "operation",
            "model",
            "generation_id",
            "request_id",
            "input_tokens",
            "output_tokens",
            "audio_duration_seconds",
            "cost_usd",
            "cost_status",
            "cost_source",
            "voice_type",
            "input_characters",
            "generation_status",
            "purpose",
        ]
    )
    query = (
        select(Usage, User.name, User.email, Meeting.title)
        .outerjoin(User, User.id == Usage.user_id)
        .outerjoin(Meeting, Meeting.id == Usage.meeting_id)
        .where(Usage.excluded_as_duplicate.is_(False), *period_scope(days))
        .order_by(Usage.created)
    )

    def safe(value):
        value = str(value or "")
        return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

    for usage, name, email, title in db.execute(query).yield_per(500):
        writer.writerow(
            [
                datetime.fromtimestamp(usage.created, timezone.utc).isoformat(),
                safe(name),
                safe(email),
                safe(usage.meeting_id),
                safe(
                    "Voice preview"
                    if usage.purpose in ("voice_preview", "preset_preview")
                    else title or "Deleted meeting"
                ),
                safe(usage.operation),
                safe(usage.model),
                safe(usage.generation_id),
                safe(usage.request_id),
                usage.units.get("input_tokens", usage.units.get("prompt_tokens", "")),
                usage.units.get("output_tokens", usage.units.get("completion_tokens", "")),
                usage.units.get("seconds", ""),
                str(usage.cost) if usage.cost is not None else "",
                usage.cost_status,
                safe(usage.cost_source),
                safe(usage.voice_type),
                usage.input_characters if usage.input_characters is not None else "",
                safe(usage.generation_status),
                safe(usage.purpose),
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="sumx-costs-{days}-days.csv"'},
    )
