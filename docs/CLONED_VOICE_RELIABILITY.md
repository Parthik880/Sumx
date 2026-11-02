# Cloned-voice reliability

Inference now runs on Modal; see the [Modal speech guide](MODAL_TTS.md). The measurements below were recorded on the earlier local RTX 3060 pipeline, and the same chunking, validation and retry logic moved to `modal_service/inference.py`.

This change applies only to Base-model cloned speech. Preset synthesis, preset chunking/assembly, preset previews, model checkpoints, GPU configuration, frontend, authentication, STT, LLM and database schema are unchanged.

## Evidence and diagnosis

The old cloned-narration path used the preset character-based chunker (240 characters by default), rebuilt reference conditioning for each call, and sampled without recording seeds. Signal validation checked overall duration and clipping but did not inspect internal silence. The existing GPU worker already serialized inference; inspection found no evidence of a sample-rate mismatch, concurrent model mutation or duplicated chunk buffers.

A baseline on the local RTX 3060 generated the exact preview sentence 20 times for each of three consented, verified reference profiles, with seeds 7000–7019. Two of 60 accepted WAV files contained internal silences of **3.64 and 4.32 seconds**. Those pauses were in the raw model output, before concatenation or MP3 encoding. This establishes a missing validation check, rather than an assembly-induced pause. The baseline does not prove the cause of every reported distorted ending; stochastic codec sampling can vary, and waveform checks alone cannot certify intelligibility.

## Changes

- Cloned meeting narration uses up to **22 whitespace-delimited words** per chunk. Sentence boundaries are preferred; long sentences use clause punctuation or whole-word boundaries. Paragraph/word/punctuation order is preserved. A single unbroken token over the existing 600-character model-call limit is rejected rather than split. Presets keep their existing character-based splitter.
- Chunks run sequentially through the existing Base checkpoint. One precomputed ICL prompt is reused within the same user/profile/job, with file identity and transcript included in the cache key. A different owner/profile/job, a changed reference, an idle timeout or CUDA OOM invalidates it. Reference audio is truly decoded once per prompt; the existing verified transcript remains separate from narration text.
- Precomputation matches the installed Faster implementation's `append_silence=True` path by adding **0.5 seconds to the reference conditioning only**. It does not insert that silence into generated narration.
- Sampling defaults remain temperature **0.65**, top-k **30**, top-p **0.9**, repetition penalty **1.15**, with stochastic sampling enabled. Parameters are now configurable for cloned voices only. A request gets a recorded random base seed unless a test seed is configured. Retries use controlled different seeds and retain the same sampling parameters. A scoped Torch RNG context restores the preset pipeline's RNG state after each cloned call.
- Each raw waveform is checked for valid dimensions/rate, nonempty finite samples, clipping, abnormal duration, an estimated token-cap ending and silence. The default silence threshold is **3 seconds**, measured using 20 ms RMS windows below **−45 dBFS**. Internal and edge silences are reported separately. Natural shorter pauses remain; speech is never truncated to conceal a bad sample.
- A suspicious chunk gets at most **two additional attempts**. Exhaustion reports its chunk index. Successful chunks and their original text plan remain checkpointed; retries do not regenerate them even if the configured word limit changes.
- Valid chunks are lossless mono **24 kHz PCM16 WAV**. Assembly writes them in index order, with a **200 ms gap** only when existing edge silence does not already provide it. Five-millisecond edge fades prevent clicks without overlap or removing frames. The assembled WAV is validated before a single final MP3 encoding. Preset assembly is unchanged.
- Historical generated audio remains available. New cloned requests use a versioned output path and internal audio voice key (`cloned:<profile>:clone-v2`) so old, unchecked narration is not silently reused and the existing database uniqueness constraint permits both records. Profile IDs and API request fields are unchanged. Old interrupted jobs with incompatible chunk boundaries request a new narration instead of mixing chunks.

## Configuration and diagnostics

Root `.env` optional settings (defaults shown):

```dotenv
QWEN_CLONE_CHUNK_WORDS=22
QWEN_CLONE_GAP_MS=200
QWEN_CLONE_TEMPERATURE=0.65
QWEN_CLONE_TOP_K=30
QWEN_CLONE_TOP_P=0.9
QWEN_CLONE_REPETITION_PENALTY=1.15
QWEN_CLONE_MAX_NEW_TOKENS=600
QWEN_CLONE_DURATION_RATIO=2
QWEN_CLONE_MAX_SILENCE_SECONDS=3
QWEN_CLONE_SILENCE_DB=-45
QWEN_CLONE_NON_STREAMING_MODE=true
# QWEN_CLONE_SEED=7000
QWEN_CLONE_DEBUG=false
```

The actual token budget is the smaller of the text-dependent budget and the configured cap. A fixed seed is optional for testing; production remains stochastic by default. `non_streaming_mode` controls the text-feeding layout while output is still returned as a complete waveform. Both settings are compared independently; defaults are not changed globally.

Per-attempt diagnostics include profile ID, request ID, chunk index, word/character counts, seed, sampling parameters, generation mode, validation thresholds, duration, maximum internal silence, status/rejection reason and timing. They are logged as metadata and retained in the existing generation diagnostics JSON. Production logs omit narration text and reference content. The installed high-level API returns audio without an EOS flag: `eos_reached` is explicitly null, and token-cap detection is labeled as a waveform-duration estimate.

`QWEN_CLONE_DEBUG=true` is restricted to a localhost application origin. It saves raw generated WAVs and matching narration-text/diagnostic JSON under ignored `data/clone-debug/`; it never saves or logs reference transcripts/audio there. Validated intermediates needed for retries are retained until final assembly; normal successful intermediates are cleaned up. Debug artifacts contain private derived audio/text and should remain local.

## Reproduce tests

Stop the GPU worker before the standalone benchmark so it can take the existing single-GPU process lock:

```powershell
.\.venv\Scripts\python -m backend.benchmark_modal --repeats 20
python -m unittest backend.tests.test_clone_inference backend.tests.test_qwen_waveform -v  # needs numpy and soundfile
.\.venv\Scripts\python -m pytest backend/tests -q
```

The operator benchmark reads existing consented/verified profiles owned by approved users, leaves their records unchanged, and stores generated test audio/reports under ignored `data/clone-repeatability/`. It reports each profile's saved/draft status; different reference recordings do not necessarily represent different speakers. There are three verified references in the local test library, but only one is saved. Testing three saved voices therefore remains a library limitation; no records are marked saved just for a test.

Automated tests cover 100/500/1000-word texts, unpunctuated paragraphs, abbreviations/numbers/punctuation, exact word preservation, ordered compatible WAV assembly, silence rejection, bounded seed retries, prompt reuse/owner isolation, failure recovery, and resuming only unfinished chunks. NumPy/SoundFile waveform tests run in the isolated GPU environment; CPU-only pytest skips those methods explicitly.

## Measured results

The 60 baseline repetitions averaged **5.58 seconds** of audio; the old runtime retried four requests for its existing duration/signal checks, but still accepted two results with internal silence over three seconds (**3.3%**).

| New cloned mode | Repetitions | First-attempt rejection | Failed after up to 3 attempts | Accepted silence >3 s | Average duration | ASR word agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `non_streaming_mode=True` | 60 | 8/60 (13.3%) | 0/60 | 0/60 | 5.23 s | 99.70% |
| `non_streaming_mode=False` | 60 | 6/60 (10.0%) | 0/60 | 0/60 | 4.51 s | 99.64% |

Each mode tested the same sentence 20 times per reference, seeds 7000–7019. First-attempt rejection now includes silence: True rejected four requests for silence and four for abnormal duration; False rejected four for silence and two for duration. These stricter rejection rates should not be confused with published bad audio. All recovered within the bounded retries. Each mode had exact ASR word matches for 57/60 outputs; remaining differences were small, not proof of a corrupted waveform. The test does not establish a clear quality advantage for changing the text-feeding mode, so True and the previous sampling defaults remain configured.

All **213 new benchmark chunks** completed after validation/retries: 120 repeated previews, three number/punctuation cases and multi-chunk narration. The 100/500/1000-word synthetic narration used 5/25/50 chunks and produced valid MP3; the first reference's durations were 32.8/164/328 seconds. The long synthetic fixture repeats a 20-word sentence; additional real meeting tests exercise diverse content and per-request random seeds. Other references also completed 100-word narration. ASR checks include first and final chunks to inspect endings.

A real saved voice also completed a **103-word meeting through the production queue in seven chunks**, with precomputed prompt reuse confirmed after chunk one and 100% ASR word agreement on the assembled MP3. A real Aiden preset preview completed unchanged at 4.32 seconds with 100% ASR word agreement.

The same saved voice completed a **681-word real meeting in 43 diverse chunks**, with recorded random seeds per chunk, no inference retries and a maximum internal silence of **0.9 seconds**. Initial saving exposed a legacy-audio uniqueness conflict: versioning only the filename was insufficient. The internal audio key now includes the pipeline version. Finalization was retried successfully with **all 43 task IDs and GPU attempt counts unchanged**, preserving both historical and new playable audio. A regression test creates the legacy record and verifies coexistence. Beginning/middle/end ASR checks returned 100%/100%/88.37% word agreement; the last difference involved two acronym recognitions and one omitted word. This is a remaining pronunciation/word-recovery limitation, not evidence of a long silent or garbled continuation. No human listening assessment is claimed.

Verification: **104 backend tests passed**; 11 waveform methods were explicitly skipped by CPU pytest and **all 11 passed in the GPU environment**. All **19 browser tests** passed, including preset generation, preset previews, saved selection, cloned narration and existing UI/auth workflows. Ruff, the Alembic schema check and diff whitespace checks passed. No new migration was needed.

Detailed results and generated audio remain in ignored `data/clone-repeatability/`: `before.json`, `after.json`, `summary.json`, `asr.json` and queue-check reports. ASR word agreement is a useful intelligibility check, but does not measure speaker similarity, prosody or every audible artifact. Human listening review remains necessary for those properties. No claim of zero failure probability on all texts, languages or references is made.

## Files changed for this fix

`backend/clone_speech.py`, `modal_service/inference.py`, `backend/benchmark_modal.py`, clone context in `backend/tts_worker.py`, clone-only branches in `jobs.py` and `tts_queue.py`, `config.py`, `.env.example`, focused tests and documentation. There are no new dependencies, database migrations or frontend changes.
