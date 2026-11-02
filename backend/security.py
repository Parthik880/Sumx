import hashlib
import secrets
import time
import math
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from .config import settings
from .models import AllowedEmail, RateBucket, Session, User, get_db

passwords = PasswordHasher()


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def origin_check(request: Request):
    if request.headers.get("origin") != settings.app_url:
        raise HTTPException(403, "Request origin is not permitted")


def rate_limit(db: DBSession, key: str, limit: int, seconds: int):
    now = time.time()
    # Persist counters across workers and restarts; increment in the database.
    db.execute(
        update(RateBucket)
        .where(RateBucket.key == key, RateBucket.expires < now)
        .values(count=0, expires=now + seconds)
    )
    result = db.execute(
        update(RateBucket)
        .where(RateBucket.key == key, RateBucket.count < limit)
        .values(count=RateBucket.count + 1)
    )
    if not result.rowcount:
        bucket = db.get(RateBucket, key)
        if bucket:
            retry_after = max(1, math.ceil(bucket.expires - now))
            db.commit()
            raise HTTPException(
                429, "Too many requests. Try again later.", headers={"Retry-After": str(retry_after)}
            )
        try:
            with db.begin_nested():
                db.add(RateBucket(key=key, count=1, expires=now + seconds))
                db.flush()
        except IntegrityError:
            db.rollback()
            return rate_limit(db, key, limit, seconds)
    db.commit()


def session_record(request: Request, db: DBSession, *, allow_revoked=False):
    token = request.cookies.get("sumx_session", "")
    session = db.get(Session, digest(token)) if token else None
    if not session or session.expires < time.time():
        raise HTTPException(401, "Sign in to continue", headers={"X-SUMX-Auth-Error": "session_required"})
    if session.revoked_at is not None and not allow_revoked:
        raise HTTPException(
            403,
            "You are not approved. Contact your administrator for access.",
            headers={"X-SUMX-Auth-Error": "access_revoked"},
        )
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin_check(request)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "Invalid security token. Reload and try again.")
    request.state.session = session
    return session


def current_session(request: Request, db: DBSession = Depends(get_db)):
    return session_record(request, db)


def logout_session(request: Request, db: DBSession = Depends(get_db)):
    return session_record(request, db, allow_revoked=True)


def current_user(request: Request, db: DBSession = Depends(get_db)):
    session = current_session(request, db)
    user = db.get(User, session.user_id)
    allowed = db.get(AllowedEmail, user.email) if user else None
    if not user or (user.role != "admin" and (not allowed or not allowed.active)):
        raise HTTPException(
            403,
            "You are not approved. Contact your administrator for access.",
            headers={"X-SUMX-Auth-Error": "access_revoked"},
        )
    return user


def admin_user(request: Request, user: User = Depends(current_user)):
    if user.role != "admin" or request.state.session.auth_method != "admin_password":
        raise HTTPException(403, "Administrator access required")
    return user


def start_session(db: DBSession, user: User, response, auth_method=None):
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)
    db.add(
        Session(
            token_hash=digest(token),
            user_id=user.id,
            csrf=csrf,
            expires=time.time() + settings.session_hours * 3600,
            auth_method=auth_method or ("admin_password" if user.role == "admin" else "google"),
        )
    )
    db.commit()
    response.set_cookie(
        "sumx_session",
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return csrf


def owns(db: DBSession, model, meeting_id: str, user: User):
    meeting = db.scalar(select(model).where(model.id == meeting_id, model.user_id == user.id))
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    return meeting
