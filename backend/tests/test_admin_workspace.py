import csv
import io
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from backend.config import Settings
from backend.models import (
    AdminAudit,
    AllowedEmail,
    DB,
    LocalTTSGeneration,
    Meeting,
    RateBucket,
    Session,
    Usage,
    User,
)
from backend.tts_usage import record_tts_usage
from backend.security import digest, passwords
from backend.tests.test_google_popup import credential, login, signing  # noqa: F401


def seed_costs():
    with DB() as db:
        user = User(email="costs@example.edu", name="=测试, User")
        other = User(email="other@example.edu", name="Other")
        db.add_all([user, other])
        db.flush()
        db.add(AllowedEmail(id=user.id, email=user.email))
        meeting = Meeting(user_id=user.id, title="+Unicode, meeting", status="ready", duration=90)
        missing = Meeting(user_id=user.id, title="Historical unrecorded", status="ready")
        old = Meeting(user_id=user.id, title="Older meeting", created=time.time() - 100 * 86400)
        db.add_all([meeting, missing, old])
        db.flush()
        for op, cost in [("llm", ".100000000123"), ("stt", ".2"), ("tts", None)]:
            db.add(
                Usage(
                    user_id=user.id,
                    meeting_id=meeting.id,
                    operation=op,
                    model="real-model-" + op,
                    cost=cost,
                    cost_status="actual" if cost else "pending",
                    units={"input_tokens": 100},
                )
            )
        db.add_all(
            [
                Usage(
                    user_id=user.id,
                    meeting_id=meeting.id,
                    operation="llm",
                    model="duplicate",
                    cost="999",
                    excluded_as_duplicate=True,
                ),
                Usage(
                    user_id=user.id,
                    meeting_id=old.id,
                    operation="llm",
                    model="old",
                    cost="10",
                    cost_status="actual",
                    created=time.time() - 100 * 86400,
                ),
                Usage(
                    user_id=user.id,
                    operation="tts",
                    model="local",
                    provider="local_qwen3",
                    cost="0",
                    cost_status="actual",
                    purpose="voice_preview",
                ),
                Usage(user_id=other.id, operation="llm", model="other", cost="2", cost_status="actual"),
            ]
        )
        db.commit()
        return user.id, meeting.id


def test_admin_id_login_rotation_csrf_expiry_and_logout(user_client):
    client = user_client
    with DB() as db:
        admin = User(
            email="contact@example.edu",
            name="Admin",
            role="admin",
            admin_id="workspace-owner",
            password_hash=passwords.hash("Secure-test-password-123"),
        )
        db.add(admin)
        db.commit()
    old = client.cookies.get("sumx_session")
    client.cookies.clear()
    client.cookies.set("sumx_session", old, domain="testserver.local", path="/")
    assert (
        client.post("/api/admin/login", json={"admin_id": "workspace-owner", "password": "wrong"}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/admin/login",
            json={"admin_id": "workspace-owner", "password": "Secure-test-password-123"},
            headers={"Origin": "https://bad.example"},
        ).status_code
        == 403
    )
    result = client.post(
        "/api/admin/login", json={"admin_id": " WORKSPACE-OWNER ", "password": "Secure-test-password-123"}
    )
    assert result.status_code == 200
    assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=lax" in result.headers["set-cookie"]
    with DB() as db:
        assert db.get(Session, digest(old)) is None
    me = client.get("/api/admin/session").json()
    assert me["admin_id"] == "workspace-owner"
    assert client.post("/api/admin/approved-users", json={"email": "person@example.edu"}).status_code == 403
    client.headers["X-CSRF-Token"] = me["csrf"]
    assert client.post("/api/admin/approved-users", json={"email": "person@example.edu"}).status_code == 201
    token = client.cookies.get("sumx_session")
    assert client.post("/api/admin/logout").status_code == 200
    client.cookies.set("sumx_session", token, domain="testserver.local", path="/")
    assert client.get("/api/admin/session").status_code == 401
    assert (
        client.post(
            "/api/admin/login", json={"admin_id": "workspace-owner", "password": "Secure-test-password-123"}
        ).status_code
        == 200
    )
    with DB() as db:
        db.get(Session, digest(client.cookies.get("sumx_session"))).expires = time.time() - 1
        db.commit()
    assert client.get("/api/admin/overview").status_code == 401


def test_all_admin_endpoints_reject_regular_or_google_sessions(user_client):
    for path in [
        "session",
        "overview",
        "users",
        "users/export",
        "users/anything",
        "users/anything/meetings",
        "users/anything/usage",
        "users/anything/export",
        "approved-users",
    ]:
        assert user_client.get("/api/admin/" + path).status_code == 403, path
    assert user_client.post("/api/admin/approved-users", json={"email": "x@example.edu"}).status_code == 403
    assert user_client.delete("/api/admin/approved-users/anything").status_code == 403
    with DB() as db:
        # A role change alone must not elevate an existing Google session.
        db.get(User, user_client.user_id).role = "admin"
        db.commit()
    assert user_client.get("/api/admin/overview").status_code == 403


def test_unauthenticated_admin_api_and_password_rate_limit(client):
    assert client.get("/api/admin/overview").status_code == 401
    client.headers["Origin"] = "http://localhost:5173"
    for _ in range(8):
        assert (
            client.post("/api/admin/login", json={"admin_id": "missing", "password": "wrong"}).status_code
            == 401
        )
    with DB() as db:
        db.get(RateBucket, "login-id:" + digest("missing")).expires = time.time() + 10
        db.commit()
    result = client.post("/api/admin/login", json={"admin_id": "missing", "password": "wrong"})
    assert result.status_code == 429
    assert 1 <= int(result.headers["Retry-After"]) <= 10


def test_overview_directory_precise_costs_filters_and_unknowns(admin_client):
    user_id, meeting_id = seed_costs()
    data = admin_client.get("/api/admin/overview?days=7").json()
    assert data["registered_users"] == 3 and data["meetings"] == 2
    assert data["costs"]["total"]["known_usd"] == "2.300000000123"
    costs = data["costs"]
    assert Decimal(costs["total"]["known_usd"]) == sum(
        Decimal(costs[op]["known_usd"]) for op in ("llm", "stt", "tts")
    )
    result = admin_client.get(
        "/api/admin/users?q=costs%40example.edu&days=7&sort=total&direction=desc"
    ).json()
    assert result["total"] == 1
    p = result["users"][0]
    assert p["id"] == user_id and p["meetings"] == 2
    assert p["costs"]["total"]["known_usd"] == "0.300000000123"
    assert p["costs"]["total"]["status"] == "partial"
    assert p["costs"]["total"]["unrecorded_meetings"] == 1
    rows = admin_client.get(f"/api/admin/users/{user_id}/meetings?days=7").json()
    assert rows["total"] == 3  # two meetings plus previews
    missing = next(m for m in rows["meetings"] if m["title"] == "Historical unrecorded")
    assert missing["costs"]["total"]["known_usd"] is None
    usage = admin_client.get(f"/api/admin/users/{user_id}/usage?meeting_id={meeting_id}").json()
    assert usage["total"] == 3
    assert usage["usage"][0]["currency"] == "USD" and usage["usage"][0]["provider"] == "openrouter"
    assert admin_client.get("/api/admin/users?sort=invalid").status_code == 422
    assert admin_client.get("/api/admin/users?limit=101").status_code == 422
    assert admin_client.get("/api/admin/users?offset=1&limit=1").json()["users"]
    assert admin_client.get("/api/admin/users/not-a-user").status_code == 404


def test_authorized_csv_unicode_formulas_scope_and_totals(admin_client):
    user_id, meeting_id = seed_costs()
    result = admin_client.get("/api/admin/users/export?days=7&q=costs%40example.edu")
    assert (
        result.status_code == 200
        and 'filename="sumx-users-costs.csv"' in result.headers["content-disposition"]
    )
    rows = list(csv.DictReader(io.StringIO(result.text.lstrip("\ufeff"))))
    assert len(rows) == 1 and rows[0]["name"] == "'=测试, User"
    assert rows[0]["total_ai_cost"] == "0.300000000123"
    rows = list(
        csv.DictReader(
            io.StringIO(admin_client.get(f"/api/admin/users/{user_id}/export?days=7").text.lstrip("\ufeff"))
        )
    )
    meetings = [r for r in rows if r["row_type"] != "summary"]
    assert len(meetings) == 3 and rows[-1]["row_type"] == "summary"
    assert next(r for r in rows if r["meeting_id"] == meeting_id)["meeting_title"].startswith("'+")
    assert rows[-1]["total_ai_cost"] == str(sum(Decimal(r["total_ai_cost"] or 0) for r in meetings))
    assert next(r for r in rows if r["meeting_title"] == "Historical unrecorded")["total_ai_cost"] == ""


def test_approval_before_registration_duplicate_revoke_and_google(admin_client, signing):  # noqa: F811
    client = admin_client
    added = client.post("/api/admin/approved-users", json={"email": " Approved@College.edu "})
    assert added.status_code == 201, added.text
    approval_id = added.json()["id"]
    assert client.post("/api/admin/approved-users", json={"email": "approved@college.edu"}).status_code == 409
    assert client.post("/api/admin/approved-users", json={"email": "bad@@example.com"}).status_code == 422
    person = client.get(f"/api/admin/users/{approval_id}").json()
    assert person["name"] is None and person["status"] == "Approved — Not Yet Registered"
    assert person["costs"]["total"]["status"] == "no_activity"
    admin_cookie = client.cookies.get("sumx_session")
    admin_csrf = client.headers["X-CSRF-Token"]
    assert (
        login(client, credential(client, signing, picture="https://example.com/avatar.png")).status_code
        == 200
    )
    me = client.get("/api/auth/me").json()
    assert me["id"] == approval_id
    student_cookie = client.cookies.get("sumx_session")
    with DB() as db:
        assert db.get(User, approval_id).picture == "https://example.com/avatar.png"
        assert db.get(User, approval_id).last_login is not None
        db.add(Meeting(user_id=approval_id, title="Retain history"))
        db.commit()
        # Google identity switching rotates the old browser session, so make a fresh admin session.
        from starlette.responses import Response
        from backend.security import start_session

        admin = db.scalar(select(User).where(User.role == "admin"))
        response = Response()
        admin_csrf = start_session(db, admin, response)
        admin_cookie = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    client.cookies.clear()
    client.cookies.set("sumx_session", admin_cookie)
    client.headers["X-CSRF-Token"] = admin_csrf
    assert client.delete(f"/api/admin/approved-users/{approval_id}").status_code == 200
    with DB() as db:
        assert db.get(Session, digest(student_cookie)).revoked_at is not None
        assert db.scalar(select(func.count(Meeting.id)).where(Meeting.user_id == approval_id)) == 1
        assert db.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "revoke_user")) == 1
    assert login(client, credential(client, signing)).status_code == 403
    assert client.post("/api/admin/approved-users", json={"email": "approved@college.edu"}).status_code == 201


def test_local_usage_records_zero_api_fee_and_is_idempotent(user_client):
    with DB() as db:
        task = LocalTTSGeneration(
            provider="local_qwen3",
            request_key="local-test",
            user_id=user_client.user_id,
            voice_id="test-voice",
            model="Qwen/0.6B",
            purpose="voice_preview",
            text="",
            reference_text="",
            language="en",
            input_characters=25,
        )
        db.add(task)
        db.flush()
        record_tts_usage(db, task)
        db.commit()
        usage = db.scalar(select(Usage))
        assert usage.cost is None
        task.state = "completed"
        task.audio_seconds = 3
        record_tts_usage(db, task)
        record_tts_usage(db, task)
        db.commit()
        assert db.scalar(select(func.count(Usage.id))) == 1
        assert usage.cost == 0 and usage.provider == "local_qwen3"
        assert usage.cost_source == "local_no_external_api_fee"


def test_https_cookies_cannot_be_disabled():
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(app_url="https://sumx.example.edu", cookie_secure=False)


def test_future_provider_billing_is_namespaced_and_currency_is_explicit(user_client):
    import httpx
    from backend.jobs import account

    with DB() as db:
        for provider in ("openrouter", "future-provider", "future-provider"):
            account(
                db,
                None,
                "tts",
                "model",
                {"id": "shared-generation-id", "usage": {"cost": Decimal("0.000123")}},
                httpx.Response(200),
                user_id=user_client.user_id,
                provider=provider,
            )
        assert db.scalar(select(func.count(Usage.id))) == 2
        with pytest.raises(ValueError, match="USD"):
            account(
                db, None, "tts", "model", {}, httpx.Response(200), user_id=user_client.user_id, currency="EUR"
            )


def test_admin_migration_preserves_references_and_legacy_login(tmp_path):
    url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    env = {**os.environ, "DATABASE_URL": url}
    root = Path(__file__).resolve().parents[2]

    def migrate(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    migrate("0006")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users (id,email,name,role,created) VALUES ('owner','owner@example.edu','Owner','admin',1), ('student','student@example.edu','Student','user',1)"
            )
        )
        db.execute(
            text(
                "INSERT INTO allowed_emails (email,active) VALUES ('student@example.edu',1), ('waiting@example.edu',0)"
            )
        )
        db.execute(
            text(
                "INSERT INTO sessions (token_hash,user_id,csrf,expires) VALUES ('old-session','owner','csrf',9999999999)"
            )
        )
        db.execute(
            text(
                "INSERT INTO meetings (id,user_id,title,created,duration,status,retain_source,detail,transcript_version,summary_version) VALUES ('meeting','student','Keep me',1,3,'ready',1,'standard',1,1)"
            )
        )
    migrate("head")
    with engine.connect() as db:
        assert db.execute(text("SELECT admin_id FROM users WHERE id='owner'")).scalar() == "owner@example.edu"
        assert db.execute(text("SELECT auth_method FROM sessions")).scalar() == "admin_password"
        assert (
            db.execute(text("SELECT id FROM allowed_emails WHERE email='student@example.edu'")).scalar()
            == "student"
        )
        assert db.execute(text("SELECT title FROM meetings")).scalar() == "Keep me"
        assert (
            db.execute(text("SELECT active FROM allowed_emails WHERE email='waiting@example.edu'")).scalar()
            == 0
        )
    engine.dispose()
