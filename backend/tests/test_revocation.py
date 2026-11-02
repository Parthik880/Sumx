import time
from sqlalchemy import select
from backend.admin import approve
from backend.models import DB, Session, User
from backend.security import digest


def test_revoked_session_is_identified_on_all_protected_requests_and_can_logout(user_client):
    client = user_client
    cookie = client.cookies.get("sumx_session")
    with DB() as db:
        admin = User(email="revoke-admin@example.edu", name="Admin", role="admin")
        db.add(admin)
        db.flush()
        approve(db, admin, "student@college.edu", active=False)
        assert db.get(Session, digest(cookie)).revoked_at <= time.time()
        # Restoring approval requires a new sign-in; old revoked cookies stay invalid.
        approve(db, admin, "student@college.edu", active=True)
    for path in ("/api/auth/me", "/api/config", "/api/meetings", "/api/dashboard", "/api/voices"):
        result = client.get(path)
        assert result.status_code == 403, path
        assert result.headers["X-SUMX-Auth-Error"] == "access_revoked"
    assert client.post("/api/meetings", json={"title": "Blocked"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.set("sumx_session", cookie)
    assert client.get("/api/auth/me").status_code == 401


def test_revocation_does_not_invalidate_password_admin_session(admin_client):
    with DB() as db:
        admin = db.scalar(select(User).where(User.role == "admin"))
        approve(db, admin, admin.email, active=False)
    assert admin_client.get("/api/admin/session").status_code == 200


def test_session_expiry_is_distinct_from_revocation(user_client):
    cookie = user_client.cookies.get("sumx_session")
    with DB() as db:
        db.get(Session, digest(cookie)).expires = time.time() - 1
        db.commit()
    response = user_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["X-SUMX-Auth-Error"] == "session_required"
