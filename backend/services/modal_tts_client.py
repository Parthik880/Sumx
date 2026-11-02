"""Private Modal SDK client. All TTS inference happens remotely; there is no local fallback."""

import os
import time
from ..config import settings
from ..speech_contract import LocalTTSError
from modal_service.config import MODAL_APP_NAME, MODAL_GPU, MODAL_PROGRESS_DICT

TRANSIENT_ATTEMPTS = 3


class ModalSpeechClient:
    # Replaced by the GPU actually allocated once a request reports it.
    gpu_name = "Modal " + (MODAL_GPU if isinstance(MODAL_GPU, str) else MODAL_GPU[0])
    vram_mb = 0

    def __init__(self):
        # The SDK reads these from the process environment; .env values are not exported.
        for name, value in (
            ("MODAL_TOKEN_ID", settings.modal_token_id),
            ("MODAL_TOKEN_SECRET", settings.modal_token_secret),
        ):
            if value:
                os.environ.setdefault(name, value)
        import modal

        self.modal = modal
        environment = settings.modal_environment or None
        self.workers = {}
        # Preset voices use OpenRouter and never reach Modal.
        worker = modal.Cls.from_name(MODAL_APP_NAME, "CloneTTS", environment_name=environment)
        worker.hydrate()
        self.workers["cloned"] = worker

    def _transient(self):
        errors = self.modal.exception
        return (errors.ConnectionError, errors.InternalFailure, errors.ServiceError, ConnectionError)

    def _spawn(self, payload, method):
        for attempt in range(TRANSIENT_ATTEMPTS):
            try:
                return getattr(self.workers[payload["voice_type"]](), method).spawn(payload)
            except self._transient() as exc:
                if attempt == TRANSIENT_ATTEMPTS - 1:
                    raise LocalTTSError(
                        "Modal could not be reached. Retry the failed stage shortly."
                    ) from exc
                time.sleep(2**attempt)

    def _wait(self, call, poll=None):
        errors = self.modal.exception
        deadline = time.monotonic() + settings.qwen_tts_timeout
        failures = 0
        while time.monotonic() < deadline:
            try:
                return call.get(timeout=2 if poll else 20)
            except errors.FunctionTimeoutError as exc:
                raise LocalTTSError(
                    "The Modal GPU worker exceeded its time limit. Retry this stage."
                ) from exc
            except (TimeoutError, errors.TimeoutError):
                if poll:
                    poll()
            except self._transient() as exc:
                failures += 1
                if failures >= TRANSIENT_ATTEMPTS:
                    raise LocalTTSError("The Modal connection failed. Retry the failed stage.") from exc
                time.sleep(2**failures)
        # Stop paying for a request nobody is waiting for.
        call.cancel()
        raise LocalTTSError("Modal generation is taking too long. Retry the failed stage.")

    def _collect(self, payload, method, call_id, on_started, poll=None):
        if call_id:
            # A restarted queue worker collects its running call rather than paying for it twice.
            try:
                return self._wait(self.modal.FunctionCall.from_id(call_id), poll)
            except (self.modal.exception.NotFoundError, self.modal.exception.OutputExpiredError):
                pass
        call = self._spawn(payload, method)
        if on_started:
            on_started(call.object_id)
        return self._wait(call, poll)

    def synthesize(self, payload, *, call_id=None, on_started=None, progress=None):
        if progress:
            progress("generating_audio")
        return self._collect(payload, "synthesize", call_id, on_started)

    def narrate(self, payload, *, call_id=None, on_started=None, progress=None):
        """One Modal invocation for a whole narration; progress holds counts only."""
        state = self.modal.Dict.from_name(MODAL_PROGRESS_DICT, create_if_missing=True)
        key = payload["request_id"]

        def poll():
            try:
                update = state.get(key)
            except Exception:
                return
            if update and progress:
                progress(update["stage"], update)

        try:
            return self._collect(payload, "narrate", call_id, on_started, poll)
        finally:
            try:
                state.pop(key)
            except Exception:
                pass
