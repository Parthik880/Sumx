# SUMX Voice Cloning Studio

The current studio uses **local Faster Qwen3-TTS 0.6B with CUDA Graphs on the NVIDIA GPU**. See [installation, architecture, privacy, live narration progress and measured GPU results](FASTER_QWEN_TTS.md).

The existing library, recording workflow, consent, owner-only media access, search, rename and deletion are preserved. Existing recordings now need a matching editable reference transcript before their first local preview. Preset narration continues through OpenRouter/Voxtral; cloned previews and narration make no external inference requests.

Earlier OpenRouter/Fish generation records remain historical accounting only. The old remote cloning configuration and reference-audio request path have been removed.
