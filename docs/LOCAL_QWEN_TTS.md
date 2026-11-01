# Local Qwen3-TTS in SUMX

Historical standard-engine implementation. The active runtime is now [Faster Qwen3-TTS 0.6B with CUDA Graphs and live narration progress](FASTER_QWEN_TTS.md); use that guide for current setup and model configuration.

Implemented and exercised on 1 November 2026. Cloned previews and meeting narration run locally. OpenRouter remains responsible for Whisper STT, the configured DeepSeek summary model and Voxtral preset narration. Cloned reference audio and target text are never sent to an external inference provider.

## Installed workstation configuration

| Component | Verified value |
| --- | --- |
| GPU | NVIDIA GeForce RTX 3060, 12 GiB VRAM |
| Driver | 610.88; NVIDIA reports CUDA compatibility 13.3 |
| Isolated Python | 3.12.0 in `.venv-qwen` |
| PyTorch / torchaudio | 2.10.0+cu128 |
| Official package | qwen-tts 0.1.1; Transformers 4.57.3; Accelerate 1.12.0 |
| Selected model | Qwen/Qwen3-TTS-12Hz-1.7B-Base |
| Inference | cuda:0, bfloat16, PyTorch SDPA |
| Loaded model allocation | approximately 4,007 MiB |
| Measured generation peak | 4,756.55 MiB / approximately 4.65 GiB PyTorch allocated VRAM |

`torch.cuda.is_available()`, GPU properties, BF16 support and a CUDA matrix multiplication passed. The primary model fit, so no fallback was used. Total GPU memory visible in NVIDIA-SMI also includes desktop/browser use and PyTorch reserved memory; it is higher than the allocation metric above.

The backend remains in its original `.venv` / Python 3.13 environment. No CUDA/ML packages were installed there. Runtime constraints are in `requirements-qwen.txt`; exact installed versions are captured in `requirements-qwen.lock`. FlashAttention is not required or installed. The official package may emit optional FlashAttention/SoX import warnings; the tested 12 Hz path uses SDPA and librosa and generated audio successfully without the SoX executable.

## Install and launch

```powershell
cd C:\Projects\Sumx
.\setup_qwen_tts.ps1
.\start_sumx.ps1
```

Setup creates/reuses only `.venv-qwen`, installs CUDA 12.8 wheels from PyTorch's official index, installs the official Qwen package and runtime dependencies, verifies CUDA, downloads the Base model and tokenizer to the configured cache, then verifies model loading. It does not overwrite the FastAPI environment. FFmpeg/FFprobe must already be on PATH (both were found and exercised on this computer).

`start_sumx.ps1` starts the API launcher and frontend. `run.ps1 api` starts an installed Qwen worker and the meeting worker before starting FastAPI. Startup never reinstalls packages or downloads model weights. The inference worker loads an already downloaded snapshot with offline Hugging Face/Transformers settings. Models are ignored by Git under `models/`; references and generated audio remain under ignored private `data/` storage.

Individual terminals:

```powershell
.\run_qwen_tts.ps1       # GPU worker only
.\run.ps1 api           # API + missing background workers
.\run.ps1 web           # React/Vite frontend
```

For an explicit lower-memory selection:

```powershell
.\setup_qwen_tts.ps1 -Model Qwen/Qwen3-TTS-12Hz-0.6B-Base
# Set QWEN_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-0.6B-Base in .env, then restart workers/API.
```

Stop an existing GPU worker before rerunning model-load setup to avoid loading a second model alongside it. Setup reports insufficient free VRAM rather than silently changing models. An OOM error preserves the reference and allows retry after freeing memory or explicitly choosing the smaller model. A model change requires a new preview; no new recording is required.

## Configuration

```env
LOCAL_VOICE_CLONING_ENABLED=true
LOCAL_TTS_PROVIDER=qwen3
QWEN_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-Base
QWEN_TTS_FALLBACK_MODEL=Qwen/Qwen3-TTS-12Hz-0.6B-Base
QWEN_TTS_DEVICE=cuda:0
QWEN_TTS_ATTENTION=sdpa
QWEN_TTS_MAX_CONCURRENT_JOBS=1
QWEN_TTS_MODEL_CACHE=./models/qwen3-tts
QWEN_TTS_CHUNK_CHARS=400
OPENROUTER_TTS_MODEL=mistralai/voxtral-mini-tts-2603
```

The fallback setting documents the explicit alternative; runtime never switches automatically. The former `OPENROUTER_CLONING_MODEL`, remote capability discovery and OpenRouter reference-audio request code were removed.

## Voice library and local inference

Existing profiles, consent records, reference audio, previews and meeting narration are retained. Migration 0005 adds `reference_text`, initially empty for old profiles because SUMX cannot assume what was spoken. Open a draft, play the existing recording, enter its exact transcript and choose a supported language; generate a new local preview and Save Voice. Recording again is optional. The studio still supports microphone levels, pause/resume, review, search, rename and permanent deletion.

Supported languages are English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish and Italian. Unsupported saved languages must be explicitly changed or a suitable sample recorded. Qwen does not support the prior Hindi/Arabic/Dutch options in this configuration.

`backend/qwen_worker.py` is a dedicated process with no network listener. FastAPI authenticates and validates ownership/consent, then writes a private database task. The worker revalidates ownership, active access, reference path, consent and transcript. It loads `Qwen3TTSModel.from_pretrained` once. `create_voice_clone_prompt` prepares an in-memory, bounded cache keyed by voice/reference/transcript; `generate_voice_clone` reuses this conditioning across chunks under `torch.inference_mode()`. No prompt tensors are pickled or exposed publicly. Deleted voices are purged from the in-memory cache on the next worker loop.

A process file lock and database worker lease prevent duplicate GPU workers. Only one generation executes at a time. Queue/task heartbeats support crash recovery. The studio polls genuine queued/preparing/generating/encoding/completed states; a preview is published only after WAV generation, MP3 conversion and successful FFprobe decoding. Technical diagnostics stay in private server logs; regular users see actionable errors.

Long narration splits at sentence/paragraph boundaries (400 characters by default), reuses the same model and prompt, checkpoints each valid part and joins compatible mono 24 kHz MP3 chunks in order. Completed local task results can be reused on retry without another synthesis. Main job and summary-version protections remain. A failed task can be retried; restarting a crashed worker recovers expired running leases. References and preview files are owner-only. Deleting a voice removes its private directory (including conditioning-source and intermediate files) and clears text/file paths from retained metric records; existing meeting narrations remain available.

## Storage and costs

Migration `0005_local_qwen_tts.py` adds `cloned_voices.reference_text`, `local_tts_generations` and `local_tts_worker`. Before migration, `data/sumx-before-local-qwen-20261101-103459.db` backed up the database. The migration preserved 1 user, 1 approved-email entry, 2 meetings, 2 transcripts, 2 summaries, 2 narration outputs, 2 voice drafts and 24 OpenRouter usage records.

Local generations record `provider=local_qwen3`, actual model, owner, optional meeting, purpose, character count, audio duration, processing time, peak allocated VRAM, attempts and status. External API cost is explicitly zero. These records are separate from OpenRouter billing; no synthetic OpenRouter cost row is created. Admin analytics shows local computation separately. Meeting API totals and historical external charges remain unchanged. Zero external charge does not mean zero hardware/electricity cost.

## Tests and real GPU results

Automated tests mock inference only; they exercise consent, audio validation, all owner-only operations, active work/deletion, CSRF, exact-transcript updates, unsupported languages, worker unavailability, failures/retries, long-form chunking, valid MP3 output, no external calls for cloned voices, separate accounting, CUDA-unavailable behavior, official load arguments, OOM cleanup and process singleton locking. Browser tests cover the studio and existing recording, preset narration, Google login, light theme and analytics.

Real GPU tests used the clearly synthetic Windows speech fixture already held privately by the project, with its matching transcript. No existing personal draft was modified or falsely assigned a transcript. The official 1.7B model generated:

- A direct short WAV smoke test: 3.92 seconds of audio; 24 kHz; about 4,486.76 MiB peak allocation.
- A durable-queue preview: 5.92 seconds of audio, converted to playable MP3 in 14.875 seconds.
- A durable meeting narration: two chunks, 368 and 311 characters; 22.72 and 18.96 seconds of audio; combined MP3 duration 41.736 seconds. Chunk processing took 48.985 and 39.516 seconds. Peak allocation was 4,756.55 MiB.

No OpenRouter request or billing row was added by these local tests. Temporary test voice/meeting entries were removed; the original 2 drafts, 2 meetings and 24 external usage rows remained. Three local generation metric rows remain, each with zero external API charge. The measured report is `data/local-qwen-smoke-report.json`. Private playable artifacts are `data/media/integration-voice-check/qwen-local-preview.mp3` and `qwen-local-narration.mp3`.

Human voice likeness still requires a consented personal recording, accurate transcript and listening review. Existing personal drafts are ready for that transcript step; their original recordings were preserved.

Verification commands:

Final verification: **60 backend tests passed**; all **10 browser workflows passed**, followed by another passing run of the 4 studio/analytics workflows after final changes. TypeScript/Vite build, ESLint, Ruff, Alembic schema check and PowerShell setup/launcher parsing passed. The API, meeting worker and real GPU worker are running with the final local configuration.

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
.\.venv\Scripts\ruff check backend
.\.venv\Scripts\python -m alembic check
cd frontend
npm run build
npm run lint
npm test
```

## Changed files

- Configuration/launch: `.env`, `.env.example`, `.gitignore`, `backend/config.py`, `requirements-qwen.txt`, `requirements-qwen.lock`, `setup_qwen_tts.ps1`, `run_qwen_tts.ps1`, `start_sumx.ps1`, `run.ps1`.
- Runtime/backend: `backend/qwen_runtime.py`, `backend/qwen_worker.py`, `backend/qwen_setup.py`, `backend/local_tts.py`, `backend/models.py`, `backend/voices.py`, `backend/jobs.py`, `backend/provider.py`, `backend/main.py`, `backend/analytics.py`.
- Migration: `backend/migrations/versions/0005_local_qwen_tts.py`.
- Frontend: `frontend/src/VoiceStudio.tsx`, `frontend/src/voices.css`, `frontend/src/CostAnalytics.tsx`.
- Tests: `backend/tests/qwen_mock.py`, `backend/tests/test_qwen_runtime.py`, `backend/tests/test_voices.py`, `backend/tests/e2e_server.py`, `frontend/tests/voices.spec.ts`.
- Docs: `README.md`, `docs/VOICE_STUDIO.md`, `docs/OPENROUTER.md`, `docs/LOCAL_QWEN_TTS.md`.

Primary sources: [official Qwen3-TTS package/API](https://github.com/QwenLM/Qwen3-TTS), [official Base checkpoint](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), [PyTorch CUDA wheels](https://download.pytorch.org/whl/cu128).
