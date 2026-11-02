# SUMX Voice Cloning Studio

The studio uses Faster Qwen3-TTS 0.6B on Modal GPUs. See the [Modal speech guide](MODAL_TTS.md).

Record/upload a clean single-speaker reference, provide its exact transcript (or leave it empty for transcription), confirm permission, generate the preview, listen, then save. Reference audio is sent to the configured OpenRouter STT service for transcript verification; speech synthesis runs locally. A preview sentence is never used as the reference transcript.

The library, recording workflow, consent, owner-only media access, search, rename and deletion remain. Existing profiles retain their references, saved status and historical audio; they require verification and a new preview to become usable with the current pipeline. No model is retrained per voice. Names are unique within each user's library.

Preset narration uses Voxtral TTS 3 through OpenRouter, with stored previews and default voice `en_paul_neutral`. Cloned narration uses Base with the selected user's verified reference. Both categories support preview and meeting narration. Their actual model IDs and GPU time are recorded separately; historical remote accounting is preserved.
