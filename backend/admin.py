"""Password-only administration, SQL aggregates and authorized CSV downloads."""

import csv
import io
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, BeforeValidator, Field
from sqlalchemy import Numeric, and_, case, cast, func, literal, or_, select, union, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from .analytics import total_expr
from .config import settings
from .models import AdminAudit, AllowedEmail, DB, Meeting, Session, Usage, User, get_db
from .security import admin_user, digest, origin_check, passwords, rate_limit, start_session

router = APIRouter(prefix="/api/admin", tags=["administration"])
Period = Annotated[Literal[0, 7, 30, 90], BeforeValidator(int)]
Sort = Literal["name", "email", "meetings", "llm", "stt", "tts", "total"]
OPS = ("llm", "stt", "tts")


def audit(db, actor, action, target=None):
    db.add(AdminAudit(actor_id=actor.id if actor else None, action=action, target_id=target))


class AdminLogin(BaseModel):
    admin_id: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(body: AdminLogin, request: Request, db: DBSession = Depends(get_db)):
    origin_check(request)
    identity = body.admin_id.strip().lower()
    rate_limit(db, "login-ip:" + request.client.host, 10, 900)
    rate_limit(db, "login-id:" + digest(identity), 8, 900)
    user = db.scalar(
        select(User).where(
            User.role == "admin",
            or_(User.admin_id == identity, and_(User.admin_id.is_(None), User.email == identity)),
        )
    )
    dummy = (
        "$argon2id$v=19$m=65536,t=3,p=4$c3VteC1ub25leGlzdGVudA$7yCx3zrI4Bg6zOqS1FRZ7wMzmiGZW2Vc8jPqwENr8YU"
    )
    try:
        passwords.verify(user.password_hash if user and user.password_hash else dummy, body.password)
        if not user or not user.password_hash:
            raise VerificationError()
    except VerificationError:
        audit(db, None, "login_failed")
        db.commit()
        raise HTTPException(401, "Admin ID or password is incorrect")
    if passwords.check_needs_rehash(user.password_hash):
        user.password_hash = passwords.hash(body.password)
    user.last_login = time.time()
    old = db.get(Session, digest(request.cookies.get("sumx_session", "")))
    if old:
        db.delete(old)
    audit(db, user, "login", user.id)
    response = JSONResponse({"ok": True})
    start_session(db, user, response, "admin_password")
    request.session.clear()
    return response


@router.get("/session")
def session_info(request: Request, user=Depends(admin_user)):
    return {
        "id": user.id,
        "admin_id": user.admin_id or user.email,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "csrf": request.state.session.csrf,
        "expires": request.state.session.expires,
    }


@router.post("/logout")
def logout(request: Request, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    db.delete(request.state.session)
    audit(db, user, "logout", user.id)
    db.commit()
    request.session.clear()
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        "sumx_session", path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )
    return response


def date_scope(column, days):
    return [column >= (int(time.time() // 86400) - days + 1) * 86400, column <= time.time()] if days else []


def identities():
    """An approval can exist before registration; never invent a Google profile."""
    registered = select(
        User.id,
        User.name,
        User.email,
        User.picture,
        User.created,
        User.last_login,
        literal(True).label("registered"),
        User.role,
        AllowedEmail.active,
        AllowedEmail.approved_at,
        AllowedEmail.approved_by,
        AllowedEmail.revoked_at,
    ).outerjoin(AllowedEmail, AllowedEmail.email == User.email)
    waiting = select(
        AllowedEmail.id,
        literal(None),
        AllowedEmail.email,
        literal(None),
        literal(None),
        literal(None),
        literal(False),
        literal("user"),
        AllowedEmail.active,
        AllowedEmail.approved_at,
        AllowedEmail.approved_by,
        AllowedEmail.revoked_at,
    ).where(~select(User.id).where(User.email == AllowedEmail.email).exists())
    return union_all(registered, waiting).subquery()


def scoped_meeting_ids(days, user_id=None):
    m = select(Meeting.id.label("id"), Meeting.user_id.label("user_id")).where(
        *date_scope(Meeting.created, days)
    )
    u = select(Usage.meeting_id, Usage.user_id).where(
        Usage.meeting_id.is_not(None),
        Usage.excluded_as_duplicate.is_(False),
        *date_scope(Usage.created, days),
    )
    if user_id:
        m, u = m.where(Meeting.user_id == user_id), u.where(Usage.user_id == user_id)
    return union(m, u).subquery()


def money_columns():
    cols = [
        total_expr().label("total"),
        func.count(Usage.id).label("requests"),
        func.sum(case((Usage.cost.is_(None), 1), else_=0)).label("pending"),
        func.sum(case((Usage.cost_status == "estimated", 1), else_=0)).label("estimated"),
    ]
    for op in OPS:
        cols.extend(
            [
                total_expr(Usage.operation == op).label(op),
                func.sum(case((Usage.operation == op, 1), else_=0)).label(op + "_requests"),
                func.sum(case((and_(Usage.operation == op, Usage.cost.is_(None)), 1), else_=0)).label(
                    op + "_pending"
                ),
                func.sum(
                    case((and_(Usage.operation == op, Usage.cost_status == "estimated"), 1), else_=0)
                ).label(op + "_estimated"),
            ]
        )
    return cols


def ledger_query(days, user_id=None):
    query = select(*money_columns()).where(
        Usage.excluded_as_duplicate.is_(False),
        Usage.currency == "USD",
        Usage.operation.in_(OPS),
        *date_scope(Usage.created, days),
    )
    return query.where(Usage.user_id == user_id) if user_id else query


def amounts(row, missing=0):
    def one(op):
        key = "" if op == "total" else op + "_"
        count, pending, estimated = (int(row.get(key + n) or 0) for n in ("requests", "pending", "estimated"))
        known = count > pending
        status = (
            ("partial" if pending or missing else "estimated" if estimated else "actual")
            if known
            else ("unavailable" if count or missing else "no_activity")
        )
        return {
            "known_usd": str(Decimal(str(row.get(op) or 0))) if known else None,
            "status": status,
            "requests": count,
            "pending_requests": pending,
            "estimated_requests": estimated,
            "unrecorded_meetings": missing,
            "currency": "USD",
        }

    return {op: one(op) for op in ("total", *OPS)}


def account_status(row):
    if row.get("role") == "admin":
        return "Administrator"
    if row.get("active"):
        return "Approved" if row.get("registered") else "Approved — Not Yet Registered"
    return "Revoked" if row.get("active") is False else "Pending"


def directory_query(days=0, q="", sort="name", direction="asc"):
    people = identities()
    ids = scoped_meeting_ids(days)
    has_usage = (
        select(Usage.id)
        .where(
            Usage.meeting_id == Meeting.id,
            Usage.user_id == Meeting.user_id,
            Usage.excluded_as_duplicate.is_(False),
            *date_scope(Usage.created, days),
        )
        .exists()
    )
    counts = (
        select(
            Meeting.user_id,
            func.count(Meeting.id).label("meetings"),
            func.sum(case((~has_usage, 1), else_=0)).label("missing"),
        )
        .where(Meeting.id.in_(select(ids.c.id)))
        .group_by(Meeting.user_id)
        .subquery()
    )
    costs = ledger_query(days).add_columns(Usage.user_id).group_by(Usage.user_id).subquery()
    query = (
        select(
            *people.c,
            func.coalesce(counts.c.meetings, 0).label("meetings"),
            func.coalesce(counts.c.missing, 0).label("missing"),
            *[costs.c[c.name] for c in money_columns()],
        )
        .select_from(people)
        .outerjoin(counts, counts.c.user_id == people.c.id)
        .outerjoin(costs, costs.c.user_id == people.c.id)
    )
    if q:
        query = query.where(
            or_(
                func.lower(people.c.name).contains(q.lower(), autoescape=True),
                func.lower(people.c.email).contains(q.lower(), autoescape=True),
            )
        )
    order = (
        counts.c.meetings
        if sort == "meetings"
        else cast(costs.c[sort], Numeric(24, 12))
        if sort in (*OPS, "total")
        else func.lower(people.c[sort])
    )
    return query.order_by(
        order.desc().nulls_last() if direction == "desc" else order.asc().nulls_last(), people.c.id
    )


def directory_row(row):
    return {
        k: row[k]
        for k in (
            "id",
            "name",
            "email",
            "picture",
            "created",
            "last_login",
            "registered",
            "approved_at",
            "approved_by",
            "revoked_at",
            "meetings",
        )
    } | {"status": account_status(row), "costs": amounts(row, row["missing"])}


@router.get("/overview")
def overview(days: Period = 0, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    rows = directory_query(days).subquery()
    count = (
        db.execute(
            select(
                func.count().label("accounts"),
                func.sum(case((rows.c.registered.is_(True), 1), else_=0)).label("registered"),
                func.sum(rows.c.meetings).label("meetings"),
                func.sum(rows.c.missing).label("missing"),
            )
        )
        .mappings()
        .one()
    )
    costs = db.execute(ledger_query(days)).mappings().one()
    return {
        "accounts": count["accounts"],
        "registered_users": count["registered"] or 0,
        "meetings": count["meetings"] or 0,
        "costs": amounts(costs, count["missing"] or 0),
        "currency": "USD",
        "days": days,
    }


@router.get("/users")
def users(
    days: Period = 0,
    q: str = Query("", max_length=200),
    sort: Sort = "name",
    direction: Literal["asc", "desc"] = "asc",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    user=Depends(admin_user),
    db: DBSession = Depends(get_db),
):
    query = directory_query(days, q, sort, direction)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "users": [directory_row(r) for r in db.execute(query.offset(offset).limit(limit)).mappings()],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def profile(db, user_id, days):
    query = directory_query(days).subquery()
    row = db.execute(select(query).where(query.c.id == user_id)).mappings().first()
    if not row:
        raise HTTPException(404, "User not found")
    return directory_row(row)


def csv_safe(value):
    text = "" if value is None else str(value)
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n"))
        else text
    )


def csv_line(values):
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerow([csv_safe(v) for v in values])
    return buffer.getvalue()


def csv_response(rows, filename):
    def stream():
        yield "\ufeff"  # Excel and other spreadsheet tools can identify UTF-8.
        for row in rows:
            yield csv_line(row)

    return StreamingResponse(
        stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def cost_cells(costs):
    return [costs[op]["known_usd"] for op in (*OPS, "total")] + [
        "USD",
        costs["total"]["status"],
        costs["total"]["pending_requests"],
        costs["total"]["unrecorded_meetings"],
    ]


@router.get("/users/export")
def export_users(
    days: Period = 0,
    q: str = Query("", max_length=200),
    sort: Sort = "name",
    direction: Literal["asc", "desc"] = "asc",
    user=Depends(admin_user),
    db: DBSession = Depends(get_db),
):
    audit(db, user, "export_users")
    db.commit()

    def rows():
        yield [
            "user_id",
            "name",
            "email",
            "approval_status",
            "total_meetings",
            "llm_cost",
            "stt_cost",
            "tts_cost",
            "total_ai_cost",
            "currency",
            "cost_status",
            "pending_requests",
            "unrecorded_meetings",
        ]
        with DB() as connection:
            for row in (
                connection.execute(directory_query(days, q, sort, direction)).mappings().yield_per(250)
            ):
                p = directory_row(row)
                yield [p["id"], p["name"], p["email"], p["status"], p["meetings"], *cost_cells(p["costs"])]

    return csv_response(rows(), "sumx-users-costs.csv")


@router.get("/users/{user_id}")
def user_detail(user_id: str, days: Period = 0, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    result = profile(db, user_id, days)
    audit(db, user, "view_user", user_id)
    db.commit()
    return result


def meetings_query(user_id, days):
    ids = scoped_meeting_ids(days, user_id)
    # Include unlinked voice previews and retained usage for deleted meetings so totals reconcile.
    all_ids = union(
        select(ids.c.id),
        select(Usage.meeting_id).where(
            Usage.user_id == user_id, Usage.excluded_as_duplicate.is_(False), *date_scope(Usage.created, days)
        ),
    ).subquery()
    costs = ledger_query(days, user_id).add_columns(Usage.meeting_id).group_by(Usage.meeting_id).subquery()
    return (
        select(
            all_ids.c.id,
            Meeting.title,
            Meeting.created,
            Meeting.duration,
            Meeting.status,
            *[costs.c[c.name] for c in money_columns()],
        )
        .select_from(all_ids)
        .outerjoin(Meeting, and_(Meeting.id == all_ids.c.id, Meeting.user_id == user_id))
        .outerjoin(
            costs,
            or_(
                costs.c.meeting_id == all_ids.c.id, and_(costs.c.meeting_id.is_(None), all_ids.c.id.is_(None))
            ),
        )
        .order_by(Meeting.created.desc(), all_ids.c.id)
    )


def meeting_row(row):
    missing = int(bool(row["title"]) and not row["requests"])
    return {
        "id": row["id"],
        "title": row["title"] or ("Deleted meeting" if row["id"] else "Voice previews / unlinked usage"),
        "created": row["created"],
        "duration": row["duration"],
        "status": row["status"] or "retained usage",
        "costs": amounts(row, missing),
    }


@router.get("/users/{user_id}/meetings")
def user_meetings(
    user_id: str,
    days: Period = 0,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    user=Depends(admin_user),
    db: DBSession = Depends(get_db),
):
    profile(db, user_id, days)
    query = meetings_query(user_id, days)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "meetings": [meeting_row(r) for r in db.execute(query.offset(offset).limit(limit)).mappings()],
        "total": total,
    }


@router.get("/users/{user_id}/usage")
def user_usage(
    user_id: str,
    meeting_id: str | None = None,
    days: Period = 0,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user=Depends(admin_user),
    db: DBSession = Depends(get_db),
):
    profile(db, user_id, days)
    query = select(Usage).where(
        Usage.user_id == user_id, Usage.excluded_as_duplicate.is_(False), *date_scope(Usage.created, days)
    )
    if meeting_id:
        query = query.where(Usage.meeting_id == (None if meeting_id == "unlinked" else meeting_id))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Usage.created.desc(), Usage.id).offset(offset).limit(limit))
    return {
        "total": total,
        "usage": [
            {
                k: getattr(u, k)
                for k in (
                    "id",
                    "meeting_id",
                    "operation",
                    "provider",
                    "model",
                    "generation_id",
                    "request_id",
                    "created",
                    "units",
                    "currency",
                    "cost_status",
                    "cost_source",
                    "generation_status",
                    "purpose",
                )
            }
            | {"cost": str(u.cost) if u.cost is not None else None}
            for u in rows
        ],
    }


@router.get("/users/{user_id}/export")
def export_user(user_id: str, days: Period = 0, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    person = profile(db, user_id, days)
    audit(db, user, "export_user", user_id)
    db.commit()

    def rows():
        yield [
            "user_id",
            "user_name",
            "email",
            "meeting_id",
            "meeting_title",
            "meeting_date_utc",
            "duration_seconds",
            "meeting_status",
            "llm_cost",
            "stt_cost",
            "tts_cost",
            "total_ai_cost",
            "currency",
            "cost_status",
            "pending_requests",
            "unrecorded_meetings",
            "row_type",
        ]
        with DB() as connection:
            for row in connection.execute(meetings_query(user_id, days)).mappings().yield_per(250):
                m = meeting_row(row)
                timestamp = (
                    datetime.fromtimestamp(m["created"], timezone.utc).isoformat() if m["created"] else ""
                )
                yield [
                    user_id,
                    person["name"],
                    person["email"],
                    m["id"],
                    m["title"],
                    timestamp,
                    m["duration"],
                    m["status"],
                    *cost_cells(m["costs"]),
                    "meeting" if row["title"] else "standalone_usage",
                ]
            fresh = profile(connection, user_id, days)
            yield [
                user_id,
                fresh["name"],
                fresh["email"],
                "",
                "Cumulative totals",
                "",
                "",
                "",
                *cost_cells(fresh["costs"]),
                "summary",
            ]

    return csv_response(rows(), f"sumx-user-meetings-{user_id}.csv")


class Approval(BaseModel):
    email: str = Field(min_length=3, max_length=320)


def normalized_email(value):
    try:
        local, domain = value.strip().rsplit("@", 1)
        domain = domain.encode("idna").decode("ascii").lower()
        labels = domain.split(".")
        if (
            not 1 <= len(local) <= 64
            or local.startswith(".")
            or local.endswith(".")
            or ".." in local
            or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
            or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or len(local + "@" + domain) > 320
        ):
            raise ValueError()
        return local.lower() + "@" + domain
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Enter a valid email address")


def approve(db, actor, email, active=True, reject_duplicate=False):
    email = normalized_email(email)
    row = db.get(AllowedEmail, email)
    if row and row.active and active and reject_duplicate:
        raise HTTPException(409, "This email is already approved")
    if not row:
        existing = db.scalar(select(User).where(User.email == email))
        row = AllowedEmail(email=email, **({"id": existing.id} if existing else {}))
        db.add(row)
    row.active = active
    if active:
        row.approved_at, row.approved_by, row.revoked_at = time.time(), actor.id, None
    else:
        row.revoked_at = time.time()
        db.execute(
            update(Session)
            .where(Session.user_id.in_(select(User.id).where(User.email == email, User.role != "admin")))
            .values(revoked_at=row.revoked_at)
        )
    audit(db, actor, "approve_user" if active else "revoke_user", email)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This approval changed concurrently. Reload and try again.")
    return {
        "id": row.id,
        "email": row.email,
        "active": row.active,
        "approved_at": row.approved_at,
        "approved_by": row.approved_by,
        "revoked_at": row.revoked_at,
    }


@router.post("/approved-users", status_code=201)
def add_approval(body: Approval, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    return approve(db, user, body.email, reject_duplicate=True)


@router.get("/approved-users")
def approvals(
    q: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    user=Depends(admin_user),
    db: DBSession = Depends(get_db),
):
    query = select(AllowedEmail, User.google_sub).outerjoin(User, User.email == AllowedEmail.email)
    if q:
        query = query.where(func.lower(AllowedEmail.email).contains(q.lower(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "total": total,
        "users": [
            {
                "id": a.id,
                "email": a.email,
                "active": a.active,
                "approved_at": a.approved_at,
                "approved_by": a.approved_by,
                "revoked_at": a.revoked_at,
                "registered": bool(subject),
            }
            for a, subject in db.execute(query.order_by(AllowedEmail.email).offset(offset).limit(limit))
        ],
    }


@router.delete("/approved-users/{approval_id}")
def revoke(approval_id: str, user=Depends(admin_user), db: DBSession = Depends(get_db)):
    row = db.scalar(select(AllowedEmail).where(AllowedEmail.id == approval_id))
    if not row:
        raise HTTPException(404, "Approval not found")
    return approve(db, user, row.email, active=False)
