"""SUMX Faster Qwen3-TTS GPU workers. Private SDK access only; no web endpoints.

Deploy: modal deploy -m modal_service.modal_tts
"""

import json
import tempfile
import time
from pathlib import Path
import modal
from modal_service.config import (
    MODAL_APP_NAME,
    MODAL_GPU,
    MODAL_GPU_USD_PER_SECOND,
    MODAL_MAX_CONTAINERS,
    MODAL_MIN_CONTAINERS,
    MODAL_PROGRESS_DICT,
    MODAL_SCALEDOWN_WINDOW,
    MODAL_STARTUP_TIMEOUT,
    MODAL_TIMEOUT,
    MODAL_VOLUME_NAME,
)
from modal_service.narration import narrate
from modal_service.speech_contract import BASE_MODEL, LocalTTSError

BOOTED_AT = time.time()
CACHE = "/models/hub"
TOKENIZER = "Qwen/Qwen3-TTS-Tokenizer-12Hz"

app = modal.App(MODAL_APP_NAME)
weights = modal.Volume.from_name(MODAL_VOLUME_NAME, create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install_from_requirements(str(Path(__file__).with_name("requirements.txt")))
    .env({"HF_HOME": "/models", "HF_HUB_CACHE": CACHE, "PYTHONUNBUFFERED": "1"})
    .add_local_python_source("modal_service")
)
WORKER = dict(
    image=image,
    gpu=MODAL_GPU,
    volumes={"/models": weights},
    min_containers=MODAL_MIN_CONTAINERS,
    max_containers=MODAL_MAX_CONTAINERS,
    scaledown_window=MODAL_SCALEDOWN_WINDOW,
    timeout=MODAL_TIMEOUT,
    startup_timeout=MODAL_STARTUP_TIMEOUT,
)


def ensure_weights(model_id):
    """Download only what the volume lacks, then persist it for later containers."""
    from huggingface_hub import snapshot_download

    missing = False
    for repo in (model_id, TOKENIZER):
        try:
            snapshot_download(repo, cache_dir=CACHE, local_files_only=True)
        except Exception:
            print(json.dumps({"event": "downloading_weights", "repo": repo}), flush=True)
            snapshot_download(repo, cache_dir=CACHE, max_workers=8)
            missing = True
    if missing:
        weights.commit()
    return missing


def load_engine(model_id):
    from modal_service.inference import QwenEngine

    enter_at = time.time()
    started = time.perf_counter()
    downloaded = ensure_weights(model_id)
    weights_seconds = time.perf_counter() - started
    engine = QwenEngine(model_id, CACHE)
    engine.warmup()
    engine.cold_start_seconds = time.perf_counter() - started
    engine.startup = {
        "container_booted_at": BOOTED_AT,
        "enter_started_at": enter_at,
        "weights_check_seconds": weights_seconds,
    }
    print(
        json.dumps(
            {
                "event": "container_ready",
                "model": model_id,
                "gpu": engine.gpu_name,
                "dtype": str(engine.dtype),
                "weights_downloaded": downloaded,
                "cold_start_seconds": engine.cold_start_seconds,
                "load_seconds": engine.load_seconds,
                "warmup_seconds": engine.warmup_seconds,
                "cuda_graphs": engine.cuda_graphs,
            }
        ),
        flush=True,
    )
    return engine


def gpu_label(name):
    """The GPU this container actually received; longest match keeps A100 apart from A10."""
    upper = name.upper()
    if "A100" in upper and "40GB" in upper:
        return "A100-40GB"
    return next((g for g in sorted(MODAL_GPU_USD_PER_SECOND, key=len, reverse=True) if g in upper), name)


def timed(engine, cold, started, metrics):
    seconds = time.perf_counter() - started
    startup = engine.cold_start_seconds if cold else 0
    metrics = {
        **metrics,
        "modal_gpu": gpu_label(engine.gpu_name),
        "gpu_name": engine.gpu_name,
        "dtype": str(engine.dtype),
        "cuda_graphs": engine.cuda_graphs,
        "cold_start": cold,
        "cold_start_seconds": startup,
        "load_seconds": engine.load_seconds if cold else 0,
        "warmup_seconds": engine.warmup_seconds if cold else 0,
        **(engine.startup if cold else {}),
        "gpu_execution_seconds": seconds,
        "billable_seconds": seconds + startup,
    }
    print(
        json.dumps({k: v for k, v in metrics.items() if isinstance(v, (str, int, float, bool))}), flush=True
    )
    return metrics


def run_narration(engine, payload, cold):
    """The whole narration in this container. Progress carries counts only, never text or audio."""
    started = time.perf_counter()
    progress = modal.Dict.from_name(MODAL_PROGRESS_DICT, create_if_missing=True)

    def report(update):
        try:
            progress[payload["request_id"]] = update
        except Exception:
            pass  # Progress is informational and must never fail a paid generation.

    engine.debug_audio = []
    result = narrate(engine, payload, report)
    result["debug_audio"] = engine.debug_audio
    result["metrics"] = timed(engine, cold, started, result["metrics"])
    return result


def run(engine, payload, cold):
    """One chunk. A reference exists only in this temporary folder and in memory."""
    started = time.perf_counter()

    def done(metrics):
        return timed(engine, cold, started, metrics)

    debug, engine.debug_audio = engine.debug_audio, []
    debug.clear()
    with tempfile.TemporaryDirectory(prefix="sumx-reference-") as folder:
        reference = None
        if payload["voice_type"] == "cloned":
            reference = Path(folder) / "reference.wav"
            reference.write_bytes(payload["reference_wav"])
        options = (
            {
                "clone_context": payload.get("clone_context"),
                "clone_options": payload.get("clone_options"),
            }
            if payload["voice_type"] == "cloned"
            else {}
        )
        try:
            wav, metrics = engine.synthesize(
                payload["text"],
                payload["language"],
                reference=reference,
                ref_text=payload["reference_text"],
                speaker=payload["speaker"],
                preview=payload["preview"],
                **options,
            )
        except LocalTTSError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "debug_audio": engine.debug_audio,
                "metrics": done(getattr(exc, "metrics", {})),
            }
    return {
        "ok": True,
        "wav": wav,
        "model": engine.model_id,
        "debug_audio": engine.debug_audio,
        "metrics": done(metrics),
    }


@app.cls(**WORKER)
class CloneTTS:
    @modal.enter()
    def load(self):
        self.engine, self.cold = load_engine(BASE_MODEL), True

    @modal.method()
    def synthesize(self, payload):
        if not self.engine.cuda_graphs:
            self.engine, self.cold = load_engine(BASE_MODEL), True
        result = run(self.engine, payload, self.cold)
        self.cold = False
        return result

    @modal.method()
    def narrate(self, payload):
        if not self.engine.cuda_graphs:
            self.engine, self.cold = load_engine(BASE_MODEL), True
        result = run_narration(self.engine, payload, self.cold)
        self.cold = False
        return result
