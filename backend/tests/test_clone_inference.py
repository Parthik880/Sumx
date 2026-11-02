import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from backend.speech_contract import PREVIEW_TEXT
from modal_service.inference import synthesize_clone, CloneGenerationError

try:
    import numpy as np
    import soundfile as sf
except ImportError:
    np = sf = None


@unittest.skipUnless(
    np is not None and sf is not None, "Requires numpy and soundfile from modal_service/requirements.txt"
)
class CloneInferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reference = Path(self.temp.name) / "reference.wav"
        self.tone = np.sin(np.arange(24000 * 6) * 2 * np.pi * 220 / 24000) * 0.2
        sf.write(self.reference, self.tone, 24000)
        self.prompts, self.calls, self.seeds = [], [], []

        def prompt(**kwargs):
            self.prompts.append(kwargs)
            return [object()]

        self.outputs = [self.tone]

        def generate(**kwargs):
            self.calls.append(kwargs)
            return [self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]], 24000

        self.engine = SimpleNamespace(
            cuda_graphs=True,
            gpu_name="fixture",
            torch=SimpleNamespace(
                inference_mode=nullcontext,
                manual_seed=self.seeds.append,
                random=SimpleNamespace(fork_rng=lambda **kw: nullcontext()),
                cuda=SimpleNamespace(
                    reset_peak_memory_stats=lambda: None,
                    max_memory_allocated=lambda: 0,
                    OutOfMemoryError=MemoryError,
                    empty_cache=lambda: None,
                ),
            ),
            model=SimpleNamespace(
                model=SimpleNamespace(create_voice_clone_prompt=prompt),
                generate_voice_clone=generate,
                _voice_prompt_cache={},
            ),
        )
        self.options = {"seed": 1234}

    def synthesize(self, **context):
        return synthesize_clone(
            self.engine,
            PREVIEW_TEXT,
            "en",
            self.reference,
            "Actual reference transcript",
            True,
            {"user_id": "owner", "profile_id": "voice", "job_id": "job", **context},
            self.options,
        )

    def test_precomputed_prompt_is_reused_and_isolated_by_owner(self):
        self.synthesize(chunk_index=0)
        self.synthesize(chunk_index=1)
        self.assertEqual(len(self.prompts), 1)
        self.assertIs(self.calls[0]["voice_clone_prompt"], self.calls[1]["voice_clone_prompt"])
        self.assertEqual(len(self.prompts[0]["ref_audio"][0]), len(self.tone) + 12000)
        self.assertFalse(np.any(self.prompts[0]["ref_audio"][0][-12000:]))
        self.synthesize(user_id="other-owner")
        self.assertEqual(len(self.prompts), 2)

    def test_long_internal_silence_retries_only_bad_chunk_with_new_seeds(self):
        bad = self.tone.copy()
        bad[24000 : 24000 * 5] = 0
        self.outputs = [bad, self.tone]
        _, metrics = self.synthesize(chunk_index=4)
        self.assertEqual(metrics["attempts"], 2)
        self.assertEqual(self.seeds, [1234, 105963])
        self.assertEqual(metrics["attempt_diagnostics"][0]["max_internal_silence_seconds"], 4)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.calls[0]["temperature"], self.calls[1]["temperature"])

    def test_retry_exhaustion_and_later_recovery(self):
        self.outputs = [np.zeros(24000 * 6)]
        with self.assertRaises(CloneGenerationError) as failed:
            self.synthesize(chunk_index=2)
        self.assertEqual(len(self.calls), 3)
        self.assertIn("chunk 3", str(failed.exception))
        self.assertEqual(failed.exception.metrics["attempts"], 3)
        self.outputs = [self.tone]
        _, metrics = self.synthesize(chunk_index=2)
        self.assertEqual(metrics["status"], "validated")

    def test_natural_short_pause_is_preserved(self):
        audio = self.tone.copy()
        audio[24000:48000] = 0
        self.outputs = [audio]
        _, metrics = self.synthesize()
        self.assertEqual(metrics["attempts"], 1)
        self.assertEqual(metrics["max_internal_silence_seconds"], 1)

    def test_oom_clears_conditioning_and_requires_reload(self):
        def oom(**kwargs):
            raise MemoryError()

        self.engine.model.generate_voice_clone = oom
        with self.assertRaisesRegex(ValueError, "ran out of memory"):
            self.synthesize()
        self.assertFalse(self.engine.cuda_graphs)
        self.assertIsNone(self.engine._clone_prompt)

    def test_reference_precomputation_oom_requires_reload(self):
        def oom(**kwargs):
            raise MemoryError()

        self.engine.model.model.create_voice_clone_prompt = oom
        with self.assertRaisesRegex(ValueError, "preparing the reference"):
            self.synthesize()
        self.assertFalse(self.engine.cuda_graphs)

    def test_invalid_waveforms_and_token_cap_are_rejected(self):
        for audio in [np.array([]), np.full(24000 * 6, np.nan), np.ones(24000 * 6)]:
            with self.subTest(shape=audio.shape):
                self.outputs = [audio]
                before = len(self.calls)
                with self.assertRaises(CloneGenerationError):
                    self.synthesize()
                self.assertEqual(len(self.calls) - before, 3)
        self.outputs = [self.tone]
        self.options["max_new_tokens"] = 48
        with self.assertRaises(CloneGenerationError) as failed:
            self.synthesize()
        self.assertTrue(failed.exception.metrics["attempt_diagnostics"][0]["hit_token_limit"])

    def test_text_feeding_mode_is_clone_specific_and_configurable(self):
        self.options["non_streaming_mode"] = False
        self.synthesize()
        self.assertFalse(self.calls[0]["non_streaming_mode"])
        self.assertEqual(self.calls[0]["temperature"], 0.65)


if __name__ == "__main__":
    unittest.main()
