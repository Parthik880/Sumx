"""Hybrid TTS: previews are stored files, and each voice type reaches only its own provider."""

import asyncio
import shutil
import subprocess
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select
from backend import tts_queue
from backend.config import settings
from backend.jobs import process_job
from backend.models import DB, Job, LocalTTSWorker, Meeting, Summary, Usage
from backend.preset_voices import DEFAULT_PRESET, PRESET_MODEL, PRESETS, PREVIEW_VERSION, preview_text
from backend.provider import OpenRouter, ProviderError
from backend.services.preset_tts import generate_preset
from backend.services.modal_tts_client import ModalSpeechClient
from backend.tests.test_sumx import MINUTES, MockRouter
from backend.tests.test_voices import finish_preview, upload
from backend.voice_catalog import preview_file


def mp3(path, seconds=2):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={seconds}",
        ]
        + ["-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True,
    )
    return path


@pytest.fixture(autouse=True)
def speech_worker(database):
    with DB() as db:
        db.add(LocalTTSWorker(id="qwen3", state="ready", model=settings.qwen_tts_model))
        db.commit()


@pytest.fixture
def no_inference(monkeypatch):
    """Any inference attempt fails the test, proving a path is provider-free."""

    def forbidden(*args, **kwargs):
        pytest.fail("A provider was called")

    monkeypatch.setattr(OpenRouter, "speech", forbidden)
    monkeypatch.setattr(OpenRouter, "speech_async", forbidden)
    monkeypatch.setattr(OpenRouter, "request", forbidden)
    monkeypatch.setattr(ModalSpeechClient, "__init__", forbidden)
    monkeypatch.setattr(ModalSpeechClient, "synthesize", forbidden)
    monkeypatch.setattr(ModalSpeechClient, "narrate", forbidden)
    monkeypatch.setattr(tts_queue, "submit", forbidden)
    monkeypatch.setattr(tts_queue, "submit_narration", forbidden)


@pytest.fixture
def stored_preview():
    target = preview_file(DEFAULT_PRESET)
    target.parent.mkdir(parents=True, exist_ok=True)
    mp3(target)
    yield target
    shutil.rmtree(target.parent, ignore_errors=True)


def meeting(owner):
    with DB() as db:
        row = Meeting(user_id=owner, title="Catalog", summary_version=1, status="ready")
        db.add(row)
        db.flush()
        db.add(Summary(meeting_id=row.id, version=1, transcript_version=1, content=MINUTES))
        db.commit()
        return row.id


def test_curated_presets_are_real_provider_ids_with_default():
    assert settings.openrouter_tts_model == PRESET_MODEL == "mistralai/voxtral-mini-tts-3-0"
    assert settings.tts_default_voice == DEFAULT_PRESET in PRESETS
    assert {v["gender"] for v in PRESETS.values()} == {"male", "female", None}
    assert len(PRESETS) == 32
    assert len({v["group"] for v in PRESETS.values()}) == 22
    assert PRESETS["voice_3153"]["language"] == "hi"
    assert PRESETS["voice_3139"]["group"] == "Hindi"
    assert PRESETS["voice_3136"]["gender"] is None
    assert all(preview_text(voice) for voice in PRESETS)


def test_all_catalog_previews_and_preferences_are_provider_free(user_client, tmp_path, no_inference, monkeypatch):
    # One fake saved MP3 is enough to prove routing/cache behavior for every preset.
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    audio = mp3(tmp_path / "preview.mp3").read_bytes()
    for voice in PRESETS:
        target = preview_file(voice)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(audio)
        entry = next(v for v in user_client.get("/api/voices/presets").json()["speakers"] if v["id"] == voice)
        assert entry["group"] == PRESETS[voice]["group"]
        assert entry["gender"] == PRESETS[voice]["gender"]
        assert user_client.post("/api/voices/preferences", json={"preset_voice_id": voice}).status_code == 200
        for _ in range(2):
            response = user_client.get(entry["preview_url"])
            assert response.content == audio and "immutable" in response.headers["cache-control"]


def test_preview_generator_uses_native_text_without_changing_english(monkeypatch, tmp_path):
    from backend import preset_previews
    from backend.speech_contract import PREVIEW_TEXT

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    monkeypatch.setattr(preset_previews, "probe", lambda path: (4, {}))
    monkeypatch.setattr(preset_previews, "ledger_owner", lambda db: "unused")
    monkeypatch.setattr(preset_previews, "account", lambda *a, **k: None)
    seen = []

    class Router:
        def speech(self, text, voice):
            seen.append((text, voice))
            return b"fake", {}, None

    for voice in PRESETS:
        preset_previews.generate(voice, Router())
    assert all(text == preview_text(voice) for text, voice in seen)
    assert all(text == PREVIEW_TEXT for text, voice in seen if PRESETS[voice]["language"] == "en")
    assert all(text != PREVIEW_TEXT for text, voice in seen if PRESETS[voice]["language"] != "en")


@pytest.mark.parametrize("voice", [v for v in PRESETS if v.startswith("voice_")])
def test_each_new_preset_narrates_through_openrouter(user_client, voice):
    mid = meeting(user_client.user_id)
    response = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "preset", "preset_voice_id": voice},
        headers={"Idempotency-Key": "regional-voice-test-1234"},
    )
    assert response.status_code == 200
    provider = MockRouter()
    seen = []
    original = provider.speech_async

    async def speech(text, requested, client):
        seen.append(requested)
        return await original(text, requested, client)

    provider.speech_async = speech
    process_job(response.json()["id"], provider)
    with DB() as db:
        job = db.get(Job, response.json()["id"])
        assert job.state == "completed", job.error
    assert seen and set(seen) == {voice}


def test_selecting_and_replaying_a_preview_makes_no_provider_calls(user_client, stored_preview, no_inference):
    data = user_client.get("/api/voices/presets").json()
    entry = next(v for v in data["speakers"] if v["id"] == DEFAULT_PRESET)
    assert entry["preview_available"] and entry["provider"] == "openrouter"
    assert (
        user_client.post("/api/voices/preferences", json={"preset_voice_id": DEFAULT_PRESET}).status_code
        == 200
    )
    for _ in range(3):
        response = user_client.get(entry["preview_url"])
        assert response.status_code == 200 and response.headers["content-type"] == "audio/mpeg"
        assert "immutable" in response.headers["cache-control"]
    with DB() as db:
        assert db.scalar(select(Usage)) is None


def test_missing_preview_is_reported_and_never_generated(user_client, no_inference):
    entry = user_client.get("/api/voices/presets").json()["speakers"][1]
    assert entry["preview_url"] is None and not entry["preview_available"]
    assert user_client.get(f"/api/voices/presets/{entry['id']}/preview").status_code == 404
    assert user_client.get("/api/voices/presets/not_a_voice/preview").status_code == 404


def test_paid_preview_endpoints_are_gone(user_client):
    assert user_client.post(f"/api/voices/presets/{DEFAULT_PRESET}/preview").status_code == 405


def test_unified_catalog_lists_both_types_with_trusted_providers(user_client):
    voice = upload(user_client).json()
    voices = user_client.get("/api/voices/catalog").json()["voices"]
    assert {v["provider"] for v in voices if v["voice_type"] == "preset"} == {"openrouter"}
    cloned = next(v for v in voices if v["id"] == voice["id"])
    assert cloned["provider"] == "modal" and cloned["user_id"] == user_client.user_id
    assert not cloned["preview_available"] and cloned["preview_url"] is None
    assert len(voices) == len(PRESETS) + 1


def test_cloned_preview_is_generated_once_and_selection_is_free(user_client, monkeypatch):
    from backend.tests import test_voices

    voice = upload(user_client).json()
    finish_preview(user_client, voice["id"])
    calls = []
    original = tts_queue.submit
    monkeypatch.setattr(tts_queue, "submit", lambda *a, **k: calls.append(1) or original(*a, **k))
    again = test_voices.preview(user_client, voice["id"])
    assert again.json()["preview_current"] and calls == []
    assert user_client.get(f"/api/voices/{voice['id']}/preview").status_code == 200
    assert calls == []
    # An explicit refresh is the only way to regenerate a current preview.
    refreshed = user_client.post(
        f"/api/voices/{voice['id']}/preview?refresh=true",
        headers={"Idempotency-Key": "preview-refresh-123456"},
    )
    assert refreshed.status_code == 200 and calls == [1]


def test_changed_reference_marks_the_preview_stale(user_client):
    voice = upload(user_client).json()
    finish_preview(user_client, voice["id"])
    path = f"/api/voices/{voice['id']}"
    user_client.patch(path, json={"reference_text": "Different words were spoken here today."})
    assert not user_client.get(path).json()["preview_current"]
    entry = next(v for v in user_client.get("/api/voices/catalog").json()["voices"] if v["id"] == voice["id"])
    assert not entry["preview_available"]


def test_preset_narration_uses_openrouter_only(user_client, monkeypatch, tmp_path):
    monkeypatch.setattr(ModalSpeechClient, "__init__", lambda *a: pytest.fail("Presets must not reach Modal"))
    monkeypatch.setattr(tts_queue, "submit_narration", lambda *a, **k: pytest.fail("No GPU queue"))
    mid = meeting(user_client.user_id)
    response = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "preset", "preset_voice_id": "gb_jane_neutral"},
        headers={"Idempotency-Key": "preset-route-123456789"},
    )
    provider = MockRouter()
    process_job(response.json()["id"], provider)
    with DB() as db:
        job = db.get(Job, response.json()["id"])
        assert job.state == "completed", job.error
        assert job.model_name == PRESET_MODEL
        usage = db.scalars(select(Usage).where(Usage.meeting_id == mid)).all()
        assert usage and all(u.operation == "tts" and u.model == PRESET_MODEL for u in usage)
    assert provider.calls.count("tts") >= 1
    # The same narration again reuses the saved audio with no second charge.
    before = provider.calls.count("tts")
    second = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "preset", "preset_voice_id": "gb_jane_neutral"},
        headers={"Idempotency-Key": "preset-route-again-12345"},
    )
    process_job(second.json()["id"], provider)
    assert provider.calls.count("tts") == before


def test_cloned_narration_never_uses_openrouter_speech(user_client, monkeypatch):
    voice = upload(user_client).json()
    finish_preview(user_client, voice["id"])
    user_client.patch(f"/api/voices/{voice['id']}", json={"saved": True})
    mid = meeting(user_client.user_id)
    response = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "cloned", "cloned_voice_id": voice["id"]},
        headers={"Idempotency-Key": "cloned-route-123456789"},
    )
    provider = MockRouter()
    monkeypatch.setattr(provider, "speech_async", lambda *a: pytest.fail("Clones must not reach Voxtral"))
    process_job(response.json()["id"], provider)
    with DB() as db:
        job = db.get(Job, response.json()["id"])
        assert job.state == "completed", job.error
        assert {u.provider for u in db.scalars(select(Usage).where(Usage.meeting_id == mid))} == {
            "modal_qwen3"
        }


def test_unknown_preset_is_rejected_on_the_backend(user_client):
    mid = meeting(user_client.user_id)
    response = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "preset", "preset_voice_id": "Aiden"},
        headers={"Idempotency-Key": "stale-preset-123456789"},
    )
    assert response.status_code == 422


def test_failed_preset_chunk_keeps_finished_parts_for_retry(tmp_path):
    calls = []

    class Router(MockRouter):
        async def speech_async(self, text, voice, client):
            calls.append(text)
            if text == "two" and calls.count("two") == 1:
                raise ProviderError("Voxtral failed")
            return await super().speech_async(text, voice, client)

    parts = [tmp_path / f"{i}.mp3" for i in range(3)]
    saved = []
    router = Router()
    with pytest.raises(ProviderError):
        asyncio.run(
            generate_preset(
                router, "gb_jane_neutral", ["one", "two", "three"], parts, lambda *a: saved.append(a[0])
            )
        )
    assert parts[0].is_file() and not parts[1].is_file()
    first = list(calls)
    asyncio.run(
        generate_preset(
            router, "gb_jane_neutral", ["one", "two", "three"], parts, lambda *a: saved.append(a[0])
        )
    )
    assert all(part.is_file() for part in parts)
    assert calls[len(first) :].count("one") == 0


def test_speech_request_shape_and_invalid_audio_rejected(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    bodies = []

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200, json={"data": [{"id": PRESET_MODEL, "pricing": {"prompt": "0.000013"}}]}
            )
        bodies.append(request.read())
        return httpx.Response(200, content=b"not audio", headers={"content-type": "text/plain"})

    router = OpenRouter(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match="invalid MP3"):
        router.speech("Hello", DEFAULT_PRESET)
    assert b'"model":"mistralai/voxtral-mini-tts-3-0"' in bodies[0].replace(b" ", b"")
    assert router.speech_rate() is not None


def test_preview_command_skips_existing_files(user_client, stored_preview, monkeypatch):
    from backend import preset_previews

    calls = []

    class Router:
        def request(self, *args, **kwargs):
            return httpx.Response(200, json={"data": [{"id": PRESET_MODEL, "supported_voices": list(PRESETS)}]})

        def json(self, response):
            return response.json()

        def speech(self, text, voice):
            calls.append((text, voice))
            sample = mp3(Path(settings.storage_dir) / "sample.mp3")
            return (
                sample.read_bytes(),
                {"estimated_cost": 0.001},
                httpx.Response(200, headers={"x-generation-id": "gen-" + voice}),
            )

        def close(self):
            pass

    monkeypatch.setattr(preset_previews, "OpenRouter", Router)
    monkeypatch.setattr(
        "sys.argv", ["preset_previews", "--voice", DEFAULT_PRESET, "--voice", "gb_jane_neutral"]
    )
    preset_previews.main()
    assert [voice for _, voice in calls] == ["gb_jane_neutral"]
    assert calls[0][0] == "Hello. This is my new voice. I can read your meeting notes clearly."
    target = preview_file("gb_jane_neutral")
    assert target.name == f"{PREVIEW_VERSION}.mp3" and target.is_file()
    with DB() as db:
        usage = db.scalar(select(Usage).where(Usage.purpose == "preset_preview"))
        assert usage.cost_status == "estimated" and usage.model == PRESET_MODEL
    shutil.rmtree(target.parent, ignore_errors=True)
