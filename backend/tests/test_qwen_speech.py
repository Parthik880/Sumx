import io
import math
import random
import struct
import wave
import httpx
import pytest
from sqlalchemy import select
from backend.preset_voices import DEFAULT_PRESET, PRESETS
from backend.models import DB, ClonedVoice, Usage
from backend.speech_contract import (
    PREVIEW_TEXT,
    SAMPLE_RATE,
    LocalTTSError,
    duration_budget,
    pcm_metrics,
    validate_duration,
)
from backend.reference_quality import verify_reference as actual_verify
from backend import reference_quality
from backend.tests.test_voices import upload, REF_TEXT


def wav(seconds=4, rate=SAMPLE_RATE, mode="speech"):
    result = io.BytesIO()
    rng = random.Random(5)
    with wave.open(result, "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        samples = (
            0
            if mode == "silent"
            else 32767
            if mode == "clipped"
            else rng.randint(-12000, 12000)
            if mode == "noise"
            else int(6000 * math.sin(i * 2 * math.pi * 220 / rate))
            for i in range(int(rate * seconds))
        )
        out.writeframes(b"".join(struct.pack("<h", x) for x in samples))
    return result.getvalue()


def test_preview_budget_rejects_147_second_failure_without_truncation():
    assert duration_budget(PREVIEW_TEXT, True)["max_new_tokens"] < 180
    with pytest.raises(LocalTTSError, match="duration is abnormal"):
        validate_duration(146.88, PREVIEW_TEXT, True)
    with pytest.raises(LocalTTSError):
        validate_duration(float("nan"), PREVIEW_TEXT, True)
    assert validate_duration(6, PREVIEW_TEXT, True)
    assert duration_budget("这是一个用于测试清晰朗读和稳定声音的中文参考句子。" * 3)["max_seconds"] > 10


@pytest.mark.parametrize(
    "mode,rate", [("silent", 24000), ("clipped", 24000), ("noise", 24000), ("speech", 44100)]
)
def test_invalid_reference_signal_is_rejected(mode, rate):
    with pytest.raises(LocalTTSError):
        pcm_metrics(wav(rate=rate, mode=mode), reference=True)


def test_preset_catalog_and_preferences(user_client):
    data = user_client.get("/api/voices/presets").json()
    assert data["voices"] == list(PRESETS) and data["default"] == DEFAULT_PRESET
    assert data["preview_text"] == PREVIEW_TEXT
    assert user_client.post("/api/voices/preferences", json={"preset_voice_id": "invalid"}).status_code == 422
    assert (
        user_client.post("/api/voices/preferences", json={"preset_voice_id": "gb_jane_neutral"}).json()[
            "default"
        ]
        == "gb_jane_neutral"
    )
    assert user_client.get("/api/voices/presets").json()["default"] == "gb_jane_neutral"


@pytest.mark.parametrize("case", ["match", "empty", "mismatch", "speakers"])
def test_actual_reference_verifier_preserves_transcript_and_charges_stt(user_client, monkeypatch, case):
    response = upload(user_client, name="Verifier sample", reference_text="" if case == "empty" else REF_TEXT)
    assert response.status_code == 201
    transcript = "Completely different words about an unrelated topic" if case == "mismatch" else REF_TEXT
    segments = [{"speaker": "A"}, {"speaker": "B"}] if case == "speakers" else []

    class Provider:
        def transcribe(self, *args):
            return (
                {"text": transcript, "segments": segments},
                {"id": "ref-check", "usage": {"cost": "0.001"}},
                httpx.Response(200),
            )

        def close(self):
            pass

    monkeypatch.setattr(reference_quality, "OpenRouter", Provider)
    with DB() as db:
        voice = db.get(ClonedVoice, response.json()["id"])
        if case in ("mismatch", "speakers"):
            with pytest.raises(LocalTTSError):
                actual_verify(db, voice)
            assert not voice.reference_verified_at
        else:
            actual_verify(db, voice)
            assert voice.reference_verified_at and voice.reference_text == REF_TEXT
            assert voice.reference_text != PREVIEW_TEXT
        record = db.scalar(select(Usage).where(Usage.purpose == "voice_reference"))
        assert record.operation == "stt" and record.cost > 0


def test_profile_names_and_old_preview_require_revalidation(user_client):
    response = upload(user_client)
    assert response.status_code == 201
    assert upload(user_client, name="MY PERSONAL VOICE").status_code == 409
    with DB() as db:
        voice = db.get(ClonedVoice, response.json()["id"])
        voice.saved = True
        voice.preview_path = "historical-preview.mp3"
        db.commit()
    data = user_client.get("/api/voices/" + response.json()["id"]).json()
    assert data["reenrollment_required"] and not data["usable"]
    assert data["has_preview"] and data["saved"]
