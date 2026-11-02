import time
from sqlalchemy import select
from backend.models import DB, User, ClonedVoice, Meeting, Job, LocalTTSGeneration
from backend.config import settings


def create_job(client, **values):
    with DB() as db:
        m = Meeting(user_id=client.user_id, title="Progress test", summary_version=1)
        db.add(m)
        db.flush()
        j = Job(
            meeting_id=m.id,
            user_id=client.user_id,
            kind="speech",
            request_key="status-test",
            payload={"voice_type": "cloned"},
            model_name=settings.qwen_tts_model,
            narration_stage="QUEUED",
            **values,
        )
        db.add(j)
        db.commit()
        return j.id


def test_actual_progress_never_completes_before_merge_and_validation(user_client):
    jid = create_job(user_client, state="running", total=2, completed=1, started_at=time.time() - 5)
    path = f"/api/narration/jobs/{jid}/status"
    data = user_client.get(path).json()
    assert data["percent"] == 50 and data["completed_segments"] == 1
    with DB() as db:
        j = db.get(Job, jid)
        j.completed = 2
        j.narration_stage = "MERGING_AUDIO"
        db.commit()
    data = user_client.get(path).json()
    assert data["percent"] is None and data["stage"] == "MERGING_AUDIO" and not data["audio_ready"]
    with DB() as db:
        j = db.get(Job, jid)
        j.narration_stage = "FINALIZING"
        db.commit()
    assert user_client.get(path).json()["percent"] is None
    with DB() as db:
        j = db.get(Job, jid)
        j.state = "completed"
        j.narration_stage = "COMPLETED"
        j.output_path = "private-output.mp3"
        j.completed_at = time.time()
        db.commit()
    data = user_client.get(path).json()
    assert data["percent"] == 100 and data["audio_ready"] and "output_path" not in data


def test_live_stream_events_are_not_text_segment_completions(user_client):
    jid = create_job(user_client, state="running", total=1, completed=0)
    with DB() as db:
        j = db.get(Job, jid)
        j.narration_stage = "GENERATING_AUDIO"
        j.audio_seconds = 4.2
        db.add(
            LocalTTSGeneration(
                parent_job_id=jid,
                segment_index=0,
                meeting_id=j.meeting_id,
                user_id=user_client.user_id,
                voice_id="a" * 32,
                request_key="stream-progress",
                model=settings.qwen_tts_model,
                purpose="meeting",
                text="private",
                reference_text="private",
                language="en",
                input_characters=20,
                state="running",
                stage="generating_audio",
                stream_events=8,
                audio_seconds=4.2,
            )
        )
        db.commit()
    data = user_client.get(f"/api/narration/jobs/{jid}/status").json()
    assert data["percent"] == 0 and data["completed_segments"] == 0
    assert data["stream_events"] == 8 and data["segment_audio_seconds"] == 4.2
    assert "private" not in str(data)


def test_status_ownership_and_failed_state(user_client):
    jid = create_job(
        user_client,
        state="failed",
        total=3,
        completed=1,
        error="GPU worker stopped",
        completed_at=time.time(),
    )
    with DB() as db:
        j = db.get(Job, jid)
        j.narration_stage = "FAILED"
        db.commit()
    result = user_client.get(f"/api/narration/jobs/{jid}/status").json()
    assert result["stage"] == "FAILED" and result["percent"] < 100 and result["error"] == "GPU worker stopped"
    with DB() as db:
        stranger = User(email="stranger-progress@college.edu", name="Other")
        db.add(stranger)
        db.flush()
        db.get(Meeting, db.get(Job, jid).meeting_id).user_id = stranger.id
        db.commit()
    assert user_client.get(f"/api/narration/jobs/{jid}/status").status_code == 404


def test_double_click_idempotence_and_queue_different_meetings(user_client):
    with DB() as db:
        v = ClonedVoice(
            user_id=user_client.user_id,
            name="Existing voice",
            reference_path="private-reference.wav",
            reference_text="Confirmed reference transcript",
            duration=20,
            language="en",
            consent_at=time.time(),
            saved=True,
            preview_path="old-preview.mp3",
            model=settings.qwen_tts_model,
            reference_verified_at=time.time(),
            preview_version="faster-qwen-local-icl-v1",
        )
        a = Meeting(user_id=user_client.user_id, title="First", summary_version=1)
        b = Meeting(user_id=user_client.user_id, title="Second", summary_version=1)
        db.add_all([v, a, b])
        db.commit()
        vid = v.id
        mid = a.id
        other = b.id
    body = {"voice_type": "cloned", "cloned_voice_id": vid}
    headers = {"Idempotency-Key": "test-aaaaaaaaaaaaaaaa"}
    a = user_client.post(f"/api/meetings/{mid}/speech", json=body, headers=headers)
    b = user_client.post(f"/api/meetings/{mid}/speech", json=body, headers=headers)
    c = user_client.post(
        f"/api/meetings/{mid}/speech", json=body, headers={"Idempotency-Key": "test-bbbbbbbbbbbbbbbb"}
    )
    d = user_client.post(f"/api/meetings/{other}/speech", json=body, headers=headers)
    assert a.status_code == b.status_code == c.status_code == d.status_code == 200
    assert a.json()["id"] == b.json()["id"] == c.json()["id"] and d.json()["id"] != a.json()["id"]
    with DB() as db:
        assert len(list(db.scalars(select(Job)))) == 2
