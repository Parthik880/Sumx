import time
from sqlalchemy import select
from backend.models import DB, Job, Meeting, SttState


def test_meeting_and_polling_status_explain_provider_cooldown(user_client, monkeypatch):
    now = time.time()
    monkeypatch.setattr('backend.main.time.time', lambda: now)
    with DB() as db:
        meeting = Meeting(user_id=user_client.user_id, title='Cooldown test', status='transcribing')
        db.add(meeting)
        db.flush()
        job = Job(meeting_id=meeting.id, kind='pipeline', request_key='cooldown-test',
                  state='running', stage='transcribing', completed=2, total=15)
        db.add(job)
        db.add(SttState(id=1, level=2, updated=now, control={'paused_until': now + 120}))
        db.commit()
        mid = meeting.id
    for endpoint in (f'/api/meetings/{mid}', f'/api/meetings/{mid}/status'):
        response = user_client.get(endpoint)
        assert response.status_code == 200
        job = response.json()['job']
        assert job['completed'] == 2 and job['total'] == 15
        assert job['retry_after_seconds'] == 120
    with DB() as db:
        db.get(SttState, 1).control = {'paused_until': now - 1}
        db.commit()
    assert user_client.get(f'/api/meetings/{mid}/status').json()['job']['retry_after_seconds'] == 0
    with DB() as db:
        db.get(SttState, 1).control = {'paused_until': now + 120}
        db.scalar(select(Job).where(Job.meeting_id == mid)).stage = 'summarizing'
        db.commit()
    assert user_client.get(f'/api/meetings/{mid}/status').json()['job']['retry_after_seconds'] == 0
