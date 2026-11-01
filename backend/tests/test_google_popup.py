import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt
from google.oauth2 import id_token
from sqlalchemy import create_engine, text
from backend.config import settings
from backend.models import AllowedEmail, DB, Meeting, User


@pytest.fixture
def signing(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(id_token, "_fetch_certs", lambda *a, **kw: {"test-key": public.decode()})
    return crypt.RSASigner.from_string(private, key_id="test-key")


def credential(client, signer, **changes):
    nonce = client.get("/api/auth/google/config").json()["nonce"]
    claims = {
        "iss": "https://accounts.google.com",
        "aud": settings.google_client_id,
        "iat": int(time.time()) - 1,
        "exp": int(time.time()) + 300,
        "email": "approved@college.edu",
        "email_verified": True,
        "sub": "google-subject-123",
        "nonce": nonce,
        "name": "Approved Student",
        **changes,
    }
    return jwt.encode(signer, claims).decode()


def login(client, token):
    return client.post("/api/auth/google", json={"credential": token}, headers={"Origin": settings.app_url})


def test_popup_login_preserves_user_and_meetings_and_logout(client, signing):
    with DB() as db:
        user = User(email="approved@college.edu", name="Existing user")
        db.add(user)
        db.add(AllowedEmail(email=user.email))
        db.flush()
        user_id = user.id
        db.add(Meeting(user_id=user.id, title="Existing meeting"))
        db.commit()
    result = login(client, credential(client, signing))
    assert result.status_code == 200, result.text
    assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=lax" in result.headers["set-cookie"]
    me = client.get("/api/auth/me").json()
    assert me["id"] == user_id
    assert client.get("/api/meetings").json()["stats"]["meetings"] == 1
    with DB() as db:
        assert db.get(User, user_id).google_sub == "google-subject-123"
    old = client.cookies.get("sumx_session")
    response = client.post(
        "/api/auth/logout", headers={"Origin": settings.app_url, "X-CSRF-Token": me["csrf"]}
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("sumx_session", old)
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize(
    "claims,status",
    [
        ({"aud": "wrong-client"}, 401),
        ({"iss": "https://attacker.example"}, 401),
        ({"exp": 1}, 401),
        ({"iat": int(time.time()) + 600}, 401),
        ({"nonce": "wrong-nonce"}, 401),
        ({"email_verified": False}, 403),
        ({"email_verified": "true"}, 403),
        ({"sub": ""}, 401),
    ],
)
def test_invalid_signed_tokens_rejected(client, signing, claims, status):
    assert login(client, credential(client, signing, **claims)).status_code == status
    assert client.get("/api/auth/me").status_code == 401


def test_unapproved_inactive_and_admin_rejected(client, signing):
    assert login(client, credential(client, signing)).status_code == 403
    with DB() as db:
        db.add(AllowedEmail(email="approved@college.edu", active=False))
        db.commit()
    assert login(client, credential(client, signing)).status_code == 403
    with DB() as db:
        db.get(AllowedEmail, "approved@college.edu").active = True
        db.add(User(email="approved@college.edu", name="Admin", role="admin"))
        db.commit()
    assert "separate admin login" in login(client, credential(client, signing)).json()["detail"]


def test_signature_origin_nonce_and_old_flow_rejected(client, signing):
    token = credential(client, signing)
    parts = token.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    assert login(client, ".".join(parts)).status_code == 401
    token = credential(client, signing)
    assert (
        client.post(
            "/api/auth/google", json={"credential": token}, headers={"Origin": "https://attacker.example"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert login(client, token).status_code == 403
    assert (
        client.post(
            "/api/auth/google", json={"email": "approved@college.edu"}, headers={"Origin": settings.app_url}
        ).status_code
        == 422
    )
    assert client.get("/api/auth/google").status_code == 405
    assert client.get("/api/auth/google/callback?code=forged&state=forged").status_code == 404


def test_switch_identity_rotates_session_and_cannot_rebind_subject(client, signing):
    with DB() as db:
        db.add_all([AllowedEmail(email="approved@college.edu"), AllowedEmail(email="another@college.edu")])
        db.commit()
    assert login(client, credential(client, signing)).status_code == 200
    first = client.get("/api/auth/me").json()
    old = client.cookies.get("sumx_session")
    assert (
        login(client, credential(client, signing, email="another@college.edu", sub="subject-2")).status_code
        == 200
    )
    second = client.get("/api/auth/me").json()
    assert first["id"] != second["id"]
    assert (
        login(client, credential(client, signing, email="approved@college.edu", sub="subject-3")).status_code
        == 403
    )
    client.cookies.clear()
    client.cookies.set("sumx_session", old)
    assert client.get("/api/auth/me").status_code == 401


def test_additive_migration_preserves_existing_records(tmp_path):
    url = "sqlite:///" + (tmp_path / "old.db").as_posix()
    env = {**os.environ, "DATABASE_URL": url}
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0001"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users (id,email,name,role,created) VALUES ('existing','approved@college.edu','Existing','user',1)"
            )
        )
        db.execute(text("INSERT INTO allowed_emails (email,active) VALUES ('approved@college.edu',1)"))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    )
    with engine.connect() as db:
        assert db.execute(text("SELECT id,email,google_sub FROM users")).one() == (
            "existing",
            "approved@college.edu",
            None,
        )
        assert db.execute(text("SELECT active FROM allowed_emails")).scalar() == 1
    engine.dispose()


def test_revoked_user_can_still_end_their_own_session(client, signing):
    with DB() as db:
        db.add(AllowedEmail(email="approved@college.edu"))
        db.commit()
    assert login(client, credential(client, signing)).status_code == 200
    csrf = client.get("/api/auth/me").json()["csrf"]
    with DB() as db:
        db.get(AllowedEmail, "approved@college.edu").active = False
        db.commit()
    assert client.get("/api/meetings").status_code == 403
    assert (
        client.post(
            "/api/auth/logout", headers={"Origin": settings.app_url, "X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me").status_code == 401
