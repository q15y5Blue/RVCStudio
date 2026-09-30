"""Runs under the pinned Applio Python, never under the frozen desktop Python."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
from queue import Empty
import sys
import threading
import time
import traceback

from settings import Settings
from audio_buffers import LatestQueue
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
        from rvc.realtime.core import VoiceChanger
        report["engine"] = True
    except Exception as exc:
        report["error"] = str(exc)
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


def extra_settings(sd, device):
    return sd.WasapiSettings(exclusive=False, auto_convert=True) if "WASAPI" in device["host"] else None


def realtime(config, stop):
    import numpy as np
    import sounddevice as sd
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("实时模式需要可用的 NVIDIA CUDA 显卡；当前环境只能尝试文件转换")
    validate_model(config)
    from rvc.realtime.core import VoiceChanger
    devices = device_list(sd)
    inp = resolve_device(devices, config.input_device, "inputs")
    out = resolve_device(devices, config.output_device, "outputs")
    mon = resolve_device(devices, config.monitor_device, "outputs") if config.monitor else None
    if mon and mon["id"] == out["id"]:
        raise ValueError("监听耳机与主输出相同时，请关闭额外监听，避免重复播放")
    emit("status", text="加载模型、ContentVec 和音高提取器…")
    vc = VoiceChanger(**config.engine_args())
    if stop.is_set():
        return
    block = int(config.chunk_ms * 48)
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
                       latency="low", extra_settings=extra_settings(sd, dev), callback=output(queue)))
            streams.append(stream)
        stream = stack.enter_context(sd.InputStream(device=inp["id"], samplerate=48000,
                    channels=min(2, inp["inputs"]), blocksize=block, dtype="float32",
                    latency="low", extra_settings=extra_settings(sd, inp), callback=capture))
        streams.append(stream)
        emit("started", text="实时变声已启动", output=out["name"])
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
            result, volume, latency = vc.on_request(audio, **config.inference_args())
            result = np.asarray(result, dtype=np.float32)
            if not np.isfinite(result).all():
                raise RuntimeError("模型输出了非有限音频值，已停止输出")
            result = np.clip(result, -1, 1)
            main_output.put(result)
            if mon:
                monitor_output.put(result)
            if time.monotonic() - last > .5:
                emit("metrics", inference_ms=round(float(latency[1]), 1),
                     rms=float(volume), dropped=captures.dropped + main_output.dropped + monitor_output.dropped,
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
                        dtype="float32", extra_settings=extra_settings(sd, inp)) as stream:
        for _ in range(150):
            if stop.is_set():
                return False
            samples, overflowed = stream.read(4800)
            if overflowed:
                raise RuntimeError("录音发生溢出，请关闭占用音频设备的软件后重试")
            buffer.append(samples.mean(axis=1))
    sf.write(destination, np.concatenate(buffer), 48000, subtype="PCM_16")
    return True


def offline(config, source, destination):
    validate_model(config)
    from core import run_infer_script
    import soundfile as sf
    emit("status", text="正在转换音频文件；首次加载可能需要下载基础模型…")
    run_infer_script(pitch=config.pitch, index_rate=config.effective_index_rate,
        volume_envelope=config.volume_envelope, protect=config.protect,
        f0_method=config.f0_method, input_path=source, output_path=destination,
        pth_path=config.model, index_path=config.index, split_audio=False,
        f0_autotune=False, f0_autotune_strength=1, proposed_pitch=False,
        proposed_pitch_threshold=155, clean_audio=False, clean_strength=.5,
        export_format="WAV", embedder_model="contentvec")
    result = Path(destination)
    if not result.is_file() or sf.info(result).frames == 0:
        raise RuntimeError("转换没有生成有效音频，请查看运行日志")
    emit("converted", path=str(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["probe", "realtime", "offline", "recordtest"])
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--config")
    parser.add_argument("--input")
    parser.add_argument("--output")
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

    def control():
        try:
            for line in sys.stdin:
                if line.strip() == "stop":
                    break
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
    validate_model(config)
    import sounddevice as _sd  # noqa: F401
    try:
        _sd.query_devices()
    except Exception:
        pass
    from rvc.realtime.core import VoiceChanger  # noqa: F401  heavy import, done pre-reader

    threading.Thread(target=control, daemon=True).start()
    if args.command == "realtime":
        realtime(config, stop)
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
            offline(config, source, str(destination))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        emit("error", text=str(exc))
        sys.exit(1)
