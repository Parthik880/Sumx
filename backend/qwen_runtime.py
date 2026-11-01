"""Faster Qwen CUDA Graph inference, imported only in the isolated GPU environment."""

from pathlib import Path

LANGUAGES = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}


class LocalTTSError(ValueError):
    pass


def split_narration(text, size=400):
    import re

    if not 80 <= size <= 600:
        raise LocalTTSError("Local narration chunk size must be between 80 and 600 characters")
    units = re.split(r"(?<=[.!?。！？])\s+|\n\s*\n", text.strip())
    chunks, current = [], ""
    for unit in units:
        while len(unit) > size:
            cut = unit.rfind(" ", 0, size + 1)
            cut = cut if cut > 0 else size
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:cut])
            unit = unit[cut:].lstrip()
        if len(current) + len(unit) + 1 > size:
            chunks.append(current)
            current = unit
        else:
            current = (current + " " + unit).strip()
    if current:
        chunks.append(current)
    return chunks


class QwenEngine:
    """Faster engine only. CUDA-graph failures never select the standard implementation."""

    def __init__(self, model_id, cache, device="cuda:0", attention="sdpa"):
        import time
        import torch
        from huggingface_hub import snapshot_download
        from faster_qwen3_tts import FasterQwen3TTS

        if not torch.cuda.is_available():
            raise LocalTTSError(
                "The local NVIDIA GPU is unavailable. Check the Faster Qwen environment and driver."
            )
        if device != "cuda:0" or attention != "sdpa":
            raise LocalTTSError("Faster narration requires cuda:0 with SDPA")
        self.torch = torch
        self.gpu_name = torch.cuda.get_device_name(0)
        self.vram_mb = torch.cuda.get_device_properties(0).total_memory / 1024**2
        self.dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        path = snapshot_download(model_id, cache_dir=str(Path(cache).resolve()), local_files_only=True)
        started = time.perf_counter()
        self.model = FasterQwen3TTS.from_pretrained(
            path,
            backend="torch",
            device=device,
            dtype=self.dtype,
            attn_implementation=attention,
            local_files_only=True,
        )
        torch.cuda.synchronize()
        self.load_seconds = time.perf_counter() - started
        self.warmup_seconds = 0
        self.cuda_graphs = False
        self.references = {}

    def warmup(self):
        import time

        started = time.perf_counter()
        try:
            self.model.warmup(prefill_len=100)
            self.torch.cuda.synchronize()
            self.cuda_graphs = bool(self.model.predictor_graph.captured and self.model.talker_graph.captured)
            if not self.cuda_graphs:
                raise RuntimeError("CUDA graphs were not captured")
        except Exception as exc:
            raise LocalTTSError(
                "CUDA graph warmup failed. Check the GPU worker logs and restart it; no slower fallback was selected."
            ) from exc
        self.warmup_seconds = time.perf_counter() - started

    def purge(self, valid_voice_ids):
        removed = set(self.references) - valid_voice_ids
        if removed:
            # Pinned faster-qwen3-tts 0.5.4 uses this in-memory reference cache.
            self.model._voice_prompt_cache.clear()
            for key in removed:
                self.references.pop(key, None)

    def generate(self, voice_id, reference, ref_text, text, language, output, stage=None):
        import time
        import numpy as np
        import soundfile as sf
        from .config import settings

        if language not in LANGUAGES:
            raise LocalTTSError("Choose a language supported by local Faster Qwen3-TTS")
        if not ref_text.strip():
            raise LocalTTSError("Add the exact words spoken in your reference recording first")
        if not Path(reference).is_file():
            raise LocalTTSError("The reference recording is missing. Restore it or record a new sample.")
        if not self.cuda_graphs:
            raise LocalTTSError("The CUDA graphs are not ready. Restart the local GPU worker.")
        torch = self.torch
        self.references[voice_id] = str(Path(reference).resolve())
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        first = None
        events = 0
        parts = []
        samples = 0
        rate = 24000
        try:
            with torch.inference_mode():
                if stage:
                    stage("preparing_voice")
                args = dict(
                    text=text,
                    language=LANGUAGES[language],
                    ref_audio=str(reference),
                    ref_text=ref_text,
                    max_new_tokens=2048,
                )
                if stage:
                    stage("generating_audio")
                if settings.qwen_tts_streaming:
                    for audio, rate, timing in self.model.generate_voice_clone_streaming(
                        **args, chunk_size=settings.qwen_tts_stream_chunk_size
                    ):
                        part = np.asarray(audio, dtype=np.float32).reshape(-1)
                        if not part.size:
                            continue
                        if first is None:
                            first = time.perf_counter() - started
                        parts.append(part)
                        samples += part.size
                        events += 1
                        if stage:
                            stage(
                                "generating_audio", {"audio_seconds": samples / rate, "stream_events": events}
                            )
                else:
                    waves, rate = self.model.generate_voice_clone(**args)
                    parts = [np.asarray(waves[0], dtype=np.float32).reshape(-1)]
                    first = time.perf_counter() - started
                    events = 1
            audio = np.concatenate(parts) if parts else np.array([], dtype=np.float32)
            if not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.00001:
                raise LocalTTSError("Local synthesis returned no usable audio. Retry this generation.")
            peak = float(np.max(np.abs(audio)))
            if peak > 0.98:
                audio = audio * (0.98 / peak)
            sf.write(str(output), audio, rate, subtype="PCM_16")
            while len(self.model._voice_prompt_cache) > 4:
                self.model._voice_prompt_cache.pop(next(iter(self.model._voice_prompt_cache)))
            return {
                "audio_seconds": len(audio) / rate,
                "peak_vram_mb": torch.cuda.max_memory_allocated() / 1024**2,
                "sample_rate": rate,
                "time_to_first_audio": first,
                "stream_events": events,
            }
        except torch.cuda.OutOfMemoryError as exc:
            self.model._voice_prompt_cache.clear()
            self.cuda_graphs = False
            torch.cuda.empty_cache()
            raise LocalTTSError(
                "The GPU ran out of memory. Close GPU-heavy apps, restart the GPU worker and retry."
            ) from exc
