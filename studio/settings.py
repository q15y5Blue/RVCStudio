"""Validated settings shared by the desktop UI and the engine process."""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_VERSION = "0.3.0-offline"
F0_METHODS = ("rmvpe", "fcpe", "crepe-tiny", "crepe")


def data_dir() -> Path:
    root = Path(os.environ.get("RVC_STUDIO_DATA", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RVCStudio")))
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class Param:
    """One numeric parameter: the single source of truth for UI sliders and validation."""
    name: str
    label: str
    low: float
    high: float
    step: float
    live: bool          # True: can be changed while realtime conversion is running
    hint: str
    group: str
    integer: bool = False


# 对照：原版 RVC 实时 GUI（realtime_gui.py）/ Applio 3.6.5 引擎参数名
PARAMS = (
    Param("pitch", "音调（半音）", -24, 24, 1, True,
          "RVC“音调设置”/ f0_up_key。男转女常用 +8～+12，女转男 -8～-12", "core", True),
    Param("formant", "共振峰 / 性别因子", -2, 2, 0.05, True,
          "RVC“性别因子/声线粗细”。正值声线更细（更女性化），负值更粗；不改变音调", "core"),
    Param("index_rate", "检索特征占比", 0, 1, 0.01, True,
          "RVC“Index Rate”。越高越贴近模型音色，过高可能含糊；没有 .index 时自动为 0", "timbre"),
    Param("protect", "清辅音保护", 0, 0.5, 0.01, True,
          "越小越保护清辅音和呼吸声（减少撕裂电音），0.5 = 关闭保护；只在使用索引时起作用", "timbre"),
    Param("volume_envelope", "响度因子", 0, 1, 0.01, True,
          "RVC“响度因子”/ 音量包络。0 = 完全跟随麦克风的响度起伏，1 = 使用模型自身响度", "timbre"),
    Param("chunk_ms", "采样长度（ms）", 40, 1500, 10, False,
          "RVC“采样长度”/ 分块。越小延迟越低，但显卡来不及时会卡顿、丢块", "perf", True),
    Param("crossfade_ms", "淡入淡出长度（ms）", 10, 150, 5, False,
          "RVC“淡入淡出长度”。块与块之间的平滑拼接，必须小于采样长度", "perf", True),
    Param("extra_ms", "额外推理时长（ms）", 50, 5000, 50, False,
          "RVC“额外推理时长”。给模型更多上下文，音质更稳；只增加显卡负担，不增加延迟", "perf", True),
    Param("threshold_db", "响应阈值（dB）", -90, 0, 1, True,
          "RVC“响应阈值”/ 噪声门。输入音量低于此值时输出静音；-90 相当于关闭", "noise", True),
    Param("denoise_strength", "输出降噪强度", 0, 1, 0.05, True,
          "开启“输出降噪”时的降噪力度", "noise"),
    Param("output_gain", "输出增益（倍）", 0, 8, 0.05, True,
          "1.0 为原始；1.5≈+3.5dB，2.0≈+6dB；过高会削顶爆音", "noise"),
    Param("autotune_strength", "Autotune 强度", 0, 1, 0.05, True,
          "开启 Autotune 时把音高吸附到半音的力度（唱歌用，说话建议关闭）", "advanced"),
    Param("proposed_pitch_threshold", "自动音高目标（Hz）", 50, 1200, 5, True,
          "开启“自动音高”时，把你的平均音高自动移到此频率附近（女声约 220～260）", "advanced", True),
)
PARAM_BY_NAME = {p.name: p for p in PARAMS}

# 开关：(name, label, live, hint)
TOGGLES = (
    ("input_denoise", "输入降噪", True, "RVC“输入降噪”：先对麦克风去底噪再变声（约增加 40 ms 延迟）"),
    ("output_denoise", "输出降噪", True, "RVC“输出降噪”/ Applio Clean Audio：对变声结果去底噪"),
    ("f0_autotune", "Autotune", True, "音高吸附到半音"),
    ("proposed_pitch", "自动音高", True, "按目标频率自动计算升降调，再叠加上面的“音调”"),
    ("phase_vocoder", "相位声码器拼接", True, "Applio 的相位对齐交叉淡化，可减少拼接处的相位抵消"),
    ("wasapi_exclusive", "独占 WASAPI 设备", False, "RVC“独占 WASAPI 设备”：延迟更低，但其他软件无法同时使用该设备"),
)
LIVE_KEYS = tuple([p.name for p in PARAMS if p.live] + [t[0] for t in TOGGLES if t[2]])


@dataclass
class Settings:
    runtime: str = ""
    model: str = ""
    index: str = ""
    # 出厂默认＝男变女推荐预设（依据 B 站高播放量实时变声教程实测，详见 README）
    pitch: int = 10
    formant: float = 0.5
    index_rate: float = 0.0
    protect: float = 0.33
    volume_envelope: float = 0.55
    output_gain: float = 1.0
    chunk_ms: int = 160
    crossfade_ms: int = 60
    extra_ms: int = 250
    threshold_db: int = -60
    f0_method: str = "rmvpe"
    input_denoise: bool = False
    output_denoise: bool = False
    denoise_strength: float = 0.5
    f0_autotune: bool = False
    autotune_strength: float = 1.0
    proposed_pitch: bool = False
    proposed_pitch_threshold: int = 240
    phase_vocoder: bool = False
    wasapi_exclusive: bool = False
    input_device: str = ""
    output_device: str = ""
    monitor_device: str = ""
    monitor: bool = False

    def validate(self, require_model=False):
        for p in PARAMS:
            value = getattr(self, p.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not p.low <= value <= p.high:
                raise ValueError(f"{p.label} 必须在 {p.low:g} 到 {p.high:g} 之间")
            if p.integer and int(value) != value:
                raise ValueError(f"{p.label} 必须为整数")
        for name, label, *_ in TOGGLES:
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{label} 只能是开或关")
        if self.chunk_ms % 10:
            raise ValueError("采样长度必须是 10 ms 的整数倍（引擎按 10 ms 帧对齐音高缓存）")
        if self.crossfade_ms >= self.chunk_ms:
            raise ValueError("淡入淡出长度必须小于采样长度")
        if self.f0_method not in F0_METHODS:
            raise ValueError("音高算法仅支持 " + " / ".join(F0_METHODS))
        if require_model:
            if not self.model or Path(self.model).suffix.lower() != ".pth" or not Path(self.model).is_file():
                raise ValueError("请先导入有效的 .pth 模型文件")
            if self.index and (Path(self.index).suffix.lower() != ".index" or not Path(self.index).is_file()):
                raise ValueError("找不到 .index 文件，请重新选择或清空")
        return self

    @property
    def effective_index_rate(self):
        return self.index_rate if self.index else 0.0

    @property
    def effective_pitch(self):
        # The formant shift renders the voice `formant` semitones higher overall and
        # then compensates the F0 — exactly like RVC's `f0_up_key - formant_shift`.
        return self.pitch - self.formant

    def algorithm_latency_ms(self):
        """Same formula as RVC realtime_gui: block + crossfade + 10 ms SOLA (+ input NR buffer)."""
        latency = self.chunk_ms + self.crossfade_ms + 10
        if self.input_denoise:
            latency += min(self.crossfade_ms, 40)
        return latency

    def engine_args(self):
        # Applio's create_pipeline() calls index_path.strip(): never pass None.
        # Output denoise (clean_audio) is managed live by the worker, so it starts off here.
        return dict(block_frame=int(self.chunk_ms * 48),
                    cross_fade_overlap_size=self.crossfade_ms / 1000,
                    extra_convert_size=self.extra_ms / 1000,
                    model_path=self.model, index_path=self.index or "",
                    f0_method=self.f0_method, embedder_model="contentvec",
                    silent_threshold=int(self.threshold_db), vad_enabled=False,
                    clean_audio=False, post_process=False)

    def inference_args(self):
        return dict(f0_up_key=self.effective_pitch, index_rate=self.effective_index_rate,
                    protect=self.protect, volume_envelope=self.volume_envelope,
                    f0_autotune=self.f0_autotune, f0_autotune_strength=self.autotune_strength,
                    proposed_pitch=self.proposed_pitch and not self.f0_autotune,
                    proposed_pitch_threshold=float(self.proposed_pitch_threshold),
                    use_phase_vocoder=self.phase_vocoder)

    def live_values(self):
        return {k: getattr(self, k) for k in LIVE_KEYS}

    def save(self, path: Path):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Settings from 0.3.0 allowed any integer chunk; the pitch cache needs 10 ms frames.
        if isinstance(raw.get("chunk_ms"), int) and raw["chunk_ms"] % 10:
            raw["chunk_ms"] = max(40, (raw["chunk_ms"] + 5) // 10 * 10)
        return cls(**{f.name: raw[f.name] for f in fields(cls) if f.name in raw}).validate()
