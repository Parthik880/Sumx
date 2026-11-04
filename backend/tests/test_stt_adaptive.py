"""Adaptive concurrency, error classification and shared budget, with no network or charges."""

import asyncio
import random
import httpx
import pytest
from sqlalchemy import func, select
from backend.config import settings
from backend.jobs import process_job
from backend.models import DB, Job, Meeting, SttChunk, SttLease, Usage
from backend.provider import OpenRouter, ProviderError, transcription_failure
from backend.services.stt_control import AdaptiveLimit, GlobalBudget, preferred_level, remember_level
from backend.services import stt_pipeline
from backend.services.stt_pipeline import merge_parts, transcribe_chunks

real_sleep = asyncio.sleep


@pytest.fixture
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "stt_initial_concurrency", 12)
    monkeypatch.setattr(settings, "stt_increase_successes", 2)
    monkeypatch.setattr(settings, "stt_increase_cooldown_seconds", 0)

    async def no_sleep(delay):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    def fake_extract(source, target, offset, duration):
        target.write_bytes(b"ID3" + str(offset).encode())

    monkeypatch.setattr(stt_pipeline, "extract", fake_extract)
    return tmp_path


def router_for(handler):
    return OpenRouter(
        httpx.Client(transport=httpx.MockTransport(handler)), async_transport=httpx.MockTransport(handler)
    )


def ok(index):
    return httpx.Response(
        200,
        json={"text": f"chunk {index}", "segments": [{"text": f"chunk {index}", "start": 1, "end": 2}]},
        headers={"x-generation-id": f"adaptive-{index}-{random.random()}"},
    )


def index_of(request):
    return int(request.content.split(b"ID3")[1].split(b"\r\n")[0]) // settings.audio_chunk_seconds


def run(handler, folder, chunks, stored=None):
    stored = {} if stored is None else stored
    stats = {}

    def save(chunk, part, data, response, seconds):
        assert chunk["index"] not in stored
        stored[chunk["index"]] = part

    error = None
    try:
        asyncio.run(
            transcribe_chunks(
                router_for(handler), folder / "s.mp4", folder, chunks * 180, None, stored, save, stats
            )
        )
    except ProviderError as exc:
        error = exc
    return stored, stats, error


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def limit_at(start=12, **options):
    return AdaptiveLimit([12, 10, 8, 6, 4, 2, 1], start, clock=Clock(), **options)


def test_levels_step_down_in_order_to_one_and_stop():
    limit, seen = limit_at(), []
    for _ in range(8):
        limit.throttled(limit.epoch, 0)
        seen.append(limit.limit)
    assert seen == [10, 8, 6, 4, 2, 1, 1, 1]


def test_one_burst_costs_one_step():
    limit = limit_at()
    epoch = limit.epoch
    for _ in range(12):
        limit.throttled(epoch, 0)
    assert limit.limit == 10


def test_single_server_error_does_not_reduce_but_repeats_do(monkeypatch):
    limit = limit_at()
    limit.problem(limit.epoch)
    limit.problem(limit.epoch)
    assert limit.limit == 12
    limit.problem(limit.epoch)
    assert limit.limit == 10


def test_rising_latency_reduces():
    limit = limit_at()
    for _ in range(12):
        limit.success(limit.epoch, 10)
    for _ in range(12):
        limit.success(limit.epoch, 25)
    assert limit.limit == 10


def test_increase_needs_successes_and_cooldown(monkeypatch):
    monkeypatch.setattr(settings, "stt_increase_successes", 12)
    monkeypatch.setattr(settings, "stt_increase_cooldown_seconds", 60)
    limit = limit_at(8)
    limit.throttled(limit.epoch, 0)
    assert limit.limit == 6
    for _ in range(11):
        limit.success(limit.epoch, 10)
    assert limit.limit == 6
    limit.success(limit.epoch, 10)
    assert limit.limit == 6, "cooldown has not passed"
    limit.clock.now += 61
    limit.success(limit.epoch, 10)
    assert limit.limit == 8 and limit.stable_level == 6


def test_worse_probe_reverts_and_holds_at_the_stable_level(monkeypatch):
    monkeypatch.setattr(settings, "stt_increase_successes", 12)
    monkeypatch.setattr(settings, "stt_increase_cooldown_seconds", 60)
    limit = limit_at(8)
    limit.throttled(limit.epoch, 0)
    limit.clock.now += 61
    for _ in range(12):
        limit.success(limit.epoch, 10)
    assert limit.limit == 8
    limit.throttled(limit.epoch, 0)
    assert limit.limit == 6
    limit.clock.now += 121
    for _ in range(24):
        limit.success(limit.epoch, 10)
    assert limit.limit == 6, "must not retry the failed level straight away"
    limit.clock.now += 300
    for _ in range(12):
        limit.success(limit.epoch, 10)
    assert limit.limit == 8


def test_fixed_mode_never_moves():
    limit = limit_at(8, adaptive=False)
    for _ in range(5):
        limit.throttled(limit.epoch, 0)
    assert limit.limit == 8


def test_conservative_start_can_probe_above_its_starting_level(monkeypatch):
    monkeypatch.setattr(settings, "stt_increase_successes", 12)
    limit = limit_at(4)
    limit.clock.now += 61
    for _ in range(12):
        limit.success(limit.epoch, 2)
    assert limit.limit == 6 and limit.stable_level == 4


def test_date_retry_after_and_message_only_402_is_permanent():
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    from backend.provider import retry_after

    response = httpx.Response(
        429, headers={"retry-after": format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90))}
    )
    assert 88 <= retry_after(response) <= 90
    assert (
        transcription_failure(httpx.Response(402, json={"error": {"message": "openrouter_in_flight_budget"}}))
        == "credits"
    )


def test_stt_pool_is_independent_of_the_preset_pool(monkeypatch):
    monkeypatch.setattr(settings, "stt_initial_concurrency", 1)
    monkeypatch.setattr(settings, "preset_tts_concurrency", 3)

    async def check():
        async with OpenRouter().async_client() as preset, OpenRouter().async_client(capacity=12) as stt:
            assert preset._transport._pool._max_connections == 3
            assert stt._transport._pool._max_connections == 12

    asyncio.run(check())
    assert (
        transcription_failure(
            httpx.Response(402, json={"error": {"metadata": {"limit_source": "openrouter_in_flight_budget"}}})
        )
        == "credits"
    )


def test_throttle_is_shared_between_worker_budgets(monkeypatch):
    from backend.models import SttState

    monkeypatch.setattr(settings, "stt_initial_concurrency", 4)
    monkeypatch.setattr(settings, "stt_concurrency_levels", [4, 2, 1])
    a, b = GlobalBudget("worker-a"), GlobalBudget("worker-b")
    first, second = a.try_take(), b.try_take()
    a.feedback(first, ProviderError("throttled", kind="rate_limit", retry_after=120))
    b.feedback(second, ProviderError("same burst", kind="rate_limit", retry_after=120))
    with DB() as db:
        state = db.get(SttState, 1)
        assert state.level == 2, "stale in-flight responses must not reduce twice"
    a.release(first)
    b.release(second)
    assert GlobalBudget("worker-c").try_take() is None


def test_shared_extraction_budget_is_separate_and_bounded(monkeypatch):
    monkeypatch.setattr(settings, "stt_extract_concurrency", 2)
    a, b, c = (GlobalBudget(str(i)) for i in range(3))
    first, second = a.try_take(True), b.try_take(True)
    assert first < 0 and second < 0 and c.try_take(True) is None
    assert c.try_take() >= 0
    a.release(first)
    assert c.try_take(True) is not None


def test_shared_capacity_recovers_after_stable_successes(monkeypatch):
    from backend.models import SttState

    monkeypatch.setattr(settings, "stt_initial_concurrency", 2)
    monkeypatch.setattr(settings, "stt_concurrency_levels", [4, 2, 1])
    monkeypatch.setattr(settings, "stt_increase_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "stt_increase_successes", 12)
    budget = GlobalBudget("stable")
    for _ in range(12):
        slot = budget.try_take()
        budget.feedback(slot, seconds=2)
        budget.release(slot)
    with DB() as db:
        assert db.get(SttState, 1).level == 4
    monkeypatch.setattr(settings, "stt_initial_concurrency", 12)
    assert preferred_level() == 2, "a probe has not yet earned becoming the remembered start"


def test_late_release_cannot_delete_a_reissued_lease(monkeypatch):
    monkeypatch.setattr(settings, "stt_global_concurrency", 1)
    old, new = GlobalBudget("same-job"), GlobalBudget("same-job")
    slot = old.try_take()
    with DB() as db:
        db.get(SttLease, slot).expires = 0
        db.commit()
    assert new.try_take() == slot
    old.release(slot)
    with DB() as db:
        assert db.get(SttLease, slot).owner == new.owner


def test_reduced_global_cap_waits_for_high_numbered_slots_to_drain(monkeypatch):
    from backend.models import SttState

    monkeypatch.setattr(settings, "stt_initial_concurrency", 4)
    monkeypatch.setattr(settings, "stt_concurrency_levels", [4, 2, 1])
    old, new = GlobalBudget("old"), GlobalBudget("new")
    slots = [old.try_take() for _ in range(4)]
    old.feedback(slots[0], ProviderError("limit", kind="rate_limit", retry_after=1))
    with DB() as db:
        state = db.get(SttState, 1)
        state.control = {**state.control, "paused_until": 0}
        db.commit()
    old.release(slots[0])
    assert new.try_take() is None
    old.release(slots[1])
    assert new.try_take() is None
    old.release(slots[2])
    assert new.try_take() is not None


def test_three_parallel_requests_share_capacity_and_back_off_to_two(monkeypatch):
    from backend.models import SttState

    monkeypatch.setattr(settings, "stt_initial_concurrency", 3)
    monkeypatch.setattr(settings, "stt_concurrency_levels", [4, 3, 2, 1])
    first, other = GlobalBudget("first"), GlobalBudget("other")
    slots = [first.try_take() for _ in range(3)]
    assert set(slots) == {0, 1, 2}
    assert other.try_take() is None
    first.feedback(slots[0], ProviderError("throttled", kind="rate_limit", retry_after=120))
    first.feedback(slots[1], ProviderError("same burst", kind="rate_limit", retry_after=120))
    with DB() as db:
        assert db.get(SttState, 1).level == 2
    for slot in slots:
        first.release(slot)
    assert other.try_take() is None, "provider cooldown is shared even after slots drain"


def test_large_plan_has_a_bounded_number_of_scheduler_tasks(fast):
    peaks = []

    def handler(request):
        peaks.append(len(asyncio.all_tasks()))
        return ok(index_of(request))

    stored, _, error = run(handler, fast, 100)
    assert error is None and len(stored) == 100 and max(peaks) < 20


def test_deadline_bounds_an_in_flight_request(fast, monkeypatch):
    monkeypatch.setattr(settings, "stt_job_deadline_seconds", 0.03)

    async def handler(request):
        await asyncio.Event().wait()

    _, stats, error = run(handler, fast, 1)
    assert error and "total job timeout" in str(error)
    assert stats["failed_chunks"] == [0]


def test_global_budget_is_safe_across_processes(monkeypatch):
    import os
    import subprocess
    import sys
    from backend.config import ROOT

    script = """
import time
from backend.services.stt_control import GlobalBudget
from backend.models import DB,SttLease
from sqlalchemy import select,func
b=GlobalBudget('process')
for i in range(5):
 slot=None
 while slot is None:
  slot=b.try_take()
  if slot is None: time.sleep(.01)
 with DB() as db:
  assert db.scalar(select(func.count()).select_from(SttLease).where(SttLease.slot>=0))<=2
 time.sleep(.03)
 b.release(slot)
print('ok')
"""
    env = {
        **os.environ,
        "STT_INITIAL_CONCURRENCY": "2",
        "STT_GLOBAL_CONCURRENCY": "2",
        "STT_ADAPTIVE": "false",
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(4)
    ]
    try:
        for process in processes:
            out, error = process.communicate(timeout=30)
            assert process.returncode == 0, error.decode()
            assert out.strip() == b"ok"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()


@pytest.mark.parametrize(
    "status, body, kind",
    [
        (
            402,
            '{"error":{"code":402,"metadata":{"limit_source":"openrouter_in_flight_budget"}}}',
            "in_flight",
        ),
        (402, '{"error":{"message":"Insufficient credits"}}', "credits"),
        (402, '{"error":{"message":"Key limit exceeded"}}', "credits"),
        (429, "", "rate_limit"),
        (503, "", "server"),
        (400, "", "fatal"),
        (401, "", "fatal"),
    ],
)
def test_classification(status, body, kind):
    assert transcription_failure(httpx.Response(status, text=body, headers={"retry-after": "2"})) == kind


def test_real_credit_failure_stops_at_once_and_is_clear(fast):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})

    stored, stats, error = run(handler, fast, 30)
    assert "credits" in str(error) and not stored
    assert len(calls) <= settings.stt_initial_concurrency, "must not keep trying"
    assert stats["responses"]["credits"] == len(calls)


def test_in_flight_rejection_recovers_without_user_error_and_lowers_the_limit(fast):
    seen = {}

    def handler(request):
        index = index_of(request)
        seen[index] = seen.get(index, 0) + 1
        if seen[index] == 1 and index < 8:
            return httpx.Response(
                402,
                json={"error": {"metadata": {"limit_source": "openrouter_in_flight_budget"}}},
                headers={"retry-after": "2"},
            )
        return ok(index)

    stored, stats, error = run(handler, fast, 20)
    assert error is None and sorted(stored) == list(range(20))
    assert stats["concurrency_changes"][0]["reason"] == "throttled"
    assert stats["responses"]["in_flight"] == 8


def test_retries_are_bounded_and_finished_chunks_are_kept(fast):
    calls = []

    def handler(request):
        index = index_of(request)
        if index == 3:
            calls.append(1)
            return httpx.Response(503)
        return ok(index)

    stored, stats, error = run(handler, fast, 6)
    assert len(calls) == settings.stt_chunk_attempts
    assert sorted(stored) == [0, 1, 2, 4, 5] and stats["failed_chunks"] == [3]
    assert "5 of 6 parts are saved" in str(error) and "resume transcription" in str(error)
    resumed = []

    def healthy(request):
        resumed.append(index_of(request))
        return ok(index_of(request))

    stored, _, error = run(healthy, fast, 6, stored)
    assert error is None and resumed == [3] and sorted(stored) == list(range(6))


def test_deadline_stops_retrying(fast, monkeypatch):
    monkeypatch.setattr(settings, "stt_job_deadline_seconds", 1)
    monkeypatch.setattr(stt_pipeline, "backoff", lambda attempt, wait: 5)
    _, _, error = run(lambda request: httpx.Response(429), fast, 2)
    assert error and "resume transcription" in str(error)


def test_out_of_order_and_retried_chunks_merge_with_correct_timestamps(fast):
    seen = {}

    async def handler(request):
        index = index_of(request)
        seen[index] = seen.get(index, 0) + 1
        if seen[index] == 1 and index % 5 == 0:
            return httpx.Response(429)
        await real_sleep(random.random() / 100)
        return ok(index)

    stored, _, error = run(handler, fast, 25)
    text, segments = merge_parts(stored)
    assert error is None and text.split("\n") == [f"chunk {i}" for i in range(25)]
    assert [s["start"] for s in segments] == [i * 180 + 1 for i in range(25)]
    assert [s["end"] for s in segments] == [i * 180 + 2 for i in range(25)]


def test_temporary_files_are_removed_after_failure(fast):
    run(lambda request: httpx.Response(503), fast, 4)
    assert not list(fast.glob("chunk-*.mp3"))


def test_budget_is_shared_and_expired_leases_return(monkeypatch):
    monkeypatch.setattr(settings, "stt_global_concurrency", 2)
    with DB() as db:
        db.query(SttLease).delete()
        db.commit()
    first, second = GlobalBudget("job-a"), GlobalBudget("job-b")
    a, b = first.try_take(), second.try_take()
    assert {a, b} == {0, 1} and GlobalBudget("job-c").try_take() is None
    first.release(a)
    assert GlobalBudget("job-c").try_take() == a
    with DB() as db:
        for lease in db.scalars(select(SttLease)):
            lease.expires = 0
        db.commit()
    assert GlobalBudget("job-d").try_take() is not None
    with DB() as db:
        db.query(SttLease).delete()
        db.commit()


def test_future_jobs_start_at_the_remembered_level(monkeypatch):
    monkeypatch.setattr(settings, "stt_initial_concurrency", 12)
    remember_level(8)
    assert preferred_level() == 8
    monkeypatch.setattr(settings, "stt_initial_concurrency", 6)
    assert preferred_level() == 6
    remember_level(12)


def test_checkpoints_cost_and_resume_through_the_worker(user_client, fast, tmp_path, monkeypatch):
    source = settings.storage_dir / "adaptive-source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"media")
    with DB() as db:
        meeting = Meeting(
            user_id=user_client.user_id,
            title="Adaptive",
            original_path=str(source),
            duration=540.0,
            status="queued",
        )
        db.add(meeting)
        db.flush()
        job = Job(meeting_id=meeting.id, kind="transcribe", request_key="adaptive-worker", state="running")
        db.add(job)
        db.commit()
        mid, jid = meeting.id, job.id
    broken, calls = [True], []

    async def handler(request):
        index = index_of(request)
        calls.append(index)
        if index == 1 and broken[0]:
            # Make this a checkpoint/resume test, independent of which extraction finishes first.
            for _ in range(1000):
                with DB() as db:
                    saved = db.scalar(
                        select(SttChunk.id).where(SttChunk.job_id == jid, SttChunk.chunk_index == 0)
                    )
                if saved:
                    break
                await real_sleep(0.005)
            assert saved
            return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})
        response = ok(index)
        response.headers["x-request-id"] = f"request-{index}"
        return response

    router = router_for(handler)
    process_job(jid, router)
    with DB() as db:
        assert db.get(Job, jid).state == "failed" and "credits" in db.get(Job, jid).error
        rows = db.scalars(select(SttChunk).where(SttChunk.job_id == jid)).all()
        finished_before = sorted(r.chunk_index for r in rows)
        assert 0 in finished_before and 1 not in finished_before
        assert rows[0].request_id and rows[0].status == "completed" and rows[0].segments
        assert db.get(Job, jid).payload["stt_metrics"]["failed_chunks"] == [1]
        charges = db.scalar(select(func.count()).select_from(Usage).where(Usage.meeting_id == mid))
        assert charges == len(finished_before)
    broken[0] = False
    calls.clear()
    with DB() as db:
        db.get(Job, jid).state = "running"
        db.commit()
    process_job(jid, router)
    assert sorted(calls) == sorted(set(range(3)) - set(finished_before))
    with DB() as db:
        assert db.get(Job, jid).state == "completed"
        assert db.scalar(select(func.count()).select_from(Usage).where(Usage.meeting_id == mid)) == 3
        assert all(r.text == "" for r in db.scalars(select(SttChunk).where(SttChunk.job_id == jid)))
    assert user_client.delete(f"/api/meetings/{mid}").status_code in (200, 204)
    with DB() as db:
        assert not db.scalar(select(func.count()).select_from(SttChunk).where(SttChunk.meeting_id == mid))
