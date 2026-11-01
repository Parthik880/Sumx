# Faster Qwen3-TTS 0.6B and narration progress

Implemented and tested on 1 November 2026. SUMX uses Faster Qwen3-TTS exclusively for local cloned previews and narration. Google authentication, the approved-email database, OpenRouter Whisper/DeepSeek/Voxtral presets and existing recordings/narration remain intact.

## Runtime and configuration

The isolated `.venv-faster-qwen` environment contains Python 3.12, torch/torchaudio 2.10.0+cu128, faster-qwen3-tts 0.5.4, qwen-tts-hf 0.1.1.post1, Transformers 5.15.1 and huggingface-hub 1.33.0. It contains no standard `qwen-tts` distribution; `qwen-tts-hf` legitimately provides the underlying `qwen_tts` namespace required by Faster. The backend `.venv` stays separate. Exact GPU environment versions are in `requirements-qwen.lock`.

Verified hardware: RTX 3060, 12 GiB, driver 610.88. PyTorch CUDA and BF16 are available. Both predictor and talker CUDA graphs were captured and used for streaming; the worker only reports ready after graph verification. FlashAttention compilation is not required. The package emits harmless optional SoX/deprecation warnings in private logs; actual generation passed.

```env
LOCAL_VOICE_CLONING_ENABLED=true
LOCAL_TTS_PROVIDER=faster_qwen3
QWEN_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-0.6B-Base
QWEN_TTS_DEVICE=cuda:0
QWEN_TTS_ATTENTION=sdpa
QWEN_TTS_CUDA_GRAPHS=true
QWEN_TTS_STREAMING=true
QWEN_TTS_STREAM_CHUNK_SIZE=8
QWEN_TTS_MAX_CONCURRENT_JOBS=1
QWEN_TTS_MODEL_CACHE=./models/qwen3-tts
QWEN_TTS_CHUNK_CHARS=400
```

Unsupported provider/model/device/attention/concurrency/graph combinations and out-of-range chunk sizes are rejected. Streaming may explicitly be disabled while retaining the Faster CUDA Graph engine; the default is enabled. There is no standard-Qwen or CPU fallback.

## Setup and startup

```powershell
cd C:\Projects\Sumx
.\setup_qwen_tts.ps1
.\start_sumx.ps1
```

Setup installs in `.venv-faster-qwen`, verifies CUDA, downloads only the 0.6B Base model/tokenizer into the ignored cache, checks namespace/distribution conflicts and warms up the graphs. Stop an existing GPU worker before rerunning model-load setup. Launching SUMX never reinstalls dependencies or downloads weights. Runtime uses local snapshots and offline Hugging Face settings.

Individual launches remain `run_qwen_tts.ps1`, `run.ps1 api`, and `run.ps1 web`. API startup launches missing processing/GPU workers. Only one GPU worker can own the OS lock; queued requests are processed serially. Separate meetings can queue simultaneously. Repeated clicks on one active meeting return the same job instead of duplicating generation.

## Private references and migration

Both existing voice records, their consent timestamps and recordings were preserved. Saved voices continue to work after the model switch; they do not require recording again or losing their saved status. The user confirmed reading the displayed paragraph exactly: this transcript was filled into the empty voice draft. The saved voice's existing matching transcript and saved status were preserved. Both received real new 0.6B previews. The other voice remains a draft for the user to review/save; it was not silently promoted to saved.

The Faster wrapper uses its pinned, audited in-memory reference-conditioning cache. It reuses conditioning across segments; deleted voices purge cached entries. No private audio, transcript or target text is sent to OpenRouter. There is no worker network listener. Existing ownership, allowlist, consent, CSRF, file validation and permanent profile-deletion checks remain.

Migration `0006_faster_narration_progress.py` extends existing jobs with owner/voice/model, narration stage, timestamps, final private path and generated duration. Local segment jobs gain parent job ID, segment index, timestamps, first-audio time and stream-event count. Worker state gains engine/graph/load/warmup metrics. Backup: `data/sumx-before-faster-progress-20261101-111006.db`. Before migration there were 2 meetings, 2 voices, 3 narration files, 24 OpenRouter usage records and 13 local-generation records; all were retained.

## Accurate job progress

The existing durable meeting worker counts text segments before inference. Its lifecycle is QUEUED → LOADING_MODEL (when needed) → PREPARING_VOICE → GENERATING_AUDIO → MERGING_AUDIO → FINALIZING → COMPLETED, or FAILED. Intermediate MP3 parts/checkpoints survive safe retries. Model changes cannot mix old and new chunks.

Each real streaming yield updates generated audio duration and event count in the private database. Stream events are not counted as completed text segments. Segment completion advances only after the entire segment is generated, encoded, decoded/validated and checkpointed. Percentage is actual completed/total segments. When all segments are generated but merge/validation is unfinished, percentage is indeterminate. **100% is returned only after validated final audio and successful job completion.**

`GET /api/narration/jobs/{job_id}/status` requires the current user to own the meeting. It exposes stage, segment counts, measurable percentage/duration, elapsed time, engine/GPU label and readable errors; it exposes no private filesystem path or text. The white/blue progress card appears immediately on click, polls once per second, reconnects to the persisted job after page reload and stops polling on completion/failure. Streaming activity is indeterminate within a segment. Failure offers retry. Completion loads playback/download while preserving older narration versions.

All local costs remain in `local_tts_generations` as `provider=local_qwen3` with the actual 0.6B model and zero external API cost. Existing OpenRouter billing totals stay separate; this does not imply zero hardware/electricity cost.

## Measured workstation benchmark

The comparison used the same synthetic reference WAV, matching reference transcript, target texts, RTX 3060, torch build, BF16 and seed 1234. Long form used two text segments. These are full installed-pipeline comparisons, not a claim that dependency/model changes produce identical waveforms. Standard Qwen's first returned audio is full output; Faster's time-to-first-audio is its first streamed playable chunk.

| Engine/model | Load | Graph warmup | Cold preview | Warm first audio | Warm preview total | Long narration total | Long audio duration | Peak allocated VRAM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Standard 1.7B | 8.37 s | n/a | 14.17 s | 12.89 s (full output) | 12.89 s | 84.73 s | 38.32 s | 4756.5 MiB |
| Standard 0.6B | 7.24 s | n/a | 13.69 s | 11.90 s (full output) | 11.90 s | 83.96 s | 38.32 s | 2821.0 MiB |
| Faster 0.6B | 14.73 s | 1.22 s | 3.37 s | **0.44 s** | **2.90 s** | **17.93 s** | 37.20 s | 2807.9 MiB |

Warm preview improved about 4.4× relative to the prior 1.7B installation; long-form wall time improved about 4.7×. Relative to standard 0.6B, the measured improvements were about 4.1× and 4.7×. Faster's compute/audio real-time factors were 0.534 (warm preview) and 0.482 (long form); lower than 1 means synthesis completed faster than playback. Cold load and graph warmup are separate from inference. Worker reload after filesystem caches were warm took 8.02 seconds plus 1.25 seconds graph capture.

Full JSON reports and waveform comparisons are in ignored `data/faster-qwen-benchmark/`. Current repeatable benchmark:

```powershell
.\.venv-faster-qwen\Scripts\python -m backend.benchmark_qwen --engine faster
```

Audio durations/cadence differ between model/runtime versions. Valid decoding and measured speed were verified; pronunciation/timbre/naturalness parity has not been established by human listening. Both baseline and Faster waveforms are retained for that review.

## Real personal-voice tests

Saved-voice preview: 8.88 seconds of audio, 14 streaming events, first event 1.26 seconds, total processing 5.83 seconds. Draft-voice preview: 6.32 seconds of audio, 10 events, first event 0.60 seconds, processing 3.95 seconds. These use the existing consented recordings, not test replacements.

An existing authorized meeting was narrated with the saved personal voice through the normal durable worker: **9 completed text segments, 219.84 seconds of generated audio, 118.33 seconds wall time**, final validated MP3 available in the meeting. Prior narration remains. Peak allocation across real 0.6B jobs was 3107.45 MiB. OpenRouter usage remained at 24 rows: no external charge was created.

A separate one-segment integration test observed real QUEUED, GENERATING_AUDIO, MERGING_AUDIO and COMPLETED status responses. It recorded 38 streaming events while completed text segments stayed at zero; final output was 23.92 seconds, wall time 12.94 seconds, and 100% appeared only with COMPLETED. Trace: `data/faster-qwen-one-segment-trace.json`. Its temporary meeting was removed after validation; retained local metric rows have zero external cost.

## Retired runtime cleanup limitation

The active scripts and installed Faster environment no longer use standard Qwen or 1.7B. Automatic approval review rejected permanent removal twice, returning only **“blocked by policy”**, despite explicit user authorization and verified workspace paths. Thus `.venv-qwen` and `models/qwen3-tts/models--Qwen--Qwen3-TTS-12Hz-1.7B-Base` remain unused on disk. No deletion was claimed or bypassed. To complete removal manually, review and run `remove_retired_qwen.ps1`; it validates targets, refuses active processes/junctions and preserves the Faster environment, 0.6B cache and all user data.

## Files changed

Final verification: **70 backend tests passed** and **all 11 Chrome browser workflows passed**. Frontend TypeScript/Vite build, ESLint, Ruff, Alembic schema check, and PowerShell setup/launcher/cleanup-script parsing passed. Browser checks cover immediate progress, streaming activity, reload reconnect, one-segment work, no premature 100%, failure/retry and stopping polling at completion, plus the existing auth, recording, preset, library, analytics and light-theme workflows. The real Faster GPU worker and API are running and idle after the successful personal-voice generation.

- `.env`, `.env.example`, `.gitignore`, `requirements-qwen.txt`, `requirements-qwen.lock`.
- `setup_qwen_tts.ps1`, `run_qwen_tts.ps1`, `run.ps1`, `remove_retired_qwen.ps1`.
- `backend/config.py`, `backend/models.py`, `backend/qwen_runtime.py`, `backend/qwen_worker.py`, `backend/qwen_setup.py`, `backend/local_tts.py`, `backend/jobs.py`, `backend/main.py`, `backend/voices.py`, `backend/benchmark_qwen.py`.
- `backend/migrations/versions/0006_faster_narration_progress.py`.
- `frontend/src/NarrationProgress.tsx`, `frontend/src/main.tsx`, `frontend/src/VoiceStudio.tsx`, `frontend/src/voices.css`.
- `backend/tests/qwen_mock.py`, `backend/tests/test_qwen_runtime.py`, `backend/tests/test_narration_progress.py`, `backend/tests/test_voices.py`, `frontend/tests/narration-progress.spec.ts`, `frontend/tests/voices.spec.ts`.
- `README.md`, `docs/FASTER_QWEN_TTS.md`, current documentation pointers.

Sources checked: [Faster repository/API](https://github.com/andimarafioti/faster-qwen3-tts), [PyPI 0.5.4 package metadata](https://pypi.org/project/faster-qwen3-tts/0.5.4/), and installed `model.py`, `predictor_graph.py`, `talker_graph.py`.
