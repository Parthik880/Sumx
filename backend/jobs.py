import asyncio
import hashlib
import logging
import threading
import time
from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .config import settings
from .media import probe, run, safe_path
from .models import AudioOutput, ClonedVoice, DB, Job, Meeting, Summary, Transcript, Usage, SttChunk
from .provider import OpenRouter, ProviderError
from .schema import spoken_text
from .money import usd
from . import tts_queue
from .services.preset_tts import generate_preset
from .summary_modes import build_summary, prompt_identity
from .services.stt_control import GlobalBudget, preferred_level
from .services.stt_pipeline import chunk_plan, merge_parts, transcribe_chunks
from .preset_voices import PRESETS
from .speech_contract import PIPELINE_VERSION, split_narration
from .clone_speech import PIPELINE as CLONE_PIPELINE, split_clone_narration

ACTIVE = ("queued", "running")
logger = logging.getLogger(__name__)


class JobConflict(ValueError):
    pass


def latest(db, cls, meeting_id):
    return db.scalar(select(cls).where(cls.meeting_id == meeting_id).order_by(cls.version.desc()))


def enqueue(db, meeting, kind, request_key, payload=None):
    # Serialize enqueue/edit/delete for one meeting on both SQLite and PostgreSQL.
    db.execute(update(Meeting).where(Meeting.id == meeting.id).values(status=Meeting.status))
    db.refresh(meeting)
    payload = dict(payload or {})
    if kind in ("pipeline", "summarize"):
        payload.setdefault("detail", meeting.detail)
    if kind == "summarize":
        # The same attempt ID in another mode must never return that mode's summary job.
        legacy = db.scalar(select(Job).where(Job.meeting_id == meeting.id, Job.request_key == request_key))
        if legacy and legacy.kind == kind and legacy.payload.get("detail", meeting.detail) == payload["detail"]:
            return legacy
        request_key = "summary:" + hashlib.sha256(f"{payload['detail']}:{request_key}".encode()).hexdigest()
    previous = db.scalar(select(Job).where(Job.meeting_id == meeting.id, Job.request_key == request_key))
    if previous:
        return previous
    active = db.scalar(select(Job).where(Job.meeting_id == meeting.id, Job.state.in_(ACTIVE)))
    if active:
        if kind == "summarize" and not (
            active.kind in ("summarize", "pipeline")
            and active.payload.get("detail", meeting.detail) == payload["detail"]
        ):
            raise JobConflict("This meeting is busy. Try again when the current task finishes.")
        return active
    job = Job(
        meeting_id=meeting.id,
        kind=kind,
        request_key=request_key,
        payload=payload,
        user_id=meeting.user_id,
        voice_id=(payload or {}).get("cloned_voice_id"),
        model_name=settings.qwen_tts_model
        if (payload or {}).get("voice_type") == "cloned"
        else settings.openrouter_tts_model
        if kind == "speech"
        else None,
        narration_stage="QUEUED" if kind == "speech" else None,
    )
    db.add(job)
    meeting.status = "queued"
    meeting.error = None
    db.commit()
    return job


def account(
    db,
    meeting,
    operation,
    model,
    data,
    response,
    *,
    user_id=None,
    voice_type=None,
    input_characters=None,
    purpose="meeting",
    generation_status="succeeded",
    provider="openrouter",
    currency="USD",
    audio_seconds=None,
):
    if currency != "USD":
        raise ValueError("SUMX currently accepts USD usage only; convert explicitly before accounting")
    units = data.get("usage") or {}
    if not isinstance(units, dict):
        units = {}
    units = dict(units)
    if audio_seconds is not None:
        units.setdefault("seconds", audio_seconds)
    cost = usd(units.get("cost"))
    actual = cost is not None
    if cost is None:
        cost = usd(data.get("estimated_cost"))
    gen = data.get("id") or response.headers.get("x-generation-id")
    request_id = response.headers.get("x-request-id")
    gen = gen if isinstance(gen, str) and len(gen) <= 200 else None
    request_id = request_id if isinstance(request_id, str) and len(request_id) <= 200 else None
    key = "generation:" + gen if gen else "request:" + request_id if request_id else None
    if key and provider != "openrouter":
        key = provider + ":" + key
    if key:
        existing = db.scalar(select(Usage).where(Usage.billing_key == key))
        if existing:
            if (actual and existing.cost_status != "actual") or (
                cost is not None and existing.cost_status == "pending"
            ):
                existing.cost, existing.cost_status, existing.cost_source = (
                    cost,
                    "actual" if actual else "estimated",
                    "provider_reported" if actual else "catalog_character_estimate",
                )
            if generation_status != "succeeded":
                existing.generation_status = generation_status
            db.commit()
            return existing
    usage = Usage(
        meeting_id=meeting.id if meeting else None,
        user_id=meeting.user_id if meeting else user_id,
        operation=operation,
        provider=provider,
        currency=currency,
        model=model,
        generation_id=gen,
        request_id=request_id,
        cost=cost,
        cost_source="provider_reported"
        if actual
        else "catalog_character_estimate"
        if cost is not None
        else "unavailable",
        cost_status="actual" if actual else "estimated" if cost is not None else "pending",
        billing_key=key,
        voice_type=voice_type,
        input_characters=input_characters,
        purpose=purpose,
        generation_status=generation_status,
        units={
            k: float(v) if isinstance(v, Decimal) else v
            for k, v in units.items()
            if k != "cost" and isinstance(v, (float, int, Decimal))
        },
    )
    if not actual and cost is not None and isinstance(data.get("pricing"), dict):
        usage.units = {**usage.units, "pricing": data["pricing"]}
    try:
        with db.begin_nested():
            db.add(usage)
            db.flush()
        db.commit()
        return usage
    except IntegrityError:
        # Another worker already recorded this provider request; do not charge it twice.
        if not key or not db.scalar(select(Usage.id).where(Usage.billing_key == key)):
            raise


def progress(db, job, meeting, stage, completed=0, total=0):
    job.stage = meeting.status = stage
    job.completed, job.total = completed, total
    job.heartbeat = time.time()
    db.commit()


def process_job(job_id, provider=None):
    router = provider or OpenRouter()
    speech_characters = None
    speech_model = settings.openrouter_tts_model
    try:
        with DB() as db:
            job = db.get(Job, job_id)
            if job.state == "completed":
                return
            if job.started_at is None:
                job.started_at = time.time()
            meeting = db.get(Meeting, job.meeting_id)
            folder = settings.storage_dir / meeting.id
            folder.mkdir(parents=True, exist_ok=True)
            checkpoint = dict(job.checkpoint)
            if job.kind in ("pipeline", "transcribe") and not checkpoint.get("transcript_done"):
                if not meeting.original_path:
                    raise ValueError("The original recording was removed by the retention policy")
                source = safe_path(meeting.original_path)
                # Finished chunks survive any interruption, including out-of-order completions.
                parts = {int(k): v for k, v in checkpoint.get("stt_parts", {}).items()}
                for index, part in enumerate(checkpoint.get("transcription_parts", [])):
                    parts.setdefault(index, {**part, "offset": index * settings.audio_chunk_seconds})
                for row in db.scalars(select(SttChunk).where(SttChunk.job_id == job.id)):
                    parts[row.chunk_index] = {
                        "text": row.text,
                        "segments": row.segments,
                        "language": row.language,
                        "offset": row.offset_seconds,
                    }
                n = len(chunk_plan(meeting.duration))
                progress(db, job, meeting, "transcribing", len(parts), n)

                def save_part(chunk, part, data, response, seconds):
                    row = SttChunk(
                        job_id=job.id,
                        meeting_id=meeting.id,
                        chunk_index=chunk["index"],
                        offset_seconds=chunk["offset"],
                        text=part["text"],
                        segments=part["segments"],
                        language=part.get("language"),
                        request_id=response.headers.get("x-request-id")
                        or data.get("id")
                        or response.headers.get("x-generation-id"),
                    )
                    db.add(row)
                    # The chunk and its ledger row commit together, so neither can exist alone.
                    db.flush()
                    charge = account(
                        db,
                        meeting,
                        "stt",
                        settings.openrouter_stt_model,
                        data,
                        response,
                        audio_seconds=seconds,
                    )
                    parts[chunk["index"]] = part
                    if charge:
                        row.cost, row.cost_status = charge.cost, charge.cost_status
                    progress(db, job, meeting, "transcribing", len(parts), n)

                stats = {}
                try:
                    asyncio.run(
                        transcribe_chunks(
                            router,
                            source,
                            folder,
                            meeting.duration,
                            meeting.language,
                            parts,
                            save_part,
                            stats,
                            budget=GlobalBudget(job.id),
                            start=preferred_level(),
                        )
                    )
                finally:
                    job.payload = {**job.payload, "stt_metrics": stats}
                    db.commit()
                text, segments = merge_parts(parts)
                if not text.strip():
                    raise ProviderError("No speech was detected in the recording")
                meeting.transcript_version += 1
                db.add(
                    Transcript(
                        meeting_id=meeting.id,
                        version=meeting.transcript_version,
                        text=text,
                        segments=segments,
                        language=parts[min(parts)].get("language"),
                    )
                )
                checkpoint["transcript_done"] = True
                # Text is now in the transcript table; keep only a small durable completion marker.
                checkpoint.pop("stt_parts", None)
                checkpoint.pop("transcription_parts", None)
                job.checkpoint = dict(checkpoint)
                db.execute(update(SttChunk).where(SttChunk.job_id == job.id).values(text="", segments=[]))
                db.commit()
                if not meeting.retain_source:
                    source.unlink(missing_ok=True)
                    meeting.original_path = None
                    db.commit()
            if job.kind in ("pipeline", "summarize") and not checkpoint.get("summary_done"):
                transcript = latest(db, Transcript, meeting.id)
                if not transcript:
                    raise ValueError("Create a transcript before requesting a summary")
                detail = job.payload.get("detail", meeting.detail)
                context = {"mode": detail, "transcript_version": transcript.version, "prompt": prompt_identity()}
                if checkpoint.get("summary_context", context) != context:
                    raise ValueError("Summary checkpoint does not match this mode, transcript and prompt")
                checkpoint["summary_context"] = context
                text = (
                    "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in transcript.segments)
                    if transcript.segments
                    else transcript.text
                )

                def save_notes(notes, done, total):
                    checkpoint["summary_parts"] = notes
                    job.checkpoint = dict(checkpoint)
                    progress(db, job, meeting, "summarizing", done, total)

                def save_metrics(metrics):
                    job.payload = {**job.payload, "summary_metrics": dict(metrics)}
                    db.commit()
                    logger.info("summary_call job=%s mode=%s metrics=%s", job.id, detail, metrics)

                progress(db, job, meeting, "summarizing", 0, 1)
                content, metrics = build_summary(
                    router,
                    text,
                    detail,
                    lambda data, response: account(
                        db, meeting, "llm", settings.openrouter_summary_model, data, response
                    ),
                    transcript_words=len(transcript.text.split()),
                    parts=checkpoint.get("summary_parts", []),
                    on_part=save_notes,
                    metrics=job.payload.get("summary_metrics"),
                    on_metrics=save_metrics,
                )
                job.payload = {**job.payload, "summary_metrics": metrics}
                meeting.summary_version += 1
                if not transcript.segments:
                    for action in content["action_items"]:
                        action["timestamp"] = None
                db.add(
                    Summary(
                        meeting_id=meeting.id,
                        version=meeting.summary_version,
                        transcript_version=transcript.version,
                        content=content,
                        detail=detail,
                        prompt_version=prompt_identity(),
                    )
                )
                if meeting.title == "Untitled meeting":
                    meeting.title = content["title"]
                checkpoint["summary_done"] = True
                checkpoint.pop("summary_parts", None)
                metrics["records_saved"] = 1
                job.payload = {**job.payload, "summary_metrics": metrics}
                job.checkpoint = dict(checkpoint)
                db.commit()
                logger.info("summary_saved job=%s mode=%s version=%s metrics=%s", job.id, detail, meeting.summary_version, metrics)
            if job.kind == "speech":
                summary = db.scalar(
                    select(Summary).where(
                        Summary.meeting_id == meeting.id,
                        Summary.version == job.payload.get("summary_version", meeting.summary_version),
                    )
                )
                if not summary:
                    raise ValueError("Create a summary before generating narration")
                voice_type = job.payload.get("voice_type", "preset")
                voice_name = None
                if voice_type == "cloned":
                    speech_model = settings.qwen_tts_model
                    profile = db.get(ClonedVoice, job.payload.get("cloned_voice_id"))
                    if not profile or profile.user_id != meeting.user_id or not profile.saved:
                        raise ValueError("The selected saved voice is no longer available")
                    if (
                        not profile.consent_at
                        or not profile.reference_text.strip()
                        or not profile.reference_verified_at
                        or profile.preview_version != PIPELINE_VERSION
                    ):
                        raise ValueError("This voice requires a new preview for the configured model")
                    voice = f"cloned:{profile.id}:{CLONE_PIPELINE}"
                    voice_name = profile.name
                    job.narration_stage = "PREPARING_VOICE"
                else:
                    voice = (
                        job.payload.get("preset_voice_id")
                        or job.payload.get("voice")
                        or settings.tts_default_voice
                    )
                    if voice not in PRESETS:
                        raise ValueError("Voice is not supported by the configured TTS model")
                # Keyed by the spoken words, so another mode or older wording never matches.
                spoken_hash = hashlib.sha256(spoken_text(summary.content).encode()).hexdigest()[:16]
                voice_key = f"{voice}:{spoken_hash}"
                audio_query = select(AudioOutput).where(
                    AudioOutput.meeting_id == meeting.id,
                    AudioOutput.voice == voice_key,
                    AudioOutput.model == speech_model,
                )
                if voice_type == "cloned":
                    audio_query = audio_query.where(
                        AudioOutput.path.endswith(f"{CLONE_PIPELINE}-narration.mp3")
                    )
                existing = db.scalar(audio_query.order_by(AudioOutput.summary_version.desc()))
                if existing and not safe_path(existing.path).is_file():
                    existing = None
                if existing:
                    if existing.summary_version != summary.version and not db.scalar(
                        audio_query.where(AudioOutput.summary_version == summary.version)
                    ):
                        db.add(
                            AudioOutput(
                                meeting_id=meeting.id,
                                summary_version=summary.version,
                                voice=voice_key,
                                voice_type=existing.voice_type,
                                voice_name=existing.voice_name,
                                model=speech_model,
                                path=existing.path,
                            )
                        )
                    job.audio_seconds = probe(safe_path(existing.path))[0]
                    job.output_path = existing.path
                    job.total = job.completed = 1
                if not existing:
                    if checkpoint.get("speech_model", speech_model) != speech_model:
                        raise ValueError(
                            "The narration model changed during a failed job. Start a new narration to preserve existing chunks."
                        )
                    checkpoint["speech_model"] = speech_model
                    job.checkpoint = dict(checkpoint)
                    db.commit()
                    if voice_type == "cloned":
                        if checkpoint.get("speech_chunks", 0) and not checkpoint.get("clone_chunks"):
                            raise ValueError(
                                "This older cloned narration uses different chunk boundaries. Start a new narration; its saved audio is preserved."
                            )
                        chunks = checkpoint.get("clone_chunks") or split_clone_narration(
                            spoken_text(summary.content), settings.qwen_clone_chunk_words
                        )
                        checkpoint["clone_chunks"] = chunks
                        job.checkpoint = dict(checkpoint)
                        db.commit()
                    else:
                        chunks = split_narration(spoken_text(summary.content), settings.preset_chunk_chars)
                    job.total = len(chunks)
                    speech_characters = sum(len(chunk) for chunk in chunks)
                    if voice_type == "cloned":
                        # Cloned voices: one Modal invocation, chunked inside the GPU container.
                        done = 0
                        while done < len(chunks) and (folder / f"{job.id}-speech-{done}.wav").is_file():
                            done += 1
                        job.completed = done
                        db.commit()
                        progress(db, job, meeting, "generating_speech", done, len(chunks))

                        def update_stream(task):
                            job.narration_stage = {
                                "queued": "QUEUED",
                                "preparing_voice": "PREPARING_VOICE",
                                "generating_audio": "GENERATING_AUDIO",
                                "encoding_audio": "MERGING_AUDIO",
                                "completed": "MERGING_AUDIO",
                                "failed": "FAILED",
                            }.get(task.stage, "LOADING_MODEL")
                            job.completed = min(max(done, task.stream_events), len(chunks))
                            job.audio_seconds = task.audio_seconds
                            db.commit()

                        generated = tts_queue.generate_narration(
                            profile, chunks, job.id, meeting.id, folder, done=done, progress=update_stream
                        )
                        job.completed = len(chunks)
                        output = folder / f"{job.id}-{CLONE_PIPELINE}-narration.mp3"
                        generated.replace(output)
                    else:
                        # Presets: OpenRouter Voxtral with one pooled client; Modal is never used.
                        speech_parts = [folder / f"{job.id}-speech-{i}.mp3" for i in range(len(chunks))]
                        finished = sum(part.is_file() for part in speech_parts)
                        job.narration_stage = "GENERATING_AUDIO"
                        progress(db, job, meeting, "generating_speech", finished, len(chunks))

                        def save_speech(index, data, response):
                            account(
                                db,
                                meeting,
                                "tts",
                                settings.openrouter_tts_model,
                                data,
                                response,
                                voice_type="preset",
                                input_characters=len(chunks[index]),
                            )
                            progress(
                                db,
                                job,
                                meeting,
                                "generating_speech",
                                sum(part.is_file() for part in speech_parts),
                                len(chunks),
                            )

                        asyncio.run(generate_preset(router, voice, chunks, speech_parts, save_speech))
                        job.narration_stage = "MERGING_AUDIO"
                        db.commit()
                        manifest = folder / f"{job.id}-concat.txt"
                        manifest.write_text(
                            "\n".join(f"file '{part.name}'" for part in speech_parts), encoding="utf-8"
                        )
                        output = folder / f"{job.id}-narration.mp3"
                        # Voxtral already returned MP3, so chunks join without re-encoding.
                        run(
                            [settings.ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "1"]
                            + ["-i", str(manifest), "-c", "copy", str(output)]
                        )
                        manifest.unlink(missing_ok=True)
                    probe(output)
                    job.narration_stage = "FINALIZING"
                    db.commit()
                    db.add(
                        AudioOutput(
                            meeting_id=meeting.id,
                            summary_version=summary.version,
                            voice=voice_key,
                            voice_type=voice_type,
                            voice_name=voice_name or PRESETS[voice]["name"],
                            model=speech_model,
                            path=str(output),
                        )
                    )
                    db.commit()
                    job.output_path = str(output)
                    for i in range(len(chunks)):
                        for extension in ("wav", "mp3"):
                            (folder / f"{job.id}-speech-{i}.{extension}").unlink(missing_ok=True)
            job.state = "completed"
            job.completed_at = time.time()
            if job.kind == "speech":
                job.narration_stage = "COMPLETED"
            job.stage = "completed"
            job.completed = job.total
            job.checkpoint = {}
            meeting.status = "ready"
            meeting.error = None
            db.commit()
    except Exception as exc:
        with DB() as db:
            job = db.get(Job, job_id)
            if job:
                if isinstance(exc, ProviderError) and exc.response is not None and not exc.kind:
                    context = {
                        "transcribing": ("stt", settings.openrouter_stt_model),
                        "summarizing": ("llm", settings.openrouter_summary_model),
                        "generating_speech": ("tts", speech_model),
                    }.get(job.stage)
                    if context:
                        try:
                            data = exc.response.json(parse_float=Decimal)
                        except (ValueError, TypeError):
                            data = {}
                        account(
                            db,
                            db.get(Meeting, job.meeting_id),
                            *context,
                            data if isinstance(data, dict) else {},
                            exc.response,
                            voice_type=job.payload.get("voice_type", "preset")
                            if job.kind == "speech"
                            else None,
                            input_characters=speech_characters,
                            generation_status="invalid_response",
                        )
                # Provider/network exceptions never include request payloads or credentials.
                message = (
                    str(exc)
                    if isinstance(exc, (ValueError, ProviderError))
                    else "Processing failed unexpectedly. Review the server logs and retry."
                )
                job.state, job.error = "failed", message[:500]
                job.completed_at = time.time()
                if job.kind == "speech":
                    job.narration_stage = "FAILED"
                meeting = db.get(Meeting, job.meeting_id)
                if meeting:
                    meeting.status, meeting.error = "failed", job.error
                db.commit()
    finally:
        if provider is None:
            router.close()


def run_worker(stop=None):
    stop = stop or threading.Event()
    while not stop.is_set():
        with DB() as db:
            now = time.time()
            # Renewable lease supports crash recovery without holding DB locks over HTTP.
            db.execute(
                update(Job).where(Job.state == "running", Job.heartbeat < now - 300).values(state="queued")
            )
            candidate = db.scalar(select(Job.id).where(Job.state == "queued").order_by(Job.created).limit(1))
            if candidate:
                claimed = db.execute(
                    update(Job)
                    .where(Job.id == candidate, Job.state == "queued")
                    .values(state="running", heartbeat=now)
                ).rowcount
                db.commit()
                if claimed:
                    heartbeat_stop = threading.Event()

                    def renew():
                        while not heartbeat_stop.wait(15):
                            with DB() as lease_db:
                                lease_db.execute(
                                    update(Job)
                                    .where(Job.id == candidate, Job.state == "running")
                                    .values(heartbeat=time.time())
                                )
                                lease_db.commit()

                    pulse = threading.Thread(target=renew, daemon=True)
                    pulse.start()
                    try:
                        process_job(candidate)
                    finally:
                        heartbeat_stop.set()
                        pulse.join()
            else:
                db.commit()
                stop.wait(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_worker()
