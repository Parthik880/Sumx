import json
import math
import subprocess
from pathlib import Path
from .config import settings

EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".webm", ".mp4", ".mov"}
MIMES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "application/ogg",
    "audio/webm",
    "video/webm",
    "video/mp4",
    "video/quicktime",
    "application/octet-stream",
}


def run(args, timeout=120):
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except FileNotFoundError as exc:
        raise ValueError("FFmpeg and ffprobe must be installed and on PATH") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Media could not be decoded within the processing limit") from exc


def probe(path: Path):
    data = json.loads(
        run(
            [
                settings.ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            30,
        )
    )
    streams = data.get("streams", [])
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise ValueError("The file contains no decodable audio track")
    duration = float(data.get("format", {}).get("duration", 0))
    if duration == 0:
        # MediaRecorder WebM often omits duration; measure a bounded audio decode.
        output = run(
            [
                settings.ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-t",
                str(settings.max_duration_seconds + 1),
                "-map",
                "0:a:0",
                "-vn",
                "-f",
                "null",
                "-",
                "-progress",
                "pipe:1",
                "-nostats",
            ]
        )
        times = [
            int(line.split("=", 1)[1]) / 1000000
            for line in output.decode().splitlines()
            if line.startswith("out_time_us=") and line.split("=", 1)[1].lstrip("-").isdigit()
        ]
        duration = max(times, default=0)
    if not math.isfinite(duration) or duration <= 0 or duration > settings.max_duration_seconds:
        raise ValueError(
            f"Media must have a known duration between 0 and {settings.max_duration_seconds // 60} minutes"
        )
    return duration, any(s.get("codec_type") == "video" for s in streams)


def signature_ok(path: Path):
    with path.open("rb") as f:
        head = f.read(32)
    return (
        head.startswith((b"ID3", b"fLaC", b"OggS", b"\x1aE\xdf\xa3"))
        or (head.startswith(b"RIFF") and head[8:12] == b"WAVE")
        or head[4:8] == b"ftyp"
        or (len(head) > 1 and head[0] == 255 and (head[1] & 0xE0) == 0xE0)
    )


def extract(source: Path, target: Path, offset: float, duration: float):
    run(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-protocol_whitelist",
            "file,pipe",
            "-ss",
            str(offset),
            "-i",
            str(source),
            "-t",
            str(duration),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(target),
        ]
    )
    if target.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("Extracted audio exceeds the safe provider upload limit")


def safe_path(value: str):
    path = Path(value).resolve()
    if not path.is_relative_to(settings.storage_dir.resolve()):
        raise ValueError("Invalid storage path")
    return path


def text_chunks(text: str, size: int):
    chunks = []
    while len(text) > size:
        cut = max(text.rfind("\n", 0, size), text.rfind(". ", 0, size))
        if cut < size // 2:
            cut = size
        else:
            cut += 1
        chunks.append(text[:cut])
        text = text[cut:]
    if text.strip():
        chunks.append(text)
    return chunks
