"""Formant shift ("性别因子 / 声线粗细") for the pinned Applio 3.6.5 engine.

Applio's realtime pipeline keeps a `formant_length = return_length * 1.0` stub but
never implements the shift, and its offline formant option is a different STFT
pre-processing. This module adds the original RVC realtime algorithm
(infer/rtrvc.py + SynthesizerTrn.infer(return_length2)) at runtime, without touching
the hash-pinned engine files:

1. F0 is extracted with key ``pitch - formant`` (done by Settings.effective_pitch).
2. The decoder latent and F0 track are stretched in time by ``k = 2**(formant/12)``
   and decoded, so the vocoder renders natural formants over k-times more frames.
3. The waveform is resampled back to the original length (rate upp_res -> hop),
   which multiplies every frequency by ~k: the pitch returns to the requested key
   while the formants (spectral envelope) end up shifted by k.

The patch is installed on ``Synthesizer.infer`` so realtime and file conversion use
the same algorithm. With formant == 0 the original method runs unchanged.
"""
from __future__ import annotations

import math

STATE = {"semitones": 0.0}
_RESAMPLERS = {}


def set_semitones(value):
    STATE["semitones"] = float(value)


def factor(semitones=None):
    return 2.0 ** ((STATE["semitones"] if semitones is None else semitones) / 12.0)


def stretched_frames(frames, k):
    """Frames to decode so that frames * floor(k * hop) samples are always available."""
    return max(1, int(math.ceil(frames * k)))


def plan(frames, stretched, total_samples, k):
    """Return (hop, upp_res, keep): samples per frame, per stretched frame, samples to keep."""
    hop = total_samples // stretched
    upp_res = max(1, int(math.floor(k * hop)))
    keep = frames * upp_res
    if keep > total_samples:
        raise RuntimeError("formant: decoder returned fewer samples than planned")
    return hop, upp_res, keep


def _resampler(upp_res, hop, device):
    import torchaudio.transforms as tat
    key = (upp_res, hop, str(device))
    if key not in _RESAMPLERS:
        _RESAMPLERS[key] = tat.Resample(orig_freq=upp_res, new_freq=hop).to(device)
    return _RESAMPLERS[key]


def install():
    import torch
    import torch.nn.functional as F
    from rvc.lib.algorithm.synthesizers import Synthesizer

    if getattr(Synthesizer.infer, "_rvc_studio_formant", False):
        return
    original = Synthesizer.infer

    @torch.no_grad()
    def infer(self, phone, phone_lengths, pitch=None, nsff0=None, sid=None, rate=None):
        k = factor()
        if abs(k - 1.0) < 1e-4:
            return original(self, phone, phone_lengths, pitch, nsff0, sid, rate)
        # Mirrors Applio's Synthesizer.infer up to the decoder call.
        g = self.emb_g(sid).unsqueeze(-1)
        m_p, logs_p, x_mask = self.enc_p(phone, pitch, phone_lengths)
        z_p = (m_p + torch.exp(logs_p) * torch.randn_like(m_p) * 0.66666) * x_mask
        if rate is not None:
            head = int(z_p.shape[2] * (1.0 - rate.item()))
            z_p, x_mask = z_p[:, :, head:], x_mask[:, :, head:]
            if self.use_f0 and nsff0 is not None:
                nsff0 = nsff0[:, head:]
        z = self.flow(z_p, x_mask, g=g, reverse=True)
        frames = z.shape[2]
        stretched = stretched_frames(frames, k)
        zs = F.interpolate((z * x_mask).float(), size=stretched, mode="linear", align_corners=False).to(z.dtype)
        if self.use_f0:
            # nearest keeps unvoiced frames at exactly 0 Hz
            f0 = F.interpolate(nsff0[:, None, :].float(), size=stretched, mode="nearest")[:, 0, :].to(nsff0.dtype)
            o = self.dec(zs, f0, g=g)
        else:
            o = self.dec(zs, g=g)
        hop, upp_res, keep = plan(frames, stretched, o.shape[-1], k)
        audio = o[..., :keep].float()
        if upp_res != hop:
            audio = _resampler(upp_res, hop, audio.device)(audio)
        audio = audio[..., : frames * hop].to(o.dtype)
        return audio, x_mask, (z, z_p, m_p, logs_p)

    infer._rvc_studio_formant = True
    Synthesizer.infer = infer
