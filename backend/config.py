from pathlib import Path
from urllib.parse import urlsplit
from pydantic import Field, model_validator
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
    tts_enabled: bool = True
    qwen_tts_model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    openrouter_tts_model: str = "mistralai/voxtral-mini-tts-3-0"
    tts_default_voice: str = "en_paul_neutral"
    preset_chunk_chars: int = Field(default=1800, ge=200, le=4000)
    preset_tts_concurrency: int = Field(default=3, ge=1, le=8)
    modal_token_id: str = ""
    modal_token_secret: str = ""
    modal_environment: str = ""
    qwen_tts_timeout: int = Field(default=3200, ge=60, le=3600)
    qwen_tts_chunk_chars: int = Field(default=240, ge=80, le=600)
    qwen_clone_chunk_words: int = Field(default=22, ge=10, le=35)
    qwen_clone_gap_ms: int = Field(default=200, ge=0, le=250)
    qwen_clone_temperature: float = Field(default=0.65, gt=0, le=1.5)
    qwen_clone_top_k: int = Field(default=30, ge=1, le=200)
    qwen_clone_top_p: float = Field(default=0.9, gt=0, le=1)
    qwen_clone_repetition_penalty: float = Field(default=1.15, ge=1, le=2.5)
    qwen_clone_max_new_tokens: int = Field(default=600, ge=48, le=1100)
    qwen_clone_duration_ratio: float = Field(default=2, ge=1.2, le=4)
    qwen_clone_max_silence_seconds: float = Field(default=3, ge=0.5, le=5)
    qwen_clone_silence_db: float = Field(default=-45, ge=-65, le=-25)
    qwen_clone_non_streaming_mode: bool = True
    qwen_clone_seed: int | None = Field(default=None, ge=0, le=2147483647)
    qwen_clone_debug: bool = False
    openrouter_stt_timestamps: bool = True
    summary_chunk_chars: int = 12000
    summary_note_concurrency: int = Field(default=2, ge=1, le=4)
    summary_request_deadline_seconds: int = Field(default=180, ge=1, le=600)
    summary_pipeline_deadline_seconds: int = Field(default=600, ge=1, le=3600)
    audio_chunk_seconds: int = 180
    stt_adaptive: bool = True
    stt_initial_concurrency: int = Field(default=2, ge=1, le=12)
    stt_max_concurrency: int = Field(default=12, ge=1, le=12)
    stt_concurrency_levels: list[int] = [12, 10, 8, 6, 4, 2, 1]
    stt_min_concurrency: int = Field(default=1, ge=1)
    # Shared by every meeting in every worker.
    stt_global_concurrency: int = Field(default=12, ge=1, le=64)
    stt_extract_concurrency: int = Field(default=2, ge=1, le=4)
    stt_chunk_attempts: int = Field(default=6, ge=1, le=12)
    stt_ambiguous_retries: int = Field(default=1, ge=0, le=3)
    stt_job_deadline_seconds: int = Field(default=1800, ge=60)
    stt_increase_successes: int = Field(default=12, ge=1)
    stt_increase_cooldown_seconds: int = Field(default=60, ge=0)
    stt_overlap_seconds: float = Field(default=1.5, ge=0, le=5)
    max_upload_mb: int = Field(default=0, ge=0)
    max_duration_seconds: int = 14400
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    @property
    def frontend_origins(self) -> list[str]:
        """Allow the two loopback names only for local HTTP development."""
        origin = urlsplit(self.app_url)
        if origin.scheme == "http" and origin.hostname in ("localhost", "127.0.0.1"):
            port = f":{origin.port}" if origin.port else ""
            return [f"http://localhost{port}", f"http://127.0.0.1{port}"]
        return [self.app_url]

    @model_validator(mode="after")
    def validate_frontend_origin(self):
        from .preset_voices import PRESETS
        from .speech_contract import BASE_MODEL

        if not self.stt_min_concurrency <= self.stt_initial_concurrency <= self.stt_max_concurrency:
            raise ValueError("STT concurrency must satisfy min <= initial <= max")
        if not self.stt_concurrency_levels or any(
            not isinstance(n, int) or n < 1 or n > 12 for n in self.stt_concurrency_levels
        ):
            raise ValueError("STT concurrency levels must be integers between 1 and 12")

        if self.qwen_tts_model != BASE_MODEL:
            raise ValueError("Use Qwen3-TTS 0.6B Base for cloned voices")
        if self.tts_default_voice not in PRESETS:
            raise ValueError("Choose a preset from backend/preset_voices.py as TTS_DEFAULT_VOICE")
        origin = urlsplit(self.app_url)
        if self.qwen_clone_debug and origin.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Cloned speech debug audio/text is restricted to local development")
        if origin.scheme == "https" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true for HTTPS deployments")
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
