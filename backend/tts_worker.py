"""CPU-only durable queue for speech tasks. Inference runs on Modal, never in this process."""

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import select, update
from .config import ROOT, settings
from .media import probe, run, safe_path
from .models import ClonedVoice, DB, LocalTTSGeneration, LocalTTSWorker, User, AllowedEmail
from .speech_contract import PIPELINE_VERSION, pcm_metrics, LocalTTSError
from .services.modal_tts_client import ModalSpeechClient
from .tts_usage import record_tts_usage

log = logging.getLogger("sumx.tts")


def clone_options():
    return {
        "temperature": settings.qwen_clone_temperature,
        "top_k": settings.qwen_clone_top_k,
        "top_p": settings.qwen_clone_top_p,
        "repetition_penalty": settings.qwen_clone_repetition_penalty,
        "max_new_tokens": settings.qwen_clone_max_new_tokens,
        "duration_ratio": settings.qwen_clone_duration_ratio,
        "max_silence_seconds": settings.qwen_clone_max_silence_seconds,
        "silence_db": settings.qwen_clone_silence_db,
        "non_streaming_mode": settings.qwen_clone_non_streaming_mode,
        "seed": settings.qwen_clone_seed,
        "debug": settings.qwen_clone_debug,
    }


def save_debug_audio(task, items):
    """Development only: keeps every generated waveform, including rejected attempts."""
    if not settings.qwen_clone_debug or not items:
        return
    folder = settings.storage_dir.parent / "clone-debug"
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        (folder / f"{task.id}-{item['seed']}.wav").write_bytes(item["wav"])


def process(task_id, engine):
    started = time.monotonic()
    wav = mp3 = None
    try:
        with DB() as db:
            task = db.get(LocalTTSGeneration, task_id)
            user = db.get(User, task.user_id)
            allowed = db.get(AllowedEmail, user.email) if user else None
            if not user or (user.role != "admin" and (not allowed or not allowed.active)):
                raise LocalTTSError("Access to speech generation is no longer approved")
            voice = db.get(ClonedVoice, task.voice_id) if task.voice_type == "cloned" else None
            reference = None
            if task.voice_type == "cloned":
                if (
                    not voice
                    or voice.user_id != task.user_id
                    or not voice.consent_at
                    or not voice.reference_verified_at
                ):
                    raise LocalTTSError("Revalidate your consented reference before generating speech")
                if task.reference_text != voice.reference_text:
                    raise LocalTTSError("The reference transcript changed. Start a new generation.")
                reference = safe_path(voice.reference_path).read_bytes()
            preview = task.purpose in ("voice_preview", "preset_preview")
            folder = (
                settings.storage_dir
                / ("voices" if voice else "preset-previews")
                / (voice.id if voice else task.user_id)
                / "gpu"
                / task.id
            )
            folder.mkdir(parents=True, exist_ok=True)
            wav, mp3 = folder / "audio.wav", folder / "audio.mp3"
            text = task.text
            payload = {
                "text": text,
                "reference_wav": reference,
                "reference_text": task.reference_text,
                "speaker": task.preset_voice,
                "voice_type": task.voice_type,
                "language": task.language,
                "preview": preview,
                "request_id": task.id,
            }
            if voice:
                payload["clone_options"] = clone_options()
                payload["clone_context"] = {
                    "user_id": task.user_id,
                    "profile_id": voice.id,
                    "job_id": task.parent_job_id,
                    "chunk_index": task.segment_index or 0,
                    "request_id": task.id,
                }

            def progress(stage, metrics=None):
                task.stage, task.heartbeat = stage, time.time()
                db.commit()

            def on_started(call_id):
                task.remote_call_id = call_id
                db.commit()

            result = engine.synthesize(
                payload, call_id=task.remote_call_id, on_started=on_started, progress=progress
            )
            metrics = result.get("metrics", {})
            task.diagnostics = metrics
            task.processing_seconds = float(metrics.get("gpu_execution_seconds", time.monotonic() - started))
            db.commit()
            save_debug_audio(task, result.get("debug_audio"))
            if not result.get("ok"):
                raise LocalTTSError(result.get("error") or "GPU synthesis rejected the generated speech")
            if result.get("model") != task.model:
                raise LocalTTSError("The GPU returned speech from an unexpected model")
            audio = result.get("wav")
            if not isinstance(audio, bytes):
                raise LocalTTSError("The GPU returned an invalid audio payload")
            checked = pcm_metrics(audio, text=text, preview=preview)
            wav.write_bytes(audio)
            if abs(probe(wav)[0] - checked["audio_seconds"]) > 0.1:
                raise LocalTTSError("The raw WAV failed playback validation")
            progress("encoding_audio")
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
            encoded_seconds, _ = probe(mp3)
            if abs(encoded_seconds - checked["audio_seconds"]) > 0.3:
                raise LocalTTSError("The encoded preview failed duration/playback validation")
            # Approval can be revoked during GPU generation.
            db.expire_all()
            user = db.get(User, task.user_id)
            allowed = db.get(AllowedEmail, user.email) if user else None
            if not user or (user.role != "admin" and (not allowed or not allowed.active)):
                raise LocalTTSError("Access to speech generation is no longer approved")
            task.state, task.stage = "completed", "completed"
            task.output_path = str(mp3)
            task.audio_seconds = checked["audio_seconds"]
            task.peak_vram_mb = float(metrics.get("peak_vram_mb", 0))
            task.completed_at = time.time()
            task.stream_events = 1
            task.first_audio_at = task.completed_at
            task.text, task.reference_text = "", ""
            if voice and task.purpose == "voice_preview":
                voice.preview_path, voice.model = str(mp3), task.model
                voice.preview_version = PIPELINE_VERSION
                voice.preview_until, voice.preview_error, voice.updated = 0, None, time.time()
            record_tts_usage(db, task)
            db.commit()
            if voice and preview and not settings.qwen_clone_debug:
                wav.unlink(missing_ok=True)
    except Exception as exc:
        fail_task(task_id, exc, started, (wav, mp3))


def fail_task(task_id, exc, started, outputs=()):
    log.warning("Speech task rejected: %s", type(exc).__name__)
    for output in outputs:
        if output:
            output.unlink(missing_ok=True)
    with DB() as db:
        task = db.get(LocalTTSGeneration, task_id)
        if task:
            task.state, task.stage = "failed", "failed"
            task.error = (
                str(exc)[:500]
                if isinstance(exc, LocalTTSError)
                else "Speech generation failed. Check the Modal deployment and retry."
            )
            task.completed_at = time.time()
            task.processing_seconds = task.processing_seconds or time.monotonic() - started
            task.remote_call_id = None
            record_tts_usage(db, task)
            voice = db.get(ClonedVoice, task.voice_id)
            if voice and task.purpose == "voice_preview":
                voice.preview_until, voice.preview_error = 0, task.error
            db.commit()


def approved(db, task):
    user = db.get(User, task.user_id)
    allowed = db.get(AllowedEmail, user.email) if user else None
    return bool(user and (user.role == "admin" or (allowed and allowed.active)))


def process_narration(task_id, engine):
    """One Modal invocation; only the final MP3 returns unless a chunk fails."""
    started = time.monotonic()
    mp3 = None
    try:
        with DB() as db:
            task = db.get(LocalTTSGeneration, task_id)
            if not approved(db, task):
                raise LocalTTSError("Access to speech generation is no longer approved")
            plan = task.diagnostics
            folder, done, total = Path(plan["folder"]), plan["done"], plan["total"]
            job_id = task.parent_job_id
            parts = [folder / f"{job_id}-speech-{i}.wav" for i in range(total)]
            payload = {
                "chunks": json.loads(task.text),
                "total_chunks": total,
                # Only a retry after a failure sends finished chunks back to Modal.
                "prior_wavs": {i: parts[i].read_bytes() for i in range(done)},
                "reference_wav": None,
                "reference_text": task.reference_text,
                "speaker": task.preset_voice,
                "voice_type": task.voice_type,
                "language": task.language,
                "request_id": task.id,
                "gap_ms": settings.qwen_clone_gap_ms,
            }
            if task.voice_type == "cloned":
                voice = db.get(ClonedVoice, task.voice_id)
                if (
                    not voice
                    or voice.user_id != task.user_id
                    or not voice.consent_at
                    or not voice.reference_verified_at
                ):
                    raise LocalTTSError("Revalidate your consented reference before generating speech")
                if task.reference_text != voice.reference_text:
                    raise LocalTTSError("The reference transcript changed. Start a new generation.")
                payload["reference_wav"] = safe_path(voice.reference_path).read_bytes()
                payload["clone_options"] = clone_options()
                payload["clone_context"] = {
                    "user_id": task.user_id,
                    "profile_id": voice.id,
                    "job_id": job_id,
                    "request_id": task.id,
                }

            def progress(stage, update=None):
                task.stage, task.heartbeat = stage, time.time()
                if update:
                    task.stream_events = update["completed"]
                    task.audio_seconds = update["audio_seconds"]
                db.commit()

            def on_started(call_id):
                task.remote_call_id = call_id
                db.commit()

            progress("generating_audio")
            result = engine.narrate(
                payload, call_id=task.remote_call_id, on_started=on_started, progress=progress
            )
            metrics = result.get("metrics", {})
            task.diagnostics = metrics
            task.processing_seconds = float(metrics.get("gpu_execution_seconds", time.monotonic() - started))
            task.peak_vram_mb = float(metrics.get("peak_vram_mb", 0))
            db.commit()
            save_debug_audio(task, result.get("debug_audio"))
            if not result.get("ok"):
                for index, wav in (result.get("partial_wavs") or {}).items():
                    pcm_metrics(wav)
                    parts[int(index)].write_bytes(wav)
                raise LocalTTSError(result.get("error") or "GPU synthesis rejected the narration")
            if result.get("model") != task.model:
                raise LocalTTSError("The GPU returned speech from an unexpected model")
            audio = result.get("mp3")
            if not isinstance(audio, bytes):
                raise LocalTTSError("The GPU returned an invalid audio payload")
            mp3 = folder / f"{job_id}-{task.id}.mp3"
            mp3.write_bytes(audio)
            if abs(probe(mp3)[0] - metrics["audio_seconds"]) > 0.3:
                raise LocalTTSError("The assembled narration failed duration validation")
            # Approval can be revoked during GPU generation.
            db.expire_all()
            if not approved(db, task):
                raise LocalTTSError("Access to speech generation is no longer approved")
            task.state, task.stage = "completed", "completed"
            task.output_path = str(mp3)
            task.audio_seconds = metrics["audio_seconds"]
            task.completed_at = task.first_audio_at = time.time()
            task.text, task.reference_text = "", ""
            record_tts_usage(db, task)
            db.commit()
    except Exception as exc:
        fail_task(task_id, exc, started, (mp3,))


def _run_worker(engine_factory=ModalSpeechClient, stop=None):
    stop = stop or threading.Event()
    active = [None]
    with DB() as db:
        row = db.get(LocalTTSWorker, "qwen3")
        if not row:
            row = LocalTTSWorker(id="qwen3", state="loading", model=settings.qwen_tts_model)
            db.add(row)
        row.engine, row.state = "modal_faster_qwen3", "loading"
        row.cuda_graphs, row.gpu_name = False, None
        # A remembered remote call is collected, not regenerated.
        db.execute(
            update(LocalTTSGeneration)
            .where(LocalTTSGeneration.state == "running")
            .values(state="queued", stage="queued")
        )
        db.commit()

    def pulse():
        while not stop.wait(3):
            with DB() as db:
                row = db.get(LocalTTSWorker, "qwen3")
                row.heartbeat = time.time()
                if active[0]:
                    task = db.get(LocalTTSGeneration, active[0])
                    if task:
                        task.heartbeat = time.time()
                db.commit()

    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            try:
                engine = engine_factory()
                break
            except Exception as exc:
                log.warning("Modal client unavailable: %s", type(exc).__name__)
                with DB() as db:
                    row = db.get(LocalTTSWorker, "qwen3")
                    row.state, row.error = (
                        "offline",
                        "The Modal speech app is unavailable. Deploy it and check the server credentials.",
                    )
                    db.commit()
                if stop.wait(10):
                    return
        else:
            return
        with DB() as db:
            row = db.get(LocalTTSWorker, "qwen3")
            row.state, row.error = "ready", None
            row.gpu_name, row.vram_mb = engine.gpu_name, engine.vram_mb
            db.commit()
        while not stop.is_set():
            with DB() as db:
                task_id = db.scalar(
                    select(LocalTTSGeneration.id)
                    .where(LocalTTSGeneration.state == "queued")
                    .order_by(LocalTTSGeneration.created)
                    .limit(1)
                )
                if task_id:
                    claimed = db.execute(
                        update(LocalTTSGeneration)
                        .where(LocalTTSGeneration.id == task_id, LocalTTSGeneration.state == "queued")
                        .values(
                            state="running",
                            stage="loading_model",
                            heartbeat=time.time(),
                            attempts=LocalTTSGeneration.attempts + 1,
                            started_at=time.time(),
                            completed_at=None,
                        )
                    ).rowcount
                    db.commit()
                    if not claimed:
                        continue
                    active[0] = task_id
                    row = db.get(LocalTTSWorker, "qwen3")
                    row.state = "busy"
                    db.commit()
            if task_id:
                with DB() as db:
                    whole = db.get(LocalTTSGeneration, task_id).purpose == "narration"
                (process_narration if whole else process)(task_id, engine)
                active[0] = None
                with DB() as db:
                    row = db.get(LocalTTSWorker, "qwen3")
                    task = db.get(LocalTTSGeneration, task_id)
                    row.state = "ready"
                    if task.diagnostics.get("modal_gpu"):
                        row.gpu_name = "Modal " + task.diagnostics["modal_gpu"]
                    row.cuda_graphs = bool(task.diagnostics.get("cuda_graphs"))
                    row.peak_vram_mb = max(row.peak_vram_mb, task.peak_vram_mb)
                    db.commit()
            else:
                stop.wait(0.5)
    finally:
        stop.set()
        thread.join(timeout=5)
        with DB() as db:
            row = db.get(LocalTTSWorker, "qwen3")
            row.state = "offline"
            db.commit()


@contextmanager
def single_tts_worker():
    path = settings.storage_dir.resolve().parent / "tts-worker.lock"
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


def run_worker(engine_factory=ModalSpeechClient, stop=None):
    with single_tts_worker():
        return _run_worker(engine_factory, stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    assert ROOT.is_dir()
    run_worker()
