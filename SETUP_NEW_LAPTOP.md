# SUMX — Windows laptop setup

Setup date: 3 November 2026 (Asia/Kolkata)
Project: `C:\Users\PARTHIK\Music\Sumx`
Repository: https://github.com/Parthik880/Sumx

## Status

**PARTIAL — local application is running and verified.** Hosted transcription, DeepSeek summaries and preset narration passed real, small synthetic requests. Cloned-voice inference requires Modal credentials. Real Google account sign-in requires the Google Console origins and a local approved-email entry; no live Google consent was performed.

## Start and stop

Double-click `C:\Users\PARTHIK\Music\Sumx\start-sumx.bat`.

The launcher checks the virtual environment, imports, dependency consistency, Node version, FFmpeg/FFprobe, configuration, database migration revision and ports. It starts the API, meeting worker, speech queue worker and Vite as separate hidden background processes, waits for API/frontend/proxy readiness, then opens the browser. Running it again reuses this project's services. An unrelated process on port 8000 or 5173 produces a PID-specific error without terminating it. Dependencies are not reinstalled on launch.

The batch file uses a process-scoped PowerShell execution-policy option. No persistent execution policy is changed. The older `start_sumx.ps1` command forwards to the new launcher.

```powershell
cd C:\Users\PARTHIK\Music\Sumx
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-sumx.ps1
# Start without opening a browser:
.\start-sumx.bat -NoBrowser
# Stop this project's API, frontend and queue workers after jobs finish:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\stop-sumx.ps1
```

| Page | Exact URL |
| --- | --- |
| Application | http://localhost:5173 |
| Alternate loopback origin | http://127.0.0.1:5173 |
| Google user login | http://localhost:5173/login |
| Administrator | http://localhost:5173/admin |
| Backend health | http://127.0.0.1:8000/api/health |
| API documentation | http://127.0.0.1:8000/docs |

## Local administrator

A new local administrator was provisioned with a cryptographically generated password and an Argon2id database hash. No existing account was replaced. Admin ID: `sumx-local-admin`.

Read the password locally in `C:\Users\PARTHIK\Music\Sumx\data\local-admin-credentials.txt`. This private file and the environment files have access restricted to the current Windows account and SYSTEM. Passwords and API keys are intentionally absent from this report and terminal output. The synthetic `local-admin@sumx.invalid` database identifier is not an external email account.

Use Administration to approve the real Google email addresses that should access the meeting workspace. No real person's address was guessed or automatically approved. The local allowlist initially contains zero entries.

## Tools and dependencies

| Tool | Verified version / status |
| --- | --- |
| Git | 2.55.0.windows.5, already installed |
| Backend Python | 3.13.17, installed per user with winget Python.Python.3.13 |
| Python environment | `.venv`, independent of the old `venv` folder |
| pip | 26.3 in `.venv` |
| setuptools / wheel | 84.2.1 / 0.48.0 |
| Node.js | 24.20.0, already installed |
| npm | 11.19.0 |
| FFmpeg / FFprobe | 9.0.2 full build, installed with winget Gyan.FFmpeg |
| Visual C++ x64 runtime | 14.51.36247.00, already installed |
| C++ Build Tools | Not needed; all backend dependencies installed from wheels |
| Frontend | React 19.3.1, Vite 7.3.7, TypeScript 5.9.3 from the existing lockfile |
| API | FastAPI 0.162.0, Uvicorn 0.54.0, SQLAlchemy 2.0.44, Modal SDK 1.6.3 |

`backend/requirements-lock.txt` was installed exactly. `frontend/package-lock.json` was used with `npm ci`; it was not rewritten. No CUDA, GPU PyTorch build, NVIDIA driver or local model weights were installed. Windows user PATH contains the FFmpeg commands; the launcher refreshes its environment from the persisted user/machine PATH.

## Environment configuration

The backend, both workers and API read the existing **root `.env`**. Its existing credentials were reused without printing them. `data/env-before-laptop-setup.bak` preserves the prior environment. Empty `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` and `MODAL_ENVIRONMENT` entries were added for the unavailable integration.

| Name | Local configuration |
| --- | --- |
| `SESSION_SECRET` | Existing value preserved; length validation passed |
| `APP_URL` | `http://localhost:5173` |
| `COOKIE_SECURE` | `False` for local HTTP |
| `GOOGLE_CLIENT_ID` | Existing public web client ID reused; frontend obtains it from `/api/auth/google/config` |
| `OPENROUTER_API_KEY` | Existing inference key authenticated successfully; it is not a management/provisioning key |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` |
| `OPENROUTER_STT_MODEL` | `openai/whisper-large-v3` |
| `OPENROUTER_SUMMARY_MODEL` | `deepseek/deepseek-v4.1-flash-1017`, matching source default and requested DeepSeek setup; replaced the prior local Gemini override |
| `OPENROUTER_TTS_MODEL` | `mistralai/voxtral-mini-tts-3-0`, existing preset pathway retained |
| `QWEN_TTS_MODEL` | `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, remote cloned voices |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | Blank named placeholders; real existing-account credentials are required |
| `MODAL_ENVIRONMENT` | Blank uses the Modal default environment |
| `DATABASE_URL` | Default local SQLite at project `data/sumx.db` |
| `STORAGE_DIR` | Default private project `data/media` |
| `FFMPEG`, `FFPROBE` | Absolute installed executable paths in root `.env`, also available on the persisted user PATH |
| Frontend `API_TARGET` | Default Vite proxy target `http://127.0.0.1:8000`; browser uses same-origin `/api` |

The existing `GOOGLE_CLIENT_SECRET` and `OAUTH_REDIRECT_URI` entries were preserved but are unused by the implemented popup ID-token flow. Do not put API keys, client secrets or Modal tokens in the frontend. Restart the API/workers after changing server settings.

All supported backend environment variable names (defaults and bounds live in `backend/config.py`):

`DATABASE_URL`, `STORAGE_DIR`, `APP_URL`, `SESSION_SECRET`, `COOKIE_SECURE`, `SESSION_HOURS`, `GOOGLE_CLIENT_ID`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `OPENROUTER_STT_MODEL`, `OPENROUTER_SUMMARY_MODEL`, `TTS_ENABLED`, `QWEN_TTS_MODEL`, `OPENROUTER_TTS_MODEL`, `TTS_DEFAULT_VOICE`, `PRESET_CHUNK_CHARS`, `PRESET_TTS_CONCURRENCY`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_ENVIRONMENT`, `QWEN_TTS_TIMEOUT`, `QWEN_TTS_CHUNK_CHARS`, `QWEN_CLONE_CHUNK_WORDS`, `QWEN_CLONE_GAP_MS`, `QWEN_CLONE_TEMPERATURE`, `QWEN_CLONE_TOP_K`, `QWEN_CLONE_TOP_P`, `QWEN_CLONE_REPETITION_PENALTY`, `QWEN_CLONE_MAX_NEW_TOKENS`, `QWEN_CLONE_DURATION_RATIO`, `QWEN_CLONE_MAX_SILENCE_SECONDS`, `QWEN_CLONE_SILENCE_DB`, `QWEN_CLONE_NON_STREAMING_MODE`, `QWEN_CLONE_SEED`, `QWEN_CLONE_DEBUG`, `OPENROUTER_STT_TIMESTAMPS`, `SUMMARY_CHUNK_CHARS`, `AUDIO_CHUNK_SECONDS`, `STT_ADAPTIVE`, `STT_INITIAL_CONCURRENCY`, `STT_MAX_CONCURRENCY`, `STT_CONCURRENCY_LEVELS`, `STT_MIN_CONCURRENCY`, `STT_GLOBAL_CONCURRENCY`, `STT_EXTRACT_CONCURRENCY`, `STT_CHUNK_ATTEMPTS`, `STT_AMBIGUOUS_RETRIES`, `STT_JOB_DEADLINE_SECONDS`, `STT_INCREASE_SUCCESSES`, `STT_INCREASE_COOLDOWN_SECONDS`, `STT_OVERLAP_SECONDS`, `MAX_UPLOAD_MB`, `MAX_DURATION_SECONDS`, `FFMPEG`, `FFPROBE`

## Google OAuth and token time validation

The browser and server share the single backend `GOOGLE_CLIENT_ID`. For that Google Web application client, the Authorized JavaScript Origins should include:

- `http://localhost`
- `http://localhost:5173`
- `http://127.0.0.1:5173`

**Authorized Redirect URIs: none required** for this GIS popup credential-callback implementation. The backend URL is not an OAuth redirect URI. If the consent app is in Testing, its real permitted Google users also need access as Google test users. Cloud Console access/settings were not available to verify or change these entries.

CORS and mutation Origin/CSRF checks accept the two exact loopback names on the configured local HTTP port. Other origins and ports remain rejected; non-local/HTTPS deployments retain their one exact configured origin. Stay on the same hostname throughout an individual sign-in because browser cookies are host scoped.

Google signature, issuer, audience, expiration, issued-at, verified-email and nonce checks remain enabled. No clock tolerance was added. The actual verifier has a default zero-second skew allowance. Existing tests reject future-issued tokens as well as wrong audience, issuer, signature, expiry and nonce.

Windows timezone is India Standard Time. An NTP stripchart against `time.windows.com` measured approximately +0.003 seconds of clock offset. Windows Time was stopped; starting it from this non-elevated session was denied. The clock measurement does not indicate a drift problem, and no security or token validation was weakened.

## Hosted services and storage

- Whisper transcription: authenticated real synthetic speech request returned HTTP 200 and nonempty text.
- DeepSeek summaries: a short real request returned HTTP 200 and a valid meeting-minutes schema.
- Preset speech: Voxtral returned HTTP 200 and a valid 1.25-second MP3. The source uses Voxtral presets, while Qwen handles cloned voices; this architecture was preserved.
- Stored preset previews: all **32** preset files were generated, decoded with FFprobe and stored for instant playback. These use only short preset requests, with published aggregate input pricing estimated at about USD 0.029 before execution; this is not a billing statement.
- Modal cloning: local SDK and queue worker run, but the worker accurately reports the remote capability offline because no Modal token pair or `.modal.toml` exists. Supply authorized credentials for the existing `sumx-tts` app and its `CloneTTS` class. Existing deployment availability cannot be confirmed without those credentials. No deployment, GPU invocation, cloud resource creation or model download was performed.
- AWS S3: the current application source has no S3/boto integration or S3 settings. Its required storage is local SQLite/private media; unused AWS packages and credentials were not invented.

The default OpenRouter model catalog omits specialized audio models. Modality-filtered catalogs and per-model endpoint metadata confirmed Whisper and Voxtral availability. They were subsequently exercised with real short requests. The stored concurrency, adaptive STT orchestration, summary variants, diarization metadata handling and voice features were preserved.

## Database and application data

No local `data` directory or application database existed before setup. SQLite was initialized with all pending Alembic migrations through **0013**. `alembic check` reports no pending model/schema changes. The existing old virtual environment and all source modifications were preserved.

Private media, database, worker lock files and process/log state live under `data/`. An administrator provisioning audit entry was added. Application records, meetings and transcripts were not reset or deleted. Before any future migration of an existing database, stop services after jobs finish and back up SQLite through its backup API; preserve private media and root `.env` alongside the database backup.

## Validation

- Backend suite: **279 passed** in 79 seconds; the initial 11 skips required optional NumPy/SoundFile helpers. Those exact pinned CPU libraries (`numpy==2.5.4`, `soundfile==0.14.0`) were installed into an isolated setup test folder, and all **11 additional tests plus 9 subtests passed**. Thus 290 backend tests were exercised across the two runs. The application environment remains on its original backend lockfile; no GPU stack or model weights were fetched. One upstream Starlette/httpx TestClient deprecation warning remains.
- Browser suite: **21 passed** in Chrome against isolated test ports 8100/5174 with mocked AI/Google fixtures, including microphone recording, upload, Google popup/token flow, admin authorization/CSV, revocation, independent summaries, voice lifecycle and narration progress.
- Frontend: TypeScript, ESLint and production build passed.
- Backend Ruff and `pip check` passed; database at revision 0013 with no pending schema changes.
- Live local API health and frontend API proxy passed on both loopback origins.
- Local database-backed admin password login, overview, users, approvals, CSV export and logout passed.
- Google nonce/configuration endpoint returned HTTP 200. Actual Google account selection/consent was not performed.
- API route registration checked for uploads, transcription, retry/status, summaries, narration, voices and administration (59 registered paths).
- CORS accepted both intended local origins and rejected an unrelated origin. Focused tests also reject wrong local ports and preserve exact production origins.
- Launcher startup and the output-folder batch wrapper passed. A deliberately occupied port 5173 produced the expected owning-PID error before startup. A repeat launch retained identical API/worker/TTS/frontend process IDs. The application was left running. A final SQLite integrity check returned `ok`, with revision 0013 and zero meetings.

`npm audit` reports 11 moderate development-tool findings in the ESLint/minimatch dependency chain. The production-only audit reports **zero vulnerabilities**. The suggested automated changes include incompatible downgrades; the existing lockfile was preserved. No forced dependency changes were made.

## Preservation and troubleshooting

Initial local changes were a deleted root `.env.example` and an untracked root `requirements.txt` containing unrelated scientific/motion-capture dependencies. Both remain untouched. SUMX's actual backend lockfile was used; the unrelated root requirements were not installed. No changes were committed or pushed, and secret-containing files remain ignored by Git.

Installation-related tracked changes add exact local loopback origin support and forward the legacy launcher. New files provide `start-sumx.ps1`, `start-sumx.bat`, `stop-sumx.ps1`, this report and focused origin security tests. No UI design or inference model implementation was redesigned.

Runtime diagnostics: `data/api.stderr.log`, `data/worker.stderr.log`, `data/tts.stderr.log`, `data/web.stderr.log`, corresponding stdout files, `data/preflight.log`, `data/dependency-check.log` and `data/launcher-processes.json`. A parent launcher process plus its child Python process is normal for Windows virtual environments; repeat startup does not add another service tree.

If a port is occupied, the launcher identifies the owning PID; inspect it before closing anything. On dependency failure, read the named log and install from the existing backend/frontend lockfiles once. On schema mismatch, back up the existing local database before applying pending migrations. Never reset a database to solve a launcher problem.

## Remaining external requirements

1. Add intended real Google emails to the local allowlist through the generated local administrator account; verify the exact origins/test-user settings in the existing Google Cloud project and complete a real Google sign-in.
2. Provide the authorized Modal token pair for the existing hosted speech app. Cloud changes and deployment were not performed. Cloned voice previews/narration remain unavailable until that integration is accessible.


## Verification references

Local setup evidence is retained in the Codex workspace `work/` folder: backend/browser/optional audio test logs, integration-checks.json, local-smoke-tests.json, launcher-tests.json and preset-preview-setup.log. No credentials are included in these reports.

Provider contract references: [OpenRouter transcription guide](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/), [Voxtral speech endpoint](https://openrouter.ai/mistralai/voxtral-mini-tts-3-0/), [source-default DeepSeek model](https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017/providers). These references informed catalog checks; successful live tests are reported separately above.

## FFmpeg follow-up repair

On 3 November 2026, local FFMPEG/FFPROBE settings were changed from bare command names to the full installed executable paths. This prevents older terminals with a pre-installation PATH from breaking media uploads. `run.ps1` now refreshes the user/machine PATH too. Duplicate SUMX frontend processes were stopped and services restarted through the launcher. A synthetic WAV was inspected, converted to MP3 and inspected again with PATH deliberately empty; all checks passed. No meeting data or external inference jobs were created by this verification.

## Modal connection recovery

The Windows Modal profile `masterbruce909` is now authenticated. The existing `sumx-tts` / `CloneTTS` deployment was successfully resolved without deploying or invoking GPU inference. The earlier speech worker had started before the profile was saved and retained its previous authentication state. Restarting only that idle worker restored `ready`, `gpu_available=true` and `accepting=true`, confirmed through the live frontend/API capability endpoint. The root `.env` token placeholders may remain blank when this Windows user profile supplies authentication. No Modal credentials were copied into the source or frontend. This update supersedes the original missing-Modal-credentials blocker above.

## Summary and parallel transcription follow-up

DeepSeek V4 summary calls now disable optional reasoning. Provider generation diagnostics showed that reasoning consumed all 4,350 completion tokens in a failed attempt and 8,247 of the 8,700-token retry, leaving too little room for the JSON answer. The transcript chunk default and local SUMMARY_CHUNK_CHARS were reduced to 12,000 characters. The failed meeting was resumed using its existing transcript, processed as four summary chunks, merged and saved successfully. No retranscription was performed.

Local Whisper transcription now starts with STT_INITIAL_CONCURRENCY=3 and adaptive levels [12,10,8,6,4,3,2,1]. Throttle backoff, shared request limits, checkpoints and chronological merging remain enabled. Three is the stable starting level supported by the existing account-specific benchmark; higher levels remain available through adaptation. The idle shared controller was updated to that start and the meeting worker restarted. A short synthetic live test observed **three simultaneous Whisper HTTP requests**, all returning HTTP 200, no retries and correctly ordered timestamps. That test is functional evidence of parallelism, not a full-meeting speed benchmark.

Final regression verification for the summary/transcription changes: **295 backend tests and 9 subtests passed** with the optional CPU audio test helpers, plus Ruff and Git whitespace checks. New coverage verifies DeepSeek visible-output configuration, full coverage and synthesis of summary chunks, three simultaneous request slots, and shared throttle fallback from three to two. Tests tied to specific concurrency steps now declare those steps explicitly instead of depending on private local environment tuning.

## Transcription cooldown recovery — 4 November 2026

A full-length transcription at three concurrent 180-second chunk requests received an explicit OpenRouter in-flight-budget HTTP 402 with a two-minute Retry-After. Two paid successful chunks were preserved while the shared controller waited; the job then completed all 15 chunks and saved the summary. No chunks were discarded or manually retranscribed. The earlier short three-request smoke test did not establish account capacity for full-length chunks.

This laptop is now configured with STT_INITIAL_CONCURRENCY=2, STT_MAX_CONCURRENCY=2 and STT_GLOBAL_CONCURRENCY=2. Requests remain parallel, with automatic reduction to one on throttling. These values supersede the earlier three-request/12-maximum tuning. The live shared controller was lowered without interrupting the user's running narration; persisted settings are applied by subsequent worker starts.

Meeting/detail polling responses now include retry_after_seconds during a provider-requested cooldown. The frontend shows the waiting state, approximate retry countdown and reassurance that completed parts are saved, then returns to normal progress when the cooldown expires. Verification: 45 concurrency/status tests, 30 API/meeting regression tests, four browser workflow tests, TypeScript, ESLint, production build, Ruff and whitespace checks passed. Test concurrency policies are isolated from private local account settings.
