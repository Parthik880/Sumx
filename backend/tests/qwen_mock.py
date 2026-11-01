"""Deterministic local worker fixture. No GPU or network used by automated tests."""

from backend.media import run


class MockQwenEngine:
    gpu_name = "Mock NVIDIA GPU (test fixture)"
    vram_mb = 12288
    cuda_graphs = True
    load_seconds = 0
    warmup_seconds = 0

    def __init__(self, *args):
        pass

    def purge(self, ids):
        pass

    def warmup(self):
        pass

    def generate(self, voice_id, reference, ref_text, text, language, output, stage=None):
        from backend.qwen_runtime import LANGUAGES, LocalTTSError

        if language not in LANGUAGES:
            raise LocalTTSError("Unsupported language")
        if not reference.is_file():
            raise LocalTTSError("Reference is missing")
        if stage:
            stage("generating_audio", {"stream_events": 1, "audio_seconds": 0.2})
        output.write_bytes(
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.4",
                    "-f",
                    "wav",
                    "pipe:1",
                ]
            )
        )
        return {"audio_seconds": 0.4, "peak_vram_mb": 123, "sample_rate": 44100}
