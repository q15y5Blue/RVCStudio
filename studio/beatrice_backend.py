"""Beatrice v2 voice conversion backend. Runs under the Applio Python, like worker.py.

The network code is the official MIT-licensed Beatrice trainer 2.0.0-rc.0, vendored
unmodified as beatrice_trainer.py. Only training checkpoints (checkpoint_*.pt.gz) can be
loaded: they hold the converter, phone extractor and pitch estimator weights as plain
PyTorch state dicts. The paraphernalia_* folders made for the official VST / VCClient
use the closed engine's packed fp16 format and are rejected.

Realtime: every layer of the converter is causal (CausalConv1d, causal self-attention),
so each block is converted by re-running a sliding window over the recent input (the
"额外推理时长" context) and keeping the newest output. The network peeks about 27.5 ms
ahead (phones 2.5 ms + vocoder prenet 20 ms + post filter 5 ms), so the kept output ends
BEATRICE_LOOKAHEAD_MS before the window end. The vocoder picks a random initial phase on
every call, so consecutive blocks are joined with RVC's SOLA crossfade.
"""
from __future__ import annotations

import gzip
import math
import sys
import time
import types
from pathlib import Path

from settings import BEATRICE_LOOKAHEAD_MS, BEATRICE_SUFFIX

IN_SR, OUT_SR, IO_SR = 16000, 24000, 48000
IN_HOP, OUT_HOP, IO_HOP = 160, 240, 480      # one 10 ms frame at each rate
LOOKAHEAD = BEATRICE_LOOKAHEAD_MS // 10      # frames
SOLA_SEARCH = IO_HOP                         # 10 ms, RVC's sola_search_frame
FRAME_MULTIPLE = 4                           # the phone extractor's self-attention needs frames % 4 == 0
SINGLE_PASS_SECONDS = 60                     # longer files are converted block by block


def import_trainer():
    """Import the vendored trainer inside the Applio env, which differs from its pins."""
    if "beatrice_trainer" in sys.modules:
        return sys.modules["beatrice_trainer"]
    import torch  # noqa: F401
    import torchaudio
    try:
        import pyworld  # noqa: F401
    except ImportError:
        # Only the trainer's formant-shift augmentation uses WORLD; Applio does not ship it.
        sys.modules["pyworld"] = types.ModuleType("pyworld")
    try:
        from torch.utils.tensorboard import SummaryWriter  # noqa: F401
    except Exception:
        stub = types.ModuleType("torch.utils.tensorboard")
        stub.SummaryWriter = None
        sys.modules["torch.utils.tensorboard"] = stub
    try:
        from tqdm.auto import tqdm  # noqa: F401  progress bars: training only
    except ImportError:
        stub = types.ModuleType("tqdm.auto")
        stub.tqdm = lambda iterable=None, *a, **k: iterable
        sys.modules.setdefault("tqdm", types.ModuleType("tqdm"))
        sys.modules["tqdm.auto"] = stub
    # torchaudio >= 2.9 dropped list_audio_backends(); the trainer asserts on it at import
    # time, but inference never loads audio through torchaudio.
    original = getattr(torchaudio, "list_audio_backends", None)
    torchaudio.list_audio_backends = lambda: ["soundfile"]
    try:
        import beatrice_trainer
    finally:
        if original is None:
            del torchaudio.list_audio_backends
        else:
            torchaudio.list_audio_backends = original
    return beatrice_trainer


def warm_up():
    """Load every native library the Beatrice paths touch, before worker.py starts its
    stdin reader thread: on Windows a DLL loaded while that thread blocks on the pipe can
    deadlock the loader. librosa imports scipy/soxr lazily, so a real call is needed."""
    import io
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    import_trainer()
    tone = io.BytesIO()
    sf.write(tone, np.zeros(1600, dtype=np.float32), IN_SR, format="WAV")
    tone.seek(0)
    librosa.load(tone, sr=IO_SR, mono=True)
    torchaudio.functional.resample(torch.zeros(IO_HOP), IO_SR, IN_SR)
    try:
        from noisereduce.torchgate import TorchGate  # noqa: F401  输入降噪 (worker.InputDenoiser)
    except ImportError:
        pass


def pick_device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


class BeatriceModel:
    """A trained Beatrice v2 converter: 16 kHz speech in, 24 kHz converted speech out."""

    def __init__(self, path, device):
        import torch
        bt = import_trainer()
        path = Path(path)
        if not path.name.lower().endswith(BEATRICE_SUFFIX):
            raise ValueError("Beatrice 模型需要训练检查点 checkpoint_*.pt.gz；paraphernalia 文件夹是官方 VST 专用格式，无法使用")
        try:
            with gzip.open(path, "rb") as stream:
                checkpoint = torch.load(stream, map_location="cpu", weights_only=True)
        except (OSError, EOFError, RuntimeError) as exc:
            raise ValueError(f"无法读取 Beatrice 检查点（文件损坏或不是 .pt.gz）：{exc}") from exc
        required = ("net_g", "phone_extractor", "pitch_estimator", "h")
        if not isinstance(checkpoint, dict) or any(k not in checkpoint for k in required):
            raise ValueError("这不是 Beatrice v2 训练检查点（缺少 net_g / phone_extractor / pitch_estimator）")
        h, state = checkpoint["h"], checkpoint["net_g"]
        self.n_speakers = int(state["embed_speaker.weight"].shape[0])
        try:
            phone = bt.PhoneExtractor()
            loaded = phone.load_state_dict(checkpoint["phone_extractor"], strict=False)
            if loaded.missing_keys:
                raise RuntimeError("phone_extractor 缺少 " + ", ".join(loaded.missing_keys[:3]))
            pitch = bt.PitchEstimator()
            pitch.load_state_dict(checkpoint["pitch_estimator"])
            net = bt.ConverterNetwork(phone, pitch, self.n_speakers, h.get("pitch_bins", 448),
                                      h.get("hidden_channels", 256), h.get("vq_topk", 4),
                                      h.get("training_time_vq", "none"), h.get("phone_noise_ratio", 0.5),
                                      h.get("floor_noise_level", 1e-3))
            loaded = net.load_state_dict(state, strict=False)
            if loaded.missing_keys:
                raise RuntimeError("net_g 缺少 " + ", ".join(loaded.missing_keys[:3]))
        except RuntimeError as exc:
            raise ValueError("模型结构与内置 Beatrice 2.0.0-rc.0 推理代码不一致（可能是 beta 版训练器产出的模型）："
                             + str(exc).splitlines()[0]) from exc
        del checkpoint, state
        # The phone extractor and pitch estimator are held in a plain dict, so net.to()
        # does not move them.
        phone.to(device).eval()
        pitch.to(device).eval()
        net.to(device).eval()
        # Snap phone features to the target speaker's VQ codebook, exactly as the trainer
        # does after loading; without it the source speaker's timbre leaks through.
        net.enable_hook()
        self.net, self.device = net, device

    def check_speaker(self, speaker):
        if not 0 <= speaker < self.n_speakers:
            raise ValueError(f"该 Beatrice 模型只有 {self.n_speakers} 个说话人（编号 0～{self.n_speakers - 1}）")

    def convert(self, wav16, speaker, pitch, formant):
        """[n] 16 kHz tensor with n % (160 * 4) == 0 -> [n / 160 * 240] 24 kHz tensor."""
        import torch
        with torch.inference_mode():
            x = wav16.to(self.device, torch.float32)[None, None]
            y = self.net(x, torch.tensor([int(speaker)], device=self.device),
                         torch.tensor([float(formant)], device=self.device),
                         torch.tensor([float(pitch)], device=self.device))
        return y[0, 0]


class BeatriceStream:
    """Block-by-block conversion at 48 kHz with sliding-window context and SOLA joins."""

    def __init__(self, model, block, crossfade, extra):
        import torch
        import torchaudio
        if block % IO_HOP or crossfade % 2 or not 0 < crossfade < block:
            raise ValueError("采样长度需为 10 ms 的整数倍，淡入淡出须小于采样长度")
        self.torch, self.model = torch, model
        device = model.device
        self.block, self.crossfade = block, crossfade
        self.tail = block + crossfade + SOLA_SEARCH            # 48 kHz samples taken from each window
        frames = math.ceil((extra + self.tail) / IO_HOP) + LOOKAHEAD
        frames = -(-frames // FRAME_MULTIPLE) * FRAME_MULTIPLE
        self.frames = frames
        self.history = torch.zeros(frames * IO_HOP, device=device)
        self.sola_buffer = torch.zeros(crossfade, device=device)
        fade = torch.sin(0.5 * math.pi * torch.linspace(0.0, 1.0, crossfade, device=device)) ** 2
        self.fade_in, self.fade_out = fade, 1 - fade
        self.ones = torch.ones(1, 1, crossfade, device=device)
        self.down = torchaudio.transforms.Resample(IO_SR, IN_SR).to(device)
        self.up = torchaudio.transforms.Resample(OUT_SR, IO_SR).to(device)
        self.margin = OUT_HOP // 2                              # resampler edge guard at 24 kHz

    @property
    def delay(self):
        """Samples (48 kHz) from an input sample to the same moment in the output."""
        return LOOKAHEAD * IO_HOP + self.crossfade + SOLA_SEARCH

    def push(self, audio):
        torch = self.torch
        block = torch.as_tensor(audio, dtype=torch.float32, device=self.history.device)
        if block.numel() != self.block:
            raise RuntimeError("Beatrice 收到的音频块长度不正确")
        self.history = torch.cat([self.history[self.block:], block])

    def __call__(self, audio, speaker, pitch, formant):
        torch, F = self.torch, self.torch.nn.functional
        self.push(audio)
        y = self.model.convert(self.down(self.history), speaker, pitch, formant)
        # Newest output that has all of its look-ahead, plus a margin for the resampler.
        end = self.frames * OUT_HOP - LOOKAHEAD * OUT_HOP
        start = end - self.tail // 2
        segment = y[start - self.margin:end + self.margin]
        infer = self.up(segment)[2 * self.margin:2 * self.margin + self.tail]
        # SOLA (RVC realtime_gui): align the new block to the previous tail, then crossfade.
        head = infer[None, None, :self.crossfade + SOLA_SEARCH]
        nom = F.conv1d(head, self.sola_buffer[None, None])
        den = torch.sqrt(F.conv1d(head ** 2, self.ones) + 1e-8)
        offset = int(torch.argmax(nom[0, 0] / den[0, 0]))
        infer = infer[offset:].clone()
        infer[:self.crossfade] = infer[:self.crossfade] * self.fade_in + self.sola_buffer * self.fade_out
        self.sola_buffer = infer[self.block:self.block + self.crossfade].clone()
        return infer[:self.block].float().cpu().numpy()


class BeatriceEngine:
    """Realtime engine for worker.realtime(): same interface as the RVC engine there."""

    label = "Beatrice"

    def __init__(self, config, device=None):
        self.device = device or pick_device()
        self.model = BeatriceModel(config.beatrice_model, self.device)
        self.model.check_speaker(config.beatrice_speaker)
        self.stream = BeatriceStream(self.model, int(config.chunk_ms * 48), int(config.crossfade_ms * 48),
                                     int(config.extra_ms * 48))

    def apply(self, config):
        self.model.check_speaker(config.beatrice_speaker)

    def feed(self, audio):
        # Inactive in an A/B session: keep the context current so switching back is clean.
        self.stream.push(audio)

    def process(self, audio, config):
        import numpy as np
        start = time.perf_counter()
        result = self.stream(audio, config.beatrice_speaker, config.pitch, config.beatrice_formant)
        if self.device == "cuda":
            self.stream.torch.cuda.synchronize()
        elapsed = (time.perf_counter() - start) * 1000
        rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
        if 20 * math.log10(max(rms, 1e-9)) < config.threshold_db:
            result = np.zeros_like(result)  # 响应阈值：与 RVC 一样低于门限时输出静音
        return result, rms, elapsed


def convert_file(config, source, destination, device=None, progress=None):
    """Offline conversion of an audio file to a 48 kHz WAV (output gain applied)."""
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    device = device or pick_device()
    model = BeatriceModel(config.beatrice_model, device)
    model.check_speaker(config.beatrice_speaker)
    speaker, pitch, formant = config.beatrice_speaker, config.pitch, config.beatrice_formant
    if len(librosa.load(source, sr=IN_SR, mono=True, duration=SINGLE_PASS_SECONDS + 1)[0]) \
            <= SINGLE_PASS_SECONDS * IN_SR:
        wav, _ = librosa.load(source, sr=IN_SR, mono=True)
        if len(wav) < IN_SR // 10:
            raise ValueError("音频太短（不足 0.1 秒）")
        n = len(wav)
        pad = LOOKAHEAD * IN_HOP
        pad += (-(n + pad)) % (IN_HOP * FRAME_MULTIPLE)
        x = torch.nn.functional.pad(torch.from_numpy(wav), (0, pad))
        y = model.convert(x, speaker, pitch, formant)[: n * OUT_SR // IN_SR]
        import torchaudio
        audio = torchaudio.functional.resample(y.float().cpu(), OUT_SR, IO_SR).numpy()
    else:
        # Long file: stream it in 1 s blocks with 2 s of context, like realtime but bigger.
        wav, _ = librosa.load(source, sr=IO_SR, mono=True)
        stream = BeatriceStream(model, IO_SR, IO_SR // 20, 2 * IO_SR)
        n = len(wav)
        wav = np.pad(wav, (0, stream.delay + (-(n + stream.delay)) % stream.block))
        blocks = []
        for i in range(0, len(wav), stream.block):
            blocks.append(stream(wav[i:i + stream.block], speaker, pitch, formant))
            if progress:
                progress(f"Beatrice 正在转换… {min(100, round(100 * (i + stream.block) / len(wav)))}%")
        audio = np.concatenate(blocks)[stream.delay:stream.delay + n]
    audio = np.clip(audio * float(config.output_gain), -1, 1).astype(np.float32)
    sf.write(destination, audio, IO_SR, subtype="PCM_16")
