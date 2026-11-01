import json
import math
import threading
import time
from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .config import settings
from .media import extract, probe, run, safe_path, text_chunks
from .models import AudioOutput, ClonedVoice, DB, Job, Meeting, Summary, Transcript, Usage
from .provider import OpenRouter, ProviderError
from .schema import spoken_text
from .money import usd
from . import local_tts
from .qwen_runtime import split_narration

ACTIVE = ("queued", "running")


def latest(db, cls, meeting_id):
    return db.scalar(select(cls).where(cls.meeting_id == meeting_id).order_by(cls.version.desc()))


def enqueue(db, meeting, kind, request_key, payload=None):
    # Serialize enqueue/edit/delete for one meeting on both SQLite and PostgreSQL.
    db.execute(update(Meeting).where(Meeting.id == meeting.id).values(status=Meeting.status))
    db.refresh(meeting)
    previous = db.scalar(select(Job).where(Job.meeting_id == meeting.id, Job.request_key == request_key))
    if previous:
        return previous
    active = db.scalar(select(Job).where(Job.meeting_id == meeting.id, Job.state.in_(ACTIVE)))
    if active:
        return active
    job = Job(
        meeting_id=meeting.id,
        kind=kind,
        request_key=request_key,
        payload=payload or {},
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
):
    units = data.get("usage") or {}
    if not isinstance(units, dict):
        units = {}
    cost = usd(units.get("cost"))
    actual = cost is not None
    if cost is None:
        cost = usd(data.get("estimated_cost"))
    gen = data.get("id") or response.headers.get("x-generation-id")
    request_id = response.headers.get("x-request-id")
    gen = gen if isinstance(gen, str) and len(gen) <= 200 else None
    request_id = request_id if isinstance(request_id, str) and len(request_id) <= 200 else None
    key = "generation:" + gen if gen else "request:" + request_id if request_id else None
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
            return
    usage = Usage(
        meeting_id=meeting.id if meeting else None,
        user_id=meeting.user_id if meeting else user_id,
        operation=operation,
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
    try:
        with db.begin_nested():
            db.add(usage)
            db.flush()
        db.commit()
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
                n = math.ceil(meeting.duration / settings.audio_chunk_seconds)
                parts = list(checkpoint.get("transcription_parts", []))
                for index in range(len(parts), n):
                    offset = index * settings.audio_chunk_seconds
                    chunk = folder / f"chunk-{index}.mp3"
                    progress(db, job, meeting, "extracting", index, n)
                    extract(source, chunk, offset, settings.audio_chunk_seconds)
                    progress(db, job, meeting, "transcribing", index, n)
                    part, data, response = router.transcribe(chunk, meeting.language)
                    account(db, meeting, "stt", settings.openrouter_stt_model, data, response)
                    for segment in part["segments"]:
                        segment["start"] += offset
                        segment["end"] += offset
                        if "speaker" in segment:
                            segment["speaker"] = f"Clip {index + 1}, speaker {segment['speaker']}"
                    parts.append(part)
                    checkpoint["transcription_parts"] = parts
                    job.checkpoint = dict(checkpoint)
                    db.commit()
                    chunk.unlink(missing_ok=True)
                text = "\n".join(p["text"] for p in parts)
                if not text.strip():
                    raise ProviderError("No speech was detected in the recording")
                meeting.transcript_version += 1
                db.add(
                    Transcript(
                        meeting_id=meeting.id,
                        version=meeting.transcript_version,
                        text=text,
                        segments=[s for p in parts for s in p["segments"]],
                        language=parts[0].get("language"),
                    )
                )
                checkpoint["transcript_done"] = True
                # Text is now in the transcript table; keep only a small durable completion marker.
                checkpoint.pop("transcription_parts", None)
                job.checkpoint = dict(checkpoint)
                db.commit()
                if not meeting.retain_source:
                    source.unlink(missing_ok=True)
                    meeting.original_path = None
                    db.commit()
            if job.kind in ("pipeline", "summarize") and not checkpoint.get("summary_done"):
                transcript = latest(db, Transcript, meeting.id)
                if not transcript:
                    raise ValueError("Create a transcript before requesting a summary")
                text = (
                    "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in transcript.segments)
                    if transcript.segments
                    else transcript.text
                )
                chunks = text_chunks(text, settings.summary_chunk_chars)
                partials = list(checkpoint.get("summary_parts", []))
                for index in range(len(partials), len(chunks)):
                    progress(db, job, meeting, "summarizing", index, len(chunks))
                    result, data, response = router.summarize(
                        chunks[index], job.payload.get("detail", meeting.detail)
                    )
                    account(db, meeting, "llm", settings.openrouter_summary_model, data, response)
                    partials.append(result)
                    checkpoint["summary_parts"] = partials
                    job.checkpoint = dict(checkpoint)
                    db.commit()
                while len(partials) > 1:
                    combined = text_chunks(
                        "\n".join(json.dumps(p) for p in partials), settings.summary_chunk_chars
                    )
                    reduced = []
                    for text in combined:
                        result, data, response = router.summarize(
                            text, job.payload.get("detail", meeting.detail), True
                        )
                        account(db, meeting, "llm", settings.openrouter_summary_model, data, response)
                        reduced.append(result)
                    if len(reduced) >= len(partials):
                        raise ProviderError(
                            "Summary synthesis could not reduce the input; increase SUMMARY_CHUNK_CHARS"
                        )
                    partials = reduced
                meeting.summary_version += 1
                content = partials[0]
                if not transcript.segments:
                    for action in content["action_items"]:
                        action["timestamp"] = None
                db.add(
                    Summary(
                        meeting_id=meeting.id,
                        version=meeting.summary_version,
                        transcript_version=transcript.version,
                        content=content,
                    )
                )
                if meeting.title == "Untitled meeting":
                    meeting.title = content["title"]
                checkpoint["summary_done"] = True
                checkpoint.pop("summary_parts", None)
                job.checkpoint = dict(checkpoint)
                db.commit()
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
                reference = None
                voice_name = None
                if voice_type == "cloned":
                    speech_model = settings.qwen_tts_model
                    profile = db.get(ClonedVoice, job.payload.get("cloned_voice_id"))
                    if not profile or profile.user_id != meeting.user_id or not profile.saved:
                        raise ValueError("The selected saved voice is no longer available")
                    if not profile.consent_at or not profile.reference_text.strip():
                        raise ValueError("This voice requires a new preview for the configured model")
                    reference = safe_path(profile.reference_path)
                    voice = "cloned:" + profile.id
                    voice_name = profile.name
                    job.narration_stage = "PREPARING_VOICE"
                else:
                    voice = (
                        job.payload.get("preset_voice_id")
                        or job.payload.get("voice")
                        or settings.openrouter_tts_voice
                    )
                    if voice not in router.voices():
                        raise ValueError("Voice is not supported by the configured TTS model")
                existing = db.scalar(
                    select(AudioOutput).where(
                        AudioOutput.meeting_id == meeting.id,
                        AudioOutput.summary_version == summary.version,
                        AudioOutput.voice == voice,
                        AudioOutput.model == speech_model,
                    )
                )
                if existing:
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
                    chunks = (
                        split_narration(spoken_text(summary.content), settings.qwen_tts_chunk_chars)
                        if reference is not None
                        else text_chunks(spoken_text(summary.content), settings.tts_chunk_chars)
                    )
                    done = int(checkpoint.get("speech_chunks", 0))
                    job.total = len(chunks)
                    job.completed = done
                    db.commit()
                    for index in range(done, len(chunks)):
                        progress(db, job, meeting, "generating_speech", index, len(chunks))
                        speech_characters = len(chunks[index])
                        if reference is not None:

                            def update_stream(task):
                                job.narration_stage = {
                                    "queued": "QUEUED",
                                    "preparing_voice": "PREPARING_VOICE",
                                    "generating_audio": "GENERATING_AUDIO",
                                    "encoding_audio": "GENERATING_AUDIO",
                                    "completed": "GENERATING_AUDIO",
                                    "failed": "FAILED",
                                }.get(task.stage, "LOADING_MODEL")
                                job.audio_seconds = (
                                    sum(float(part) for part in checkpoint.get("chunk_durations", []))
                                    + task.audio_seconds
                                )
                                db.commit()

                            audio = local_tts.generate(
                                profile,
                                chunks[index],
                                f"meeting:{job.id}:{index}",
                                meeting.id,
                                progress=update_stream,
                            )
                        else:
                            audio, data, response = router.speech(chunks[index], voice)
                            account(
                                db,
                                meeting,
                                "tts",
                                speech_model,
                                data,
                                response,
                                voice_type=voice_type,
                                input_characters=len(chunks[index]),
                            )
                        part = folder / f"{job.id}-speech-{index}.mp3"
                        part.write_bytes(audio)
                        try:
                            part_seconds, _ = probe(part)
                        except ValueError as exc:
                            if reference is not None:
                                raise ValueError(
                                    "The local narration chunk could not be decoded; retry the failed stage."
                                ) from exc
                            raise ProviderError(
                                "OpenRouter audio could not be decoded; retry the failed narration.", response
                            ) from exc
                        checkpoint["speech_chunks"] = index + 1
                        checkpoint["chunk_durations"] = list(checkpoint.get("chunk_durations", [])) + [
                            part_seconds
                        ]
                        job.completed = index + 1
                        job.checkpoint = dict(checkpoint)
                        db.commit()
                    job.narration_stage = "MERGING_AUDIO"
                    db.commit()
                    manifest = folder / f"{job.id}-concat.txt"
                    manifest.write_text(
                        "\n".join(f"file '{job.id}-speech-{i}.mp3'" for i in range(len(chunks))),
                        encoding="utf-8",
                    )
                    output = folder / f"{job.id}-narration.mp3"
                    run(
                        [
                            settings.ffmpeg,
                            "-nostdin",
                            "-v",
                            "error",
                            "-y",
                            "-f",
                            "concat",
                            "-safe",
                            "1",
                            "-i",
                            str(manifest),
                            "-c",
                            "copy",
                            str(output),
                        ]
                    )
                    probe(output)
                    job.narration_stage = "FINALIZING"
                    db.commit()
                    db.add(
                        AudioOutput(
                            meeting_id=meeting.id,
                            summary_version=summary.version,
                            voice=voice,
                            voice_type=voice_type,
                            voice_name=voice_name,
                            model=speech_model,
                            path=str(output),
                        )
                    )
                    db.commit()
                    job.output_path = str(output)
                    for i in range(len(chunks)):
                        (folder / f"{job.id}-speech-{i}.mp3").unlink(missing_ok=True)
                    manifest.unlink(missing_ok=True)
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
                if isinstance(exc, ProviderError) and exc.response is not None:
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
    run_worker()
