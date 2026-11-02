# Regional presets and independent summaries

Verified locally on 2026-11-02, continuing `main` from `0ee5ceb`. The working tree was clean before this task. Adaptive STT, summary history, authentication, cloned voices, Modal GPU configuration and narration concurrency were preserved. No deployment or git push was performed.

## What caused the apparent extra Standard generation

Read-only inspection of the existing database found that every recorded upload requested `detail=standard`. The most recent affected meeting had a completed Standard **upload pipeline** job, followed later by a separate Concise **summarize** job. The Concise job reported seven LLM calls and saved exactly one Concise summary. It did not insert another Standard summary. An older affected meeting likewise had Standard from upload followed by two explicit Concise revisions.

The upload form defaults to Standard. Its automatic post-transcription summary was therefore already requested before Concise was selected on the detail page. Long-transcript notes, synthesis and an optional shortening pass also made one Concise generation look like several generations. There was no loop generating all three modes, and no generation in the polling or mode-switch effects.

Two real routing gaps were found: the legacy `/summarize` route could return an unrelated active job, and idempotency keys were unique only by meeting rather than meeting/mode/attempt. A conflict check outside the enqueue lock also left a race. These are now handled inside the serialized enqueue operation shared by both routes. Existing saved Standard summaries have deliberately not been deleted.

## Summary changes

- Upload continues to enqueue exactly one pipeline with the form's selected mode. The form now explains the automatic summary explicitly. The API exposes that original `detail`, and a meeting without saved summaries initially displays its requested mode.
- The detail-page button captures its selected mode once, blocks duplicate submission synchronously, and retains the same attempt UUID across an ambiguous network failure. A new deliberate generation gets a new UUID. Mode changes and polling remain read-only.
- Both `POST /api/meetings/{id}/summaries/{mode}/generate` and legacy `POST /api/meetings/{id}/summarize` share atomic mode-scoped idempotency and conflict checks. A conflicting mode receives HTTP 409. A same-mode active pipeline/job is reused, avoiding a second job. Old raw retry keys remain recognized for their original mode.
- Completed jobs ignore duplicate delivery. A failed job retains successful intermediate notes and cumulative call counters; its mode, transcript version and prompt identity must match its checkpoint before resuming.
- A job persists only its requested mode. Existing globally ordered summary versions, per-mode revisions, transcript/prompt identities, usage ledger deduplication and exact-version narration remain intact. No schema migration is required.
- Mode status now examines that mode's own latest job, so another mode's later job cannot hide its failure or progress. Empty selected modes never fall back to generic latest summary content.
- `summary_metrics` in the job payload and worker logs distinguish transcript chunks, direct calls, note calls, synthesis calls, correction calls, total LLM calls and records saved. Failed attempts count as attempts; resumed jobs retain counts. HTTP transport retries inside the provider are not additional summary jobs. Logs include job ID and mode, without transcript, notes, reference recordings or credentials.

### Mocked call-count regression results

| Case | Calls | Saved modes / result |
| --- | ---: | --- |
| Fresh meeting, Generate Concise | 1 direct | Concise revision 1 only |
| Generate Standard afterward | 1 direct | Standard revision 1; Concise unchanged; Detailed absent |
| Regenerate Standard | 1 direct | Standard revision 2; Concise unchanged; Detailed still absent |
| Generate Detailed afterward | 1 direct | Detailed revision 1; both other modes unchanged |
| Repeat completed Concise attempt / duplicate job delivery | 0 additional | Same job and saved record |
| Same attempt UUID deliberately used in another mode | 1 direct for that mode | Distinct mode-scoped job |
| Initial upload with Concise selected | 1 direct, plus mocked STT | Exactly one Concise record; no Standard/Detailed |
| Eight-chunk Concise transcript; second notes call fails, then resume | 10 total: 9 notes attempts + 1 final synthesis | Nine successful billable mocked responses; one Concise record; first successful notes reused |
| Truncated response, bounded retry | 2 direct attempts | Same selected mode, both responses accounted |
| Overlong result, bounded shortening pass | 1 direct + 1 correction | One result for the same mode |
| Mode switching, polling, browser refresh, saved-summary viewing | 0 generation calls | Correct selected record / empty state |
| Two concurrent requests for different modes | At enqueue: 0 | One queued job; one conflict; never a silently substituted mode |
| Narration selection | 0 LLM calls | Reads the exact saved version of the chosen generated mode |

Summary tests used mocks, not paid live LLM requests. Existing tests also cover all mode orders, failure/retry handling, prompt budgets, old-mode inference, exports, audio caching, revision edits and authenticated ownership.

## Voice catalog and evidence

Model remains `mistralai/voxtral-mini-tts-3-0` on OpenRouter. The six original presets are unchanged: Paul neutral/confident (`en_paul_neutral`, `en_paul_confident`), Oliver neutral/confident (`gb_oliver_neutral`, `gb_oliver_confident`) and Jane neutral/confident (`gb_jane_neutral`, `gb_jane_confident`). Paul neutral remains the default.

Names, IDs and language groups were checked against [Mistral's official voice table](https://docs.mistral.ai/studio/audio/text-to-speech/voices). All **32** selected IDs, including all **26 new** IDs, were found in the live [OpenRouter speech-model catalog](https://openrouter.ai/api/v1/models?output_modalities=speech) on the verification date. The frontend uses the existing dropdown and controls with 22 native language/region optgroups. It introduces no accent categories absent from the documentation. Hindi is Hindi, not Indian English.

Mistral's public table and OpenRouter's supported-ID array do not provide per-speaker gender fields. Gender labels below retain the verified examples supplied in the request; remaining speakers are explicitly unlabeled rather than inferred from names. The additional French names are not evidence of a verified male counterpart. Korean lists Sung and Jin without genders, so both are available without guessed labels. No unavailable male/female counterpart was invented for French, German, Spanish, Italian, Portuguese, Japanese or Chinese, or for the other single-speaker languages. Extra same-region English and French speakers were omitted to avoid duplicate variants beyond the requested catalog.

| Region / language | Voice | Exact provider ID | Gender label | Duration | Best ASR agreement |
| --- | --- | --- | --- | ---: | ---: |
| American English | Rob | `voice_485` | male | 4.03 s | 100.0% |
| American English | Sheila | `voice_478` | female | 3.92 s | 100.0% |
| British English | David | `voice_1788` | male | 4.35 s | 100.0% |
| British English | Mary | `voice_1787` | female | 4.29 s | 100.0% |
| Hindi | Himanshu | `voice_3153` | male | 6.66 s | 100.0% |
| Hindi | Ananya | `voice_3139` | female | 8.28 s | 100.0% |
| French | Marie | `voice_592` | female | 3.90 s | 100.0% |
| German | Heidi | `voice_481` | female | 5.75 s | 100.0% |
| Spanish | Elena | `voice_483` | female | 4.29 s | 100.0% |
| Italian | Lorenzo | `voice_482` | male | 5.48 s | 100.0% |
| Portuguese | Gabriela | `voice_480` | female | 6.24 s | 100.0% |
| Japanese | Haruto | `voice_3133` | male | 4.79 s | 100.0% |
| Chinese | Mei | `voice_3142` | female | 4.59 s | 100.0% |
| Arabic | Maryam | `voice_479` | Not published | 5.96 s | 100.0% |
| Catalan | Jordi | `voice_3143` | Not published | 5.99 s | 100.0% |
| Hungarian | Olivia | `voice_3146` | Not published | 5.73 s | 100.0% |
| Korean | Sung | `voice_3136` | Not published | 3.74 s | 100.0% |
| Korean | Jin | `voice_3135` | Not published | 6.78 s | 100.0% |
| Dutch | Lotte | `voice_3145` | Not published | 5.78 s | 100.0% |
| Norwegian | Lars | `voice_3134` | Not published | 4.43 s | 100.0% |
| Polish | Leon | `voice_3154` | Not published | 4.64 s | 100.0% |
| Romanian | Irina | `voice_3155` | Not published | 4.33 s | 92.3% |
| Russian | Tatiana | `voice_3132` | Not published | 5.78 s | 92.3% |
| Turkish | Oran | `voice_3138` | Not published | 4.27 s | 100.0% |
| Ukrainian | Taras | `voice_3140` | Not published | 5.78 s | 100.0% |
| Vietnamese | Huong | `voice_3144` | Not published | 5.06 s | 100.0% |

## Preview generation and verification

All 26 new previews were generated **once** through `backend/preset_previews.py` in `data/media/preset-previews/{voice-id}/v1.mp3`. All six existing MP3 SHA-256 hashes remained unchanged. The generator checks requested IDs against the live configured model before paid generation, skips existing files, and uses a language-specific sentence from the shared backend catalog. English retains exactly: “Hello. This is my new voice. I can read your meeting notes clearly.”

Selection, preferences, catalog reads, playback and replay never invoke inference. A missing MP3 returns 404 and exposes no preview URL; the existing disabled control says “Preview unavailable”. Versioned URLs retain immutable browser caching. New presets use the same OpenRouter narration path as existing presets; cloned profiles and Modal inference are untouched.

All MP3s decoded correctly. New preview duration was **3.74–8.28 seconds**, decoded clipping fraction **0**, maximum internal silence **0.90 seconds**. Independent configured Whisper ASR was run without a language hint on every new preview. Several MP3 transcriptions stopped early, and one Hindi sample was rendered in Urdu script. Rechecking the **same audio**, decoded to true 16-kHz mono WAV with its documented language hint, recovered the full text in those cases. This did not require regenerating any TTS audio or modifying production STT.

Best text agreement was **92.3–100%** using Unicode word/character comparison. The remaining Romanian difference was `Pot` versus ASR `Poți`; Russian differs only in `ё`/`е` spelling. These automated checks support language/content intelligibility, but do not establish native accent quality or perfect pronunciation. A native-speaker listening review has **not** been completed; do not interpret the ASR percentages as a human pronunciation score.

The bounded test budget was $0.15. Generation ledger estimate: **$0.023803** for 26 previews. Verification used 47 accounted STT calls (initial 26 plus 21 WAV rechecks). Total measured key-budget change was approximately **$0.02755**, including verification, below the cap. No successful preview was regenerated.

Local verification artifacts (ignored by git; public synthetic preview text only):

- `data/benchmarks/regional-voices-20261102/manifest.json`: live supported-ID snapshot, pricing, original six hashes and generation plan.
- `data/benchmarks/regional-voices-20261102/verification.json`: expected/recognized native text, per-voice audio checks and independent ASR results, including initial partial results.
- `data/benchmarks/regional-voices-20261102/LISTEN.html`: labeled saved audio for manual review.

Previews are persistent application media, not committed source files. A new deployment must copy the versioned preview directory or explicitly run the generator with credentials; startup and browser playback never spend money to fill missing files. This task made no Modal deployment or concurrency changes.

## Validation and reproduction

From the project root:

```powershell
.venv\Scripts\python -m pytest backend/tests -q
.venv\Scripts\python -m ruff check backend
.venv\Scripts\python -m alembic check
cd frontend
npm.cmd run build
npm.cmd run lint
npm.cmd test
```

Results: **272 backend tests passed**, **11 optional GPU waveform tests skipped** in the CPU application environment; **21 Chromium browser tests passed**. Build, ESLint, Ruff and Alembic schema check passed. Browser tests verify the original light UI, all 22 catalog groups, 32 options, new Hindi selection, cached playback with no inference POST, empty-mode isolation, switching/refresh, narration, OAuth, admin, revocation and existing workflows. The skipped tests do not imply a new live Modal quality test; cloning was not changed or redeployed.

Explicitly generate only missing preview assets, using the existing backend environment:

```powershell
.venv\Scripts\python -m backend.preset_previews
# One missing preview only:
.venv\Scripts\python -m backend.preset_previews --voice voice_3139
```

These commands are paid maintenance operations only when a file is missing. Avoid `--refresh` unless replacement is intentional. Playback is a file GET. For independent quality reproduction, decode the saved MP3 using FFmpeg (`-ac 1 -ar 16000`) and call the existing `OpenRouter.transcribe(wav_path, language_code)` under a separate bounded verification budget; compare recognized text with `preview_text(voice_id)`. Never use the preview text as a cloned voice's reference transcript. Use `LISTEN.html` for native-speaker review.

## Exact source files changed

- `backend/preset_voices.py`: documented regional metadata and shared native preview sentences.
- `backend/voice_catalog.py`: group and verified/unknown gender metadata.
- `backend/preset_previews.py`: native text and pre-generation provider-ID check.
- `backend/jobs.py`: atomic mode-scoped enqueue, completed-delivery guard, checkpoint context and per-job metrics/logging.
- `backend/main.py`: shared conflict response and original upload-mode exposure.
- `backend/summaries.py`: mode-specific job status.
- `backend/summary_modes.py`: separate cumulative LLM call counters.
- `frontend/src/VoiceStudio.tsx`: grouped native dropdown, same controls and styling.
- `frontend/src/api.ts`: optional stable idempotency key and meeting mode type.
- `frontend/src/main.tsx`: explicit automatic-upload behavior, initial selected mode and guarded stable-key submission.
- `backend/tests/test_voice_catalog.py`: all regional routing, native previews, provider-free playback/cache/preferences.
- `backend/tests/test_summary_independence.py`: exact requested mode sequence, upload, long-job resume, idempotency and concurrent conflict regressions.
- `backend/tests/test_voices.py`: expanded-catalog assertion.
- `backend/tests/e2e_server.py`: regional preview fixtures and one explicit unavailable preset.
- `frontend/tests/voices.spec.ts`: catalog grouping, new selection and provider-free preview regressions.
- `docs/SUMMARY_MODES.md`: remove stale cache claims and explain actual independent generation/diagnostics.
- `docs/REGIONAL_VOICES_AND_SUMMARY_ISOLATION.md`: this evidence and reproduction report.

No changes were made to provider request formats, STT scheduling/configuration, database schema, authentication, summary prompts, preset sampling/chunk sizes/concurrency, cloned-voice storage, GPU fallback order or Modal deployment settings.
