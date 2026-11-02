"""Spoken action items and summary modes, without any provider calls."""

import copy
import json
import pytest
from sqlalchemy import select
from backend.jobs import process_job
from backend.models import AudioOutput, DB, Job, Meeting, Summary, Transcript
from backend.schema import ActionItem, spoken_action, spoken_text
from backend.summary_modes import PROMPT_VERSION, build_summary, prompt_identity, target_words
from backend.tests.test_sumx import MINUTES, MockRouter


def item(task, owner=None, deadline=None):
    return ActionItem(task=task, owner=owner, deadline=deadline)


@pytest.mark.parametrize(
    "task,owner,deadline,spoken",
    [
        ("Prepare the annual budget report", None, None, "Prepare the annual budget report."),
        ("Prepare the annual budget report", "John", None, "John will prepare the annual budget report."),
        ("Submit the project proposal", None, "Friday", "Submit the project proposal by Friday."),
        ("Submit the project proposal", "John", "Friday", "John will submit the project proposal by Friday."),
        ("Submit the proposal.", "John", "by Friday", "John will submit the proposal by Friday."),
        ("Submit the proposal by Friday", None, "Friday", "Submit the proposal by Friday."),
        ("John to send the notes", "John", None, "John will send the notes."),
        ("to send the notes", "John", None, "John will send the notes."),
        ("NASA report review", "John", None, "John will handle this: NASA report review."),
        ("The venue booking", "Priya", None, "Priya will handle this: The venue booking."),
    ],
)
def test_action_sentences(task, owner, deadline, spoken):
    assert spoken_action(item(task, owner, deadline)) == spoken


@pytest.mark.parametrize(
    "missing",
    [
        None,
        "",
        "  ",
        "N/A",
        "n/a",
        "NA",
        "None",
        "null",
        "unknown",
        "Not specified",
        "TBD",
        "t.b.d.",
        "not provided",
        "unassigned",
        "No owner assigned",
        "Due date not provided",
        "-",
    ],
)
def test_placeholders_are_never_spoken(missing):
    text = spoken_action(item("Prepare the report", missing, missing))
    assert text == "Prepare the report."


def test_placeholder_clauses_inside_a_task_are_removed_but_real_words_stay():
    assert (
        spoken_action(item("Prepare the report. Owner not specified. Deadline not specified."))
        == "Prepare the report."
    )
    assert spoken_action(item("Prepare the report (no owner assigned)")) == "Prepare the report."
    kept = "Find an owner for the unknown vendor issue"
    assert spoken_action(item(kept)) == kept + "."


def test_narration_never_announces_missing_fields_and_keeps_stored_data():
    content = copy.deepcopy(MINUTES)
    content["action_items"] = [
        {"task": "Prepare the budget", "owner": None, "deadline": "unknown", "timestamp": None}
    ]
    content["follow_up"] = ["Owner not specified", "Confirm the room"]
    spoken = spoken_text(content)
    assert "Prepare the budget." in spoken and "Confirm the room" in spoken
    assert "not specified" not in spoken.lower() and "unknown" not in spoken.lower()
    assert content["action_items"][0]["deadline"] == "unknown"


def test_targets_scale_with_the_transcript_and_keep_the_mode_order():
    concise, standard, detailed = (target_words(m, 6500) for m in ("concise", "standard", "detailed"))
    assert standard == 618 and concise == 154 and detailed == 1082
    assert 0.2 <= concise / standard <= 0.3 and 1.5 <= detailed / standard <= 2
    assert target_words("standard", 100) < target_words("standard", 4000)


class Router:
    def __init__(self, words=30):
        self.calls, self.words = [], words

    def summarize(self, text, detail, synthesis=False, **options):
        self.calls.append((detail, synthesis, options))
        content = {**MINUTES, "executive_summary": "word " * self.words}
        if options.get("correction"):
            content["executive_summary"] = "word " * 20
        return content, {}, None


def test_one_bounded_correction_only_when_far_over_budget():
    router = Router(words=2000)
    _, info = build_summary(router, "word " * 3000, "concise", lambda *a: None)
    assert info["corrected"] and len(router.calls) == 2
    assert router.calls[-1][2]["correction"]
    router = Router(words=5)
    _, info = build_summary(router, "word " * 3000, "concise", lambda *a: None)
    assert not info["corrected"] and len(router.calls) == 1


def test_short_results_are_not_padded():
    router = Router(words=5)
    _, info = build_summary(router, "word " * 3000, "detailed", lambda *a: None)
    assert len(router.calls) == 1 and info["summary_words"] < info["target_words"]


def test_prompts_and_token_budgets_differ_by_mode():
    from backend.provider import mode_instructions, summary_token_budget

    assert mode_instructions("concise", 100) != mode_instructions("detailed", 100)
    assert "100" in mode_instructions("concise", 100)
    assert summary_token_budget(100) < summary_token_budget(1500)


def meeting_with_transcript(owner):
    with DB() as db:
        row = Meeting(user_id=owner, title="Modes", transcript_version=1, status="ready")
        db.add(row)
        db.flush()
        db.add(Transcript(meeting_id=row.id, version=1, text="We reviewed the plan. " * 400, segments=[]))
        db.commit()
        return row.id


def summarize(client, mid, mode, key):
    response = client.post(
        f"/api/meetings/{mid}/summarize", json={"detail": mode}, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_mode_reaches_the_llm_and_each_request_generates(user_client):
    mid = meeting_with_transcript(user_client.user_id)
    provider = MockRouter()
    seen = []
    original = provider.summarize

    def spy(text, detail, synthesis=False, **options):
        seen.append(detail)
        return original(text, detail, synthesis, **options)

    provider.summarize = spy
    process_job(summarize(user_client, mid, "concise", "summary-concise-12345"), provider)
    process_job(summarize(user_client, mid, "detailed", "summary-detailed-1234"), provider)
    assert seen == ["concise", "detailed"]
    process_job(summarize(user_client, mid, "concise", "summary-concise-again"), provider)
    assert seen == ["concise", "detailed", "concise"]
    with DB() as db:
        rows = db.scalars(select(Summary).where(Summary.meeting_id == mid).order_by(Summary.version)).all()
        assert [r.detail for r in rows] == ["concise", "detailed", "concise"]
        assert rows[0].prompt_version == prompt_identity() and PROMPT_VERSION in rows[0].prompt_version
        assert db.get(Meeting, mid).summary_version == 3


def test_changed_transcript_is_not_served_from_an_old_summary(user_client):
    mid = meeting_with_transcript(user_client.user_id)
    provider = MockRouter()
    process_job(summarize(user_client, mid, "standard", "summary-standard-1234"), provider)
    with DB() as db:
        db.add(Transcript(meeting_id=mid, version=2, text="A different discussion. " * 300, segments=[]))
        db.get(Meeting, mid).transcript_version = 2
        db.commit()
    before = provider.calls.count("llm")
    process_job(summarize(user_client, mid, "standard", "summary-standard-5678"), provider)
    assert provider.calls.count("llm") == before + 1


def narrate(client, mid, key, provider):
    response = client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "preset", "preset_voice_id": "gb_jane_neutral"},
        headers={"Idempotency-Key": key},
    )
    process_job(response.json()["id"], provider)
    with DB() as db:
        job = db.get(Job, response.json()["id"])
        assert job.state == "completed", job.error
        return job.output_path


def test_each_mode_gets_its_own_audio_and_returning_to_a_mode_reuses_it(user_client):
    mid = meeting_with_transcript(user_client.user_id)
    provider = MockRouter()
    original = provider.summarize

    def by_mode(text, detail, synthesis=False, **options):
        content, data, response = original(text, detail, synthesis, **options)
        return {**content, "executive_summary": f"The {detail} overview of this meeting."}, data, response

    provider.summarize = by_mode
    process_job(summarize(user_client, mid, "concise", "audio-concise-123456"), provider)
    first = narrate(user_client, mid, "narrate-concise-12345", provider)
    process_job(summarize(user_client, mid, "detailed", "audio-detailed-12345"), provider)
    second = narrate(user_client, mid, "narrate-detailed-1234", provider)
    assert first != second
    spoken = provider.calls.count("tts")
    process_job(summarize(user_client, mid, "concise", "audio-concise-again1"), provider)
    assert narrate(user_client, mid, "narrate-concise-again", provider) == first
    assert provider.calls.count("tts") == spoken
    with DB() as db:
        versions = {
            a.summary_version for a in db.scalars(select(AudioOutput).where(AudioOutput.meeting_id == mid))
        }
        assert versions == {1, 2, 3}


def test_old_audio_with_announcements_is_not_reused(user_client):
    from backend.tests.test_voice_catalog import meeting

    mid = meeting(user_client.user_id)
    with DB() as db:
        db.add(
            AudioOutput(
                meeting_id=mid,
                summary_version=1,
                model="mistralai/voxtral-mini-tts-3-0",
                voice="gb_jane_neutral",
                voice_type="preset",
                path="old.mp3",
            )
        )
        db.commit()
    provider = MockRouter()
    output = narrate(user_client, mid, "narrate-after-format-1", provider)
    assert provider.calls.count("tts") >= 1 and not output.endswith("old.mp3")


def test_request_payload_keeps_json_safe_mode_metrics(user_client):
    mid = meeting_with_transcript(user_client.user_id)
    job_id = summarize(user_client, mid, "standard", "summary-metrics-12345")
    process_job(job_id, MockRouter())
    with DB() as db:
        metrics = db.get(Job, job_id).payload["summary_metrics"]
    assert json.dumps(metrics) and metrics["mode"] == "standard" and metrics["target_words"] > 0


def test_truncated_summary_retries_once_with_a_larger_budget_and_records_both_calls(monkeypatch):
    from backend.config import settings
    from backend.provider import OpenRouter, ProviderError
    import httpx

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    budgets = []

    def handler(request):
        body = json.loads(request.content)
        budgets.append(body["max_tokens"])
        if len(budgets) == 1:
            choice = {"finish_reason": "length", "message": {"content": '{"title": "cut'}}
        else:
            choice = {"finish_reason": "stop", "message": {"content": json.dumps(MINUTES)}}
        return httpx.Response(200, json={"choices": [choice], "usage": {"cost": 0.001}})

    recorded = []
    router = OpenRouter(httpx.Client(transport=httpx.MockTransport(handler)))
    content, info = build_summary(router, "We met. " * 2000, "standard", lambda *a: recorded.append(a[0]))
    assert budgets[1] == budgets[0] * 2 and len(recorded) == 2 and info["llm_calls"] == 2
    assert content["title"] == MINUTES["title"]

    def always_cut(request):
        budgets.append(1)
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}
        )

    budgets.clear()
    router = OpenRouter(httpx.Client(transport=httpx.MockTransport(always_cut)))
    with pytest.raises(ProviderError, match="cut off"):
        build_summary(router, "We met. " * 2000, "standard", lambda *a: None)
    assert len(budgets) == 2


def test_notes_budget_covers_observed_long_meeting_output():
    from backend.provider import summary_token_budget

    assert summary_token_budget(578) >= 3000


def test_long_meeting_merges_all_notes_in_one_final_call(monkeypatch):
    router = Router(words=30)
    _, info = build_summary(router, "word " * 22000, "standard", lambda *a: None)
    finals = [call for call in router.calls if call[1] and not call[2].get("notes")]
    assert len(finals) == 1 and not finals[0][2].get("correction")
    assert sum(1 for call in router.calls if call[2].get("notes")) >= 4


def test_note_groups_keep_each_note_whole(monkeypatch):
    from backend import summary_modes

    monkeypatch.setattr(summary_modes, "SYNTHESIS_CHARS", 50)
    notes = [{"text": "x" * 30}, {"text": "y" * 30}, {"text": "z" * 30}]
    groups = summary_modes.note_groups(notes)
    assert len(groups) == 3 and all(json.loads(group) for group in groups)


def test_single_item_list_from_the_model_is_accepted(monkeypatch):
    from backend.config import settings
    from backend.provider import OpenRouter
    import httpx

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    def handler(request):
        message = {"content": json.dumps([MINUTES])}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": message}]})

    router = OpenRouter(httpx.Client(transport=httpx.MockTransport(handler)))
    assert router.summarize("text", "standard")[0]["title"] == MINUTES["title"]
