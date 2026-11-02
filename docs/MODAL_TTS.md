# Speech and Modal guide

SUMX uses two speech paths, chosen on the backend from trusted records:

| Voice | Provider | Model |
| --- | --- | --- |
| Preset | OpenRouter | `mistralai/voxtral-mini-tts-3-0` |
| Cloned | Modal GPU | Faster Qwen3-TTS `Qwen/Qwen3-TTS-12Hz-0.6B-Base` |

Presets never reach Modal, and cloned voices never reach OpenRouter speech. The backend needs no GPU, CUDA, PyTorch or model weights.

## Preset voices

`backend/preset_voices.py` holds the curated list: `en_paul_neutral` (default), `en_paul_confident`, `gb_oliver_neutral`, `gb_oliver_confident`, `gb_jane_neutral` and `gb_jane_confident`. OpenRouter also lists many `voice_NNNN` IDs, but their language and gender are unpublished, so they are not offered. Jane is the only named English female voice.

Narration splits text into chunks of up to `PRESET_CHUNK_CHARS` (1800), generates up to `PRESET_TTS_CONCURRENCY` (3) at once through one pooled client, keeps finished chunks for a retry, and joins the MP3s without re-encoding.

## Previews

Selecting or replaying a voice plays a stored file and never calls a provider.

Preset previews are in `data/media/preset-previews/{voice}/{version}.mp3`. They are not in Git, so generate them once per deployment:

```powershell
.\.venv\Scripts\python -m backend.preset_previews            # missing only
.\.venv\Scripts\python -m backend.preset_previews --refresh  # replace all
.\.venv\Scripts\python -m backend.preset_previews --voice gb_jane_neutral
```

A missing file shows "Preview unavailable" and is never generated automatically. To publish new previews, raise `PREVIEW_VERSION` and run the command.

Cloned previews stay in the owner's private voice folder, behind the owner-only route. The user starts one with **Generate preview** after confirming their transcript. It is then reused until the reference or transcript changes, or until `POST /api/voices/{id}/preview?refresh=true`.

`GET /api/voices/catalog` returns both voice types with provider, model and preview status.

## Modal architecture

| Component | Runs on | Role |
| --- | --- | --- |
| `backend/tts_queue.py` | CPU | Durable database queue; the existing `local_tts_*` tables are retained |
| `backend/tts_worker.py` | CPU | Sends a request to Modal and validates the result |
| `backend/services/modal_tts_client.py` | CPU | Private SDK calls, timeouts, transient retries, cancellation |
| `modal_service/modal_tts.py` | Modal GPU | `CloneTTS`, app `sumx-tts` |
| `modal_service/narration.py` | Modal GPU | Whole narration in one request, ending in one MP3 |
| `modal_service/inference.py` | Modal GPU | Generation, conditioning and waveform validation |

Each narration is one Modal call. The container loads the model once, reuses reference conditioning, generates chunks of up to 22 words in order, retries a rejected chunk up to twice, and returns only the MP3. If a chunk still fails, finished chunks come back so a retry does not regenerate them. Live progress uses the `sumx-tts-progress` Modal Dict, which holds counts only.

Reference recordings are sent per request, held in a temporary folder and kept out of the image and weights volume. In-memory conditioning is scoped to one user, profile, reference and job.

## GPU selection

`MODAL_GPU` in `modal_service/config.py` is the only GPU setting:

```python
MODAL_GPU = ["A100", "L40S", "L4", "A10"]
```

See the README section **Changing the Modal GPU**. Deploy with:

```powershell
.\.venv\Scripts\python -m modal deploy -m modal_service.modal_tts
```

Servers need `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in the root `.env`. Only the speech worker uses them.

## Transcription

`backend/services/stt_pipeline.py` transcribes up to `STT_CONCURRENCY` (4) chunks at once through one pooled async client per job. Audio streams from disk as multipart, so no base64 copy exists, and at most that many chunks are on disk.

- **Order:** finished chunks are saved immediately, in any order, and merged chronologically.
- **Boundaries:** each chunk has `STT_OVERLAP_SECONDS` (1.5) extra audio. A segment belongs to the chunk holding its midpoint, and words repeated at a boundary are removed.
- **Retries:** 429, 502, 503, 504 and connection failures retry with backoff. A timeout after upload may already be billed, so the job stops, and resuming redoes only that chunk.
- **Metrics:** timing per chunk and job is saved in the job's `stt_metrics` payload and logged.

## Costs

| Item | Status | Source |
| --- | --- | --- |
| Preset speech and previews | `estimated`, $0.000013 per character | Replaced when reconciliation finds a provider cost |
| Cloned speech | `estimated` | Measured GPU seconds × the published rate for the GPU received |

Modal estimates exclude CPU, memory and idle time, so they understate the invoice. Playing a stored preview records nothing.

## Measured results, 2 November 2026

**Transcription, first 50 minutes of a real recording (17 chunks)**

| Run | Total | Speedup |
| --- | --- | --- |
| Sequential | 58.3 s (49.5 s STT, 8.5 s audio preparation) | Baseline |
| Concurrency 3 | 24.0 s | 2.4× |
| Concurrency 4 | 17.3 s | 3.4× |

No retries occurred. Summed upload time was about 1.7 s, so the bottleneck was waiting for transcription one chunk at a time. Whisper agrees with itself 97–99% on identical audio, and the new text agrees 97% with the sequential text.

**Narration of about 60 s of audio on A100**

| Voice | Before, warm | After, warm |
| --- | --- | --- |
| Preset (Qwen, since replaced) | 40.2 s | 31.4 s |
| Cloned | 68.1 s | 35.9 s |

Earlier "cold" narration figures are unreliable, because the container stop command silently did nothing until it was run with `-y`.

**Live hybrid check:** Voxtral narrated 50.5 s of audio in 11 s, and a warm clone narrated 47.8 s in 27.6 s. Speech recognition matched 100% and 99.2%. Repetitive test text scores far lower because Whisper collapses repeats.

**Cloned cold starts, 8 true samples**

| Phase | Median | Worst |
| --- | --- | --- |
| Total, 2 chunks | 32.3 s | 50.9 s |
| GPU provisioning | 7.8 s | 10.4 s |
| Model load | 11.1 s | 18.9 s |
| CUDA warmup | 1.5 s | 1.9 s |
| Inference | 5.2 s | 7.0 s |

Imports took under 0.1 s. Phases sum to less than the total, leaving roughly 2–16 s unattributed. The 110 s and 213 s runs seen earlier did not reproduce, so their cause is not established.

The deployed list allocated A100 in 5 of 8 samples and A10 in 3. Modal treats the list as a preference, so A100 is not guaranteed even when one was available moments earlier. L40S and L4 were never allocated by the list. Forced directly, L40S and A10 passed with BF16, CUDA graphs and warmup, while L4 did not start within 240 s.

## Troubleshooting

- **Cloned requests wait a long time:** Check `modal app list`. A request is cancelled after `QWEN_TTS_TIMEOUT`.
- **Cloned voices show as unavailable:** Start `python -m backend.tts_worker`, deploy the app and check credentials and `data/tts.stderr.log`.
- **Preview unavailable:** Run `python -m backend.preset_previews`.
- **Cost stays pending:** The GPU is missing from `MODAL_GPU_USD_PER_SECOND`, or no GPU time was measured.

Production needs the API, `python -m backend.jobs` and `python -m backend.tts_worker`, plus the `modal_service` folder. The backend has not been run on a real 2 GB EC2 instance.
