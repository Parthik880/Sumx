"""Repeatable Faster 0.6B benchmark; retired standard results remain in private JSON reports."""

import argparse
import json
import time
from pathlib import Path

REF_TEXT = "Hello, this is a synthetic speech sample for testing the private SUMX voice studio. Meeting summaries help us remember important decisions and next steps."
PREVIEW = "Hello! This is my new voice in SUMX. I can now narrate my meeting summaries."
LONG = (
    "The team reviewed the schedule and agreed to share the updated notes. Members will check the action items before the next discussion. "
    + "This summary was generated locally to verify stable voice conditioning across consecutive audio chunks. "
    * 5
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["faster"], required=True)
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    args = parser.parse_args()
    import torch
    import numpy as np
    import soundfile as sf
    from huggingface_hub import snapshot_download
    from .qwen_runtime import split_narration

    root = Path(__file__).resolve().parents[1]
    folder = root / "data" / "faster-qwen-benchmark"
    folder.mkdir(parents=True, exist_ok=True)
    reference = root / "data/media/integration-voice-check/reference.wav"
    path = snapshot_download(args.model, cache_dir=str(root / "models/qwen3-tts"), local_files_only=True)
    started = time.perf_counter()
    from faster_qwen3_tts import FasterQwen3TTS

    model = FasterQwen3TTS.from_pretrained(
        path,
        backend="torch",
        device="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    torch.cuda.synchronize()
    load = time.perf_counter() - started
    start = time.perf_counter()
    model.warmup(prefill_len=100)
    torch.cuda.synchronize()
    warmup = time.perf_counter() - start
    results = []
    for label, texts in [
        ("preview_cold", [PREVIEW]),
        ("preview_warm", [PREVIEW]),
        ("long_warm", split_narration(LONG, 400)),
    ]:
        torch.manual_seed(1234)
        torch.cuda.reset_peak_memory_stats()
        begin = time.perf_counter()
        first = None
        parts = []
        sr = 24000
        with torch.inference_mode():
            for text in texts:
                for audio, sr, timing in model.generate_voice_clone_streaming(
                    text=text,
                    language="English",
                    ref_audio=str(reference),
                    ref_text=REF_TEXT,
                    chunk_size=8,
                    max_new_tokens=2048,
                ):
                    if first is None:
                        first = time.perf_counter() - begin
                    parts.append(np.asarray(audio).reshape(-1))
        torch.cuda.synchronize()
        seconds = time.perf_counter() - begin
        audio = np.concatenate(parts)
        duration = len(audio) / sr
        sf.write(str(folder / f"{args.engine}-{args.model.split('/')[-1]}-{label}.wav"), audio, sr)
        row = dict(
            case=label,
            segments=len(texts),
            first_audio_seconds=first,
            inference_seconds=seconds,
            audio_seconds=duration,
            rtf_compute_over_audio=seconds / duration,
            peak_vram_mb=torch.cuda.max_memory_allocated() / 1024**2,
        )
        print(json.dumps(row), flush=True)
        results.append(row)
    report = dict(
        engine=args.engine,
        model=args.model,
        gpu=torch.cuda.get_device_name(0),
        torch=torch.__version__,
        load_seconds=load,
        warmup_seconds=warmup,
        results=results,
        cuda_graphs=bool(
            args.engine == "faster" and model.predictor_graph.captured and model.talker_graph.captured
        ),
    )
    target = folder / f"{args.engine}-{args.model.split('/')[-1]}.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Report:", target, flush=True)


if __name__ == "__main__":
    main()
