"""Real VB-CABLE test: generate a tone into render, capture only its virtual mic."""
import json
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
wheel = next((ROOT / "tools/audio-test").glob("sounddevice-*.whl"))
test_lib = ROOT / "tools/audio-test/lib"
test_lib.mkdir(exist_ok=True)
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(test_lib)
sys.path.insert(0, str(test_lib))
sys.path.insert(0, str(ROOT / "studio"))
import numpy as np
import sounddevice as sd
from worker import device_list
from routing import cable_pair

devices = device_list(sd)
render, capture = cable_pair(devices)
if not (render and capture):
    report = {'passed': False, 'status': 'not_run',
              'reason': 'Normal CABLE endpoints unavailable; restart after driver install and test in a local Windows session.',
              'devices': devices}
    (ROOT / 'build/audio-loopback-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(2)
recorded = []
position = 0
statuses = []


def output(outdata, frames, times, status):
    global position
    if status:
        statuses.append(str(status))
    samples = np.arange(position, position + frames)
    outdata[:] = (.08 * np.sin(2 * np.pi * 440 * samples / 48000))[:, None]
    position += frames


def input_audio(indata, frames, times, status):
    if status:
        statuses.append(str(status))
    recorded.append(indata.copy())


with sd.InputStream(device=capture["id"], samplerate=48000, channels=2,
                    blocksize=4800, dtype="float32", callback=input_audio), \
     sd.OutputStream(device=render["id"], samplerate=48000, channels=2,
                     blocksize=4800, dtype="float32", callback=output):
    time.sleep(3)
audio = np.concatenate(recorded, axis=0)[48000:, 0]
assert len(audio) > 24000
spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
frequency = np.argmax(spectrum) * 48000 / len(audio)
rms = float(np.sqrt(np.mean(audio ** 2)))
report = {"render": render, "capture": capture, "samples": len(audio),
          "expected_tone_hz": 440, "detected_peak_hz": float(frequency),
          "captured_rms": rms, "callback_statuses": statuses,
          "passed": abs(frequency - 440) < 3 and rms > .005}
(ROOT / "build/audio-loopback-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
assert report["passed"], report
