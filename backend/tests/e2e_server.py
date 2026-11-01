"""Isolated browser test server. Never imported by the production application."""

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sumx-browser-tests-"))
os.environ.update(
    DATABASE_URL="sqlite:///" + (root / "test.db").as_posix(),
    STORAGE_DIR=str(root / "media"),
    SESSION_SECRET="browser-test-secret-with-at-least-32-characters",
    COOKIE_SECURE="false",
    APP_URL="http://localhost:5174",
    OPENROUTER_API_KEY="test-only-not-a-real-key",
    GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",
)

import httpx
import uvicorn
from backend import jobs, main, voices, qwen_worker
from backend.tests.qwen_mock import MockQwenEngine
from backend.models import AllowedEmail, Base, DB, Session, User, Meeting, Summary, Usage, engine
from backend.provider import OpenRouter
from backend.tests.test_sumx import MINUTES


def handler(request):
    if request.url.path.endswith("transcriptions"):
        return httpx.Response(
            200,
            json={
                "text": "We discussed library opening hours. Review the schedule.",
                "language": "en",
                "segments": [{"text": "We discussed library opening hours.", "start": 0, "end": 1}],
                "usage": {"cost": 0.0001},
            },
        )
    if request.url.path.endswith("completions"):
        return httpx.Response(
            200,
            json={
                "id": "browser-test-generation",
                "choices": [{"message": {"content": json.dumps(MINUTES)}}],
                "usage": {"cost": 0.0001},
            },
        )
    if request.url.path.endswith("models"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": os.environ.get("OPENROUTER_TTS_MODEL", "mistralai/voxtral-mini-tts-2603"),
                        "supported_voices": ["en_paul_neutral"],
                    }
                ]
            },
        )
    if request.url.path.endswith("speech"):
        import subprocess

        audio = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.3",
                "-f",
                "mp3",
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        ).stdout
        return httpx.Response(
            200,
            content=audio,
            headers={
                "Content-Type": "audio/mpeg",
                "X-Generation-Id": "test-tts-" + __import__("uuid").uuid4().hex,
            },
        )
    return httpx.Response(400, json={"error": "Unexpected test request"})


def provider():
    return OpenRouter(httpx.Client(transport=httpx.MockTransport(handler)))


if __name__ == "__main__":
    from google.auth import crypt, jwt
    from google.oauth2 import id_token
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import time

    # Only this isolated test server issues fixture tokens; production has no such route.
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = signing_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    id_token._fetch_certs = lambda *a, **kw: {"browser-test-key": public.decode()}
    signer = crypt.RSASigner.from_string(private, key_id="browser-test-key")

    @main.app.get("/api/test/google-credential")
    def test_credential(nonce: str, email: str = "popup@college.edu"):
        return {
            "credential": jwt.encode(
                signer,
                {
                    "iss": "https://accounts.google.com",
                    "aud": os.environ["GOOGLE_CLIENT_ID"],
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 300,
                    "nonce": nonce,
                    "email": email,
                    "email_verified": True,
                    "sub": "test-sub-" + email,
                    "name": "Popup Student",
                },
            ).decode()
        }

    Base.metadata.create_all(engine)
    with DB() as db:
        user = User(email="browser@college.edu", name="Browser Test")
        db.add(user)
        db.add(AllowedEmail(email=user.email))
        db.add(AllowedEmail(email="popup@college.edu"))
        db.flush()
        db.add(
            Session(
                token_hash=hashlib.sha256(b"browser-test-token").hexdigest(),
                user_id=user.id,
                csrf="browser-test-csrf",
                expires=4102444800,
            )
        )
        admin = User(email="admin-browser@college.edu", name="Test Administrator", role="admin")
        db.add(admin)
        db.flush()
        db.add(
            Session(
                token_hash=hashlib.sha256(b"admin-browser-test-token").hexdigest(),
                user_id=admin.id,
                csrf="browser-admin-csrf",
                expires=4102444800,
            )
        )
        analyst_user = User(
            email="analytics@college.edu", name="Analytics Test", created=time.time() - 100 * 86400
        )
        db.add(analyst_user)
        db.add(AllowedEmail(email=analyst_user.email))
        db.flush()
        db.add(
            Session(
                token_hash=hashlib.sha256(b"analytics-browser-token").hexdigest(),
                user_id=analyst_user.id,
                csrf="analytics-csrf",
                expires=4102444800,
            )
        )
        for i in range(8):
            created = time.time() - i * 86400
            meeting = Meeting(
                user_id=analyst_user.id,
                title=f"Analytics fixture {i + 1}",
                duration=1260 + i * 300,
                status="queued" if i == 0 else "failed" if i == 1 else "ready",
                summary_version=0 if i < 2 else 1,
                created=created,
            )
            db.add(meeting)
            db.flush()
            if i >= 2:
                db.add(
                    Summary(
                        meeting_id=meeting.id,
                        version=1,
                        transcript_version=1,
                        content=MINUTES,
                        created=created,
                    )
                )
            for op, amount in [("stt", "0.12"), ("llm", "0.08"), ("tts", None if i == 0 else "0.04")]:
                db.add(
                    Usage(
                        user_id=analyst_user.id,
                        meeting_id=meeting.id,
                        operation=op,
                        model="test-model-" + op,
                        cost=amount,
                        cost_status="pending" if amount is None else "actual",
                        cost_source="unavailable" if amount is None else "provider_reported",
                        created=created,
                    )
                )
        db.commit()
        voice_user = User(email="voice-browser@college.edu", name="Voice Test")
        db.add(voice_user)
        db.add(AllowedEmail(email=voice_user.email))
        db.flush()
        db.add(
            Session(
                token_hash=hashlib.sha256(b"voice-browser-token").hexdigest(),
                user_id=voice_user.id,
                csrf="voice-browser-csrf",
                expires=4102444800,
            )
        )
        voice_meeting = Meeting(
            user_id=voice_user.id, title="Voice studio meeting", summary_version=1, status="ready"
        )
        db.add(voice_meeting)
        db.flush()
        db.add(Summary(meeting_id=voice_meeting.id, version=1, transcript_version=1, content=MINUTES))
        db.commit()
    jobs.OpenRouter = provider
    main.OpenRouter = provider
    voices.OpenRouter = provider
    threading.Thread(
        target=qwen_worker.run_worker, kwargs={"engine_factory": MockQwenEngine}, daemon=True
    ).start()
    threading.Thread(target=jobs.run_worker, daemon=True).start()
    uvicorn.run(main.app, host="127.0.0.1", port=8100, log_level="warning")
