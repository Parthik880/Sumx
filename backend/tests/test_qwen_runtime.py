from contextlib import nullcontext
from types import SimpleNamespace
import sys
import pytest
from backend.speech_contract import LocalTTSError
from backend.tts_worker import single_tts_worker
from modal_service.inference import QwenEngine


def test_cuda_unavailable_is_honest(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            snapshot_download=lambda *a, **k: pytest.fail("No download when CUDA is unavailable")
        ),
    )
    monkeypatch.setitem(sys.modules, "faster_qwen3_tts", SimpleNamespace(FasterQwen3TTS=object))
    with pytest.raises(LocalTTSError, match="GPU is unavailable"):
        QwenEngine("Qwen/Qwen3-TTS-12Hz-0.6B-Base", tmp_path)


def test_official_model_load_uses_cuda_bf16_sdpa_and_local_cache(monkeypatch, tmp_path):
    calls = []

    class Model:
        @staticmethod
        def from_pretrained(path, **kwargs):
            calls.append((path, kwargs))
            return object()

    cuda = SimpleNamespace(
        is_available=lambda: True,
        get_device_name=lambda _: "RTX 3060",
        get_device_properties=lambda _: SimpleNamespace(total_memory=12 * 1024**3),
        is_bf16_supported=lambda: True,
        synchronize=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda, bfloat16="bf16", float16="fp16"))

    def snapshot(*args, **kwargs):
        assert kwargs["local_files_only"] is True
        return str(tmp_path)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot))
    monkeypatch.setitem(sys.modules, "faster_qwen3_tts", SimpleNamespace(FasterQwen3TTS=Model))
    engine = QwenEngine("Qwen/Qwen3-TTS-12Hz-0.6B-Base", tmp_path)
    assert calls == [
        (
            str(tmp_path),
            dict(
                backend="torch",
                device="cuda:0",
                dtype="bf16",
                attn_implementation="sdpa",
                local_files_only=True,
            ),
        )
    ]
    assert engine.vram_mb == 12288


def test_real_runtime_oom_handler_frees_conditioning_and_reports_retry(monkeypatch, tmp_path):
    class OOM(Exception):
        pass

    freed = []
    cuda = SimpleNamespace(
        reset_peak_memory_stats=lambda: None, OutOfMemoryError=OOM, empty_cache=lambda: freed.append(True)
    )
    engine = QwenEngine.__new__(QwenEngine)
    engine.torch = SimpleNamespace(cuda=cuda, inference_mode=nullcontext)
    engine.model_id = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    engine.cuda_graphs = True

    def fail(**kwargs):
        raise OOM()

    engine.model = SimpleNamespace(generate_custom_voice=fail, _voice_prompt_cache={})
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace())
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"test")
    with pytest.raises(LocalTTSError, match="ran out of memory"):
        engine.synthesize("Target text", "en", speaker="Aiden")
    assert freed == [True] and not engine.cuda_graphs
    with pytest.raises(LocalTTSError, match="language"):
        engine.synthesize("Target text", "unsupported", reference=reference, ref_text="Reference text")


def test_only_one_speech_worker_can_hold_process_lock():
    with single_tts_worker():
        with pytest.raises(OSError):
            with single_tts_worker():
                pass


def test_graph_capture_failure_never_enables_fallback():
    engine = QwenEngine.__new__(QwenEngine)
    engine.cuda_graphs = False
    engine.torch = SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda: None))

    def fail(**kwargs):
        raise RuntimeError("capture failed")

    engine.model = SimpleNamespace(warmup=fail)
    with pytest.raises(LocalTTSError, match="warmup failed"):
        engine.warmup()
    assert not engine.cuda_graphs


@pytest.mark.parametrize(
    "values",
    [
        {"qwen_tts_model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base"},
        {"qwen_tts_chunk_chars": 0},
    ],
)
def test_unsupported_configurations_rejected(values):
    from backend.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
