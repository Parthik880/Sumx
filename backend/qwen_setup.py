"""Explicit installation-time downloads and GPU readiness test."""

import argparse
import json
from pathlib import Path
from .config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=settings.qwen_tts_model, choices=["Qwen/Qwen3-TTS-12Hz-0.6B-Base"])
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    import torch
    from importlib.metadata import version, PackageNotFoundError

    try:
        version("qwen-tts")
    except PackageNotFoundError:
        pass
    else:
        raise RuntimeError(
            "Conflicting standard qwen-tts distribution found. Use the clean .venv-faster-qwen environment containing qwen-tts-hf only."
        )
    from huggingface_hub import snapshot_download

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is unavailable in this environment. Install the matching NVIDIA driver/CUDA PyTorch wheel."
        )
    free, total = torch.cuda.mem_get_info()
    print(
        json.dumps(
            {
                "torch": torch.__version__,
                "gpu": torch.cuda.get_device_name(0),
                "vram_gib": total / 1024**3,
                "free_gib": free / 1024**3,
                "bf16": torch.cuda.is_bf16_supported(),
                "model": args.model,
            }
        ),
        flush=True,
    )
    value = torch.ones((128, 128), device="cuda")
    assert (value @ value).sum().item() == 2097152
    del value
    if free < 4 * 1024**3:
        raise RuntimeError(
            "Less than 4 GiB is free. Close GPU-heavy applications and stop any running local voice worker before setup."
        )
    cache = Path(settings.qwen_tts_model_cache).resolve()
    for model in [args.model, "Qwen/Qwen3-TTS-Tokenizer-12Hz"]:
        print("Preparing " + model, flush=True)
        print(
            snapshot_download(
                model, cache_dir=str(cache), max_workers=4, ignore_patterns=["*.md", ".gitattributes"]
            ),
            flush=True,
        )
    if not args.download_only:
        from .qwen_runtime import QwenEngine

        engine = QwenEngine(args.model, cache, settings.qwen_tts_device, settings.qwen_tts_attention)
        engine.warmup()
        print(
            json.dumps(
                {
                    "loaded": True,
                    "dtype": str(engine.dtype),
                    "gpu": engine.gpu_name,
                    "allocated_mb": torch.cuda.memory_allocated() / 1024**2,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
