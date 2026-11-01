import csv
import io
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import httpx
import pytest
from sqlalchemy import create_engine, select, text
from backend import analytics
from backend.jobs import account
from backend.models import AllowedEmail, DB, Job, Meeting, Summary, Transcript, Usage, User
from backend.jobs import enqueue, process_job
from backend.provider import OpenRouter

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def clock(monkeypatch):
    # Patch the module reference, not Python's shared time module used by sessions.
    class Clock:
        @staticmethod
        def time():
            return NOW

    monkeypatch.setattr(analytics, "time", Clock)


def add_usage(db, m, amount, op="llm", **kwargs):
    u = Usage(
        meeting_id=m.id,
        user_id=m.user_id,
        operation=op,
        model="private-model-" + op,
        cost=amount,
        cost_source="provider_reported" if amount is not None else "unavailable",
        cost_status="actual" if amount is not None else "pending",
        created=NOW - 3600,
        **kwargs,
    )
    db.add(u)
    return u


def test_user_costs_exact_combined_scoped_and_pending(user_client, clock):
    with DB() as db:
        owner = db.get(User, user_client.user_id)
        owner.created = NOW - 100 * 86400
        m = Meeting(
            user_id=owner.id,
            title="College meeting",
            duration=3665,
            status="ready",
            created=NOW - 3600,
            summary_version=1,
        )
        stranger = User(email="private@college.edu", name="Other")
        db.add_all([m, stranger])
        db.flush()
        add_usage(db, m, "0.1", "stt")
        add_usage(db, m, "0.2", "llm")
        add_usage(db, m, None, "tts")
        add_usage(db, m, "5", "llm", excluded_as_duplicate=True)
        db.add(Summary(meeting_id=m.id, version=1, transcript_version=1, content={}, created=NOW - 1800))
        other = Meeting(user_id=stranger.id, title="Other private title", created=NOW)
        db.add(other)
        db.flush()
        add_usage(db, other, "100", "llm")
        db.commit()
        meeting_id = m.id
    result = user_client.get("/api/dashboard").json()
    assert result["stats"]["cost"]["actual_usd"] == "0.300000000000"
    assert result["stats"]["cost"]["pending_requests"] == 1
    assert result["meetings"][0]["cost"]["known_usd"] == "0.300000000000"
    assert result["spending"]["change_percent"] is None
    assert result["stats"]["meetings"] == 1 and result["stats"]["seconds"] == 3665
    assert result["stats"]["weekly"]["summaries_delta"] == 1
    assert "private-model" not in str(result) and "Other private title" not in str(result)
    assert "operations" not in result and len(result["spending"]["daily"]) == 7
    with DB() as db:
        assert (
            db.execute(text("SELECT typeof(cost) FROM api_usage WHERE cost IS NOT NULL LIMIT 1")).scalar()
            == "text"
        )
    history = user_client.get("/api/meetings?sort=oldest&summaries_only=true").json()
    assert history["meetings"][0]["id"] == meeting_id
    assert (
        user_client.get("/api/analytics/spending?days=90").json()["totals"]["known_usd"] == "0.300000000000"
    )
    for path in [
        "/api/admin/analytics/costs",
        "/api/admin/analytics/users/" + user_client.user_id + "/meetings",
        "/api/admin/analytics/export",
    ]:
        assert user_client.get(path).status_code == 403


def test_provider_request_dedup_and_repeated_generation_accumulation(user_client):
    with DB() as db:
        m = Meeting(user_id=user_client.user_id, title="Repeated generations")
        db.add(m)
        db.commit()
        account(
            db,
            m,
            "llm",
            "test",
            {"usage": {"cost": Decimal("0.000000000123")}, "id": "gen-one"},
            httpx.Response(200),
        )
        account(
            db,
            m,
            "llm",
            "test",
            {"usage": {"cost": Decimal("0.000000000123")}, "id": "gen-one"},
            httpx.Response(200),
        )
        account(
            db,
            m,
            "llm",
            "test",
            {"usage": {"cost": Decimal("0.000000000456")}, "id": "gen-two"},
            httpx.Response(200),
        )
        account(db, m, "tts", "test", {}, httpx.Response(200, headers={"x-generation-id": "gen-three"}))
        account(
            db,
            m,
            "tts",
            "test",
            {"usage": {"cost": Decimal(".01")}},
            httpx.Response(200, headers={"x-generation-id": "gen-three"}),
        )
        assert len(db.scalars(select(Usage)).all()) == 3
        assert analytics.cost_total(db, Usage.user_id == m.user_id)["known_usd"] == "0.010000000579"
        assert analytics.cost_total(db)["pending_requests"] == 0


def test_admin_filtered_analytics_expansion_and_csv(admin_client, clock):
    with DB() as db:
        student = User(email="one@college.edu", name="=Unsafe Spreadsheet Name", created=NOW - 100 * 86400)
        db.add(student)
        db.add(AllowedEmail(email=student.email))
        db.flush()
        current = Meeting(
            user_id=student.id, title="Library planning", duration=1800, status="ready", created=NOW - 100
        )
        old = Meeting(
            user_id=student.id, title="Old meeting", duration=400, status="ready", created=NOW - 60 * 86400
        )
        db.add_all([current, old])
        db.flush()
        add_usage(db, current, ".1", "stt")
        add_usage(db, current, ".2", "llm")
        add_usage(db, current, None, "tts")
        estimate = add_usage(db, current, ".05", "tts")
        estimate.cost_status = "estimated"
        oldcost = add_usage(db, old, "3", "llm")
        oldcost.created = NOW - 60 * 86400
        db.commit()
        user_id = student.id
    response = admin_client.get("/api/admin/analytics/costs?days=7&q=one@college.edu")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["totals"]["known_usd"] == "0.350000000000"
    assert data["totals"]["estimated_usd"] == "0.050000000000"
    assert data["operations"]["stt"]["actual_usd"] == "0.100000000000"
    assert data["completed_meetings"] == 1 and data["active_participants"] == 1
    assert data["participants"][0]["seconds"] == 1800
    rows = admin_client.get(f"/api/admin/analytics/users/{user_id}/meetings?days=7").json()
    assert len(rows) == 1 and rows[0]["title"] == "Library planning"
    csv_result = admin_client.get("/api/admin/analytics/export?days=7")
    parsed = list(csv.DictReader(io.StringIO(csv_result.text)))
    assert len(parsed) == 4
    assert parsed[0]["user_name"].startswith("'=")
    assert {p["cost_status"] for p in parsed} == {"actual", "pending", "estimated"}
    assert len(admin_client.get("/api/admin/analytics/costs?days=90").json()["spending"]["daily"]) == 90


def test_previous_period_change_and_empty_ranges(user_client, clock):
    with DB() as db:
        m = Meeting(user_id=user_client.user_id, title="Period costs", created=NOW)
        db.add(m)
        db.flush()
        add_usage(db, m, ".1")
        previous = add_usage(db, m, ".2")
        previous.created = NOW - 7 * 86400 - 3600
        future = add_usage(db, m, "10")
        future.created = NOW + 86400
        db.commit()
    data = user_client.get("/api/analytics/spending?days=7").json()
    assert data["change_percent"] == -50.0
    assert data["totals"]["known_usd"] == "0.100000000000"
    assert user_client.get("/api/analytics/spending?days=8").status_code == 422


def test_money_migration_preserves_history_and_marks_duplicates(tmp_path):
    root = Path(__file__).resolve().parents[2]
    url = "sqlite:///" + (tmp_path / "cost-migration.db").as_posix()
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0002"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
    )
    engine = create_engine(url)
    with engine.begin() as db:
        for i, cost in enumerate([0.1, 0.1, None]):
            db.execute(
                text(
                    "INSERT INTO api_usage (id,meeting_id,user_id,operation,model,generation_id,cost,cost_source,units,created) VALUES (:id,'meeting','user','llm','test',:gen,:cost,'provider_reported','{}',:created)"
                ),
                {"id": str(i), "gen": "same-gen" if i < 2 else "unknown-gen", "cost": cost, "created": i},
            )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
    )
    with engine.connect() as db:
        rows = db.execute(
            text("SELECT cost,cost_status,excluded_as_duplicate FROM api_usage ORDER BY id")
        ).all()
        assert len(rows) == 3
        assert rows[0] == ("0.100000000000", "actual", 0)
        assert rows[1] == ("0.100000000000", "actual", 1)
        assert rows[2] == (None, "pending", 0)
    engine.dispose()


def test_billed_but_malformed_summary_keeps_its_cost(user_client):
    with DB() as db:
        m = Meeting(user_id=user_client.user_id, title="Malformed billed response", transcript_version=1)
        db.add(m)
        db.flush()
        db.add(Transcript(meeting_id=m.id, version=1, text="Discuss the library schedule.", segments=[]))
        job_id = enqueue(db, m, "summarize", "malformed-summary-test").id
    router = OpenRouter(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={
                        "id": "billed-malformed",
                        "usage": {"cost": 0.12345678},
                        "choices": [{"message": {"content": "invalid minutes"}}],
                    },
                )
            )
        )
    )
    process_job(job_id, router)
    with DB() as db:
        assert db.get(Job, job_id).state == "failed"
        assert db.scalar(select(Transcript)).text
        assert analytics.cost_total(db)["actual_usd"] == "0.123456780000"
