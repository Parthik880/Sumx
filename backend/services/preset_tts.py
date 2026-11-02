"""Voxtral preset narration through OpenRouter with one pooled client and resumable parts."""

import asyncio
import logging
import time
from ..config import settings

log = logging.getLogger("sumx.preset")


async def generate_preset(router, voice, chunks, parts, save):
    """Write each missing chunk to its part file. Finished parts survive a failed attempt."""
    started = time.perf_counter()
    gate = asyncio.Semaphore(settings.preset_tts_concurrency)
    failures = []
    await asyncio.to_thread(router.speech_rate)

    async def one(client, index):
        async with gate:
            if failures:
                return
            try:
                audio, data, response = await router.speech_async(chunks[index], voice, client)
                temporary = parts[index].with_suffix(".pending")
                temporary.write_bytes(audio)
                temporary.replace(parts[index])
                save(index, data, response)
            except Exception as exc:
                failures.append((index, exc))

    missing = [index for index in range(len(chunks)) if not parts[index].is_file()]
    async with router.async_client() as client:
        await asyncio.gather(*(one(client, index) for index in missing))
    log.info(
        "Preset narration: %s",
        {"chunks": len(chunks), "generated": len(missing), "seconds": time.perf_counter() - started},
    )
    if failures:
        raise min(failures, key=lambda item: item[0])[1]
