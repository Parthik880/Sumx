"""One GPU process, persistent model and in-memory conditioning; no listening socket."""

import logging
import os
import threading
import time
import traceback
from contextlib import contextmanager
from sqlalchemy import select, update
from .config import settings
from .media import probe, run, safe_path
from .models import ClonedVoice, DB, LocalTTSGeneration, LocalTTSWorker, User, AllowedEmail
from .qwen_runtime import QwenEngine, LocalTTSError

log = logging.getLogger("sumx.qwen")


def process(task_id, engine):
    started = time.monotonic()
    try:
        with DB() as db:
            task = db.get(LocalTTSGeneration, task_id)
            voice = db.get(ClonedVoice, task.voice_id)
            user = db.get(User, task.user_id)
            approved = db.get(AllowedEmail, user.email) if user else None
            if not user or (user.role != "admin" and (not approved or not approved.active)):
                raise LocalTTSError("Access to voice generation is no longer approved")
            if not voice or voice.user_id != task.user_id or not voice.consent_at:
                raise LocalTTSError("The selected consented voice is no longer available")
            if task.reference_text != voice.reference_text or task.model != settings.qwen_tts_model:
                raise LocalTTSError("The reference or model changed. Start a new generation.")
            folder = safe_path(voice.reference_path).parent / "local" / task.id
            folder.mkdir(parents=True, exist_ok=True)
            wav, mp3 = folder / "audio.wav", folder / "audio.mp3"

            def progress(stage, metrics=None):
                task.stage = stage
                if metrics:
                    task.audio_seconds = metrics.get("audio_seconds", task.audio_seconds)
                    task.stream_events = metrics.get("stream_events", task.stream_events)
                    if task.stream_events and task.first_audio_at is None:
                        task.first_audio_at = time.time()
                db.commit()

            metrics = engine.generate(
                voice.id,
                safe_path(voice.reference_path),
                task.reference_text,
                task.text,
                task.language,
                wav,
                progress,
            )
            progress("encoding_audio")
            probe(wav)
            run(
                [
                    settings.ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(wav),
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(mp3),
                ]
            )
            probe(mp3)
            task.state, task.stage = "completed", "completed"
            task.output_path = str(mp3)
            task.audio_seconds = metrics["audio_seconds"]
            task.peak_vram_mb = metrics["peak_vram_mb"]
            task.processing_seconds = time.monotonic() - started
            task.completed_at = time.time()
            task.stream_events = metrics.get("stream_events", task.stream_events)
            task.text, task.reference_text = "", ""
            if task.purpose == "voice_preview":
                voice.preview_path, voice.model = str(mp3), task.model
                voice.preview_until, voice.preview_error, voice.updated = 0, None, time.time()
            db.commit()
    except Exception as exc:
        # Never log reference audio or target text. Detailed CUDA diagnostics stay server-side.
        log.error("Local task %s failed: %s", task_id, type(exc).__name__)
        if not isinstance(exc, LocalTTSError):
            log.error(
                "Runtime locations: %s",
                [(frame.name, frame.lineno) for frame in traceback.extract_tb(exc.__traceback__)],
            )
        message = (
            str(exc)
            if isinstance(exc, LocalTTSError)
            else "Local speech generation failed. Check the GPU worker and retry."
        )
        with DB() as db:
            task = db.get(LocalTTSGeneration, task_id)
            if task:
                task.state, task.stage, task.error = "failed", "failed", message[:500]
                task.processing_seconds = time.monotonic() - started
                task.completed_at = time.time()
                voice = db.get(ClonedVoice, task.voice_id)
                if voice and task.purpose == "voice_preview":
                    voice.preview_until, voice.preview_error = 0, task.error
                db.commit()


def _run_worker(engine_factory=QwenEngine, stop=None):
    stop = stop or threading.Event()
    if not settings.local_voice_cloning_enabled or settings.local_tts_provider != "faster_qwen3":
        raise RuntimeError("Local Qwen voice cloning is disabled in configuration")
    if settings.qwen_tts_max_concurrent_jobs != 1:
        raise RuntimeError("SUMX currently supports one active local GPU generation")
    active = [None]
    with DB() as db:
        row = db.get(LocalTTSWorker, "qwen3")
        if not row:
            row = LocalTTSWorker(id="qwen3", state="loading", model=settings.qwen_tts_model)
            db.add(row)
        else:
            row.state, row.model, row.error = "loading", settings.qwen_tts_model, None
        row.heartbeat = time.time()
        row.engine = "faster_qwen3"
        row.cuda_graphs = False
        # The exclusive OS lock proves the previous process is gone, even after a crash.
        db.execute(
            update(LocalTTSGeneration)
            .where(LocalTTSGeneration.state == "running")
            .values(state="queued", stage="queued")
        )
        db.commit()

    def heartbeat():
        while not stop.wait(5):
            with DB() as db:
                db.execute(
                    update(LocalTTSWorker).where(LocalTTSWorker.id == "qwen3").values(heartbeat=time.time())
                )
                if active[0]:
                    db.execute(
                        update(LocalTTSGeneration)
                        .where(LocalTTSGeneration.id == active[0], LocalTTSGeneration.state == "running")
                        .values(heartbeat=time.time())
                    )
                    task = db.get(LocalTTSGeneration, active[0])
                    voice = db.get(ClonedVoice, task.voice_id) if task else None
                    if voice and task.purpose == "voice_preview":
                        voice.preview_until = time.time() + 600
                db.commit()

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        engine = engine_factory(
            settings.qwen_tts_model,
            settings.qwen_tts_model_cache,
            settings.qwen_tts_device,
            settings.qwen_tts_attention,
        )
        with DB() as db:
            row = db.get(LocalTTSWorker, "qwen3")
            row.state = "warming_up"
            db.commit()
        engine.warmup()
        with DB() as db:
            row = db.get(LocalTTSWorker, "qwen3")
            row.state, row.gpu_name, row.vram_mb = "ready", engine.gpu_name, engine.vram_mb
            row.cuda_graphs = engine.cuda_graphs
            row.load_seconds = engine.load_seconds
            row.warmup_seconds = engine.warmup_seconds
            db.commit()
        log.info("Local Qwen ready on %s; model %s", engine.gpu_name, settings.qwen_tts_model)
        while not stop.is_set():
            with DB() as db:
                db.execute(
                    update(LocalTTSGeneration)
                    .where(
                        LocalTTSGeneration.state == "running", LocalTTSGeneration.heartbeat < time.time() - 90
                    )
                    .values(state="queued", stage="queued")
                )
                task_id = db.scalar(
                    select(LocalTTSGeneration.id)
                    .where(LocalTTSGeneration.state == "queued")
                    .order_by(LocalTTSGeneration.created)
                    .limit(1)
                )
                engine.purge(set(db.scalars(select(ClonedVoice.id))))
                if task_id:
                    claimed = db.execute(
                        update(LocalTTSGeneration)
                        .where(LocalTTSGeneration.id == task_id, LocalTTSGeneration.state == "queued")
                        .values(
                            state="running",
                            stage="preparing_voice",
                            heartbeat=time.time(),
                            attempts=LocalTTSGeneration.attempts + 1,
                            started_at=time.time(),
                            completed_at=None,
                            first_audio_at=None,
                            stream_events=0,
                            audio_seconds=0,
                        )
                    ).rowcount
                    db.commit()
                    if not claimed:
                        continue
                    active[0] = task_id
                    row = db.get(LocalTTSWorker, "qwen3")
                    row.state = "busy"
                    db.commit()
                else:
                    db.commit()
            if task_id:
                process(task_id, engine)
                if not engine.cuda_graphs:
                    raise LocalTTSError(
                        "CUDA graph state is unavailable after generation; restart the worker."
                    )
                active[0] = None
                with DB() as db:
                    row = db.get(LocalTTSWorker, "qwen3")
                    row.state = "ready"
                    row.peak_vram_mb = max(row.peak_vram_mb, db.get(LocalTTSGeneration, task_id).peak_vram_mb)
                    db.commit()
            else:
                stop.wait(0.5)
    except Exception as exc:
        log.error("Qwen startup failed: %s: %s", type(exc).__name__, str(exc)[:1200])
        with DB() as db:
            row = db.get(LocalTTSWorker, "qwen3")
            row.state = "failed"
            row.error = (
                "The local voice model could not start. Review the GPU worker logs or run setup again."
            )
            db.commit()
        raise
    finally:
        stop.set()
        pulse.join(timeout=6)
        with DB() as db:
            row = db.get(LocalTTSWorker, "qwen3")
            if row.state != "failed":
                row.state = "offline"
            db.commit()


@contextmanager
def single_gpu_worker():
    path = settings.storage_dir.resolve().parent / "qwen-worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        handle.write(b"1")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_worker(engine_factory=QwenEngine, stop=None):
    with single_gpu_worker():
        return _run_worker(engine_factory, stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_worker()
