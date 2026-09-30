"""Validated settings shared by the desktop UI and the engine process."""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_VERSION = "0.3.0-offline"


def data_dir() -> Path:
    root = Path(os.environ.get("RVC_STUDIO_DATA", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RVCStudio")))
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class Settings:
    runtime: str = ""
    model: str = ""
    index: str = ""
    pitch: int = 8
    index_rate: float = 0.50
    protect: float = 0.35
    volume_envelope: float = 0.45
    chunk_ms: int = 160
    crossfade_ms: int = 60
    extra_ms: int = 250
    f0_method: str = "rmvpe"
    input_device: str = ""
    output_device: str = ""
    monitor_device: str = ""
    monitor: bool = False

    def validate(self, require_model=False):
        limits = {"pitch": (-24, 24), "index_rate": (0, 1), "protect": (0, .5),
                  "volume_envelope": (0, 1), "chunk_ms": (80, 400),
                  "crossfade_ms": (10, 120), "extra_ms": (50, 1000)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} 必须在 {low} 到 {high} 之间")
        for name in ("pitch", "chunk_ms", "crossfade_ms", "extra_ms"):
            if int(getattr(self, name)) != getattr(self, name):
                raise ValueError(f"{name} 必须为整数")
        if self.crossfade_ms >= self.chunk_ms:
            raise ValueError("交叉淡化必须小于分块时长")
        if self.f0_method not in ("rmvpe", "fcpe"):
            raise ValueError("F0 仅支持 RMVPE 或 FCPE")
        if require_model:
            if not self.model or Path(self.model).suffix.lower() != ".pth" or not Path(self.model).is_file():
                raise ValueError("请先导入有效的 .pth 模型文件")
            if self.index and (Path(self.index).suffix.lower() != ".index" or not Path(self.index).is_file()):
                raise ValueError("找不到 .index 文件，请重新选择或清空")
        return self

    @property
    def effective_index_rate(self):
        return self.index_rate if self.index else 0.0

    def engine_args(self):
        return dict(block_frame=int(self.chunk_ms * 48),
                    cross_fade_overlap_size=self.crossfade_ms / 1000,
                    extra_convert_size=self.extra_ms / 1000,
                    model_path=self.model, index_path=self.index or None,
                    f0_method=self.f0_method, embedder_model="contentvec",
                    silent_threshold=-90, vad_enabled=False, clean_audio=False,
                    post_process=False)

    def inference_args(self):
        return dict(f0_up_key=int(self.pitch), index_rate=self.effective_index_rate,
                    protect=self.protect, volume_envelope=self.volume_envelope,
                    f0_autotune=False, proposed_pitch=False, use_phase_vocoder=False)

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
        return cls(**{f.name: raw[f.name] for f in fields(cls) if f.name in raw}).validate()
