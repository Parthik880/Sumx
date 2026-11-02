"""Whole-narration generation inside one GPU container. Only the final MP3 leaves on success."""

import json
import subprocess
import tempfile
import time
from pathlib import Path
from modal_service.speech_contract import LocalTTSError, assemble_wav


def encode_mp3(source, output, ffmpeg="ffmpeg"):
    subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source), "-ac", "1", "-ar", "24000"]
        + ["-c:a", "libmp3lame", "-b:a", "128k", str(output)],
        check=True,
        timeout=300,
    )


def narrate(engine, payload, report=None):
    """Chunks run in order. A rejected chunk stops the job, keeping earlier chunks for its retry."""
    started = time.perf_counter()
    cloned = payload["voice_type"] == "cloned"
    chunks, prior = payload["chunks"], payload.get("prior_wavs") or {}
    total = payload["total_chunks"]
    done = len(prior)
    rows, generated = [], {}
    context = payload.get("clone_context") or {}

    def notify(stage, audio=0):
        if report:
            report({"stage": stage, "completed": done + len(generated), "audio_seconds": audio})

    with tempfile.TemporaryDirectory(prefix="sumx-narration-") as folder:
        folder = Path(folder)
        reference = None
        if cloned:
            reference = folder / "reference.wav"
            reference.write_bytes(payload["reference_wav"])
        notify("generating_audio")
        audio_seconds = 0.0
        for offset, text in enumerate(chunks):
            index = done + offset
            chunk_started = time.perf_counter()
            options = (
                {
                    "clone_context": {**context, "chunk_index": index},
                    "clone_options": payload.get("clone_options"),
                }
                if cloned
                else {}
            )
            try:
                wav, metrics = engine.synthesize(
                    text,
                    payload["language"],
                    reference=reference,
                    ref_text=payload["reference_text"],
                    speaker=payload["speaker"],
                    **options,
                )
            except LocalTTSError as exc:
                # Failure is the one case where finished chunks cross the network, so a retry
                # never regenerates them.
                return {
                    "ok": False,
                    "error": f"Narration chunk {index + 1}/{total} failed: {exc}",
                    "partial_wavs": generated,
                    "metrics": summary(rows, started, 0, getattr(exc, "metrics", {})),
                }
            generated[index] = wav
            audio_seconds += metrics["audio_seconds"]
            rows.append(chunk_row(index, metrics, time.perf_counter() - chunk_started))
            print(json.dumps({"event": "chunk", **rows[-1]}), flush=True)
            notify("generating_audio", audio_seconds)
        notify("encoding_audio", audio_seconds)
        assembly_started = time.perf_counter()
        parts = []
        for index in range(total):
            part = folder / f"{index}.wav"
            part.write_bytes(prior[index] if index in prior else generated[index])
            parts.append(part)
        raw, mp3 = folder / "narration.wav", folder / "narration.mp3"
        seconds = assemble_wav(parts, raw, payload["gap_ms"] if cloned else 0, fade=cloned)
        encode_mp3(raw, mp3)
        result = mp3.read_bytes()
    metrics = summary(rows, started, seconds, {"assembly_seconds": time.perf_counter() - assembly_started})
    print(json.dumps({"event": "narration", **metrics_log(metrics)}), flush=True)
    return {"ok": True, "mp3": result, "model": engine.model_id, "metrics": metrics}


def chunk_row(index, metrics, seconds):
    return {
        "chunk_index": index,
        "inference_seconds": seconds,
        "audio_seconds": metrics["audio_seconds"],
        "attempts": metrics.get("attempts", 1),
        "seed": metrics.get("seed"),
        "max_internal_silence_seconds": metrics.get("max_internal_silence_seconds", 0),
        "conditioning_seconds": metrics.get("conditioning_seconds", 0),
        "peak_vram_mb": metrics.get("peak_vram_mb", 0),
        "gpu_name": metrics.get("gpu_name"),
        "cuda_graphs": metrics.get("cuda_graphs"),
    }


def summary(rows, started, audio_seconds, extra):
    total = time.perf_counter() - started
    return {
        **extra,
        "chunks": rows,
        "generation_seconds": total,
        "audio_seconds": audio_seconds,
        "realtime_factor": audio_seconds / total if total else 0,
        "retries": sum(row["attempts"] - 1 for row in rows),
        "reference_conditioning_seconds": sum(row["conditioning_seconds"] for row in rows),
        "peak_vram_mb": max((row["peak_vram_mb"] for row in rows), default=0),
        "attempts": 1 + sum(row["attempts"] - 1 for row in rows),
        "max_internal_silence_seconds": max((row["max_internal_silence_seconds"] for row in rows), default=0),
    }


def metrics_log(metrics):
    return {k: v for k, v in metrics.items() if k != "chunks"}
