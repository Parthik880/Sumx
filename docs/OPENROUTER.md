# OpenRouter integration

SUMX uses OpenRouter for meeting transcription, structured summaries and checking voice-reference transcripts. All new speech synthesis uses [local Faster Qwen3-TTS](MODAL_TTS.md). No OpenRouter speech-generation request, preset catalog lookup or remote TTS fallback remains.

`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `OPENROUTER_STT_MODEL`, `OPENROUTER_SUMMARY_MODEL` and `OPENROUTER_STT_TIMESTAMPS` configure remote work. All requests use the configured OpenRouter gateway; Google OIDC endpoints serve authentication separately. FFmpeg handles local decoding and encoding.

The STT adapter sends the correct audio MIME type, supports verbose timestamps when configured and preserves returned segments without inventing speaker identities. A voice reference is transcribed separately from meeting audio and its words are checked against the supplied reference transcript. Its actual STT cost is tracked as voice-reference usage.

Summaries pass schema validation; unsupported structured-output formatting falls back once to prompt-directed JSON with the same validation. Only retryable 429/502/503 responses are automatically retried, with bounded backoff. Transport timeouts surface for explicit retry because remote billing may already have happened. Completed transcripts/checkpoints survive later failures.

Historical remote-speech costs and model IDs remain in the ledger and existing generated audio remains playable. New local TTS has zero external API fee, with GPU time tracked separately and hardware pricing unconfigured. Provider-reported costs take precedence for remote operations. Administrator-only generation and credit reconciliation remain available.

Preset speech uses `POST /audio/speech` with `mistralai/voxtral-mini-tts-3-0`. See the [speech guide](MODAL_TTS.md).
