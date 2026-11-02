"""Optimized CUDA inference. Runs only inside Modal GPU containers."""

import hashlib
import io
import json
import logging
import secrets
import time
from pathlib import Path
from modal_service.speech_contract import (
    BASE_MODEL,
    LANGUAGES,
    LocalTTSError,
    PRESET_MODEL,
    SAMPLE_RATE,
    SPEAKERS,
    clone_duration_limit,
    duration_budget,
    pcm_metrics,
    silence_metrics,
    speaker_id,
    validate_duration,
)

log = logging.getLogger("sumx.clone")

# Sampling and validation values come from the backend request; these match its defaults.
CLONE_DEFAULTS = {
    "temperature": 0.65,
    "top_k": 30,
    "top_p": 0.9,
    "repetition_penalty": 1.15,
    "max_new_tokens": 600,
    "duration_ratio": 2,
    "max_silence_seconds": 3,
    "silence_db": -45,
    "non_streaming_mode": True,
    "seed": None,
    "debug": False,
}


class CloneGenerationError(LocalTTSError):
    def __init__(self, message, metrics):
        super().__init__(message)
        self.metrics = metrics


class QwenEngine:
    def __init__(self, model_id, cache, device="cuda:0", attention="sdpa"):
        import torch
        from huggingface_hub import snapshot_download
        from faster_qwen3_tts import FasterQwen3TTS

        if not torch.cuda.is_available():
            raise LocalTTSError("The Modal NVIDIA GPU is unavailable")
        if model_id not in (BASE_MODEL, PRESET_MODEL) or device != "cuda:0" or attention != "sdpa":
            raise LocalTTSError("Use the supported Qwen 0.6B checkpoint on a CUDA GPU with SDPA")
        self.torch, self.model_id = torch, model_id
        self.gpu_name = torch.cuda.get_device_name(0)
        self.vram_mb = torch.cuda.get_device_properties(0).total_memory / 1024**2
        self.dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        started = time.perf_counter()
        path = snapshot_download(model_id, cache_dir=str(cache), local_files_only=True)
        self.model = FasterQwen3TTS.from_pretrained(
            path,
            backend="torch",
            device=device,
            dtype=self.dtype,
            attn_implementation=attention,
            local_files_only=True,
        )
        self.load_seconds = time.perf_counter() - started
        self.cuda_graphs = False
        self.warmup_seconds = 0
        self.debug_audio = []
        if model_id == PRESET_MODEL:
            actual = {s.lower() for s in self.model.model.get_supported_speakers()}
            if not {s.lower() for s in SPEAKERS} <= actual:
                raise LocalTTSError("The loaded checkpoint is missing a required built-in speaker")

    def warmup(self):
        started = time.perf_counter()
        try:
            self.model.warmup(prefill_len=100)
            self.torch.cuda.synchronize()
            self.cuda_graphs = bool(self.model.predictor_graph.captured and self.model.talker_graph.captured)
            if not self.cuda_graphs:
                raise RuntimeError()
        except Exception as exc:
            raise LocalTTSError(
                "CUDA graph warmup failed; no CPU or standard-Qwen fallback was selected"
            ) from exc
        self.warmup_seconds = time.perf_counter() - started

    def synthesize(
        self,
        text,
        language="en",
        *,
        reference=None,
        ref_text="",
        speaker=None,
        preview=False,
        clone_context=None,
        clone_options=None,
    ):
        if self.model_id == BASE_MODEL:
            return synthesize_clone(
                self, text, language, reference, ref_text, preview, clone_context, clone_options
            )
        import numpy as np
        import soundfile as sf

        if language not in LANGUAGES:
            raise LocalTTSError("Choose a language supported by Qwen3-TTS")
        if not self.cuda_graphs:
            raise LocalTTSError("The CUDA graphs are not ready")
        budget = duration_budget(text, preview)
        params = dict(
            text=text,
            language=LANGUAGES[language],
            max_new_tokens=budget["max_new_tokens"],
            min_new_tokens=2,
            temperature=0.65,
            top_k=30,
            top_p=0.9,
            repetition_penalty=1.15,
            do_sample=True,
            non_streaming_mode=True,
        )
        self.torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        failures = []
        try:
            # One bounded retry for a rejected waveform, never an unbounded generation loop.
            for attempt in range(2):
                if attempt:
                    params.update(temperature=0.5, repetition_penalty=1.2)
                with self.torch.inference_mode():
                    waves, rate = self.model.generate_custom_voice(**params, speaker=speaker_id(speaker))
                try:
                    if rate != SAMPLE_RATE or len(waves) != 1:
                        raise LocalTTSError(
                            "The model returned an unexpected audio sample rate or batch shape"
                        )
                    audio = np.asarray(waves[0], dtype=np.float32)
                    if audio.ndim == 2 and 1 in audio.shape:
                        audio = audio.reshape(-1)
                    if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                        raise LocalTTSError("The model returned empty, non-finite or invalid audio")
                    validate_duration(len(audio) / rate, text, preview)
                    if float(np.mean(np.abs(audio) >= 0.999)) > 0.01:
                        raise LocalTTSError("The generated waveform is excessively clipped")
                    if np.max(np.abs(audio)) > 1.2:
                        raise LocalTTSError("The generated waveform is severely overdriven")
                    peak = float(np.max(np.abs(audio)))
                    if peak > 0.98:
                        audio = audio * (0.98 / peak)
                    buffer = io.BytesIO()
                    sf.write(buffer, audio, rate, format="WAV", subtype="PCM_16")
                    result = buffer.getvalue()
                    metrics = pcm_metrics(result, text=text, preview=preview)
                    return result, {
                        **metrics,
                        "processing_seconds": time.perf_counter() - started,
                        "peak_vram_mb": self.torch.cuda.max_memory_allocated() / 1024**2,
                        "gpu_name": self.gpu_name,
                        "cuda_graphs": self.cuda_graphs,
                        "attempts": attempt + 1,
                        "max_new_tokens": budget["max_new_tokens"],
                        "expected_seconds": budget["expected_seconds"],
                        "prior_rejections": failures,
                        "decode_mode": "complete_waveform",
                    }
                except LocalTTSError as exc:
                    failures.append(type(exc).__name__)
                    if attempt:
                        raise
            raise LocalTTSError("No valid speech was generated")
        except self.torch.cuda.OutOfMemoryError as exc:
            self.cuda_graphs = False
            self.torch.cuda.empty_cache()
            raise LocalTTSError("The GPU ran out of memory. Retry after the container reloads.") from exc
        finally:
            # Private conditioning never leaks into a later user's request in a warm container.
            self.model._voice_prompt_cache.clear()


def synthesize_clone(engine, text, language, reference, ref_text, preview=False, context=None, options=None):
    import numpy as np
    import soundfile as sf

    context = context or {}
    options = {**CLONE_DEFAULTS, **(options or {})}
    if language not in LANGUAGES or not engine.cuda_graphs:
        raise LocalTTSError("Cloned speech requires a supported language and ready CUDA graphs")
    if not reference or not ref_text.strip() or not Path(reference).is_file():
        raise LocalTTSError("A validated reference and its accurate transcript are required")
    started = time.perf_counter()
    path, torch = Path(reference), engine.torch
    content = path.read_bytes()
    scope = (
        context.get("user_id"),
        context.get("profile_id"),
        context.get("job_id") or context.get("request_id"),
    )
    key = (*scope, hashlib.sha256(content).hexdigest(), ref_text)
    cached = getattr(engine, "_clone_prompt", None)
    cache_hit = bool(cached and cached["key"] == key and time.monotonic() - cached["time"] < 300)
    conditioning_seconds = 0
    if not cache_hit:
        conditioning_started = time.perf_counter()
        engine._clone_prompt = None
        pcm_metrics(content, reference=True)
        audio, rate = sf.read(path, dtype="float32")
        # Match Faster's documented append_silence=True reference path, exactly 0.5 s.
        audio = np.pad(audio, (0, round(rate * 0.5)))
        try:
            with torch.inference_mode():
                prompt = engine.model.model.create_voice_clone_prompt(
                    ref_audio=(audio, rate), ref_text=ref_text
                )
        except torch.cuda.OutOfMemoryError as exc:
            engine.cuda_graphs = False
            torch.cuda.empty_cache()
            raise LocalTTSError(
                "The GPU ran out of memory preparing the reference. Retry after the container reloads."
            ) from exc
        engine._clone_prompt = {"key": key, "time": time.monotonic(), "prompt": prompt}
        conditioning_seconds = time.perf_counter() - conditioning_started
    prompt = engine._clone_prompt["prompt"]
    engine._clone_prompt["time"] = time.monotonic()
    budget = duration_budget(text, preview)
    limit = min(options["max_new_tokens"], budget["max_new_tokens"])
    params = dict(
        text=text,
        language=LANGUAGES[language],
        max_new_tokens=limit,
        min_new_tokens=2,
        temperature=options["temperature"],
        top_k=options["top_k"],
        top_p=options["top_p"],
        repetition_penalty=options["repetition_penalty"],
        do_sample=True,
        non_streaming_mode=options["non_streaming_mode"],
        append_silence=True,
        xvec_only=False,
        ref_text=ref_text,
        voice_clone_prompt=prompt,
    )
    seed_base = options["seed"] if options["seed"] is not None else secrets.randbelow(2147483647)
    duration_limit = clone_duration_limit(text, preview, options["duration_ratio"])
    attempts = []
    torch.cuda.reset_peak_memory_stats()
    try:
        for attempt in range(3):
            seed = (seed_base + attempt * 104729) % 2147483647
            record = {
                "chunk_index": context.get("chunk_index", 0),
                "input_words": len(text.split()),
                "input_characters": len(text),
                "profile_id": context.get("profile_id"),
                "request_id": context.get("request_id"),
                "seed": seed,
                "attempt": attempt + 1,
                "generation_mode": "complete_waveform",
                "non_streaming_mode": params["non_streaming_mode"],
                "append_silence": True,
                "sampling": {
                    k: params[k]
                    for k in ("temperature", "top_k", "top_p", "repetition_penalty", "max_new_tokens")
                },
                "eos_reached": None,
                "hit_token_limit": None,
                "termination_source": "waveform_estimate",
                "reference_prompt_reused": cache_hit or attempt > 0,
                "validation": {
                    "max_silence_seconds": options["max_silence_seconds"],
                    "silence_db": options["silence_db"],
                    "max_duration_seconds": duration_limit,
                },
            }
            attempt_start = time.perf_counter()
            try:
                # Restore RNG state afterwards; preset voices retain their original RNG behavior.
                with torch.random.fork_rng(devices=[0]), torch.inference_mode():
                    torch.manual_seed(seed)
                    waves, rate = engine.model.generate_voice_clone(**params)
                if rate != SAMPLE_RATE or not isinstance(waves, (list, tuple)) or len(waves) != 1:
                    raise LocalTTSError("Unexpected cloned audio sample rate or batch shape")
                audio = np.asarray(waves[0], dtype=np.float32)
                if audio.ndim == 2 and 1 in audio.shape:
                    audio = audio.reshape(-1)
                if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                    raise LocalTTSError("Empty, non-finite or invalid cloned waveform")
                seconds = len(audio) / rate
                record.update(audio_seconds=seconds, **silence_metrics(audio, rate, options["silence_db"]))
                record["hit_token_limit"] = round(seconds * 12) >= limit - 1
                if options["debug"]:
                    raw = io.BytesIO()
                    sf.write(raw, audio, rate, format="WAV", subtype="FLOAT")
                    engine.debug_audio.append({"seed": seed, "wav": raw.getvalue()})
                if record["hit_token_limit"]:
                    raise LocalTTSError(
                        "Cloned generation reached its token limit without confirmed termination"
                    )
                if seconds > duration_limit:
                    raise LocalTTSError("Cloned speech duration is abnormal for this chunk")
                if (
                    max(
                        record[k]
                        for k in (
                            "max_internal_silence_seconds",
                            "leading_silence_seconds",
                            "trailing_silence_seconds",
                        )
                    )
                    > options["max_silence_seconds"]
                ):
                    raise LocalTTSError("Cloned speech contains an unexpectedly long silent section")
                if np.mean(np.abs(audio) >= 0.999) > 0.01 or np.max(np.abs(audio)) > 1.2:
                    raise LocalTTSError("Cloned speech is severely clipped")
                peak = float(np.max(np.abs(audio)))
                if peak > 0.98:
                    audio = audio * (0.98 / peak)
                buffer = io.BytesIO()
                sf.write(buffer, audio, rate, format="WAV", subtype="PCM_16")
                wav = buffer.getvalue()
                checked = pcm_metrics(wav, text=text, preview=preview)
                record["status"] = "validated"
            except torch.cuda.OutOfMemoryError:
                raise
            except LocalTTSError as exc:
                record.update(status="rejected", reason=str(exc))
            except (ValueError, RuntimeError, TypeError) as exc:
                record.update(status="rejected", reason=f"Model generation failed ({type(exc).__name__})")
            record["processing_seconds"] = time.perf_counter() - attempt_start
            attempts.append(record)
            log.info("Cloned chunk: %s", json.dumps(record))
            if record["status"] == "validated":
                return wav, {
                    **checked,
                    **record,
                    "attempts": attempt + 1,
                    "attempt_diagnostics": attempts,
                    "conditioning_seconds": conditioning_seconds,
                    "processing_seconds": time.perf_counter() - started,
                    "peak_vram_mb": torch.cuda.max_memory_allocated() / 1024**2,
                    "gpu_name": engine.gpu_name,
                    "cuda_graphs": engine.cuda_graphs,
                }
        raise CloneGenerationError(
            f"Cloned chunk {context.get('chunk_index', 0) + 1} failed validation after 3 attempts: {attempts[-1]['reason']}",
            {"attempts": 3, "attempt_diagnostics": attempts},
        )
    except torch.cuda.OutOfMemoryError as exc:
        engine.cuda_graphs = False
        engine._clone_prompt = None
        torch.cuda.empty_cache()
        raise LocalTTSError("The GPU ran out of memory. Retry after the container reloads.") from exc
    finally:
        engine.model._voice_prompt_cache.clear()
