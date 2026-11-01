from pathlib import Path
from urllib.parse import urlsplit
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "sqlite:///" + (ROOT / "data" / "sumx.db").as_posix()
    storage_dir: Path = ROOT / "data" / "media"
    app_url: str = "http://localhost:5173"
    session_secret: str = ""
    cookie_secure: bool = True
    session_hours: int = 12
    google_client_id: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_stt_model: str = "openai/whisper-large-v3"
    openrouter_summary_model: str = "deepseek/deepseek-v4.1-flash-1017"
    openrouter_tts_model: str = "mistralai/voxtral-mini-tts-2603"
    local_voice_cloning_enabled: bool = True
    local_tts_provider: str = "faster_qwen3"
    qwen_tts_model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    qwen_tts_cuda_graphs: bool = True
    qwen_tts_streaming: bool = True
    qwen_tts_stream_chunk_size: int = 8
    qwen_tts_device: str = "cuda:0"
    qwen_tts_attention: str = "sdpa"
    qwen_tts_max_concurrent_jobs: int = 1
    qwen_tts_model_cache: Path = ROOT / "models" / "qwen3-tts"
    qwen_tts_chunk_chars: int = 400
    openrouter_tts_voice: str = "en_paul_neutral"
    openrouter_stt_timestamps: bool = True
    summary_chunk_chars: int = 24000
    audio_chunk_seconds: int = 180
    tts_chunk_chars: int = 1800
    max_upload_mb: int = 250
    max_duration_seconds: int = 14400
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    @model_validator(mode="after")
    def validate_frontend_origin(self):
        if self.local_voice_cloning_enabled and (
            self.local_tts_provider != "faster_qwen3"
            or self.qwen_tts_model != "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
            or self.qwen_tts_device != "cuda:0"
            or self.qwen_tts_attention != "sdpa"
            or not self.qwen_tts_cuda_graphs
            or self.qwen_tts_max_concurrent_jobs != 1
            or not 1 <= self.qwen_tts_stream_chunk_size <= 32
            or not 80 <= self.qwen_tts_chunk_chars <= 600
        ):
            raise ValueError(
                "Local narration requires Faster Qwen3-TTS 0.6B Base, cuda:0, SDPA, CUDA Graphs, one GPU job, stream size 1–32 and text segments 80–600 characters"
            )
        if not self.qwen_tts_model_cache.is_absolute():
            self.qwen_tts_model_cache = ROOT / self.qwen_tts_model_cache
        origin = urlsplit(self.app_url)
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
        ):
            raise ValueError(
                "APP_URL must be an exact frontend origin, such as http://localhost:5173, without a path or trailing slash"
            )
        return self


settings = Settings()
