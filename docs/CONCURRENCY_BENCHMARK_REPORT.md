# SUMX concurrency benchmark report

Date: 2 November 2026. Working branch: `main`, starting commit `a8ec0ac`. All changes are local; nothing was pushed. This report distinguishes live measurements, historical records, and untested alternatives.

## Scope and preserved work

Claude's independent summary persistence and summary/narration selector changes were retained. Its unfinished STT draft contained the controller, migration, chunk model, provider changes and tests, but missing imports prevented startup. The completed controller fixes conservative startup, upward probing, shared cross-process adaptation, bounded scheduling, overload classification, checkpoints and accounting. No frontend source, preset inference settings, Modal deployment, GPU fallback order, model checkpoint or cloning sampling setting was changed.

## A. Adaptive Whisper STT

### Implementation and production recommendation

- `STT_ADAPTIVE=true`, `STT_INITIAL_CONCURRENCY=2`, `STT_MIN_CONCURRENCY=1`, `STT_MAX_CONCURRENCY=12`, levels `[12,10,8,6,4,2,1]`.
- A bounded worker scheduler creates at most 12 tasks, rather than one waiting task for every audio chunk. Each upload streams from disk. Two FFmpeg extractions are permitted globally, including across worker processes.
- Database-backed leases and a locked singleton controller share request capacity, cooldown, overload epoch and latency/success history across jobs/processes. Already-running calls drain; new admissions count all active leases, including high-numbered slots after a reduction. Unique lease owners prevent a late release from removing a reissued slot. Requests have a total 120-second network timeout, below the lease lifetime; crashed leases expire.
- HTTP 429 and temporary 402 reduce capacity immediately. A 402 is temporary only when structured `error.metadata.limit_source` is `openrouter_in_flight_budget` and a valid `Retry-After` is present. Message substring matching was removed. Other 402s, invalid credentials/models and permanent 403s fail without endless retry.
- Repeated 502/503/504, transient connections and ambiguous timeouts reduce capacity; sustained latency degradation requires a populated observation window. `Retry-After` accepts seconds or HTTP dates. Exponential backoff includes jitter, respects longer provider waits, and is shared across workers. Stable success/cooldown allows one-level probes; failed probes impose a longer ceiling/cooldown to prevent oscillation.
- The default bounds are six attempts per chunk, at most one ambiguous-timeout retry, and a 1,800-second total execution-attempt deadline. A user-initiated resume gets a new execution attempt and reuses its saved chunks. Ambiguous transport failures can already have been billed; no free-retry claim is made.
- Every successful chunk and its usage entry commit together. Chunk/provider identifiers, text, timestamp offsets and the existing overlap deduplication are retained; successful chunks are not resent. The final transcript/checkpoint marker commit together, preventing duplicate transcript versions. Per-chunk temporary text is cleared after final persistence. Permanent failure stops new work while in-flight successful requests finish and save.

### Live measurements

Every completed row below used the same 3,000-second (50-minute) extracted sample, 17 chunks, 180-second ownership intervals and 1.5-second overlap. The source SHA-256 is recorded in the private benchmark manifest. No reference recording or private transcript is included in this report.

Total includes extraction, upload, provider wait, retries, checkpoints and cooldown. Mean/p95 are request-attempt latencies, not full meeting latency. Memory is sampled RSS of the standalone benchmark plus its FFmpeg children on Windows; it is not a measurement of an entire 2 GB Linux EC2 deployment.

| Configuration | Total s | Mean/p95 request s | HTTP 402 / 429 / 5xx | Retries | Final stable limit | Peak RSS MiB | Reported API USD | Saved chunks | Ordered valid timestamps | Word agreement vs sequential |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 1 | 69.50 | 3.40 / 6.81 | 0 / 0 / 0 | 0 | 1 | 114.1 | 0.03220742 | 17/17 | True | 100.00% |
| 2 | 32.43 | 3.14 / 4.12 | 0 / 0 / 0 | 0 | 2 | 145.4 | 0.03008992 | 17/17 | True | 99.59% |
| 3 | 20.83 | 3.00 / 5.24 | 0 / 0 / 0 | 0 | 3 | 146.6 | 0.02865081 | 17/17 | True | 97.87% |
| 4 | 615.26 | 2.96 / 6.33 | 5 / 0 / 0 | 5 | 4 | 150.2 | 0.03536309 | 17/17 | True | 97.75% |
| Adaptive, starting at 4 | 152.79 | 3.74 / 5.63 | 1 / 0 / 0 | 1 | 2 | 144.8 | 0.02267867 | 17/17 | True | 98.42% |
| 6 | Incomplete | Unavailable | Incomplete telemetry | Unavailable | 6 fixed | Unavailable | Partial costs in manifest | 6/17 | Not a completed transcript | Not measured |
| 8 | Not run: provider budget constraint | - | - | - | - | - | - | - | - | - |
| 10 | Not run: provider budget constraint | - | - | - | - | - | - | - | - | - |
| 12 | Not run: provider budget constraint | - | - | - | - | - | - | - | - | - |

The concurrency-6 run was stopped only while it had **zero active requests** and was waiting for a provider cooldown. Its completed chunk checkpoints remain. Full 8/10/12 runs were not sent after that quota constraint; these cells are not simulated benchmarks or successful results. Controller correctness at every configured level is tested with simulated overload and recovery.

Adaptive recovered from one temporary 402, moved 4 -> 2, completed all 17 chunks, then made a final probationary probe back to 4; the last established stable level was 2. That probe does not justify using 4 as a default. Fixed 4 took 615 seconds because it honored repeated provider waits. The runs were sequential on a shared account, so the results establish an account-budget constraint, not a universal provider concurrency ceiling or a purely causal comparison of 4 versus 2.

**Production start: 2.** It completed in 32.43 seconds without throttling, about 2.14x faster than the new sequential baseline. Fixed 3 also worked at 20.83 seconds, but is a comparison to the previous implementation rather than a configured adaptive step. Higher capacity remains available through controlled recovery/probing; production does not begin at 12. Benchmark RAM peaked near 150 MiB, but a full 2 GB EC2 load test remains unperformed. The global extraction/request bounds avoid multiplying unbounded FFmpeg processes or buffered audio copies across meetings.

Historical local records (`data/stt-before.json`, `data/stt-after.json`) reported 58.34 seconds sequential, 23.97 at concurrency 3 and 17.34 at 4, without retries. Those are separate earlier runs, not evidence that 4 is reliable under the current account budget. Word agreement is a comparison to another Whisper output, not ground-truth accuracy; all completed runs covered 17/17 chunks and passed timestamp order/range checks.

## B. Voxtral preset narration

Production remains **1,800 characters / concurrency 3**, using the existing model and voice. The pooled client is decoupled from the selected STT start: production preset capacity remains 3, while STT explicitly requests its own pool capacity. This prevents an STT start of 2 from silently reducing the working preset path to 2. Experimental requests used separate benchmark-only pools; no experimental limit was enabled.

All configurations used the same source text (8,939 characters; SHA-256 `ac8aa4e6eb335c2b1af1b8cf84d807871331ef571fb4a73c701d70c3eed587d6`), voice `en_paul_neutral`, and model `mistralai/voxtral-mini-tts-3-0`. Text contains ordinary minutes, numbers, names, abbreviations, long sentences and action items. Splitter whitespace differences change billable character counts slightly, while spoken words stay identical. Each configuration is one trial; Voxtral does not expose a controlled seed here.

| Chars / concurrent | Chunks | Generation s | First completed audio s | Mean/p95 chunk s | Retries / failures | Reported TTS USD | Audio s | Max internal silence s | Raw ASR WER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1800 / 3 | 6 | 25.51 | 12.31 | 11.42 / 15.75 | 0 / 0 | 0.115804 | 489.93 | 2.30 | 3.37% |
| 1800 / 4 | 6 | 25.39 | 11.55 | 13.33 / 24.08 | 0 / 0 | 0.115804 | 554.24 | 6.42 | 12.81% |
| 1200 / 4 | 8 | 18.13 | 8.92 | 8.43 / 9.68 | 0 / 0 | 0.115778 | 495.88 | 2.20 | 3.58% |
| 1200 / 6 | 8 | 17.46 | 8.17 | 9.18 / 12.39 | 0 / 0 | 0.115778 | 487.53 | 6.12 | 4.27% |
| 900 / 6 | 11 | 15.01 | 6.96 | 7.29 / 8.40 | 0 / 0 | 0.115739 | 490.03 | 2.58 | 2.75% |
| 900 / 8 | 11 | 13.41 | 6.06 | 6.44 / 7.47 | 0 / 0 | 0.115739 | 490.71 | 1.82 | 2.20% |

All 50 TTS requests returned HTTP 200. Costs were reconciled through their generation IDs; these TTS totals exclude the separately recorded quality-transcription costs. First audio means the first complete returned MP3 chunk, not streaming first-byte latency or a change to the application player.

All parts were mono 22,050 Hz MP3 with compatible layouts. Final assembly used the production MP3-copy concatenation path and decoded successfully. Quality analysis genuinely resampled a temporary copy to mono 24 kHz; it did not relabel samples or change production audio. Clipping fractions were negligible. Nominal boundary sample jumps and two-second silence windows are recorded in each metrics file; they are signal checks, not proof that seams are inaudible.

**Best observed candidate: 900 characters / 8 requests.** It was about 1.90x faster than baseline (13.41 vs 25.51 seconds), with lower raw ASR WER (2.20% vs 3.37%) and shorter measured maximum silence (1.82 vs 2.30 seconds). Its largest nominal boundary jump was about 0.0384 on normalized PCM; human seam review is still required. Do not enable it yet: this is one trial and does not establish naturalness, pronunciation, pitch or voice consistency across repeated generations.

The 1,800/4 trial provided essentially no speedup and produced a 6.42-second pause, longer audio and worse WER. The 1,200/6 trial also had a 6.12-second pause. They are poor candidates from these observations. Stochastic provider variation prevents attributing every difference solely to concurrency. The 900/6 trial had 2.75% raw WER; a lower greedy sequence-match score on repeated text was misleading, which is why edit-distance WER is reported here.

**Recommendation: retain 1,800/3 in production.** If approved later, repeat 900/8 with varied realistic summaries and manual A/B listening before changing production. Speaker-similarity embeddings, pitch accuracy and subjective naturalness were not objectively measured. Raw WER includes ASR errors and numeral/name formatting differences; it is not a human intelligibility score.

## C. Modal Qwen3-TTS

Used the existing deployed `CloneTTS.narrate` method sequentially, one invocation per complete narration, with the saved/consented/verified owner's reference, Base 0.6B, existing 22-word chunks, BF16, CUDA graphs and unchanged sampling parameters. Seeds 7000/7001 were benchmark payload overrides only. Production GPU preference remains `[A100, L40S, L4, A10]`; `MODAL_MAX_CONTAINERS` remains **1**. No deployment or parallel GPU call was made.

The service actually allocated **NVIDIA A100-SXM4-80GB** on all four calls. Approximate one-minute/4-minute targets used 150/600 whitespace-delimited words. The same source and reference were reused for each length's two samples.

| Case | Cold | Wall s | GPU execution s | Audio s | Compute/audio RTF | Conditioning s | Peak allocated MiB | Retries | Max assembled silence s | Raw ASR WER | GPU estimate USD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| words-150-repeat-0 | True | 52.45 | 24.40 | 61.26 | 0.398 | 0.638 | 3076.9 | 0 | 1.08 | 3.25% | 0.028909 |
| words-150-repeat-1 | False | 31.31 | 24.39 | 63.04 | 0.387 | 0.264 | 3076.9 | 0 | 3.46 | 7.14% | 0.016926 |
| words-600-repeat-0 | False | 98.75 | 91.33 | 246.86 | 0.370 | 0.255 | 3076.9 | 0 | 1.80 | 5.18% | 0.063385 |
| words-600-repeat-1 | False | 99.78 | 91.89 | 248.32 | 0.370 | 0.268 | 3076.9 | 0 | 3.46 | 8.41% | 0.063768 |

The observed cold startup was 17.26 seconds (12.06-second load, 1.57-second graph warmup, plus weight/setup work); cold-inclusive wall time was 52.45 seconds for the first one-minute sample. Later calls were warm. Cold four-minute generation was not forced: doing so would disturb the existing deployment. GPU utilization and reserved-memory peaks are not exported by the deployed method and are **unmeasured**. PyTorch peak allocation was approximately 3,077 MiB. Per-chunk times, conditioning times, seeds and validation metrics are in the private JSON; graph capture was true on every call.

Configured-rate GPU execution/startup estimates total **$0.172989** for the four calls, using the existing repository rate of $0.000694/A100-second. These are not invoices or a newly configured price: they exclude CPU, memory, idle/container retention, storage and network. Cold provisioning/SDK overhead is not fully represented by the server's billing-time estimate. Consult [Modal pricing](https://modal.com/pricing) for actual billing.

All four calls returned valid audio with zero engine retries or rejected chunks. However **two of four assembled recordings had a 3.46-second silence**. Raw ASR WER ranged from 3.25% to 8.41%. These observations do not establish uniform quality. The greedy sequence matcher incorrectly scored a repeated 600-word passage near 32%; edit-distance WER was 8.41%, so the low greedy score was not called a 68% speech failure. Listening review is still needed for distortions, pronunciation and speaker similarity. This task did not alter the production validation, sampling or assembly to hide that result.

### Safe parallel alternatives

| Alternative | Evidence / feasibility | Benchmark status | Recommendation |
|---|---|---|---|
| Native batching | Pinned Faster Qwen3-TTS 0.5.4's optimized decoder uses scalar `token.item()` and single-sample codec state; passing a list is not a verified batching API. | Not run; no safe supported batching path verified. | Do not enable. |
| Two engines in one process/GPU container | CUDA graph buffers and global Torch RNG/fork state require proven isolation. A100 capacity alone is not proof of thread safety; about twice the observed allocated memory is only a projection, not a reserved-VRAM measurement. | Not run; no shared-object concurrency or `gather` was introduced. | Investigate separate spawned processes only in a separately approved experiment. |
| Two independent Modal containers | Separate processes/GPUs could isolate model/RNG/prompt state, but require a separately approved test deployment/container configuration and repeatability, ownership, ordering and cost tests. | Not deployed or measured. Current production cap remains one. | No two-container recommendation from unmeasured speed or cost projections. |

The pinned implementation's scalar decode is visible in [upstream source](https://github.com/andimarafioti/faster-qwen3-tts/blob/b35fdc7d71eb3e4d19e63f4b4a330a1aef31d682/faster_qwen3_tts/generate.py). Independent processes might avoid shared RNG state, but that has not been proven in this deployment. Two containers would duplicate some conditioning/startup and potentially idle billing. There are no measured parallel latency, quality or cost figures; none are inferred by halving wall time. **Retain one Modal container.**

## Spending, artifacts and retention

Paid OpenRouter requests reserve published catalog charges before dispatch. Ceilings were $1.50 for STT experiments, $0.80 for Voxtral plus its quality STT, and $0.10 for Modal quality STT. Reservations are conservative attempt counters, not provider invoices; actual response/generation costs are stored separately. No credit limit or account configuration was changed. Modal's separate GPU execution estimate ceiling was $1.00, enforced through planned-call checks and a benchmark-only remote timeout/cancellation path; shared production idle costs are not a hard-capped experiment invoice.

Completed STT configurations reported **$0.14898991** combined. The interrupted 6-probe's additional successful-request charges remain in its parts manifest and are excluded from that completed-run subtotal. Reconciled Voxtral generation charges total **$0.694642**, plus separately recorded acoustic quality STT. Every paid-call identifier that the provider returned is retained locally. No budget/credential values were added to Git.

All media, test text, transcripts, source hashes, timing rows and manifests are outside production user storage under ignored `data/benchmarks/concurrency/`. Nothing was inserted into meeting history. Manifests expire after seven days. Run the checked-root cleanup command below to delete expired marked runs; there is no claim that an unattended scheduled cleanup service was installed. Source recordings/reference files and user databases are never cleanup targets.

- `stt-20261102/`: source sample, 1/2/3/4/adaptive results and resumable partial 6.
- `voxtral-20261102/LISTEN.html`: labeled baseline/experimental preset recordings.
- `modal-20261102/LISTEN.html`: labeled one-/four-minute repeated clone recordings.
- `metrics.json`, `quality.json`, `http-attempts.json`, `parts.json` and `budget.json` retain evidence without committing private content.

## Reproduce

From the project root, with existing credentials and sufficient provider quota:

```powershell
.\.venv\Scripts\python -m pip install psutil
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m pytest backend/tests -q
.\.venv\Scripts\python -m backend.benchmarks.concurrency stt --source "<representative audio path>" --output data/benchmarks/concurrency/new-stt-run --maximum-usd 1.5 --job-timeout 600
# Narrow retry after the provider/account limit is resolved; completed checkpoints are reused:
.\.venv\Scripts\python -m backend.benchmarks.concurrency stt --source "<same audio path>" --output data/benchmarks/concurrency/new-stt-run --levels 6 8 10 12 adaptive --maximum-usd 1.5 --job-timeout 600
.\.venv\Scripts\python -m backend.benchmarks.concurrency voxtral --output data/benchmarks/concurrency/new-voxtral-run --maximum-usd 0.8
.\.venv\Scripts\python -m backend.benchmarks.quality --folder data/benchmarks/concurrency/new-voxtral-run --reconcile-costs
.\.venv\Scripts\python -m backend.benchmarks.modal_sequential --output data/benchmarks/concurrency/new-modal-run --maximum-gpu-usd 1
.\.venv\Scripts\python -m backend.benchmarks.quality --folder data/benchmarks/concurrency/new-modal-run --maximum-usd 0.1
.\.venv\Scripts\python -m backend.benchmarks.cleanup
.\.venv\Scripts\python -m backend.benchmarks.cleanup --delete-expired
```

`psutil` is an optional benchmark measurement tool, not an added application runtime requirement. Benchmark controller databases and artifacts are isolated from application history. Do not overlap the provider benchmarks: account-wide budgets and traffic can contaminate comparisons. Higher levels stop on a bounded failure and retain checkpoints. Repeating a new output directory incurs new charges; rerunning a completed directory reuses saved results. Keep comparison artifacts private and open the local listening indexes for manual review. Do not use the benchmark tools to change production deployment limits.

## Validation and exact files

The frontend production build and 21 Playwright tests passed, including Google/admin, independent summary modes, narration selector, stored voice previews and ownership workflows. The full backend suite passed **239 tests**, with **11 existing optional GPU waveform tests skipped** by the CPU environment. Automated controller tests cover overload classification, seconds/date Retry-After, all downshift levels, gradual recovery, failed probes, sustained latency, bounded task counts, active-request deadlines, successful-chunk resumption, ledger deduplication, global extraction, stale releases, reduced-cap draining and four separate competing worker processes. No new claim of a local GPU test is made here.

Migration `0013` adds the draft STT chunk/lease/controller tables and shared controller JSON. A SQLite backup was made first, and existing summary-mode, meeting and voice counts were verified unchanged. The existing independent-summary index was declared in model metadata to match migration 0012; it was preserved, not dropped. API and meeting worker were restarted; the existing speech worker and Modal deployment were not redeployed or reconfigured. Alembic schema and Ruff checks pass.

Files modified/added for this task:

- `.env.example` and STT-only values in ignored local `.env`.
- `backend/config.py`, `backend/models.py`, `backend/provider.py`, `backend/jobs.py`.
- `backend/services/stt_control.py`, `backend/services/stt_pipeline.py`.
- `backend/migrations/versions/0013_stt_adaptive.py`.
- `backend/tests/test_stt_adaptive.py`, `backend/tests/test_stt_pipeline.py`, `backend/tests/test_sumx.py`, `backend/tests/test_concurrency_benchmarks.py`.
- `backend/benchmarks/__init__.py`, `concurrency.py`, `modal_sequential.py`, `quality.py`, `cleanup.py`.
- `docs/CONCURRENCY_BENCHMARK_REPORT.md`.

Claude's prior changes to `docs/SUMMARY_MODES.md` and `frontend/tests/narration-progress.spec.ts` were left intact. No frontend source, production Modal source/config, preset inference module, model files or authentication code was changed. See the official [OpenRouter error/Retry-After documentation](https://openrouter.ai/docs/api_reference/errors-and-debugging) for structured in-flight-budget errors. Remaining limitations are the quota-blocked STT levels, absent EC2-wide load measurement, limited repeated TTS quality samples and untested safe GPU-parallel architectures.
