import array
import math
import wave
from pathlib import Path
import pytest
from backend.clone_speech import split_clone_narration, assemble_clone_wav, silence_metrics
from backend.speech_contract import LocalTTSError
from backend.config import Settings


@pytest.mark.parametrize("count", [8, 100, 500, 1000])
def test_word_chunks_preserve_every_word_and_punctuation(count):
    sentence = "Dr. Patel reviewed 12 reports, approved 3.5 percent growth, and requested a clear update before Friday."
    text = "  ".join((sentence.split() * (count // len(sentence.split()) + 1))[:count])
    chunks = split_clone_narration(text)
    assert chunks and all(0 < len(chunk.split()) <= 22 for chunk in chunks)
    assert [word for chunk in chunks for word in chunk.split()] == text.split()


def test_natural_sentences_paragraphs_and_long_words():
    text = "A short sentence.\n\nDr. Jones reviewed the updated plan. The next meeting starts at 10:30 a.m. on Friday."
    chunks = split_clone_narration(text, 10)
    assert all(not c.endswith("Dr.") for c in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())
    word = "x" * 590
    long_chunks = split_clone_narration(word + " another word")
    assert " ".join(long_chunks) == word + " another word"
    assert all(len(chunk) <= 600 for chunk in long_chunks)
    with pytest.raises(LocalTTSError):
        split_clone_narration("x" * 601)


def test_unpunctuated_paragraph_and_clauses():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = split_clone_narration(text)
    assert " ".join(chunks) == text and max(len(c.split()) for c in chunks) == 22
    clause = " ".join(["word"] * 15) + "; " + " ".join(["next"] * 15)
    assert split_clone_narration(clause)[0].endswith(";")


def test_silence_measures_interior_separately_from_edges():
    speech = [0.1] * 24000
    assert silence_metrics(speech + [0] * (24000 * 4) + speech)["max_internal_silence_seconds"] == 4
    signal = silence_metrics([0] * 24000 + speech + [0] * 48000)
    assert signal["max_internal_silence_seconds"] == 0
    assert signal["leading_silence_seconds"] == 1 and signal["trailing_silence_seconds"] == 2


def pcm(path, frequency, rate=24000):
    samples = array.array(
        "h", (round(6000 * math.sin(i * 2 * math.pi * frequency / rate)) for i in range(rate))
    )
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())
    return samples


def test_ordered_pcm_assembly_keeps_frames_and_only_short_gaps(tmp_path):
    paths = [tmp_path / f"{i}.wav" for i in range(3)]
    originals = [pcm(path, frequency) for path, frequency in zip(paths, [220, 440, 880])]
    output = tmp_path / "joined.wav"
    duration = assemble_clone_wav(paths, output)
    assert duration == pytest.approx(3.4)
    with wave.open(str(output), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
        samples = array.array("h", wav.readframes(wav.getnframes()))
    for i, original in enumerate(originals):
        offset = i * 28800
        assert samples[offset + 120 : offset + 23880] == original[120:23880]
    assert set(samples[24000:28800]) == {0}
    pcm(paths[1], 440, rate=16000)
    with pytest.raises(LocalTTSError, match="24 kHz"):
        assemble_clone_wav(paths, output)


def test_debug_audio_is_not_enabled_for_production_origin():
    with pytest.raises(ValueError, match="local development"):
        Settings(_env_file=None, app_url="https://sumx.example", qwen_clone_debug=True)


def test_failed_clone_retries_only_its_unfinished_chunks(user_client, monkeypatch):
    from backend.config import settings
    from backend.jobs import process_job
    from backend.models import DB, Meeting, Summary, Job, LocalTTSWorker, AudioOutput, ClonedVoice
    from backend.tests.test_sumx import MINUTES
    from backend.tests.qwen_mock import MockQwenEngine
    from backend.tests.test_voices import upload, finish_preview

    with DB() as db:
        db.add(LocalTTSWorker(id="qwen3", state="ready", model=settings.qwen_tts_model))
        db.commit()
    voice = upload(user_client).json()
    finish_preview(user_client, voice["id"])
    user_client.patch(f"/api/voices/{voice['id']}", json={"saved": True})
    with DB() as db:
        meeting = Meeting(user_id=user_client.user_id, title="Selective clone retry", summary_version=1)
        db.add(meeting)
        db.flush()
        db.add(Summary(meeting_id=meeting.id, version=1, transcript_version=1, content=MINUTES))
        # A previous engine version must coexist with the newly validated narration.
        old_path = settings.storage_dir / meeting.id / "legacy.mp3"
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_bytes(Path(db.get(ClonedVoice, voice["id"]).preview_path).read_bytes())
        legacy = AudioOutput(
            meeting_id=meeting.id,
            summary_version=1,
            model=settings.qwen_tts_model,
            voice="cloned:" + voice["id"],
            voice_type="cloned",
            voice_name="Historical voice",
            path=str(old_path),
        )
        db.add(legacy)
        db.commit()
        mid, legacy_id = meeting.id, legacy.id
    MockQwenEngine.generated.clear()
    MockQwenEngine.fail_once.add(1)
    response = user_client.post(
        f"/api/meetings/{mid}/speech",
        json={"voice_type": "cloned", "cloned_voice_id": voice["id"]},
        headers={"Idempotency-Key": "clone-chunk-retry-123456"},
    )
    jid = response.json()["id"]
    process_job(jid)
    with DB() as db:
        job = db.get(Job, jid)
        assert job.state == "failed" and "chunk 2/" in job.error
        assert MockQwenEngine.generated == job.checkpoint["clone_chunks"][:1]
        original_plan = job.checkpoint["clone_chunks"]
    # A setting change cannot invalidate successful chunks during a safe retry.
    monkeypatch.setattr(settings, "qwen_clone_chunk_words", 10)
    assert user_client.post(f"/api/meetings/{mid}/jobs/{jid}/retry").status_code == 200
    process_job(jid)
    with DB() as db:
        job = db.get(Job, jid)
        assert job.state == "completed", job.error
        assert job.total == len(original_plan)
        assert "clone-v2-narration.mp3" in job.output_path
        assert db.get(AudioOutput, legacy_id).path == str(old_path) and old_path.is_file()
    # One call covers the rest; the finished first chunk is reused, not regenerated.
    assert MockQwenEngine.generated == original_plan
