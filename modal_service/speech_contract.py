"""Shared speech contract with no ML imports, used by both the backend and Modal workers."""

import array
import io
import math
import re
import wave
from pathlib import Path

PREVIEW_TEXT = "Hello. This is my new voice. I can read your meeting notes clearly."
PIPELINE_VERSION = "faster-qwen-local-icl-v1"
BASE_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
PRESET_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
SAMPLE_RATE = 24000
SPEAKERS = {
    "Aiden": ("Clear American English male", "en"),
    "Ryan": ("Energetic English male", "en"),
    "Vivian": ("Bright Chinese female", "zh"),
    "Serena": ("Warm Chinese female", "zh"),
    "Uncle_Fu": ("Mature Chinese male", "zh"),
    "Dylan": ("Youthful Beijing Chinese male", "zh"),
    "Eric": ("Lively Sichuan Chinese male", "zh"),
    "Ono_Anna": ("Playful Japanese female", "ja"),
    "Sohee": ("Warm Korean female", "ko"),
}
LANGUAGES = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}


class LocalTTSError(ValueError):
    """User-safe TTS error, retained under the existing API exception name."""


def speaker_id(value):
    for speaker in SPEAKERS:
        if speaker.lower() == str(value).lower():
            return speaker
    raise LocalTTSError("Choose one of the nine supported Qwen preset voices")


def duration_budget(text, preview=False):
    words = len(re.findall(r"\w+", text))
    cjk = len(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    expected = max(1.0, words / 2.6, cjk / 3.5, len(text.strip()) / 14)
    maximum = min(12.0 if preview else 90.0, max(6.0, expected * 2 + 1))
    return {
        "expected_seconds": expected,
        "max_seconds": maximum,
        "min_seconds": min(1.5, max(0.15, expected * 0.2)),
        "max_new_tokens": min(1100, math.ceil((maximum + 1) * 12)),
    }


def validate_duration(seconds, text, preview=False):
    budget = duration_budget(text, preview)
    if not math.isfinite(seconds) or not budget["min_seconds"] <= seconds <= budget["max_seconds"]:
        raise LocalTTSError(
            "Speech duration is abnormal. The sample was rejected; check the reference transcript and retry."
        )
    if seconds >= (budget["max_new_tokens"] - 2) / 12:
        raise LocalTTSError(
            "Speech reached its generation budget without a clean ending. Retry with a clean reference."
        )
    return budget


def pcm_metrics(content, *, reference=False, text=None, preview=False):
    try:
        with wave.open(io.BytesIO(content), "rb") as wav:
            channels, width, rate, frames = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getnframes(),
            )
            if channels != 1 or width != 2 or rate != SAMPLE_RATE or wav.getcomptype() != "NONE":
                raise LocalTTSError("Audio must be genuine mono 24 kHz PCM WAV, not relabeled audio")
            raw = wav.readframes(frames)
            if len(raw) != frames * 2:
                raise LocalTTSError("The audio file is incomplete")
        samples = array.array("h", raw)
    except (wave.Error, EOFError, ValueError) as exc:
        if isinstance(exc, LocalTTSError):
            raise
        raise LocalTTSError("The audio file could not be decoded") from exc
    if not samples:
        raise LocalTTSError("No audio was generated")
    seconds = len(samples) / rate
    rms = math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768
    clipping = sum(abs(x) >= 32700 for x in samples) / len(samples)
    if rms < 0.003:
        raise LocalTTSError(
            "The recording is silent or too quiet. Record clear speech closer to the microphone."
        )
    if clipping > 0.01:
        raise LocalTTSError(
            "The recording is excessively clipped. Lower the microphone level and record again."
        )
    if reference:
        if not 3 <= seconds <= 30:
            raise LocalTTSError("Use 3–30 seconds of single-speaker audio; 5–15 seconds is recommended")
        # A conservative broadband-noise screen, not a claim of full speech diarization.
        frame_size = rate // 20
        noisy = active = 0
        for start in range(0, len(samples) - frame_size, frame_size):
            part = samples[start : start + frame_size]
            energy = math.sqrt(sum(x * x for x in part) / len(part)) / 32768
            if energy > 0.006:
                active += 1
                noisy += sum((a < 0) != (b < 0) for a, b in zip(part, part[1:])) / len(part) > 0.42
        if active < 20:
            raise LocalTTSError("Too little clear speech was detected in the reference recording")
        if noisy / active > 0.8:
            raise LocalTTSError(
                "Severe background noise was detected. Use a quiet room and a single speaker."
            )
    if text is not None:
        validate_duration(seconds, text, preview)
    return {
        "sample_rate": rate,
        "channels": channels,
        "audio_seconds": seconds,
        "rms": rms,
        "clipping_fraction": clipping,
    }


def split_narration(text, size=400):
    if not 80 <= size <= 4000:
        raise LocalTTSError("Narration chunk size must be between 80 and 4000 characters")
    units = re.split(r"(?<=[.!?。！？])\s+|\n\s*\n", text.strip())
    chunks, current = [], ""
    for unit in units:
        while len(unit) > size:
            cut = unit.rfind(" ", 0, size + 1)
            cut = cut if cut > 0 else size
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:cut])
            unit = unit[cut:].lstrip()
        if len(current) + len(unit) + 1 > size:
            chunks.append(current)
            current = unit
        else:
            current = (current + " " + unit).strip()
    if current:
        chunks.append(current)
    return chunks


def _window_rms(samples, width):
    """Vectorized in GPU containers; the backend has no NumPy and uses the plain path."""
    try:
        import numpy as np
    except ImportError:
        return [
            math.sqrt(sum(float(x) ** 2 for x in samples[i : i + width]) / len(samples[i : i + width]))
            for i in range(0, len(samples), width)
        ]
    data = np.asarray(samples, dtype=np.float64)
    whole = len(data) // width
    rms = np.sqrt(np.mean(data[: whole * width].reshape(whole, width) ** 2, axis=1)).tolist()
    if len(data) % width:
        rms.append(float(np.sqrt(np.mean(data[whole * width :] ** 2))))
    return rms


def silence_metrics(samples, rate=SAMPLE_RATE, silence_db=-45):
    """20 ms RMS windows; report edges separately so natural pauses are never trimmed."""
    threshold = 10 ** (silence_db / 20)
    quiet = [value < threshold for value in _window_rms(samples, rate // 50)]
    runs, begin = [], None
    for index, value in enumerate(quiet + [False]):
        if value and begin is None:
            begin = index
        elif not value and begin is not None:
            runs.append((begin, index))
            begin = None
    return {
        "max_internal_silence_seconds": max(
            ((b - a) / 50 for a, b in runs if a > 0 and b < len(quiet)), default=0
        ),
        "leading_silence_seconds": runs[0][1] / 50 if runs and runs[0][0] == 0 else 0,
        "trailing_silence_seconds": (runs[-1][1] - runs[-1][0]) / 50
        if runs and runs[-1][1] == len(quiet)
        else 0,
    }


def clone_duration_limit(text, preview, ratio):
    budget = duration_budget(text, preview)
    return min(budget["max_seconds"], max(6, budget["expected_seconds"] * ratio + 1))


def read_pcm(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (
            1,
            2,
            SAMPLE_RATE,
            "NONE",
        ):
            raise LocalTTSError("Narration chunks must all be mono 24 kHz PCM16 WAV")
        frames = wav.getnframes()
        content = wav.readframes(frames)
        if not frames or len(content) != frames * 2:
            raise LocalTTSError("A narration chunk is empty or incomplete")
        return array.array("h", content)


def assemble_wav(parts, output, gap_ms=200, fade=True):
    total, trailing = 0, 0
    with wave.open(str(output), "wb") as wav:
        wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for index, path in enumerate(parts):
            samples = read_pcm(path)
            signal = silence_metrics([x / 32768 for x in samples])
            gap = (
                max(0, round((gap_ms / 1000 - trailing - signal["leading_silence_seconds"]) * SAMPLE_RATE))
                if index
                else 0
            )
            if fade:
                # A 5 ms fade to zero at each edge prevents clicks without overlap or lost frames.
                width = min(SAMPLE_RATE // 200, len(samples) // 2)
                for i in range(width):
                    gain = i / width
                    samples[i] = round(samples[i] * gain)
                    samples[-i - 1] = round(samples[-i - 1] * gain)
            wav.writeframes(b"\x00\x00" * gap + samples.tobytes())
            total += gap + len(samples)
            trailing = signal["trailing_silence_seconds"]
    pcm_metrics(Path(output).read_bytes())
    return total / SAMPLE_RATE
