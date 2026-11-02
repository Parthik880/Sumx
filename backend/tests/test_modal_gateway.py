"""Backend gateway checks. Nothing here touches Modal, CUDA or model weights."""

import ast
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.config import ROOT
from backend.speech_contract import LocalTTSError
from backend.tts_usage import estimate_modal_cost


class Timeout(Exception):
    pass


class Transient(Exception):
    pass


class Expired(Exception):
    pass


class NotFound(Exception):
    pass


class FunctionTimeout(Exception):
    pass


class Call:
    object_id = "fc-test"

    def __init__(self, outcomes):
        self.outcomes, self.cancelled = list(outcomes), False

    def get(self, timeout=None):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def cancel(self):
        self.cancelled = True


def client(call, spawn_failures=0, resumed=None):
    from backend.services.modal_tts_client import ModalSpeechClient

    spawned = []

    class Method:
        def spawn(self, payload):
            if len(spawned) < spawn_failures:
                spawned.append("failed")
                raise Transient()
            spawned.append(payload)
            return call

    def worker():
        return SimpleNamespace(synthesize=Method())

    instance = ModalSpeechClient.__new__(ModalSpeechClient)
    instance.modal = SimpleNamespace(
        exception=SimpleNamespace(
            ConnectionError=Transient,
            InternalFailure=Transient,
            ServiceError=Transient,
            TimeoutError=Timeout,
            FunctionTimeoutError=FunctionTimeout,
            NotFoundError=NotFound,
            OutputExpiredError=Expired,
        ),
        FunctionCall=SimpleNamespace(from_id=lambda value: resumed),
    )
    instance.workers = {"preset": worker, "cloned": worker}
    return instance, spawned


PRESET = {"voice_type": "preset"}


def test_routes_voice_types_to_separate_workers():
    preset, cloned = [], []

    def maker(target):
        def build():
            def spawn(payload):
                target.append(payload)
                return Call([{"ok": True}])

            return SimpleNamespace(synthesize=SimpleNamespace(spawn=spawn))

        return build

    instance, _ = client(Call([]))
    instance.workers = {"preset": maker(preset), "cloned": maker(cloned)}
    instance.synthesize({"voice_type": "cloned"})
    instance.synthesize(PRESET)
    assert len(preset) == len(cloned) == 1


def test_call_id_is_persisted_and_polling_survives_timeouts(monkeypatch):
    call = Call([Timeout(), TimeoutError(), {"ok": True}])
    instance, _ = client(call)
    seen = []
    assert instance.synthesize(PRESET, on_started=seen.append) == {"ok": True}
    assert seen == ["fc-test"]


def test_restarted_worker_collects_existing_call_without_respawning():
    resumed = Call([{"ok": True, "resumed": True}])
    instance, spawned = client(Call([]), resumed=resumed)
    assert instance.synthesize(PRESET, call_id="fc-old")["resumed"]
    assert spawned == []


def test_expired_call_is_regenerated_once():
    instance, spawned = client(Call([{"ok": True}]), resumed=Call([Expired()]))
    assert instance.synthesize(PRESET, call_id="fc-old") == {"ok": True}
    assert len(spawned) == 1


def test_transient_spawn_failures_retry_then_report(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    instance, spawned = client(Call([{"ok": True}]), spawn_failures=2)
    assert instance.synthesize(PRESET) == {"ok": True}
    instance, _ = client(Call([]), spawn_failures=9)
    with pytest.raises(LocalTTSError, match="could not be reached"):
        instance.synthesize(PRESET)


def test_function_timeout_is_not_retried():
    instance, _ = client(Call([FunctionTimeout()]))
    with pytest.raises(LocalTTSError, match="time limit"):
        instance.synthesize(PRESET)


def test_deadline_cancels_remote_call(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "qwen_tts_timeout", 0)
    call = Call([])
    instance, _ = client(call)
    with pytest.raises(LocalTTSError, match="too long"):
        instance.synthesize(PRESET)
    assert call.cancelled


def test_cost_is_a_rate_estimate_for_the_recorded_gpu():
    assert estimate_modal_cost({"billable_seconds": 100, "modal_gpu": "L4"}) == Decimal("0.022200")
    assert estimate_modal_cost({"billable_seconds": 100, "modal_gpu": "A10"}) == Decimal("0.030600")
    assert estimate_modal_cost({}) is None
    assert estimate_modal_cost({"billable_seconds": 1, "modal_gpu": "unknown"}) is None


def test_backend_imports_without_any_gpu_stack():
    code = (
        "import sys, backend.main, backend.jobs, backend.tts_worker\n"
        "print([m for m in ('torch','torchaudio','faster_qwen3_tts','transformers','numpy') if m in sys.modules])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "CUDA_VISIBLE_DEVICES": ""},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().replace("'", '"')) == []


def test_backend_source_has_no_inference_imports():
    banned = {"torch", "torchaudio", "faster_qwen3_tts", "transformers"}
    for path in (ROOT / "backend").rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not banned & {name.split(".")[0] for name in names}, path
            assert "inference" not in names and "modal_service.inference" not in names, path


def test_gpu_is_configured_in_exactly_one_file():
    hits = []
    for folder in ("backend", "modal_service"):
        for path in (ROOT / folder).rglob("*.py"):
            if "tests" in path.parts or path == ROOT / "modal_service" / "config.py":
                continue
            if '"L4"' in path.read_text(encoding="utf-8"):
                hits.append(path)
    assert hits == []
    tree = ast.parse(Path(ROOT / "modal_service" / "modal_tts.py").read_text(encoding="utf-8"))
    assert any(
        isinstance(n, ast.keyword) and n.arg == "gpu" and isinstance(n.value, ast.Name)
        for n in ast.walk(tree)
    )


def test_modal_credentials_never_reach_public_endpoints(user_client, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "modal_token_secret", "as-test-secret-value")
    for path in ("/api/voices/capabilities", "/api/voices/presets", "/api/auth/me"):
        assert "as-test-secret-value" not in user_client.get(path).text
    source = "".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "frontend" / "src").iterdir() if p.is_file()
    )
    assert "MODAL_TOKEN" not in source


@pytest.mark.parametrize(
    "name,label",
    [
        ("NVIDIA L4", "L4"),
        ("NVIDIA A10G", "A10"),
        ("NVIDIA A10", "A10"),
        ("Tesla T4", "T4"),
        ("NVIDIA L40S", "L40S"),
        ("NVIDIA A100 80GB PCIe", "A100"),
        ("NVIDIA A100-SXM4-40GB", "A100-40GB"),
    ],
)
def test_cost_uses_the_gpu_the_container_received(name, label):
    from modal_service.modal_tts import gpu_label

    assert gpu_label(name) == label
    assert estimate_modal_cost({"billable_seconds": 1, "modal_gpu": gpu_label(name)}) is not None


def test_gpu_preference_order_keeps_a100_first_and_every_type_priced():
    from modal_service.config import MODAL_GPU, MODAL_GPU_USD_PER_SECOND, MODAL_SCALEDOWN_WINDOW

    assert MODAL_GPU == ["A100", "L40S", "L4", "A10"]
    assert MODAL_SCALEDOWN_WINDOW == 300
    assert all(gpu in MODAL_GPU_USD_PER_SECOND for gpu in MODAL_GPU)


def test_narration_failure_returns_finished_chunks_and_success_returns_only_mp3(tmp_path):
    from backend.tests.qwen_mock import MockQwenEngine

    MockQwenEngine.generated.clear()
    MockQwenEngine.fail_once.update({1})
    payload = dict(
        chunks=["First short sentence here.", "Second short sentence here.", "Third short sentence here."],
        total_chunks=3,
        prior_wavs={},
        voice_type="preset",
        language="en",
        speaker="Aiden",
        reference_text="",
        gap_ms=0,
    )
    failed = MockQwenEngine().narrate(payload)
    assert not failed["ok"] and "chunk 2/3" in failed["error"] and set(failed["partial_wavs"]) == {0}
    updates = []
    done = MockQwenEngine().narrate(
        {**payload, "chunks": payload["chunks"][1:], "prior_wavs": failed["partial_wavs"]},
        progress=lambda stage, update: updates.append(update["completed"]),
    )
    assert done["ok"] and done["mp3"] and "wav" not in done and "partial_wavs" not in done
    assert MockQwenEngine.generated == payload["chunks"]
    assert done["metrics"]["realtime_factor"] > 0 and len(done["metrics"]["chunks"]) == 2
    assert updates[-1] == 3
