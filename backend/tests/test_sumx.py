import io
import json
import subprocess
import threading
import time
import wave
import httpx
import pytest
from sqlalchemy import select
from backend.config import settings
from backend.jobs import enqueue, latest, process_job
from backend.media import extract, probe, safe_path, signature_ok, text_chunks
from backend.models import AllowedEmail, DB, Job, Meeting, Summary, Transcript, Usage, User
from backend.provider import OpenRouter, ProviderError

MINUTES = {
    "title": "Library planning",
    "executive_summary": "The group discussed library opening hours.",
    "discussion_points": ["Extended opening hours were discussed."],
    "decisions": [],
    "action_items": [{"task": "Review opening hours", "owner": None, "deadline": None, "timestamp": None}],
    "unresolved_issues": ["Final schedule"],
    "follow_up": [],
}


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return output.getvalue()


def upload(client, key="upload-test-key-123456", **fields):
    return client.post(
        "/api/meetings/upload",
        data={"consent": "true", **fields},
        files={"file": ("meeting.wav", wav_bytes(), "audio/wav")},
        headers={"Idempotency-Key": key},
    )


class MockRouter:
    def __init__(self, fail_summary=False):
        self.calls = []
        self.fail_summary = fail_summary

    def transcribe(self, path, language):
        self.calls.append("stt")
        return (
            {
                "text": "We discussed library hours. Review opening hours.",
                "segments": [{"start": 0, "end": 0.8, "text": "We discussed library hours."}],
                "language": "en",
            },
            {"usage": {"cost": 0.001, "seconds": 1}},
            httpx.Response(200, headers={"x-generation-id": "stt-generation"}),
        )

    def summarize(self, text, detail, synthesis=False):
        self.calls.append("llm")
        if self.fail_summary:
            raise ProviderError("Summary provider unavailable")
        return (
            json.loads(json.dumps(MINUTES)),
            {"id": "summary-generation", "usage": {"cost": 0.002}},
            httpx.Response(200),
        )

    def speech(self, text, voice):
        self.calls.append("tts")
        data = subprocess.run(
            [
                settings.ffmpeg,
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
        return data, {}, httpx.Response(200, headers={"x-generation-id": "tts-generation"})

    def voices(self):
        return [settings.openrouter_tts_voice]


def test_pipeline_partial_failure_retry_and_versions(user_client):
    result = upload(user_client, retain_source="false")
    assert result.status_code == 200, result.text
    m_id = result.json()["id"]
    with DB() as db:
        job_id = db.scalar(select(Job.id))
    provider = MockRouter(fail_summary=True)
    process_job(job_id, provider)
    m = user_client.get("/api/meetings/" + m_id).json()
    assert m["status"] == "failed"
    assert m["transcript"]["text"]
    assert not m["source_available"]
    assert m["summary"] is None
    retry = user_client.post(f"/api/meetings/{m_id}/jobs/{job_id}/retry")
    assert retry.status_code == 200
    provider.fail_summary = False
    process_job(job_id, provider)
    assert provider.calls.count("stt") == 1
    m = user_client.get("/api/meetings/" + m_id).json()
    assert m["status"] == "ready"
    assert m["summary"]["content"]["action_items"][0]["owner"] is None
    assert user_client.get("/api/meetings/" + m_id + "/export?kind=summary").status_code == 200
    speech = user_client.post(
        f"/api/meetings/{m_id}/speech", json={}, headers={"Idempotency-Key": "test-cccccccccccccccc"}
    ).json()
    process_job(speech["id"], provider)
    m = user_client.get("/api/meetings/" + m_id).json()
    assert len(m["audio"]) == 1
    audio_id = m["audio"][0]["id"]
    response = user_client.get(f"/api/meetings/{m_id}/audio/{audio_id}", headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert len(response.content) == 32
    result = user_client.put(f"/api/meetings/{m_id}/summary", json={"content": MINUTES, "version": 1})
    assert result.status_code == 200
    assert user_client.get(f"/api/meetings/{m_id}/audio/{audio_id}").status_code == 404
    assert user_client.get("/api/meetings/" + m_id).json()["audio"] == []
    with DB() as db:
        assert db.scalar(select(Usage).where(Usage.operation == "tts")).cost is None


def test_upload_validation_idempotency_and_ownership(user_client):
    response = upload(user_client)
    assert response.status_code == 200
    m_id = response.json()["id"]
    duplicate = upload(user_client)
    assert duplicate.json()["id"] == m_id
    with DB() as db:
        assert len(db.scalars(select(Meeting)).all()) == 1
        assert len(db.scalars(select(Job)).all()) == 1
        stranger = User(email="other@college.edu", name="Other")
        db.add(stranger)
        db.commit()
        db.get(Meeting, m_id).user_id = stranger.id
        db.commit()
    for suffix in ["", "/status", "/transcript", "/summary", "/media", "/export"]:
        assert user_client.get("/api/meetings/" + m_id + suffix).status_code == 404
    assert user_client.delete("/api/meetings/" + m_id).status_code == 404
    bad = user_client.post(
        "/api/meetings/upload",
        data={"consent": "true"},
        files={"file": ("bad.mp4", b"not video", "video/mp4")},
        headers={"Idempotency-Key": "invalid-upload-123456"},
    )
    assert bad.status_code == 422
    unsupported = user_client.post(
        "/api/meetings/upload",
        data={"consent": "true"},
        files={"file": ("bad.exe", wav_bytes(), "audio/wav")},
        headers={"Idempotency-Key": "invalid-upload-234567"},
    )
    assert unsupported.status_code == 415
    assert upload(user_client, "no-consent-test-123456", consent="false").status_code == 422


def test_csrf_role_checks_allowlist_and_login_rate_limit(user_client):
    assert user_client.get("/api/admin/users").status_code == 403
    assert user_client.post("/api/meetings", json={}, headers={"X-CSRF-Token": ""}).status_code == 403
    assert (
        user_client.post("/api/meetings", json={}, headers={"Origin": "https://attacker.example"}).status_code
        == 403
    )
    with DB() as db:
        db.get(AllowedEmail, "student@college.edu").active = False
        db.commit()
    assert user_client.get("/api/auth/me").status_code == 403
    with DB() as db:
        db.add(User(email="admin@college.edu", name="Admin", role="admin", password_hash=None))
        db.commit()
    for _ in range(8):
        response = user_client.post(
            "/api/auth/admin/login", json={"email": "unknown@college.edu", "password": "wrong"}
        )
        assert response.status_code == 401
    assert (
        user_client.post(
            "/api/auth/admin/login", json={"email": "unknown@college.edu", "password": "wrong"}
        ).status_code
        == 429
    )


def test_admin_access_and_unknown_costs(admin_client):
    assert admin_client.post("/api/admin/users", json={"email": "Student@College.edu"}).status_code == 200
    assert admin_client.get("/api/admin/users").json() == [{"email": "student@college.edu", "active": True}]
    with DB() as db:
        db.add(
            Usage(
                meeting_id="deleted-meeting", user_id="deleted-user", operation="tts", model="test", cost=None
            )
        )
        db.commit()
    analytics = admin_client.get("/api/admin/analytics").json()
    assert analytics["totals"]["reported_cost"] == 0
    assert analytics["totals"]["unknown_requests"] == 1
    assert admin_client.delete("/api/admin/users/student@college.edu").status_code == 200
    assert admin_client.post("/api/auth/logout").status_code == 200
    assert admin_client.get("/api/admin/users").status_code == 401


def test_mp4_extraction_and_signature(tmp_path):
    source, audio = tmp_path / "meeting.mp4", tmp_path / "chunk.mp3"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    assert signature_ok(source)
    seconds, video = probe(source)
    assert video and seconds >= 1
    extract(source, audio, 0, 1)
    assert not probe(audio)[1]
    with pytest.raises(ValueError, match="Invalid storage"):
        safe_path(str(tmp_path / ".." / "secret.txt"))


def test_provider_contracts_and_malformed_responses(tmp_path):
    wav = tmp_path / "audio.mp3"
    wav.write_bytes(b"ID3test")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("transcriptions"):
            assert b'name="model"' in request.content
            assert b"whisper-large-v3" in request.content
            assert b"verbose_json" in request.content
            return httpx.Response(
                200,
                json={
                    "text": "Hello",
                    "segments": [{"start": 0, "end": 1, "text": "Hello"}],
                    "usage": {"cost": 0.001},
                },
            )
        if request.url.path.endswith("completions"):
            body = json.loads(request.content)
            assert body["response_format"]["type"] == "json_object"
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(MINUTES) + "\n```"}}]}
            )
        return httpx.Response(200, json={"error": "This is not an MP3"})

    router = OpenRouter(httpx.Client(transport=httpx.MockTransport(handler)))
    transcript, _, _ = router.transcribe(wav, None)
    assert transcript["segments"][0]["start"] == 0
    assert "speaker" not in transcript["segments"][0]
    assert router.summarize("Hello", "standard")[0]["title"] == "Library planning"
    with pytest.raises(ProviderError, match="invalid MP3"):
        router.speech("Hello", settings.openrouter_tts_voice)
    for payload in [{"text": None}, {"error": "failed"}, ["not a dictionary"]]:
        router = OpenRouter(
            httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        )
        with pytest.raises(ProviderError):
            router.transcribe(wav, None)
    router = OpenRouter(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"choices": [{"message": {"content": '{"title":"Incomplete"}'}}]}
                )
            )
        )
    )
    with pytest.raises(ProviderError, match="schema"):
        router.summarize("Hello", "standard")
    assert all(r.url.host == "openrouter.ai" for r in requests)


def test_edit_transcript_no_retranscription_and_stale_edit(user_client):
    m_id = upload(user_client).json()["id"]
    with DB() as db:
        job_id = db.scalar(select(Job.id))
    provider = MockRouter()
    process_job(job_id, provider)
    edit = {"text": "Edited transcript with no action owner.", "version": 1}
    assert user_client.put(f"/api/meetings/{m_id}/transcript", json=edit).status_code == 200
    assert user_client.put(f"/api/meetings/{m_id}/transcript", json=edit).status_code == 409
    m = user_client.get("/api/meetings/" + m_id).json()
    assert m["transcript"]["segments"] == []
    assert m["summary"]["transcript_version"] != m["transcript_version"]
    response = user_client.post(
        f"/api/meetings/{m_id}/summarize",
        json={"detail": "concise"},
        headers={"Idempotency-Key": "test-dddddddddddddddd"},
    )
    job_id = response.json()["id"]
    assert user_client.delete("/api/meetings/" + m_id).status_code == 409
    assert (
        user_client.put(
            f"/api/meetings/{m_id}/transcript", json={"text": "racing edit", "version": 2}
        ).status_code
        == 409
    )
    process_job(job_id, provider)
    assert provider.calls.count("stt") == 1
    with DB() as db:
        assert latest(db, Summary, m_id).transcript_version == 2
    assert user_client.delete("/api/meetings/" + m_id).status_code == 200
    assert user_client.get("/api/meetings/" + m_id).status_code == 404


def test_chunk_checkpoint_and_worker_restart(user_client, monkeypatch):
    m_id = upload(user_client).json()["id"]
    with DB() as db:
        m = db.get(Meeting, m_id)
        m.duration = 2
        job = db.scalar(select(Job))
        job.state = "running"
        job.heartbeat = time.time() - 600
        job_id = job.id
        db.commit()
    monkeypatch.setattr(settings, "audio_chunk_seconds", 1)
    provider = MockRouter()
    original = provider.transcribe
    calls = 0

    def fail_second(path, language):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProviderError("Second chunk failed")
        return original(path, language)

    provider.transcribe = fail_second
    process_job(job_id, provider)
    with DB() as db:
        assert len(db.get(Job, job_id).checkpoint["transcription_parts"]) == 1
    # Resume only the missing chunk, including its source offset.
    provider.transcribe = original
    user_client.post(f"/api/meetings/{m_id}/jobs/{job_id}/retry")
    process_job(job_id, provider)
    with DB() as db:
        transcript = latest(db, Transcript, m_id)
        assert len(transcript.segments) == 2
        assert transcript.segments[1]["start"] == 1
        assert db.get(Job, job_id).state == "completed"
    assert provider.calls.count("stt") == 2


def test_no_duplicate_active_jobs(user_client):
    m_id = upload(user_client).json()["id"]
    results = []

    def queue():
        with DB() as db:
            m = db.get(Meeting, m_id)
            results.append(enqueue(db, m, "pipeline", str(time.time_ns())).id)

    threads = [threading.Thread(target=queue) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 3 and len(set(results)) == 1
    assert len(text_chunks("Hello. " * 100, 80)) > 1


def test_unauthenticated_access(client):
    for path in ["/api/meetings", "/api/admin/analytics", "/api/config", "/api/voices"]:
        assert client.get(path).status_code == 401
