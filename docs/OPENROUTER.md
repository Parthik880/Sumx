# Verified OpenRouter integration

Checked on 31 October 2026 from official sources:

- [Speech-to-text guide](https://openrouter.ai/docs/guides/overview/multimodal/stt): multipart or base64 JSON; multipart capped at 25 MB; `json` or `verbose_json`, with optional segment/word timestamps. Some models reject verbose output. Provider processing has a 60-second upstream timeout, so bounded clips are used.
- [Text-to-speech guide](https://openrouter.ai/docs/guides/overview/multimodal/tts): raw audio bytes, `mp3` or `pcm`, provider-dependent voice IDs, `X-Generation-Id`. SUMX explicitly requests MP3; PCM-only models require implementation changes rather than mislabeling bytes as MP3.
- [Transcription model catalog](https://openrouter.ai/api/v1/models?output_modalities=transcription): returned `openai/whisper-large-v3`, modality `audio->transcription`.
- [Speech model catalog](https://openrouter.ai/api/v1/models?output_modalities=speech): returned `mistralai/voxtral-mini-tts-2603` and voices including `en_paul_neutral`, `gb_oliver_neutral`, `gb_jane_neutral`, `fr_marie_neutral` and emotion variants. SUMX queries this catalog rather than hardcoding the selector.
- [Summary model](https://openrouter.ai/google/gemini-2.5-flash-lite): general catalog returned a 1,048,576-token context and prices of $0.10/M prompt tokens and $0.40/M completion tokens. The implementation uses a conservative configurable character bound to allow smaller model swaps.
- [Generation API](https://openrouter.ai/docs/api/api-reference/generations/get-generation) and [credits API](https://openrouter.ai/docs/api/api-reference/credits/get-credits): used only through administrator routes for reconciliation/balance.

All provider requests use `https://openrouter.ai/api/v1`. No direct external STT/TTS/LLM provider is called. Google OIDC endpoints are used for authentication only. FFmpeg runs locally for decoding, extraction and audio concatenation; it runs no AI models.

STT stores returned text/timing without inferring segments or speaker identities. Speaker indices from different clips are never treated as the same person. Summary generation is prompted to preserve missing owners/dates as null, and the schema validates structure; humans must still review AI-generated content for factual accuracy.

Only retryable 429/502/503 responses are retried automatically, up to three attempts with bounded backoff. Transport timeouts are surfaced for explicit retry because remote billing may already have happened. Unsupported summary `response_format` falls back once to prompt-directed JSON and the same schema validation. Malformed output fails visibly without deleting completed transcripts. STT verbose output is deliberately configurable instead of silently pretending timing is supported.

An earlier Fish Audio reference-synthesis test on 1 November 2026 incurred an actual $0.00114 charge; its historical accounting is preserved. Cloned speech now runs exclusively through [local Qwen3-TTS](FASTER_QWEN_TTS.md). OpenRouter receives no cloned-voice references; it continues to handle STT, summaries and Voxtral presets. Exact pricing and feature support should be rechecked after changing those models.
