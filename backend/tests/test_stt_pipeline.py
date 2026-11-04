"""Async STT behaviour against a mock provider; no network or provider charges."""

import asyncio
import threading
import time
import httpx
import pytest
from backend.config import settings
from backend.provider import OpenRouter, ProviderError
from backend.services import stt_pipeline
from backend.services.stt_pipeline import chunk_plan, merge_parts, strip_repeat, transcribe_chunks


@pytest.fixture
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "stt_initial_concurrency", 3)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def fake_extract(source, target, offset, duration):
        target.write_bytes(b"ID3" + str(offset).encode())

    monkeypatch.setattr(stt_pipeline, "extract", fake_extract)
    return tmp_path


real_sleep = asyncio.sleep


async def _no_sleep(delay):
    await real_sleep(0)


def router_for(handler):
    return OpenRouter(
        httpx.Client(transport=httpx.MockTransport(handler)), async_transport=httpx.MockTransport(handler)
    )


def ok(index=0):
    return httpx.Response(
        200,
        json={"text": f"chunk {index}", "segments": [{"text": f"chunk {index}", "start": 1, "end": 2}]},
        headers={"x-generation-id": f"gen-{index}"},
    )


def run(router, folder, duration, stored=None):
    stored = {} if stored is None else stored
    saved, stats = [], {}

    def save(chunk, part, data, response, seconds):
        stored[chunk["index"]] = part
        saved.append(chunk["index"])

    error = None
    try:
        asyncio.run(
            transcribe_chunks(router, folder / "source.mp4", folder, duration, None, stored, save, stats)
        )
    except Exception as exc:
        error = exc
    return stored, saved, stats, error


def index_of(request):
    return int(request.content.split(b"ID3")[1].split(b"\r\n")[0]) // settings.audio_chunk_seconds


def test_concurrency_is_bounded_and_order_survives_reverse_completion(fast):
    lock, active, peak = threading.Lock(), [0], [0]

    async def handler(request):
        index = index_of(request)
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        await real_sleep(0.03 * (8 - index))
        with lock:
            active[0] -= 1
        return ok(index)

    stored, saved, stats, error = run(router_for(handler), fast, 8 * 180)
    assert error is None and peak[0] == 3 and saved != sorted(saved)
    text, _ = merge_parts(stored)
    assert text.split("\n") == [f"chunk {i}" for i in range(8)]
    assert stats["total_chunks"] == 8 and len(stats["chunks"]) == 8
    assert not list(fast.glob("chunk-*.mp3"))


def test_only_rate_limited_chunk_retries_and_is_counted(fast):
    calls = {}

    def handler(request):
        index = index_of(request)
        calls[index] = calls.get(index, 0) + 1
        if index == 1 and calls[index] < 3:
            return httpx.Response(429, headers={"retry-after": "1"})
        return ok(index)

    stored, _, stats, error = run(router_for(handler), fast, 3 * 180)
    assert error is None and calls == {0: 1, 1: 3, 2: 1}
    assert stats["retries"] == 2 and stats["backoff_seconds"] > 0


def test_ambiguous_timeout_retries_once_and_is_counted(fast):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    _, _, stats, error = run(router_for(handler), fast, 180)
    assert isinstance(error, ProviderError) and "resume transcription" in str(error)
    assert len(calls) == 2 and stats["ambiguous_timeouts"] == 2


def test_connection_failure_retries_because_nothing_was_sent(fast):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("down", request=request)
        return ok()

    _, saved, _, error = run(router_for(handler), fast, 180)
    assert error is None and saved == [0] and len(calls) == 3


def test_failure_keeps_finished_chunks_and_resume_runs_only_the_rest(fast):
    calls, broken = [], [True]
    stored = {}

    async def handler(request):
        index = index_of(request)
        calls.append(index)
        if index == 1 and broken[0]:
            # Exercise a failure after a durable success, independent of thread scheduling.
            async with asyncio.timeout(2):
                while 0 not in stored:
                    await real_sleep(.001)
        return httpx.Response(400) if index == 1 and broken[0] else ok(index)

    stored, _, _, error = run(router_for(handler), fast, 5 * 180, stored)
    assert isinstance(error, ProviderError) and 1 not in stored and 0 in stored
    before = set(stored)
    broken[0] = False
    calls.clear()
    stored, _, stats, error = run(router_for(handler), fast, 5 * 180, stored)
    assert error is None and set(calls) == set(range(5)) - before and sorted(stored) == list(range(5))
    assert stats["resumed_chunks"] == len(before)


def test_chunks_overlap_but_cover_the_recording_exactly_once():
    plan = chunk_plan(361)
    assert [c["offset"] for c in plan] == [0, 180, 360]
    assert all(c["extract_seconds"] == 180 + settings.stt_overlap_seconds for c in plan)


def test_overlap_segment_belongs_to_one_chunk_and_timestamps_stay_ordered():
    parts = {
        1: {
            "offset": 180,
            "text": "",
            "segments": [
                {"text": "boundary words here", "start": 179.5, "end": 180.2},
                {"text": "second chunk", "start": 181, "end": 183},
            ],
        },
        0: {
            "offset": 0,
            "text": "",
            "segments": [
                {"text": "first chunk", "start": 1, "end": 2},
                {"text": "boundary words here", "start": 179.5, "end": 180.2},
                {"text": "extra tail", "start": 180.6, "end": 181.4},
            ],
        },
    }
    text, segments = merge_parts(parts)
    assert text == "first chunk boundary words here\nextra tail second chunk" or text.count("boundary") == 1
    assert text.count("boundary words here") == 1
    assert [s["start"] for s in segments] == sorted(s["start"] for s in segments)


def test_text_only_boundaries_drop_repeated_words_not_legitimate_short_repeats():
    assert (
        strip_repeat("we will meet on friday morning", "on Friday morning the team left") == "the team left"
    )
    assert strip_repeat("thank you", "thank you everyone") == "thank you everyone"
    merged, _ = merge_parts(
        {
            0: {"offset": 0, "text": "the budget was approved today"},
            1: {"offset": 180, "text": "was approved today and filed"},
        }
    )
    assert merged == "the budget was approved today\nand filed"


def test_job_records_timing_and_audio_is_not_held_as_base64(fast):
    async def handler(request):
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b"base64" not in request.content
        return ok(index_of(request))

    _, _, stats, _ = run(router_for(handler), fast, 2 * 180)
    for key in ("total_seconds", "prep_seconds", "upload_seconds", "provider_seconds", "retries"):
        assert key in stats
    for key in ("latency_seconds", "bytes", "provider_seconds"):
        assert key in stats["chunks"][0]
    assert stats["total_seconds"] < time.perf_counter()
