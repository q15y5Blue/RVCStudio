"""Runs under the pinned Applio Python, never under the frozen desktop Python."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from queue import Empty
import sys
import threading
import time
import traceback

from settings import BACKENDS, LIVE_KEYS, Settings
from audio_buffers import LatestQueue
import formant
from routing import cable_pair

PREFIX = "@RVC@"


def emit(event, **data):
    print(PREFIX + json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)


def device_list(sd):
    hosts = sd.query_hostapis()
    result = []
    for n, d in enumerate(sd.query_devices()):
        host = hosts[d["hostapi"]]["name"]
        if "ASIO" in host:
            continue
        result.append(dict(id=n, name=d["name"], host=host,
                           key=f"{host} | {d['name']} | {n}",
                           inputs=int(d["max_input_channels"]),
                           outputs=int(d["max_output_channels"])))
    return result


def resolve_device(devices, key, direction):
    # Device indices can change after reboot: match host/name first, index only
    # disambiguates equal names within that group.
    parts = key.rsplit(" | ", 1)
    stem = parts[0]
    matches = [d for d in devices if d[direction] and d["key"].rsplit(" | ", 1)[0] == stem]
    if not matches:
        raise ValueError("音频设备已断开，请刷新设备列表后重新选择")
    if len(matches) > 1:
        exact = [d for d in matches if d["key"] == key]
        if len(exact) != 1:
            raise ValueError("有多个同名音频设备，请刷新并重新选择")
        return exact[0]
    return matches[0]


def probe():
    import sounddevice as sd
    devices = device_list(sd)
    cable_render, cable_capture = cable_pair(devices)
    report = dict(devices=devices, cuda=False, gpu="未检测到 NVIDIA CUDA", engine=False,
                  cable_render=cable_render, cable_capture=cable_capture,
                  default_input=int(sd.default.device[0]))
    try:
        import torch
        report["cuda"] = torch.cuda.is_available()
        if report["cuda"]:
            report["gpu"] = torch.cuda.get_device_name(0)
            try:
                x = torch.ones(8, 8, device="cuda")
                float((x @ x).sum())
            except Exception as exc:
                # Applio 3.6.5 ships CUDA 12.8 PyTorch, which has no kernels for GTX 10xx
                # (Pascal): is_available() is True but every operation fails.
                report["cuda"] = False
                report["gpu"] += ("（当前内置 PyTorch 不支持这块显卡：" + str(exc).splitlines()[0][:120]
                                  + "。GTX 10 系请运行 training\\一键训练声音.bat -FixTorchOnly 换成 CUDA 12.6 版）")
        from rvc.realtime.core import VoiceChanger
        report["engine"] = True
    except Exception as exc:
        report["error"] = str(exc)
    try:
        import beatrice_backend
        beatrice_backend.import_trainer()
        report["beatrice"] = True
    except Exception as exc:
        report["beatrice"] = False
        report["beatrice_error"] = str(exc)
    emit("probe", **report)


def validate_model(config):
    import torch
    emit("status", text="检查 RVC v2 模型…")
    checkpoint = torch.load(config.model, map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != "v2":
        raise ValueError("需要 RVC v2 推理模型（不是训练检查点或 v1 模型）")
    if "weight" not in checkpoint or "config" not in checkpoint:
        raise ValueError("该 .pth 不是完整的 RVC 推理模型")
    if not checkpoint.get("f0", 1):
        raise ValueError("男声转女声预设需要带 F0 的模型")
    del checkpoint
    if config.index:
        import faiss
        index = faiss.read_index(config.index)
        if index.d != 768 or index.ntotal < 1:
            raise ValueError("特征索引不是有效的 RVC v2 768 维索引")


def extra_settings(sd, device, exclusive=False):
    if "WASAPI" not in device["host"]:
        return None
    # auto_convert only exists for shared mode; exclusive mode needs the native format.
    return sd.WasapiSettings(exclusive=True) if exclusive else sd.WasapiSettings(exclusive=False, auto_convert=True)


class LiveConfig:
    """Settings that the GUI may change while realtime conversion runs ("set {json}")."""

    def __init__(self, config):
        self.lock = threading.Lock()
        self.config = config
        self.version = 0

    def update(self, values):
        changes = {k: v for k, v in values.items() if k in LIVE_KEYS}
        if not changes:
            return
        with self.lock:
            # validate() rejects bad values; the running config stays untouched then.
            self.config = replace(self.config, **changes).validate()
            self.version += 1

    def get(self):
        with self.lock:
            return self.config, self.version


class InputDenoiser:
    """Port of RVC realtime_gui "输入降噪": TorchGate on the 48 kHz microphone signal.

    The newest `sola` samples are held back and cross-faded with the next block,
    which is where RVC's extra min(crossfade, 40 ms) of latency comes from.
    History is always kept so the noise estimate is ready when the user enables it.
    """

    def __init__(self, block, crossfade, extra, device):
        import numpy as np
        import torch
        from noisereduce.torchgate import TorchGate
        self.np, self.torch = np, torch
        zc = 480  # 10 ms at 48 kHz
        self.block = block
        self.sola = max(1, min(crossfade, 4 * zc))
        self.history = torch.zeros(max(extra, 24000) + crossfade + zc + block, device=device)
        self.nr_buffer = torch.zeros(self.sola, device=device)
        fade_in = torch.sin(0.5 * np.pi * torch.linspace(0.0, 1.0, self.sola, device=device)) ** 2
        self.fade_in, self.fade_out = fade_in, 1 - fade_in
        self.gate = TorchGate(sr=48000, n_fft=4 * zc, prop_decrease=0.9).to(device)
        self.was_enabled = False

    def __call__(self, audio, enabled):
        torch = self.torch
        block = torch.as_tensor(audio, dtype=torch.float32, device=self.history.device)
        self.history[:-self.block] = self.history[self.block:].clone()
        self.history[-self.block:] = block
        if not enabled:
            self.was_enabled = False
            return audio
        if not self.was_enabled:
            self.nr_buffer.zero_()
            self.was_enabled = True
        window = self.history[-self.sola - self.block:]
        clean = self.gate(window.unsqueeze(0), self.history.unsqueeze(0)).squeeze(0)
        clean[:self.sola] = clean[:self.sola] * self.fade_in + self.nr_buffer * self.fade_out
        self.nr_buffer[:] = clean[self.block:]
        return clean[:self.block].detach().cpu().numpy().astype(self.np.float32)


def apply_live(vc, config, state):
    """Push live-changeable values into the running Applio objects (main thread only)."""
    formant.set_semitones(config.formant)
    engine = vc.vc_model
    engine.input_sensitivity = 10 ** (config.threshold_db / 20)
    wanted = config.denoise_strength if config.output_denoise else None
    if wanted != state.get("output_denoise"):
        if wanted is None:
            engine.reduced_noise = None
        else:
            from noisereduce.torchgate import TorchGate
            engine.reduced_noise = TorchGate(engine.pipeline.tgt_sr, prop_decrease=wanted).to(engine.device)
        state["output_denoise"] = wanted


class RvcEngine:
    """Applio's realtime VoiceChanger behind the engine interface used by realtime()."""

    label = "RVC"

    def __init__(self, config):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("RVC 实时模式需要可用的 NVIDIA CUDA 显卡；当前环境只能尝试文件转换")
        validate_model(config)
        from rvc.realtime.core import VoiceChanger
        emit("status", text="加载 RVC 模型、ContentVec 和音高提取器…")
        formant.install()
        formant.set_semitones(config.formant)
        self.vc = VoiceChanger(**config.engine_args())
        self.device = self.vc.device
        self.state = {}

    def apply(self, config):
        apply_live(self.vc, config, self.state)

    def feed(self, audio):
        pass  # Applio keeps its own context; it catches up within a block after switching back

    def process(self, audio, config):
        import numpy as np
        result, volume, latency = self.vc.on_request(audio, **config.inference_args())
        return np.asarray(result, dtype=np.float32), float(volume), float(latency[1])


def load_engine(backend, config):
    if backend == "rvc":
        return RvcEngine(config)
    import beatrice_backend
    emit("status", text="加载 Beatrice v2 模型…")
    engine = beatrice_backend.BeatriceEngine(config)
    if engine.device != "cuda":
        emit("warning", text="未检测到 CUDA，Beatrice 正在用 CPU 推理；若出现丢块请加大采样长度或减小额外推理时长")
    return engine


def load_engines(config, stop):
    """Load the selected engine, plus the other one when its model is set (live A/B switching)."""
    engines = {}
    for backend in [config.backend] + [b for b in BACKENDS if b != config.backend]:
        if backend != config.backend and not config.has_model(backend):
            continue
        if stop.is_set():
            break
        try:
            engines[backend] = load_engine(backend, config)
        except Exception as exc:
            if backend == config.backend:
                raise
            emit("warning", text=f"未加载 {BACKENDS[backend]}，运行中无法切换到它：{exc}")
    return engines


def realtime(live, stop):
    config, _ = live.get()
    import numpy as np
    import sounddevice as sd
    devices = device_list(sd)
    inp = resolve_device(devices, config.input_device, "inputs")
    out = resolve_device(devices, config.output_device, "outputs")
    mon = resolve_device(devices, config.monitor_device, "outputs") if config.monitor else None
    if mon and mon["id"] == out["id"]:
        raise ValueError("监听耳机与主输出相同时，请关闭额外监听，避免重复播放")
    engines = load_engines(config, stop)
    if stop.is_set():
        return
    block = int(config.chunk_ms * 48)
    denoiser = InputDenoiser(block, int(config.crossfade_ms * 48), int(config.extra_ms * 48),
                             engines[config.backend].device)
    for engine in engines.values():
        engine.apply(config)
    good = config  # last configuration every loaded engine accepted
    applied = 0
    captures = LatestQueue(2)
    main_output = LatestQueue(2)
    monitor_output = LatestQueue(2)
    problems = LatestQueue(1)
    counters = {"underruns": 0, "audio_status": 0}

    def capture(indata, frames, times, status):
        try:
            if status:
                counters["audio_status"] += 1
            if frames != block:
                raise RuntimeError("音频设备返回了不同的分块大小")
            captures.put(indata.mean(axis=1).copy())
        except Exception as exc:
            problems.put(str(exc))

    def output(queue):
        def callback(outdata, frames, times, status):
            outdata.fill(0)
            try:
                if status:
                    counters["audio_status"] += 1
                converted = queue.get()
                if len(converted) != frames:
                    raise RuntimeError("转换结果的音频长度不匹配")
                outdata[:] = converted[:, None]
            except Empty:
                counters["underruns"] += 1
            except Exception as exc:
                problems.put(str(exc))
        return callback

    with ExitStack() as stack:
        streams = []
        for dev, queue in [(out, main_output)] + ([(mon, monitor_output)] if mon else []):
            stream = stack.enter_context(sd.OutputStream(device=dev["id"], samplerate=48000,
                       channels=min(2, dev["outputs"]), blocksize=block, dtype="float32",
                       latency="low", extra_settings=extra_settings(sd, dev, config.wasapi_exclusive), callback=output(queue)))
            streams.append(stream)
        stream = stack.enter_context(sd.InputStream(device=inp["id"], samplerate=48000,
                    channels=min(2, inp["inputs"]), blocksize=block, dtype="float32",
                    latency="low", extra_settings=extra_settings(sd, inp, config.wasapi_exclusive), callback=capture))
        streams.append(stream)
        # Device buffering of the capture stream and the main output stream.
        device_ms = 1000 * (float(stream.latency) + float(streams[0].latency))
        emit("started", text=f"实时变声已启动（{engines[config.backend].label}）", output=out["name"],
             delay_ms=round(config.algorithm_latency_ms() + device_ms), engines=list(engines),
             backend=config.backend)
        last = time.monotonic()
        last_capture = last
        while not stop.is_set():
            try:
                problem = problems.get()
                raise RuntimeError(problem)
            except Empty:
                pass
            if any(not s.active for s in streams):
                raise RuntimeError("音频设备停止工作或已断开")
            try:
                audio = captures.get(timeout=.2)
            except Empty:
                if time.monotonic() - last_capture > 5:
                    raise RuntimeError("麦克风连续 5 秒未提供音频，请检查设备和隐私权限")
                continue
            last_capture = time.monotonic()
            config, version = live.get()
            if version != applied:
                applied = version
                try:
                    if config.backend not in engines:
                        raise ValueError(f"本次没有加载 {BACKENDS[config.backend]}，请停止后重新开始再切换")
                    for engine in engines.values():
                        engine.apply(config)
                except ValueError as exc:
                    emit("warning", text=f"参数未生效：{exc}")
                else:
                    if config.backend != good.backend:
                        emit("params", text=f"已切换到 {BACKENDS[config.backend]}", backend=config.backend)
                    else:
                        emit("params", text="参数已实时生效")
                    good = config
            config = good
            audio = denoiser(audio, config.input_denoise)
            for name, engine in engines.items():
                if name != config.backend:
                    engine.feed(audio)
            result, volume, inference_ms = engines[config.backend].process(audio, config)
            if not np.isfinite(result).all():
                raise RuntimeError("模型输出了非有限音频值，已停止输出")
            gain = float(config.output_gain)
            if gain != 1.0:
                result = result * gain  # 输出增益（>1 放大，过高会被削顶而爆音）
            result = np.clip(result, -1, 1)
            main_output.put(result)
            if mon:
                monitor_output.put(result)
            if time.monotonic() - last > .5:
                emit("metrics", inference_ms=round(inference_ms, 1), backend=config.backend,
                     rms=float(volume), dropped=captures.dropped + main_output.dropped + monitor_output.dropped,
                     delay_ms=round(config.algorithm_latency_ms() + device_ms),
                     underruns=counters["underruns"], audio_status=counters["audio_status"])
                last = time.monotonic()
    emit("stopped", text="音频设备已释放")


def record(config, destination, stop):
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    inp = resolve_device(device_list(sd), config.input_device, "inputs")
    emit("status", text="正在录音 15 秒，请用平时的声音说普通话…")
    buffer = []
    with sd.InputStream(device=inp["id"], samplerate=48000,
                        channels=min(2, inp["inputs"]), blocksize=4800,
                        dtype="float32", extra_settings=extra_settings(sd, inp, config.wasapi_exclusive)) as stream:
        for _ in range(150):
            if stop.is_set():
                return False
            samples, overflowed = stream.read(4800)
            if overflowed:
                raise RuntimeError("录音发生溢出，请关闭占用音频设备的软件后重试")
            buffer.append(samples.mean(axis=1))
    sf.write(destination, np.concatenate(buffer), 48000, subtype="PCM_16")
    return True


def offline(config, source, destination, compare=False):
    """Convert with the selected engine, or with both (A/B files named -RVC / -Beatrice)."""
    import soundfile as sf
    backends = list(BACKENDS) if compare else [config.backend]
    for backend in backends:
        target = destination
        if compare:
            target = str(Path(destination).with_name(Path(destination).stem + "-" + backend_tag(backend) + ".wav"))
            if Path(target).exists():
                raise ValueError("输出文件已存在，拒绝覆盖")
        if backend == "rvc":
            offline_rvc(config, source, target)
        else:
            import beatrice_backend
            emit("status", text="正在用 Beatrice v2 转换音频文件…")
            beatrice_backend.convert_file(config, source, target, progress=lambda text: emit("status", text=text))
        result = Path(target)
        if not result.is_file() or sf.info(result).frames == 0:
            raise RuntimeError("转换没有生成有效音频，请查看运行日志")
        emit("converted", path=str(result), backend=backend)


def backend_tag(backend):
    return {"rvc": "RVC", "beatrice": "Beatrice"}[backend]


def offline_rvc(config, source, destination):
    validate_model(config)
    from core import run_infer_script
    import soundfile as sf
    emit("status", text="正在用 RVC 转换音频文件；首次加载可能需要下载基础模型…")
    formant.install()
    formant.set_semitones(config.formant)
    run_infer_script(pitch=config.effective_pitch, index_rate=config.effective_index_rate,
        volume_envelope=config.volume_envelope, protect=config.protect,
        f0_method=config.f0_method, input_path=source, output_path=destination,
        pth_path=config.model, index_path=config.index, split_audio=False,
        f0_autotune=config.f0_autotune, f0_autotune_strength=config.autotune_strength,
        proposed_pitch=config.proposed_pitch and not config.f0_autotune,
        proposed_pitch_threshold=float(config.proposed_pitch_threshold),
        clean_audio=config.output_denoise, clean_strength=config.denoise_strength,
        export_format="WAV", embedder_model="contentvec")
    result = Path(destination)
    if not result.is_file() or sf.info(result).frames == 0:
        raise RuntimeError("转换没有生成有效音频，请查看运行日志")
    gain = float(config.output_gain)
    if gain != 1.0:
        import numpy as np
        info = sf.info(result)
        audio, sr = sf.read(result, dtype="float32")
        audio = np.clip(audio * gain, -1, 1)
        sf.write(result, audio, sr, subtype=info.subtype)


def preload(config, command, compare):
    """Heavy native initialization (torch/faiss/PortAudio) before the stdin reader starts.

    The selected engine must be valid. In a realtime session the other engine is loaded
    too when its model is set, so it is warmed here as well; its errors surface later
    as a warning instead of failing the session."""
    if compare:
        for backend in BACKENDS:
            config.check_model(backend)
    for backend in BACKENDS:
        optional = backend != config.backend and not compare
        if optional and (command != "realtime" or not config.has_model(backend)):
            continue
        try:
            if backend == "rvc":
                validate_model(config)
                from rvc.realtime.core import VoiceChanger  # noqa: F401
            else:
                import beatrice_backend
                beatrice_backend.warm_up()
        except Exception:
            if not optional:
                raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["probe", "realtime", "offline", "recordtest"])
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--config")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--compare", action="store_true", help="convert with both engines (A/B)")
    args = parser.parse_args()
    engine = Path(args.runtime).resolve()
    os.chdir(engine)
    sys.path.insert(0, str(engine))
    # Applio ships a conda-style env whose OpenSSL/native DLLs live in env\Library\bin.
    # Register them explicitly (and first on PATH) so the engine binds its own DLLs
    # instead of any libssl/libcrypto sitting in the launcher's directory.
    envdir = engine / "env"
    dll_dirs = [envdir, envdir / "Library" / "bin", envdir / "Library" / "mingw-w64" / "bin",
                envdir / "Library" / "usr" / "bin", envdir / "Scripts", engine]
    dll_dirs = [p for p in dll_dirs if p.exists()]
    os.environ["PATH"] = os.pathsep.join(str(p) for p in dll_dirs) + os.pathsep + os.environ.get("PATH", "")
    for dll_dir in {str(p) for p in dll_dirs}:
        try:
            os.add_dll_directory(dll_dir)
        except (OSError, AttributeError):
            pass
    stop = threading.Event()
    live = None

    def control():
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "stop":
                    break
                if command.startswith("set ") and live is not None:
                    try:
                        live.update(json.loads(command[4:]))
                    except Exception as exc:
                        emit("warning", text=f"参数未生效：{exc}")
        finally:
            stop.set()  # Parent closed or crashed: do not leave microphone open.

    if args.command == "probe":
        # One-shot detection never needs stdin. Do not park a reader thread on an open
        # pipe here: on Windows a thread blocked on a piped stdin while the main thread
        # initializes PortAudio/torch native code can deadlock the loader/COM (the
        # process then sits at ~0 CPU with no output and the UI stays on "正在检测环境").
        probe()
        return
    config = Settings.load(Path(args.config)).validate(require_model=True)
    # Finish all heavy native initialization (torch/faiss model load and PortAudio
    # enumeration) BEFORE the stdin reader thread starts, to avoid the same deadlock
    # for long-running realtime/offline sessions.
    preload(config, args.command, args.compare)
    import sounddevice as _sd  # noqa: F401
    try:
        _sd.query_devices()
    except Exception:
        pass

    live = LiveConfig(config)
    threading.Thread(target=control, daemon=True).start()
    if args.command == "realtime":
        realtime(live, stop)
    else:
        destination = Path(args.output).resolve()
        if destination.exists():
            raise ValueError("输出文件已存在，拒绝覆盖")
        source = args.input
        if args.command == "recordtest":
            source = str(destination.with_name(destination.stem + "-original.wav"))
            if Path(source).exists():
                raise ValueError("原始录音文件已存在，拒绝覆盖")
            if not record(config, source, stop):
                return
        if not stop.is_set():
            offline(config, source, str(destination), compare=args.compare)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        emit("error", text=str(exc))
        sys.exit(1)
