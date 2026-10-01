"""Checks the formant-shift algorithm in studio/formant.py without torch or a GPU.

The torch patch can only run inside the Applio engine, so here the same frame/sample
planning helpers drive a numpy source-filter "vocoder" stub: harmonics of F0 shaped
by a fixed resonance at 1 kHz (a formant). After the shift the fundamental must stay
at the requested pitch while the resonance moves by 2**(formant/12) — the behaviour
of RVC's 性别因子/声线粗细.
"""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio"))
import formant
from settings import Settings

HOP = 400            # 40 kHz model: 400 samples per 10 ms frame
SR = HOP * 100
RESONANCE = 1000.0


def stub_decoder(f0_frames):
    """Render harmonics of a per-frame F0 track through a fixed 1 kHz resonance."""
    f0 = np.repeat(f0_frames, HOP)
    phase = 2 * np.pi * np.cumsum(f0) / SR
    out = np.zeros_like(phase)
    for h in range(1, 40):
        freq = h * f0[0]
        out += np.exp(-((freq - RESONANCE) / 250.0) ** 2) * np.sin(h * phase)
    return out


def fft_resample(x, n):
    spec = np.fft.rfft(x)
    out = np.zeros(n // 2 + 1, dtype=complex)
    m = min(len(spec), len(out))
    out[:m] = spec[:m]
    return np.fft.irfft(out, n) * n / len(x)


def shifted_render(target_hz, frames, semitones):
    """Python mirror of formant.install()'s infer() using the shared helpers."""
    k = formant.factor(semitones)
    f0_key = target_hz * 2 ** (-semitones / 12)  # Settings.effective_pitch compensation
    stretched = formant.stretched_frames(frames, k)
    audio = stub_decoder(np.full(stretched, f0_key))
    hop, upp_res, keep = formant.plan(frames, stretched, audio.shape[-1], k)
    assert hop == HOP
    return fft_resample(audio[:keep], frames * hop)


def peak_and_fundamental(x):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    band = (freqs > 60) & (freqs < 3000)
    peaks = [i for i in np.where(band)[0][1:-1] if spec[i] > spec[i - 1] and spec[i] > spec[i + 1] and spec[i] > spec[band].max() * .02]
    # harmonic spacing = F0 (the fundamental itself sits far below the resonance)
    fundamental = float(np.median(np.diff(freqs[peaks])))
    # spectral envelope centre of mass over the harmonic peaks
    centre = sum(freqs[i] * spec[i] for i in peaks) / sum(spec[i] for i in peaks)
    return fundamental, centre


class FormantTests(unittest.TestCase):
    def test_pitch_kept_and_formant_moves(self):
        base_f0, base_centre = peak_and_fundamental(shifted_render(220.0, 200, 0.0))
        for semitones in (-2.0, -1.0, 1.0, 2.0):
            with self.subTest(semitones=semitones):
                f0, centre = peak_and_fundamental(shifted_render(220.0, 200, semitones))
                self.assertAlmostEqual(f0, 220.0, delta=2.5)
                self.assertAlmostEqual(centre / base_centre, formant.factor(semitones), delta=0.02)
        self.assertAlmostEqual(base_f0, 220.0, delta=2.5)

    def test_output_length_is_exact_for_realtime_blocks(self):
        for frames in (1, 23, 37, 3000):
            for semitones in (-2.0, -0.05, 0.05, 2.0):
                k = formant.factor(semitones)
                stretched = formant.stretched_frames(frames, k)
                hop, upp_res, keep = formant.plan(frames, stretched, stretched * HOP, k)
                self.assertLessEqual(keep, stretched * HOP)
                self.assertEqual(keep, frames * upp_res)
                # torchaudio Resample(upp_res -> hop) yields ceil(keep * hop / upp_res) samples
                self.assertEqual(-(-keep * hop // upp_res), frames * hop)

    def test_settings_compensate_f0_like_rvc(self):
        s = Settings(pitch=10, formant=1.5)
        self.assertEqual(s.inference_args()["f0_up_key"], 8.5)
        self.assertEqual(Settings(pitch=10).inference_args()["f0_up_key"], 10)


if __name__ == "__main__":
    unittest.main()
