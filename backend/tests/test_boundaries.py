import json
import subprocess
import threading
import time
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend import jobs
from backend.config import settings
from backend.limits import BodyLimitMiddleware
from backend.media import probe
from backend.models import DB, Job
from backend.provider import OpenRouter, ProviderError
from backend.tests.test_sumx import MINUTES, upload, wav_bytes


def test_body_limit_without_content_length():
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, limit=64)

    @app.post("/")
    async def body(request: Request):
        return {"bytes": len(await request.body())}

    client = TestClient(app)
    assert client.post("/", content=iter([b"a" * 40, b"b" * 40])).status_code == 413


def test_browser_style_webm_duration(tmp_path):
    # Stream to pipe so FFmpeg, like MediaRecorder, cannot seek back to write duration.
    source = tmp_path / "browser.webm"
    data = subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    source.write_bytes(data)
    seconds, video = probe(source)
    assert 0.9 <= seconds <= 1.1 and not video


def test_summary_response_format_fallback_and_bounded_retries(monkeypatch):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("response_format"):
            return httpx.Response(400, json={"error": "Unsupported response_format"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(MINUTES)}}]})

    router = OpenRouter(httpx.Client(transport=httpx.MockTransport(handler)))
    assert router.summarize("Transcript", "standard")[0]["title"]
    assert len(calls) == 2
    calls.clear()
    monkeypatch.setattr(time, "sleep", lambda n: None)

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503)

    router = OpenRouter(httpx.Client(transport=httpx.MockTransport(unavailable)))
    with pytest.raises(ProviderError):
        router.request("POST", "/audio/speech", json={})
    assert len(calls) == 3


def test_worker_recovers_expired_lease(user_client, monkeypatch):
    upload(user_client)
    with DB() as db:
        job = db.scalar(select(Job))
        job.state, job.heartbeat = "running", time.time() - 1000
        job_id = job.id
        db.commit()
    stop = threading.Event()
    completed = []

    def process(id):
        with DB() as db:
            job = db.get(Job, id)
            assert job.state == "running"
            job.state = "completed"
            db.commit()
        completed.append(id)
        stop.set()

    monkeypatch.setattr(jobs, "process_job", process)
    thread = threading.Thread(target=jobs.run_worker, args=(stop,))
    thread.start()
    thread.join(5)
    assert completed == [job_id]
    assert not thread.is_alive()


def test_duration_and_size_caps(user_client, monkeypatch):
    monkeypatch.setattr(settings, "max_duration_seconds", 0.5)
    assert upload(user_client).status_code == 422
    monkeypatch.setattr(settings, "max_duration_seconds", 14400)
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    assert (
        user_client.post(
            "/api/meetings/upload",
            data={"consent": "true"},
            files={"file": ("large.wav", wav_bytes() + b"\0" * 1048576, "audio/wav")},
            headers={"Idempotency-Key": "oversized-file-123456"},
        ).status_code
        == 413
    )


def test_meeting_larger_than_250_mb_is_accepted_without_cap(user_client, monkeypatch, tmp_path):
    from backend import main

    monkeypatch.setattr(settings, "max_upload_mb", 0)
    # Exercise real request and file streaming without sending this padded fixture to FFmpeg/AI.
    monkeypatch.setattr(main, "probe", lambda path: (1.0, False))
    source = tmp_path / "large.wav"
    with source.open("wb") as file:
        file.write(wav_bytes())
        file.truncate(251 * 1024 * 1024)
    with source.open("rb") as file:
        result = user_client.post(
            "/api/meetings/upload",
            data={"consent": "true"},
            files={"file": ("large.wav", file, "audio/wav")},
            headers={"Idempotency-Key": "large-file-unlimited-12345"},
        )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "queued"
