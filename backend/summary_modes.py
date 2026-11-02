"""Concise, Standard and Detailed: distinct prompts, targets derived from the transcript, and
one bounded correction. Nothing here pads a summary that has too little to say."""

import json
from .config import settings
from .media import text_chunks
from .provider import ProviderError
from .schema import spoken_text

# Bump when prompts change so earlier summaries are not reused for a new request.
PROMPT_VERSION = "modes-v5"
STANDARD_SHARE = 0.095
MODE_SCALE = {"concise": 0.25, "standard": 1.0, "detailed": 1.75}
# A result this far over target gets one shortening pass.
OVERRUN = 1.25
# Notes are far smaller than the transcript, so one call can merge a long meeting.
SYNTHESIS_CHARS = 120000


def target_words(mode, transcript_words):
    standard = min(1100, max(150, round(transcript_words * STANDARD_SHARE)))
    return max(40, round(standard * MODE_SCALE[mode]))


def summary_words(content):
    return len(spoken_text(content).split())


def prompt_identity():
    return f"{PROMPT_VERSION}:{settings.openrouter_summary_model}"


def note_groups(parts):
    """Whole notes, never split mid-object, in groups the final call can read at once."""
    groups, current, size = [], [], 0
    for part in parts:
        text = json.dumps(part)
        if current and size + len(text) > SYNTHESIS_CHARS:
            groups.append("\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text)
    groups.append("\n".join(current))
    return groups


def build_summary(router, text, mode, on_call, *, transcript_words=None, parts=None, on_part=None,
                  metrics=None, on_metrics=None):
    """Summarize `text` in `mode`. Long transcripts become notes first, then one final summary."""
    target = target_words(mode, transcript_words or len(text.split()))
    chunks = text_chunks(text, settings.summary_chunk_chars)
    parts = list(parts or [])
    metrics = dict(metrics or {})
    metrics.update(mode=mode, transcript_chunks=len(chunks), target_words=target, records_saved=0)
    for key in ("llm_calls", "note_calls", "synthesis_calls", "correction_calls", "direct_calls"):
        metrics.setdefault(key, 0)

    def ask(body, synthesis=False, **options):
        category = ("correction_calls" if options.get("correction") else "note_calls"
                    if options.get("notes") else "synthesis_calls" if synthesis else "direct_calls")

        def count():
            metrics["llm_calls"] += 1
            metrics[category] += 1
            if on_metrics:
                on_metrics(metrics)

        count()
        try:
            result, data, response = router.summarize(body, mode, synthesis, **options)
        except ProviderError as exc:
            if not exc.retryable or options.get("token_scale"):
                raise
            # The rejected call was still billed, so it is recorded before the single retry.
            on_call(exc.response.json(), exc.response)
            count()
            result, data, response = router.summarize(body, mode, synthesis, **options, token_scale=2)
        on_call(data, response)
        return result

    if len(chunks) == 1:
        content = parts[0] if parts else ask(chunks[0], target_words=target)
    else:
        # Notes keep what the final mode may need; the last step sets the length.
        notes = max(200, round(target_words("detailed", len(text.split())) * 1.2 / len(chunks)))
        for index in range(len(parts), len(chunks)):
            parts.append(ask(chunks[index], target_words=notes, notes=True))
            if on_part:
                on_part(parts, index + 1, len(chunks))
        while True:
            groups = note_groups(parts)
            if len(groups) == 1:
                content = ask(groups[0], True, target_words=target)
                break
            reduced = [ask(group, True, target_words=notes, notes=True) for group in groups]
            if len(reduced) >= len(parts):
                raise ProviderError("Summary synthesis could not reduce the meeting notes")
            parts = reduced
    words = summary_words(content)
    corrected = False
    if words > target * OVERRUN:
        try:
            shorter = ask(json.dumps(content), True, target_words=target, correction=True)
        except ProviderError:
            shorter = None  # The uncorrected summary is still valid and already paid for.
        if shorter is not None and summary_words(shorter) < words:
            content, corrected = shorter, True
    metrics.update(summary_words=summary_words(content), corrected=corrected)
    return content, metrics
