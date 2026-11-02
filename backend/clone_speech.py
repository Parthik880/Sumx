"""Cloned narration word boundaries. Assembly runs inside Modal; see modal_service.narration."""

import re
from .speech_contract import LocalTTSError, assemble_wav, silence_metrics

assemble_clone_wav = assemble_wav
__all__ = ["PIPELINE", "assemble_clone_wav", "silence_metrics", "split_clone_narration"]

PIPELINE = "clone-v2"


def split_clone_narration(text, limit=22):
    if limit < 1:
        raise LocalTTSError("Cloned narration chunk size must be positive")
    tokens = list(re.finditer(r"\S+", text))
    chunks, start = [], 0
    while start < len(tokens):
        end = min(start + limit, len(tokens))
        while end > start + 1 and tokens[end - 1].end() - tokens[start].start() > 600:
            end -= 1
        if tokens[end - 1].end() - tokens[start].start() > 600:
            raise LocalTTSError("A narration word exceeds the 600-character generation limit")
        if end < len(tokens):
            sentence, clause = [], []
            for index in range(start, end):
                word = tokens[index].group()
                gap = text[tokens[index].end() : tokens[index + 1].start()]
                abbreviation = word.lower() in {
                    "dr.",
                    "mr.",
                    "mrs.",
                    "ms.",
                    "prof.",
                    "e.g.",
                    "i.e.",
                    "vs.",
                } or bool(re.fullmatch(r"(?:[A-Za-z]\.)+", word))
                if "\n\n" in gap or (re.search(r"[.!?。！？][\"'’”)]*$", word) and not abbreviation):
                    sentence.append(index + 1)
                elif re.search(r"[,;:—][\"'’”)]*$", word):
                    clause.append(index + 1)
            end = (
                sentence[-1]
                if sentence
                else next((i for i in reversed(clause) if i - start >= limit // 2), end)
            )
        chunks.append(text[tokens[start].start() : tokens[end - 1].end()])
        start = end
    return chunks
