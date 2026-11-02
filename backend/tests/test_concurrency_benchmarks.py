import json
from decimal import Decimal
import pytest
from backend.benchmarks.concurrency import Budget, write_json, inspect_audio, word_error_rate
from backend.benchmarks.cleanup import expired_runs
from backend.provider import ProviderError


def test_budget_survives_restart_and_stops_before_a_paid_call(tmp_path):
    budget = Budget(tmp_path, 0.1)
    budget.reserve(Decimal(".06"))
    resumed = Budget(tmp_path, 0.1)
    with pytest.raises(ProviderError, match="ceiling"):
        resumed.reserve(Decimal(".05"))
    assert json.loads((tmp_path / "budget.json").read_text())["attempts"] == 1


def test_word_error_uses_edit_distance_and_preserves_repetition():
    assert word_error_rate(["one", "two", "three"], ["one", "three"]) == pytest.approx(1 / 3)
    assert word_error_rate(["same"] * 100, ["same"] * 99) == 0.01


def test_expiry_only_selects_marked_children_and_never_the_root(tmp_path):
    write_json(tmp_path / "manifest.json", {"benchmark": "sumx-concurrency-v1", "expires_at": 1})
    marked = tmp_path / "marked"
    marked.mkdir()
    write_json(marked / "manifest.json", {"benchmark": "sumx-concurrency-v1", "expires_at": 1})
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    write_json(unrelated / "manifest.json", {"expires_at": 1})
    assert expired_runs(tmp_path, now=2) == [marked.resolve()]


def test_quality_probe_decodes_and_detects_internal_silence(tmp_path):
    import array
    import math
    import wave

    path = tmp_path / "test.wav"
    tone = array.array("h", (int(6000 * math.sin(2 * math.pi * 220 * i / 24000)) for i in range(24000)))
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(tone.tobytes() + b"\x00\x00" * 96000 + tone.tobytes())
    result = inspect_audio(path)
    assert result["audio_seconds"] == 6 and result["max_internal_silence_seconds"] == 4
    assert result["clipping_fraction"] == 0 and result["valid_pcm_decode"]
