import os
import tempfile
from pathlib import Path
import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="sumx-tests-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (TEST_ROOT / "test.db").as_posix()
os.environ["STORAGE_DIR"] = str(TEST_ROOT / "media")
os.environ["SESSION_SECRET"] = "test-session-secret-with-at-least-32-characters"
os.environ["COOKIE_SECURE"] = "false"
os.environ["OPENROUTER_API_KEY"] = "test-not-a-real-key"
os.environ["GOOGLE_CLIENT_ID"] = "test-client.apps.googleusercontent.com"

from fastapi.testclient import TestClient
from backend.main import app
from backend.models import AllowedEmail, Base, DB, User, engine
from backend.security import passwords, start_session


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def user_client(client):
    from starlette.responses import Response

    with DB() as db:
        user = User(email="student@college.edu", name="Student")
        db.add(user)
        db.add(AllowedEmail(email=user.email))
        db.commit()
        response = Response()
        csrf = start_session(db, user, response)
        cookie = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]
        client.cookies.set("sumx_session", cookie)
        client.headers.update({"Origin": "http://localhost:5173", "X-CSRF-Token": csrf})
        client.user_id = user.id
    return client


@pytest.fixture
def admin_client(client):
    with DB() as db:
        db.add(
            User(
                email="admin@college.edu",
                name="Admin",
                role="admin",
                password_hash=passwords.hash("Test-password-12345"),
            )
        )
        db.commit()
    client.headers["Origin"] = "http://localhost:5173"
    result = client.post(
        "/api/auth/admin/login", json={"email": "admin@college.edu", "password": "Test-password-12345"}
    )
    assert result.status_code == 200
    client.headers["X-CSRF-Token"] = client.get("/api/auth/me").json()["csrf"]
    return client
