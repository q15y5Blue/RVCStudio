"""Beatrice v2 backend checks. Needs torch + torchaudio (skipped otherwise).

The streaming test swaps the network for a plain 16 -> 24 kHz resampler, so the stream
must reproduce its input delayed by `stream.delay`: this pins down the window maths, the
look-ahead cut, the resampling margins and the SOLA joins without any weights.

Set BEATRICE_TEST_CHECKPOINT to a real checkpoint_*.pt.gz (for example the trainer's
assets/pretrained/151_checkpoint_libritts_r_200_02750000.pt.gz combined with its phone
extractor / pitch estimator, see make_test_checkpoint below) and BEATRICE_TEST_WAV to a
speech file to also run the real network: file conversion and realtime streaming.
"""
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio"))
from settings import Settings

try:
    import torch
    import torchaudio
    import beatrice_backend as bb
except ImportError:  # the GUI-only test environment has no torch
    torch = None


class IdentityModel:
    """Stands in for BeatriceModel: 'converts' by resampling 16 kHz to 24 kHz."""

    device = "cpu"

    def convert(self, wav16, speaker, pitch, formant):
        assert wav16.numel() % (bb.IN_HOP * bb.FRAME_MULTIPLE) == 0
        return torchaudio.functional.resample(wav16, bb.IN_SR, bb.OUT_SR)


@unittest.skipIf(torch is None, "torch not installed")
class StreamTests(unittest.TestCase):
    def run_stream(self, signal, chunk_ms=160, crossfade_ms=60, extra_ms=250):
        stream = bb.BeatriceStream(IdentityModel(), chunk_ms * 48, crossfade_ms * 48, extra_ms * 48)
        self.assertEqual(stream.frames % bb.FRAME_MULTIPLE, 0)
        out = np.concatenate([stream(signal[i:i + stream.block], 0, 0, 0)
                              for i in range(0, len(signal) - stream.block + 1, stream.block)])
        return stream, out

    def test_output_is_input_delayed_by_reported_latency(self):
        t = np.arange(48000 * 3) / 48000
        # A gliding tone: any misplaced window or bad join shows up as a mismatch.
        signal = (0.5 * np.sin(2 * np.pi * (220 * t + 40 * t ** 2))).astype(np.float32)
        for chunk, fade, extra in ((160, 60, 250), (100, 30, 1000), (40, 10, 50)):
            stream, out = self.run_stream(signal, chunk, fade, extra)
            d = stream.delay
            skip = d + stream.block * 2
            ref, got = signal[skip - d:len(out) - d], out[skip:]
            # SOLA may shift by up to 10 ms; a pure tone is matched at the best lag.
            best = max(np.corrcoef(ref[:len(ref) - 480], got[k:k + len(ref) - 480])[0, 1] for k in range(0, 481, 4))
            self.assertGreater(best, 0.98, (chunk, fade, extra))
            self.assertTrue(np.isfinite(out).all())

    def test_block_length_is_enforced(self):
        stream = bb.BeatriceStream(IdentityModel(), 7680, 2880, 12000)
        with self.assertRaises(RuntimeError):
            stream(np.zeros(100, dtype=np.float32), 0, 0, 0)


def make_test_checkpoint(pretrained_gz, phone_pt, pitch_pt, destination):
    """Bundle the trainer's pretrained converter with its frozen front-ends, the way a
    finished training run saves checkpoint_latest.pt.gz."""
    import gzip
    with gzip.open(pretrained_gz, "rb") as f:
        ckpt = torch.load(f, map_location="cpu", weights_only=True)
    out = {"net_g": ckpt["net_g"], "h": ckpt.get("h", {}),
           "phone_extractor": torch.load(phone_pt, map_location="cpu", weights_only=True)["phone_extractor"],
           "pitch_estimator": torch.load(pitch_pt, map_location="cpu", weights_only=True)["pitch_estimator"]}
    with gzip.open(destination, "wb") as f:
        torch.save(out, f)


@unittest.skipIf(torch is None or not os.environ.get("BEATRICE_TEST_CHECKPOINT"), "no real checkpoint given")
class RealModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ckpt = os.environ["BEATRICE_TEST_CHECKPOINT"]
        cls.wav = os.environ["BEATRICE_TEST_WAV"]
        cls.model = bb.BeatriceModel(cls.ckpt, "cpu")

    def test_speaker_range(self):
        self.assertGreater(self.model.n_speakers, 0)
        with self.assertRaises(ValueError):
            self.model.check_speaker(self.model.n_speakers)

    def test_file_conversion(self):
        import soundfile as sf
        import librosa
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out.wav"
            config = Settings(backend="beatrice", beatrice_model=self.ckpt, pitch=8, formant=0.5)
            bb.convert_file(config, self.wav, str(out), device="cpu")
            audio, sr = sf.read(out, dtype="float32")
            source = librosa.load(self.wav, sr=48000, mono=True)[0]
            self.assertEqual(sr, 48000)
            self.assertLessEqual(abs(len(audio) - len(source)), 480)
            self.assertTrue(np.isfinite(audio).all())
            self.assertGreater(np.sqrt(np.mean(audio ** 2)), 1e-3)

    def test_pitch_shift_raises_f0(self):
        import librosa
        wav = torch.from_numpy(librosa.load(self.wav, sr=16000, mono=True)[0])
        n = wav.numel() - wav.numel() % 640
        f0s = []
        for semitones in (0, 12):
            y = self.model.convert(wav[:n], 0, semitones, 0).numpy()
            f0 = librosa.yin(y, fmin=60, fmax=1000, sr=24000)
            f0s.append(np.median(f0))
        self.assertAlmostEqual(math.log2(f0s[1] / f0s[0]), 1.0, delta=0.15)

    def test_streaming_matches_offline_spectrum(self):
        """Realtime blocks (with SOLA joins) must sound like the one-shot conversion."""
        import librosa
        wav48 = librosa.load(self.wav, sr=48000, mono=True)[0]
        stream = bb.BeatriceStream(self.model, 160 * 48, 60 * 48, 1000 * 48)
        n = len(wav48) - len(wav48) % stream.block
        padded = np.pad(wav48[:n], (0, stream.delay + stream.block))
        out = np.concatenate([stream(padded[i:i + stream.block], 0, 0, 0)
                              for i in range(0, len(padded) - stream.block + 1, stream.block)])
        streamed = out[stream.delay:stream.delay + n]
        x16 = torchaudio.functional.resample(torch.from_numpy(wav48[:n]), 48000, 16000)
        pad = (-(x16.numel() + 480)) % 640 + 480
        y = self.model.convert(torch.nn.functional.pad(x16, (0, pad)), 0, 0, 0)
        offline = torchaudio.functional.resample(y, 24000, 48000).numpy()[:n]

        def logmel(a):
            m = librosa.feature.melspectrogram(y=a, sr=48000, n_fft=2048, hop_length=480, n_mels=80)
            return np.log(m + 1e-5)
        a, b = logmel(streamed), logmel(offline)
        voiced = a.mean(0) > np.percentile(a.mean(0), 40)
        corr = np.corrcoef(a[:, voiced].ravel(), b[:, voiced].ravel())[0, 1]
        self.assertGreater(corr, 0.9)


if __name__ == "__main__":
    unittest.main()
