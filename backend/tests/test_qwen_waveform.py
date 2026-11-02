"""Waveform boundary tests; run with numpy and soundfile from modal_service/requirements.txt.

python -m unittest backend.tests.test_qwen_waveform
These inject model results, not synthetic claims of intelligible speech.
"""

import io
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from backend.speech_contract import BASE_MODEL, PRESET_MODEL, PREVIEW_TEXT, LocalTTSError
from modal_service.inference import QwenEngine

try:
    import numpy as np
    import soundfile as sf
except ImportError:
    np = sf = None


@unittest.skipUnless(np is not None and sf is not None, "Requires numpy and soundfile")
class WaveformBoundaryTests(unittest.TestCase):
    def engine(self, outputs, model=PRESET_MODEL):
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            return outputs[min(len(calls) - 1, len(outputs) - 1)]

        engine = QwenEngine.__new__(QwenEngine)
        engine.model_id, engine.cuda_graphs, engine.gpu_name = model, True, "fixture"
        engine.debug_audio = []
        engine.torch = SimpleNamespace(
            inference_mode=nullcontext,
            random=SimpleNamespace(fork_rng=lambda **kw: nullcontext()),
            manual_seed=lambda seed: None,
            cuda=SimpleNamespace(
                reset_peak_memory_stats=lambda: None,
                max_memory_allocated=lambda: 0,
                OutOfMemoryError=MemoryError,
            ),
        )
        engine.model = SimpleNamespace(
            model=SimpleNamespace(create_voice_clone_prompt=lambda **kw: ["validated-prompt"]),
            generate_custom_voice=generate,
            generate_voice_clone=generate,
            _voice_prompt_cache={"private": object()},
        )
        return engine, calls

    def valid(self):
        return ([np.sin(np.arange(24000 * 6) * (2 * np.pi * 220 / 24000)) * 0.2], 24000)

    def test_invalid_model_output_is_rejected_after_only_one_retry(self):
        cases = {
            "empty": ([np.array([])], 24000),
            "nonfinite": ([np.full(24000 * 6, np.nan)], 24000),
            "runaway_147_seconds": ([np.zeros(int(146.88 * 24000))], 24000),
            "clipped": ([np.ones(24000 * 6)], 24000),
            "wrong_rate": (self.valid()[0], 48000),
            "invalid_shape": ([np.zeros((2, 24000))], 24000),
        }
        for label, result in cases.items():
            with self.subTest(label=label):
                engine, calls = self.engine([result])
                with self.assertRaises(LocalTTSError):
                    engine.synthesize(PREVIEW_TEXT, speaker="Aiden", preview=True)
                self.assertEqual(len(calls), 2)
                self.assertLess(calls[0]["max_new_tokens"], 180)
                self.assertEqual(engine.model._voice_prompt_cache, {})

    def test_rejected_sample_can_recover_with_valid_wav(self):
        engine, calls = self.engine([([np.array([])], 24000), self.valid()])
        wav, metrics = engine.synthesize(PREVIEW_TEXT, speaker="Aiden", preview=True)
        audio, rate = sf.read(io.BytesIO(wav))
        self.assertEqual(rate, 24000)
        self.assertAlmostEqual(len(audio) / rate, 6)
        self.assertEqual(metrics["attempts"], 2)
        self.assertEqual(calls[0]["text"], PREVIEW_TEXT)
        self.assertLess(calls[1]["temperature"], calls[0]["temperature"])

    def test_clone_uses_reference_transcript_separately(self):
        engine, calls = self.engine([self.valid()], BASE_MODEL)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "reference.wav"
            sf.write(path, self.valid()[0][0], 24000)
            engine.synthesize(
                PREVIEW_TEXT, reference=path, ref_text="These are the actual reference words.", preview=True
            )
        self.assertEqual(calls[0]["ref_text"], "These are the actual reference words.")
        self.assertEqual(calls[0]["text"], PREVIEW_TEXT)
        self.assertFalse(calls[0]["xvec_only"])
        self.assertTrue(calls[0]["non_streaming_mode"])


if __name__ == "__main__":
    unittest.main()
