import asyncio
import threading
import time
import httpx
import pytest
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.orm import Session as OrmSession
from types import SimpleNamespace
from sqlalchemy import event, func, select
from backend import jobs
from backend.config import settings
from backend.models import DB, Job, Meeting, Summary, Transcript
from backend.provider import OpenRouter, ProviderError
from backend.summary_modes import build_summary
from backend.tests.test_sumx import MINUTES, MockRouter


def test_notes_are_parallel_ordered_checkpointed_and_never_report_final_completion(monkeypatch):
    monkeypatch.setattr(settings, 'summary_chunk_chars', 100)
    monkeypatch.setattr(settings, 'summary_note_concurrency', 2)
    owner_thread = threading.get_ident()
    active = peak = 0
    lock = threading.Lock()
    saved, phases, callbacks = [], [], []

    class Router:
        def summarize(self, text, mode, synthesis=False, **options):
            nonlocal active, peak
            if options.get('notes') and not synthesis:
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(.015 if text.startswith('first') else .005)
                with lock:
                    active -= 1
            return {**MINUTES, 'executive_summary': text[:10]}, {}, None

    def callback(*args):
        callbacks.append(threading.get_ident())

    content, metrics = build_summary(Router(), 'first ' * 30 + 'second ' * 30, 'standard', callback,
        on_part=lambda parts, done, total: saved.append((list(parts), done, total)),
        on_stage=lambda *args: phases.append(args), on_metrics=callback)
    assert peak == 2
    assert callbacks and all(ident == owner_thread for ident in callbacks)
    assert saved and all(part is not None for part in saved[-1][0])
    assert [phase[0] for phase in phases][-3:] == ['summary_synthesis', 'summary_finalizing', 'summary_saving']
    assert all(done < total for _, done, total in phases)
    assert phases[-1][1] == phases[-1][2] - 1
    assert metrics['timings']['notes_seconds'] > 0 and metrics['records_saved'] == 0
    assert content['title'] == MINUTES['title']


def new_job(owner):
    with DB() as db:
        meeting = Meeting(user_id=owner, title='Progress test', status='queued', transcript_version=1)
        db.add(meeting)
        db.flush()
        db.add(Transcript(meeting_id=meeting.id, version=1, text='We reviewed the library plan. ' * 500, segments=[]))
        job = Job(meeting_id=meeting.id, user_id=owner, kind='summarize', state='running',
                  request_key='progress-test', payload={'detail': 'standard'})
        db.add(job)
        db.commit()
        return meeting.id, job.id


def test_completed_and_saved_summary_become_visible_atomically_and_only_once(user_client):
    mid, jid = new_job(user_client.user_id)
    router = MockRouter()
    observed = []

    def before_save(mapper, connection, target):
        with DB() as observer:
            row = observer.execute(select(Job.state, Job.completed, Job.total).where(Job.id == jid)).one()
        observed.append(row)
        assert row.state != 'completed' and row.completed < row.total

    event.listen(Summary, 'before_insert', before_save)
    try:
        jobs.process_job(jid, router)
        jobs.process_job(jid, router)
    finally:
        event.remove(Summary, 'before_insert', before_save)
    assert len(observed) == 1
    with DB() as db:
        job = db.get(Job, jid)
        assert job.state == 'completed' and job.completed == job.total
        assert db.scalar(select(func.count()).select_from(Summary).where(Summary.meeting_id == mid)) == 1
        assert job.payload['summary_metrics']['records_saved'] == 1
        assert job.payload['summary_metrics']['timings']['database_save_seconds'] >= 0
    status = user_client.get(f'/api/meetings/{mid}/status').json()['job']
    assert status['progress_percent'] == 100


def test_save_failure_reuses_validated_result_without_another_llm_call(user_client):
    mid, jid = new_job(user_client.user_id)
    router = MockRouter()

    def fail(mapper, connection, target):
        raise RuntimeError('Injected database write failure')

    event.listen(Summary, 'before_insert', fail)
    try:
        jobs.process_job(jid, router)
    finally:
        event.remove(Summary, 'before_insert', fail)
    with DB() as db:
        job = db.get(Job, jid)
        assert job.state == 'failed' and job.checkpoint['summary_result']
    calls = list(router.calls)
    assert user_client.post(f'/api/meetings/{mid}/jobs/{jid}/retry').status_code == 200
    jobs.process_job(jid, router)
    assert router.calls == calls
    with DB() as db:
        assert db.get(Job, jid).state == 'completed'
        assert db.scalar(select(func.count()).select_from(Summary).where(Summary.meeting_id == mid)) == 1


def test_summary_request_has_a_total_deadline_not_only_a_socket_timeout(monkeypatch):
    monkeypatch.setattr(settings, 'summary_request_deadline_seconds', .03)
    monkeypatch.setattr(settings, 'openrouter_api_key', 'offline-test')
    calls, events = [], []

    async def handler(request):
        calls.append(1)
        await asyncio.Event().wait()

    async def check():
        router = OpenRouter()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with pytest.raises(ProviderError, match='deadline'):
                    await router._summary_request({'model': 'offline-test'}, client, events.append)
        finally:
            router.close()

    asyncio.run(check())
    assert len(calls) == 1 and events[-1]['event'] == 'deadline_exceeded'


def test_duplicate_concurrent_deliveries_save_only_one_summary(user_client, monkeypatch):
    mid, jid = new_job(user_client.user_id)
    barrier = threading.Barrier(2, timeout=5)

    def build(*args, **kwargs):
        kwargs['on_stage']('summary_saving', 2, 3)
        barrier.wait()
        return dict(MINUTES), {'work_total': 3, 'timings': {}}

    monkeypatch.setattr(jobs, 'build_summary', build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: jobs.process_job(jid, MockRouter()), range(2)))
    with DB() as db:
        assert db.scalar(select(func.count()).select_from(Summary).where(Summary.meeting_id == mid)) == 1
        assert db.get(Job, jid).state == 'completed'
        assert db.get(Job, jid).payload['summary_metrics']['records_saved'] == 1


def test_telemetry_failure_after_durable_save_cannot_mark_job_failed(user_client):
    mid, jid = new_job(user_client.user_id)
    failed = []

    def fail_after_commit(session):
        if not failed and any(isinstance(row, Job) and row.id == jid and row.state == 'completed'
                              for row in session.identity_map.values()):
            failed.append(True)
            raise RuntimeError('Telemetry unavailable after durable commit')

    event.listen(OrmSession, 'after_commit', fail_after_commit)
    try:
        jobs.process_job(jid, MockRouter())
    finally:
        event.remove(OrmSession, 'after_commit', fail_after_commit)
    with DB() as db:
        assert failed and db.get(Job, jid).state == 'completed'
        assert db.scalar(select(func.count()).select_from(Summary).where(Summary.meeting_id == mid)) == 1


def test_summary_retry_after_is_nonblocking_and_timed(monkeypatch):
    monkeypatch.setattr(settings, 'openrouter_api_key', 'offline-test')
    calls, delays, events = [], [], []

    async def no_wait(delay):
        delays.append(delay)

    async def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={'retry-after': '3'}) if len(calls) == 1 else httpx.Response(200, json={'ok': True})

    async def check():
        router = OpenRouter()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await router._summary_request({'model': 'offline-test'}, client, events.append)
        finally:
            router.close()

    monkeypatch.setattr('backend.provider.asyncio.sleep', no_wait)
    assert asyncio.run(check()).status_code == 200
    assert delays == [3] and any(e['event'] == 'retry_wait_finished' for e in events)
    assert 'request_started' in [e['event'] for e in events]


def test_windows_summary_wake_request_restores_previous_flags_on_failure(monkeypatch):
    from backend import summary_power
    calls = []
    previous = summary_power.ES_CONTINUOUS | 2

    class SetState:
        def __call__(self, flags):
            calls.append(flags)
            return previous

    monkeypatch.setattr(summary_power, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(summary_power.ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(SetThreadExecutionState=SetState()), raising=False)
    with pytest.raises(RuntimeError):
        with summary_power.summary_awake():
            raise RuntimeError('Injected provider failure')
    assert calls == [summary_power.ES_CONTINUOUS | summary_power.ES_SYSTEM_REQUIRED, previous]
