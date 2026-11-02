import os
import tempfile
from pathlib import Path
import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="sumx-tests-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (TEST_ROOT / "test.db").as_posix()
os.environ["STORAGE_DIR"] = str(TEST_ROOT / "media")
os.environ["SESSION_SECRET"] = "test-session-secret-with-at-least-32-characters"
os.environ["COOKIE_SECURE"] = "false"
os.environ["MAX_UPLOAD_MB"] = "0"
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


@pytest.fixture(autouse=True)
def speech_fixtures(monkeypatch):
    """Keep normal unit tests offline; focused verifier/runtime tests exercise real guards."""
    import time
    from backend import reference_quality, tts_queue, tts_worker
    from backend.models import LocalTTSWorker, LocalTTSGeneration
    from backend.tests.qwen_mock import MockQwenEngine
    from pathlib import Path

    def verified(db, voice):
        voice.reference_verified_at = time.time()
        db.commit()
        return {}

    monkeypatch.setattr(reference_quality, "verify_reference", verified)

    def generate(voice, text, key, meeting_id=None, timeout=1800, progress=None, raw_wav=False, **options):
        with DB() as db:
            if not db.get(LocalTTSWorker, "qwen3"):
                from backend.config import settings

                db.add(LocalTTSWorker(id="qwen3", state="ready", model=settings.qwen_tts_model))
                db.commit()
            tid = tts_queue.submit(db, voice, text, key, meeting_id, **options).id
        tts_worker.process(tid, MockQwenEngine())
        with DB() as db:
            row = db.get(LocalTTSGeneration, tid)
            if progress:
                progress(row)
            if row.state != "completed":
                raise ValueError(row.error)
            output = Path(row.output_path)
            return (output.with_suffix(".wav") if raw_wav else output).read_bytes()

    monkeypatch.setattr(tts_queue, "generate", generate)
    MockQwenEngine.generated.clear()
    MockQwenEngine.fail_once.clear()

    def generate_narration(voice, chunks, job_id, meeting_id, folder, *, progress=None, **options):
        with DB() as db:
            if not db.get(LocalTTSWorker, "qwen3"):
                from backend.config import settings

                db.add(LocalTTSWorker(id="qwen3", state="ready", model=settings.qwen_tts_model))
                db.commit()
            tid = tts_queue.submit_narration(db, voice, chunks, job_id, meeting_id, folder, **options).id
        tts_worker.process_narration(tid, MockQwenEngine())
        with DB() as db:
            row = db.get(LocalTTSGeneration, tid)
            if progress:
                progress(row)
            if row.state != "completed":
                raise ValueError(row.error)
            return Path(row.output_path)

    monkeypatch.setattr(tts_queue, "generate_narration", generate_narration)


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
