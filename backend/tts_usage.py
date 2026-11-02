"""Per-invocation speech ledger. Modal amounts are rate estimates, never invoices."""

from decimal import Decimal
from sqlalchemy import select
from .models import Usage
from modal_service.config import MODAL_GPU_USD_PER_SECOND


def estimate_modal_cost(diagnostics):
    seconds = (diagnostics or {}).get("billable_seconds")
    rate = MODAL_GPU_USD_PER_SECOND.get((diagnostics or {}).get("modal_gpu"))
    if seconds is None or rate is None:
        return None
    return (Decimal(str(seconds)) * Decimal(rate)).quantize(Decimal("0.000001"))


def record_tts_usage(db, task):
    key = task.provider + ":" + task.id
    row = db.scalar(select(Usage).where(Usage.billing_key == key))
    if row is None:
        row = Usage(
            billing_key=key,
            user_id=task.user_id,
            meeting_id=task.meeting_id,
            operation="tts",
            provider=task.provider,
            model=task.model,
            request_id=task.id,
            voice_type=task.voice_type,
            purpose=task.purpose,
            created=task.created,
        )
        db.add(row)
    terminal = task.state in ("completed", "failed")
    if task.provider == "local_qwen3":
        row.cost = task.external_api_cost if terminal else None
        row.cost_status = "actual" if terminal else "pending"
        row.cost_source = "local_no_external_api_fee" if terminal else "unavailable"
    else:
        estimate = estimate_modal_cost(task.diagnostics) if terminal else None
        task.external_api_cost = estimate or Decimal("0")
        row.cost = estimate
        row.cost_status = "pending" if estimate is None else "estimated"
        row.cost_source = "unavailable" if estimate is None else "modal_gpu_rate_estimate"
    row.request_id = task.id
    row.generation_status = "succeeded" if task.state == "completed" else task.state
    row.input_characters = task.input_characters
    row.units = {
        "characters": task.input_characters,
        "seconds": task.audio_seconds,
        "processing_seconds": task.processing_seconds,
        "peak_vram_mb": task.peak_vram_mb,
        **{
            k: v
            for k, v in (task.diagnostics or {}).items()
            if k
            in (
                "gpu_execution_seconds",
                "gpu_name",
                "dtype",
                "generation_seconds",
                "realtime_factor",
                "retries",
                "reference_conditioning_seconds",
                "load_seconds",
                "warmup_seconds",
                "billable_seconds",
                "cold_start",
                "cold_start_seconds",
                "modal_gpu",
                "attempts",
                "max_new_tokens",
                "sample_rate",
            )
        },
    }
