import io
import math
import struct
import wave
from pathlib import Path
import pytest
from sqlalchemy import select
from backend import tts_queue, tts_worker
from backend.config import settings
from backend.jobs import process_job
from backend.media import probe
from backend.models import (
    AudioOutput,
    ClonedVoice,
    DB,
    Job,
    LocalTTSGeneration,
    LocalTTSWorker,
    Meeting,
    Summary,
    Usage,
    User,
)
from backend.provider import OpenRouter
from backend.speech_contract import LocalTTSError, split_narration
from backend.tests.qwen_mock import MockQwenEngine
from backend.tests.test_sumx import MINUTES

REF_TEXT = "Synthetic reference fixture used only by automated tests."


def sample(seconds=4, silent=False):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(
            b"".join(
                struct.pack("<h", 0 if silent else int(6000 * math.sin(i * 2 * math.pi * 220 / 16000)))
                for i in range(int(16000 * seconds))
            )
        )
    return out.getvalue()


def upload(client, **changes):
    data = dict(name="My Personal Voice", consent="true", language="en", reference_text=REF_TEXT)
    data.update(changes)
    audio = data.pop("audio", sample())
    return client.post("/api/voices", data=data, files={"file": ("reference.wav", audio, "audio/wav")})


def preview(client, vid):
    return client.post(f"/api/voices/{vid}/preview", headers={"Idempotency-Key": "preview-test-1234567890"})


def run_task(tid, engine=None):
    with DB() as db:
        task = db.get(LocalTTSGeneration, tid)
        task.state = "running"
        task.attempts += 1
        db.commit()
    tts_worker.process(tid, engine or MockQwenEngine())


@pytest.fixture(autouse=True)
def ready_worker(database):
    with DB() as db:
        db.add(
            LocalTTSWorker(
                id="qwen3",
                state="ready",
                model=settings.qwen_tts_model,
                gpu_name="Mock NVIDIA GPU",
                vram_mb=12288,
            )
        )
        db.commit()


def finish_preview(client, vid):
    result = preview(client, vid)
    assert result.status_code == 200, result.text
    run_task(result.json()["preview_job_id"])
    assert client.get(f"/api/voices/{vid}").json()["preview_current"]


def mock_generate(voice, text, key, meeting_id=None, timeout=1800, progress=None, raw_wav=False):
    with DB() as db:
        tid = tts_queue.submit(db, voice, text, key, meeting_id).id
    run_task(tid)
    with DB() as db:
        task = db.get(LocalTTSGeneration, tid)
        if progress:
            progress(task)
        if task.state == "failed":
            raise LocalTTSError(task.error)
        output = Path(task.output_path)
        return (output.with_suffix(".wav") if raw_wav else output).read_bytes()


def test_local_voice_end_to_end_no_external_requests(user_client, monkeypatch):
    monkeypatch.setattr(
        OpenRouter,
        "request",
        lambda *a, **kw: pytest.fail("Private cloned audio must never reach OpenRouter"),
    )
    v = upload(user_client).json()
    vid = v["id"]
    assert not v["saved"] and "reference_path" not in v
    assert user_client.patch(f"/api/voices/{vid}", json={"saved": True}).status_code == 409
    assert user_client.get("/api/voices?q=PERSONAL").json()[0]["id"] == vid
    finish_preview(user_client, vid)
    assert preview(user_client, vid).json()["preview_current"]
    user_client.patch(f"/api/voices/{vid}", json={"saved": True, "name": "Personal Narrator"})
    with DB() as db:
        m = Meeting(user_id=user_client.user_id, title="Meeting", summary_version=1, status="ready")
        db.add(m)
        db.flush()
        db.add(Summary(meeting_id=m.id, version=1, transcript_version=1, content=MINUTES))
        db.commit()
        mid = m.id
    monkeypatch.setattr(tts_queue, "generate", mock_generate)
    result = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "cloned", "cloned_voice_id": vid},
        headers={"Idempotency-Key": "cloned-narration-12345"},
    )
    assert result.status_code == 200, result.text
    assert user_client.delete(f"/api/voices/{vid}").status_code == 409
    process_job(result.json()["id"])
    with DB() as db:
        assert db.get(Job, result.json()["id"]).state == "completed"
        audio = db.scalar(select(AudioOutput).where(AudioOutput.meeting_id == mid))
        assert audio.model == settings.qwen_tts_model and audio.voice_name == "Personal Narrator"
        assert audio.summary_version == 1 and probe(Path(audio.path))[0] > 0
        tasks = list(db.scalars(select(LocalTTSGeneration)))
        assert len(tasks) >= 2 and all(
            t.provider == "modal_qwen3" and t.external_api_cost >= 0 for t in tasks
        )
        assert all(t.processing_seconds > 0 and t.audio_seconds > 0 for t in tasks)
        assert all(
            u.provider == "modal_qwen3" and u.cost_status == "estimated" and u.cost is not None
            for u in db.scalars(select(Usage))
        )
        assert all(not t.text and not t.reference_text for t in tasks)
        folder = Path(db.get(ClonedVoice, vid).reference_path).parent
        aid = audio.id
    assert user_client.delete(f"/api/voices/{vid}").status_code == 200 and not folder.exists()
    assert user_client.get(f"/api/meetings/{mid}/audio/{aid}").status_code == 200


@pytest.mark.parametrize(
    "data",
    [
        {"consent": "false"},
        {"name": "   "},
        {"audio": b"not audio"},
        {"audio": sample(1)},
        {"audio": sample(silent=True)},
        {"language": "hi"},
    ],
)
def test_invalid_reference(user_client, data):
    assert upload(user_client, **data).status_code in (415, 422)
    assert user_client.get("/api/voices").json() == []


def test_cross_user_voice_access(user_client):
    v = upload(user_client).json()
    with DB() as db:
        other = User(email="other-voice@college.edu", name="Other")
        db.add(other)
        db.flush()
        db.get(ClonedVoice, v["id"]).user_id = other.id
        db.commit()
    assert user_client.get("/api/voices").json() == []
    path = f"/api/voices/{v['id']}"
    for r in [
        user_client.get(path),
        user_client.get(path + "/reference"),
        user_client.get(path + "/preview"),
        user_client.patch(path, json={"name": "Stolen"}),
        user_client.delete(path),
        preview(user_client, v["id"]),
    ]:
        assert r.status_code == 404
    with DB() as db:
        m = Meeting(user_id=user_client.user_id, title="Owned", summary_version=1)
        db.add(m)
        db.commit()
        mid = m.id
    assert (
        user_client.post(
            f"/api/meetings/{mid}/speech",
            json={"voice_type": "cloned", "cloned_voice_id": v["id"]},
            headers={"Idempotency-Key": "foreign-reference-12345"},
        ).status_code
        == 404
    )


def test_unavailable_worker_no_fallback(user_client, monkeypatch):
    monkeypatch.setattr(OpenRouter, "request", lambda *a, **kw: pytest.fail("No external fallback"))
    v = upload(user_client).json()
    with DB() as db:
        db.get(LocalTTSWorker, "qwen3").heartbeat = 0
        db.commit()
    assert not user_client.get("/api/voices/capabilities").json()["supported"]
    assert preview(user_client, v["id"]).status_code == 503
    assert user_client.get(f"/api/voices/{v['id']}/reference").status_code == 200


def test_worker_failure_retry_and_missing_reference(user_client):
    v = upload(user_client).json()
    tid = preview(user_client, v["id"]).json()["preview_job_id"]

    class OOM(MockQwenEngine):
        def synthesize(self, *a, **kw):
            raise LocalTTSError("The GPU ran out of memory. Free memory and retry.")

    run_task(tid, OOM())
    assert "memory" in user_client.get(f"/api/voices/{v['id']}").json()["preview_error"]
    assert preview(user_client, v["id"]).json()["preview_job_id"] == tid
    run_task(tid)
    with DB() as db:
        t = db.get(LocalTTSGeneration, tid)
        assert t.attempts == 2 and t.state == "completed" and t.external_api_cost > 0
    v2 = upload(user_client, name="Missing reference").json()
    with DB() as db:
        Path(db.get(ClonedVoice, v2["id"]).reference_path).unlink()
    assert preview(user_client, v2["id"]).status_code == 422


def test_active_preview_and_csrf(user_client):
    v = upload(user_client).json()
    preview(user_client, v["id"])
    assert user_client.delete(f"/api/voices/{v['id']}").status_code == 409
    assert user_client.patch(f"/api/voices/{v['id']}", json={"reference_text": "Changed"}).status_code == 409
    user_client.headers.pop("X-CSRF-Token")
    assert user_client.patch(f"/api/voices/{v['id']}", json={"name": "Changed"}).status_code == 403


def test_existing_reference_consent_preserved(user_client):
    v = upload(user_client).json()
    with DB() as db:
        voice = db.get(ClonedVoice, v["id"])
        original = Path(voice.reference_path).read_bytes()
        consent = voice.consent_at
        voice.model = "fish-audio/s2.1-pro"
        voice.reference_text = ""
        db.commit()
    assert preview(user_client, v["id"]).status_code == 422
    user_client.patch(f"/api/voices/{v['id']}", json={"reference_text": REF_TEXT})
    finish_preview(user_client, v["id"])
    with DB() as db:
        voice = db.get(ClonedVoice, v["id"])
        assert voice.model == settings.qwen_tts_model
        assert voice.consent_at == consent and Path(voice.reference_path).read_bytes() == original


def test_long_narration_chunks(user_client, monkeypatch):
    v = upload(user_client).json()
    finish_preview(user_client, v["id"])
    user_client.patch(f"/api/voices/{v['id']}", json={"saved": True})
    with DB() as db:
        m = Meeting(user_id=user_client.user_id, title="Long", summary_version=1)
        db.add(m)
        db.flush()
        db.add(Summary(meeting_id=m.id, version=1, transcript_version=1, content=MINUTES))
        db.commit()
        mid = m.id
    monkeypatch.setattr(settings, "qwen_clone_chunk_words", 12)
    r = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "cloned", "cloned_voice_id": v["id"]},
        headers={"Idempotency-Key": "long-local-123456789"},
    )
    process_job(r.json()["id"])
    with DB() as db:
        assert db.get(Job, r.json()["id"]).state == "completed"
        tasks = list(db.scalars(select(LocalTTSGeneration).where(LocalTTSGeneration.meeting_id == mid)))
        generated = MockQwenEngine.generated
        assert len(generated) > 1 and all(len(text.split()) <= 12 for text in generated)
        # One narration is one task and so one Modal invocation, however many chunks it has.
        assert sum(task.purpose == "narration" for task in tasks) == 1
        assert all(
            u.provider == "modal_qwen3" and u.cost_status == "estimated" and u.cost is not None
            for u in db.scalars(select(Usage))
        )
    assert (
        " ".join(split_narration("First sentence. Second sentence.\n\nA final paragraph.", 100))
        == "First sentence. Second sentence. A final paragraph."
    )


def test_presets_and_separate_local_metrics(admin_client):
    catalog = admin_client.get("/api/voices/presets").json()
    assert catalog["default"] == "en_paul_neutral" and len(catalog["voices"]) == 6
    with DB() as db:
        owner = db.scalar(select(User).where(User.role == "admin"))
        db.add(
            LocalTTSGeneration(
                request_key="metrics",
                user_id=owner.id,
                voice_id="a" * 32,
                model=settings.qwen_tts_model,
                purpose="voice_preview",
                text="",
                reference_text="",
                language="en",
                input_characters=72,
                state="completed",
                audio_seconds=5,
                processing_seconds=10,
                peak_vram_mb=5000,
            )
        )
        db.commit()
    data = admin_client.get("/api/admin/analytics/costs").json()
    assert data["local_tts"]["completed"] == 1 and data["local_tts"]["external_api_cost_usd"] == "0"
    assert data["totals"]["requests"] == 0 and data["meetings"] == 0


@pytest.mark.parametrize(
    "selection",
    [
        {"voice_type": "cloned"},
        {"voice_type": "preset", "cloned_voice_id": "a" * 32},
        {"voice_type": "cloned", "cloned_voice_id": "a" * 32, "preset_voice_id": "gb_oliver_neutral"},
    ],
)
def test_voice_selection_exclusive(user_client, selection):
    assert (
        user_client.post(
            "/api/meetings/" + "a" * 32 + "/speech",
            json=selection,
            headers={"Idempotency-Key": "invalid-selection-12345"},
        ).status_code
        == 422
    )
