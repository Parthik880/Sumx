"""Concise, Standard and Detailed behave as three independent saved summaries."""

import itertools
import pytest
from backend.jobs import process_job
from backend.models import DB, Job, Meeting, Summary, Transcript, User
from backend.tests.test_sumx import MockRouter


class Router(MockRouter):
    """Marks every summary with its mode and a counter, so any mix-up is visible."""

    def __init__(self):
        super().__init__()
        self.count = 0
        self.narrated = []

    def summarize(self, text, detail, synthesis=False, **options):
        content, data, response = super().summarize(text, detail, synthesis, **options)
        self.count += 1
        unique = {**content, "executive_summary": f"{detail} text {self.count}."}
        return unique, {**data, "id": f"summary-{self.count}"}, response

    async def speech_async(self, text, voice, client):
        self.narrated.append(text)
        return await super().speech_async(text, voice, client)


@pytest.fixture
def provider():
    return Router()


def new_meeting(owner):
    with DB() as db:
        row = Meeting(user_id=owner, title="Independent", transcript_version=1, status="ready")
        db.add(row)
        db.flush()
        db.add(Transcript(meeting_id=row.id, version=1, text="We reviewed the plan. " * 400, segments=[]))
        db.commit()
        return row.id


keys = itertools.count(1)


def generate(client, mid, mode, provider):
    response = client.post(
        f"/api/meetings/{mid}/summaries/{mode}/generate",
        json={},
        headers={"Idempotency-Key": f"generate-summary-{next(keys):08d}"},
    )
    assert response.status_code == 200, response.text
    process_job(response.json()["id"], provider)


def text(client, mid, mode):
    record = client.get(f"/api/meetings/{mid}/summaries/{mode}").json()
    return record["content"]["executive_summary"] if record["exists"] else None


def narrate(client, mid, mode, provider, voice="gb_jane_neutral"):
    response = client.post(
        f"/api/meetings/{mid}/narrations",
        json={"voice_type": "preset", "preset_voice_id": voice, "summary_mode": mode},
        headers={"Idempotency-Key": f"narrate-summary-{next(keys):08d}"},
    )
    assert response.status_code == 200, response.text
    process_job(response.json()["id"], provider)
    with DB() as db:
        job = db.get(Job, response.json()["id"])
        assert job.state == "completed", job.error
    return response


def test_required_scenario_end_to_end(user_client, provider):
    mid = new_meeting(user_client.user_id)
    listed = user_client.get(f"/api/meetings/{mid}/summaries").json()
    assert all(not m["exists"] and m["status"] == "not_generated" for m in listed["modes"].values())
    assert listed["narration_options"] == [] and listed["latest_mode"] is None

    generate(user_client, mid, "concise", provider)
    concise = text(user_client, mid, "concise")
    assert concise.startswith("concise") and text(user_client, mid, "standard") is None
    assert text(user_client, mid, "detailed") is None

    generate(user_client, mid, "standard", provider)
    standard = text(user_client, mid, "standard")
    assert standard.startswith("standard") and text(user_client, mid, "concise") == concise
    assert text(user_client, mid, "detailed") is None

    generate(user_client, mid, "standard", provider)
    replaced = text(user_client, mid, "standard")
    assert replaced != standard and replaced.startswith("standard")
    assert text(user_client, mid, "concise") == concise and text(user_client, mid, "detailed") is None
    generate(user_client, mid, "detailed", provider)
    assert text(user_client, mid, "concise") == concise and text(user_client, mid, "standard") == replaced
    assert provider.count == 4
    listed = user_client.get(f"/api/meetings/{mid}/summaries").json()
    assert listed["modes"]["standard"]["revision"] == 2 and listed["modes"]["concise"]["revision"] == 1
    assert listed["narration_options"] == ["concise", "standard", "detailed"]


def test_any_generation_order_keeps_modes_separate(user_client):
    for order in itertools.permutations(("concise", "standard", "detailed")):
        mid = new_meeting(user_client.user_id)
        provider = Router()
        for mode in order:
            generate(user_client, mid, mode, provider)
        for _ in range(3):
            for mode in order:
                assert text(user_client, mid, mode).startswith(mode)


def test_viewing_never_calls_the_llm(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    before = provider.count
    for _ in range(5):
        user_client.get(f"/api/meetings/{mid}/summaries")
        user_client.get(f"/api/meetings/{mid}/summaries/concise")
        user_client.get(f"/api/meetings/{mid}/summaries/standard")
    assert provider.count == before


def test_regenerate_always_calls_the_llm(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    generate(user_client, mid, "concise", provider)
    assert provider.count == 2


def test_completed_attempt_is_idempotent_but_other_modes_have_own_scope(user_client, provider):
    mid = new_meeting(user_client.user_id)
    headers = {"Idempotency-Key": "same-generation-attempt-123"}
    path = f"/api/meetings/{mid}/summaries/concise/generate"
    first = user_client.post(path, json={}, headers=headers).json()["id"]
    process_job(first, provider)
    assert user_client.post(path, json={}, headers=headers).json()["id"] == first
    process_job(first, provider)  # A duplicate delivery must also be harmless.
    assert provider.count == 1
    other = user_client.post(path.replace("concise", "standard"), json={}, headers=headers)
    assert other.status_code == 200 and other.json()["id"] != first
    process_job(other.json()["id"], provider)
    assert provider.count == 2
    with DB() as db:
        from sqlalchemy import select
        assert [r.detail for r in db.scalars(select(Summary).where(Summary.meeting_id == mid).order_by(Summary.version))] == ["concise", "standard"]


def test_legacy_endpoint_cannot_return_a_conflicting_mode(user_client):
    mid = new_meeting(user_client.user_id)
    headers = {"Idempotency-Key": "legacy-conflict-attempt-123"}
    first = user_client.post(f"/api/meetings/{mid}/summaries/concise/generate", json={}, headers=headers)
    assert first.status_code == 200
    other = user_client.post(f"/api/meetings/{mid}/summarize", json={"detail": "standard"}, headers=headers)
    assert other.status_code == 409


def test_concurrent_different_modes_are_serialized(user_client):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from backend.jobs import enqueue, JobConflict
    from sqlalchemy import select

    mid = new_meeting(user_client.user_id)
    barrier = threading.Barrier(2)

    def queue(mode):
        with DB() as db:
            meeting = db.get(Meeting, mid)
            barrier.wait(timeout=5)
            try:
                return enqueue(db, meeting, "summarize", "parallel-attempt-1234", {"detail": mode}).id
            except JobConflict:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(queue, ("concise", "standard")))
    assert sum(result is not None for result in results) == 1
    with DB() as db:
        assert len(db.scalars(select(Job).where(Job.meeting_id == mid)).all()) == 1


def test_concise_upload_only_generates_concise(user_client, provider):
    from backend.tests.test_sumx import upload
    from sqlalchemy import select

    response = upload(user_client, detail="concise")
    assert response.status_code == 200
    mid = response.json()["id"]
    assert response.json()["detail"] == "concise"
    with DB() as db:
        job = db.scalar(select(Job).where(Job.meeting_id == mid))
        jid = job.id
        assert job.payload["detail"] == "concise"
    process_job(jid, provider)
    assert provider.count == 1
    assert text(user_client, mid, "concise").startswith("concise")
    assert text(user_client, mid, "standard") is None and text(user_client, mid, "detailed") is None
    with DB() as db:
        assert len(db.scalars(select(Summary).where(Summary.meeting_id == mid)).all()) == 1
        metrics = db.get(Job, jid).payload["summary_metrics"]
        assert metrics["mode"] == "concise" and metrics["llm_calls"] == metrics["direct_calls"] == 1
        assert metrics["records_saved"] == 1


def test_long_concise_job_retries_only_failed_notes_and_keeps_mode(user_client, provider, monkeypatch):
    from backend.config import settings
    from backend.media import text_chunks
    from backend.models import Usage
    from backend.provider import ProviderError
    from sqlalchemy import select

    monkeypatch.setattr(settings, "summary_chunk_chars", 1500)
    mid = new_meeting(user_client.user_id)
    with DB() as db:
        transcript = db.scalar(select(Transcript).where(Transcript.meeting_id == mid))
        transcript.text = " ".join(f"Discussion item {i} concerns the library plan." for i in range(250))
        db.commit()
    response = user_client.post(f"/api/meetings/{mid}/summaries/concise/generate", json={},
                               headers={"Idempotency-Key": "long-concise-attempt-123"})
    jid = response.json()["id"]
    calls = []
    import threading
    calls_lock = threading.Lock()
    original = provider.summarize

    def fail_once(text, mode, synthesis=False, **options):
        with calls_lock:
            calls.append((text, mode, synthesis, options))
            fail = len(calls) == 2
        if fail:
            raise ProviderError("Temporary failure")
        return original(text, mode, synthesis, **options)

    provider.summarize = fail_once
    process_job(jid, provider)
    with DB() as db:
        job = db.get(Job, jid)
        assert job.state == "failed"
        completed_notes = [part for part in job.checkpoint["summary_parts"] if part is not None]
        assert completed_notes  # Other in-flight successful notes are saved even when one fails.
        completed_inputs = {call[0] for call in calls if call is not calls[1]}
    retried = user_client.post(f"/api/meetings/{mid}/jobs/{jid}/retry")
    assert retried.status_code == 200
    process_job(jid, provider)
    assert sum(call[0] == calls[0][0] for call in calls) == 1
    assert all(sum(call[0] == body for call in calls) == 1 for body in completed_inputs)
    assert all(call[1] == "concise" for call in calls)
    with DB() as db:
        metrics = db.get(Job, jid).payload["summary_metrics"]
        transcript = db.scalar(select(Transcript).where(Transcript.meeting_id == mid))
        n = len(text_chunks(transcript.text, settings.summary_chunk_chars))
        assert metrics["transcript_chunks"] == n
        assert metrics["note_calls"] == n + 1 and metrics["synthesis_calls"] == 1
        assert metrics["correction_calls"] == 0 and metrics["records_saved"] == 1
        assert metrics["llm_calls"] == n + 2 == len(calls)
        assert len(db.scalars(select(Usage).where(Usage.meeting_id == mid)).all()) == n + 1
        assert [r.detail for r in db.scalars(select(Summary).where(Summary.meeting_id == mid))] == ["concise"]


def test_narration_reads_the_selected_mode_not_the_newest(user_client, provider):
    mid = new_meeting(user_client.user_id)
    for mode in ("concise", "standard", "detailed"):
        generate(user_client, mid, mode, provider)
    for mode in ("concise", "standard", "detailed"):
        provider.narrated.clear()
        narrate(user_client, mid, mode, provider)
        assert any(text(user_client, mid, mode) in part for part in provider.narrated)


def test_missing_mode_cannot_be_narrated(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    response = user_client.post(
        f"/api/meetings/{mid}/narrations",
        json={"voice_type": "preset", "preset_voice_id": "gb_jane_neutral", "summary_mode": "detailed"},
        headers={"Idempotency-Key": "narrate-missing-mode-1"},
    )
    assert response.status_code == 409


def test_audio_is_mode_specific_and_regeneration_invalidates_only_its_own(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    generate(user_client, mid, "standard", provider)
    narrate(user_client, mid, "concise", provider)
    narrate(user_client, mid, "standard", provider)
    concise = user_client.get(f"/api/meetings/{mid}/summaries/concise").json()["audio"]
    standard = user_client.get(f"/api/meetings/{mid}/summaries/standard").json()["audio"]
    assert len(concise) == len(standard) == 1 and concise[0]["id"] != standard[0]["id"]
    spoken = provider.calls.count("tts")
    narrate(user_client, mid, "concise", provider)
    assert provider.calls.count("tts") == spoken

    generate(user_client, mid, "standard", provider)
    assert user_client.get(f"/api/meetings/{mid}/summaries/standard").json()["audio"] == []
    assert user_client.get(f"/api/meetings/{mid}/audio/{standard[0]['id']}").status_code == 404
    assert user_client.get(f"/api/meetings/{mid}/audio/{concise[0]['id']}").status_code == 200
    assert len(user_client.get(f"/api/meetings/{mid}/summaries/concise").json()["audio"]) == 1


def test_editing_one_mode_leaves_the_others_and_keeps_its_mode(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    generate(user_client, mid, "standard", provider)
    record = user_client.get(f"/api/meetings/{mid}/summaries/concise").json()
    edited = {**record["content"], "executive_summary": "Edited concise text."}
    response = user_client.put(f"/api/meetings/{mid}/summary", json={"content": edited, "version": record["version"]})
    assert response.status_code == 200, response.text
    assert text(user_client, mid, "concise") == "Edited concise text."
    assert text(user_client, mid, "standard").startswith("standard")
    stale = user_client.put(f"/api/meetings/{mid}/summary", json={"content": edited, "version": record["version"]})
    assert stale.status_code == 409


def test_export_follows_the_selected_mode(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    generate(user_client, mid, "standard", provider)
    exported = user_client.get(f"/api/meetings/{mid}/export?kind=summary&mode=concise").text
    assert "concise text" in exported and "standard text" not in exported


def test_running_job_for_another_mode_is_refused_not_merged(user_client, provider):
    mid = new_meeting(user_client.user_id)
    first = user_client.post(
        f"/api/meetings/{mid}/summaries/concise/generate", json={}, headers={"Idempotency-Key": "double-click-one-1234"}
    )
    again = user_client.post(
        f"/api/meetings/{mid}/summaries/concise/generate", json={}, headers={"Idempotency-Key": "double-click-two-1234"}
    )
    assert again.json()["id"] == first.json()["id"]
    other = user_client.post(
        f"/api/meetings/{mid}/summaries/detailed/generate", json={}, headers={"Idempotency-Key": "second-mode-running-1"}
    )
    assert other.status_code == 409
    listed = user_client.get(f"/api/meetings/{mid}/summaries").json()
    assert listed["modes"]["concise"]["status"] == "generating"
    assert listed["modes"]["detailed"]["status"] == "not_generated"


def test_failure_belongs_to_the_mode_that_failed(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    provider.fail_summary = True
    generate(user_client, mid, "standard", provider)
    listed = user_client.get(f"/api/meetings/{mid}/summaries").json()
    assert listed["modes"]["standard"]["status"] == "failed"
    assert listed["modes"]["concise"]["status"] == "generated"


def test_unknown_mode_stays_legacy_and_is_never_duplicated(user_client):
    with DB() as db:
        row = Meeting(user_id=user_client.user_id, title="Old", summary_version=1, status="ready")
        db.add(row)
        db.flush()
        from backend.tests.test_sumx import MINUTES

        db.add(Summary(meeting_id=row.id, version=1, transcript_version=1, content=MINUTES))
        db.commit()
        mid = row.id
    listed = user_client.get(f"/api/meetings/{mid}/summaries").json()
    assert listed["legacy"]["version"] == 1 and listed["narration_options"] == []
    assert not any(m["exists"] for m in listed["modes"].values())


def test_migration_assigns_only_certain_modes_and_can_rerun():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from backend.config import ROOT

    path = Path(ROOT) / "backend" / "migrations" / "versions" / "0012_independent_summaries.py"
    spec = importlib.util.spec_from_file_location("migration_0012", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE meetings (id TEXT, detail TEXT)"))
        bind.execute(sa.text("CREATE TABLE summaries (id TEXT, meeting_id TEXT, version INT, detail TEXT)"))
        bind.execute(sa.text("CREATE TABLE jobs (meeting_id TEXT, state TEXT, kind TEXT, payload TEXT, created REAL)"))
        bind.execute(sa.text("INSERT INTO meetings VALUES ('known','standard'),('edited','standard')"))
        bind.execute(
            sa.text(
                "INSERT INTO summaries VALUES ('a','known',1,NULL),('b','known',2,NULL),"
                "('c','edited',1,NULL),('d','edited',2,NULL)"
            )
        )
        bind.execute(
            sa.text(
                """INSERT INTO jobs VALUES ('known','completed','pipeline','{"detail":"standard"}',1),"""
                """('known','completed','summarize','{"detail":"concise"}',2),"""
                """('edited','completed','pipeline','{"detail":"standard"}',1)"""
            )
        )

        class Batch:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def create_index(self, *args):
                pass

        class Op:
            get_bind = staticmethod(lambda: bind)
            batch_alter_table = staticmethod(lambda name: Batch())

        module.op = Op
        for _ in range(2):
            module.upgrade()
        rows = dict(bind.execute(sa.text("SELECT id, detail FROM summaries")).all())
    assert rows == {"a": "standard", "b": "concise", "c": None, "d": None}


def test_other_users_cannot_read_generate_or_narrate(user_client, provider):
    mid = new_meeting(user_client.user_id)
    generate(user_client, mid, "concise", provider)
    with DB() as db:
        other = User(email="summary-other@example.edu", name="Other")
        db.add(other)
        db.flush()
        meeting = db.get(Meeting, mid)
        meeting.user_id = other.id
        db.commit()
    for response in (
        user_client.get(f"/api/meetings/{mid}/summaries"),
        user_client.get(f"/api/meetings/{mid}/summaries/concise"),
        user_client.get(f"/api/meetings/{mid}/narration-options"),
        user_client.post(f"/api/meetings/{mid}/summaries/concise/generate", json={}, headers={"Idempotency-Key": "foreign-generate-12345"}),
        user_client.post(
            f"/api/meetings/{mid}/narrations",
            json={"voice_type": "preset", "preset_voice_id": "gb_jane_neutral", "summary_mode": "concise"},
            headers={"Idempotency-Key": "foreign-narrate-123456"},
        ),
    ):
        assert response.status_code == 404
    assert user_client.get(f"/api/meetings/{mid}/summaries/invented").status_code in (404, 422)
