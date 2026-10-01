import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

STUDIO = Path(__file__).resolve().parents[1] / "studio"
sys.path.insert(0, str(STUDIO))
from settings import Settings
from audio_buffers import LatestQueue
from queue import Empty
import runtime
from worker import LiveConfig, extra_settings, resolve_device


class SettingsTests(unittest.TestCase):
    def test_mandarin_preset_and_millisecond_mapping(self):
        s = Settings()
        self.assertEqual(s.engine_args()["block_frame"], 7680)
        self.assertEqual(s.engine_args()["cross_fade_overlap_size"], .06)
        self.assertEqual(s.engine_args()["extra_convert_size"], .25)
        # 出厂男变女预设：音调 10、共振峰 +0.5，实际 f0 移调 = 10 - 0.5 = 9.5
        self.assertEqual(s.inference_args()["f0_up_key"], 9.5)
        self.assertFalse(s.inference_args()["f0_autotune"])
        self.assertFalse(s.engine_args()["vad_enabled"])

    def test_missing_index_disables_retrieval(self):
        self.assertEqual(Settings(index_rate=.6).inference_args()["index_rate"], 0)
        self.assertEqual(Settings(index="voice.index", index_rate=.6).inference_args()["index_rate"], .6)

    def test_reject_nonfinite_and_invalid_crossfade(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.assertRaises(ValueError):
                Settings(protect=value).validate()
        with self.assertRaises(ValueError):
            Settings(chunk_ms=100, crossfade_ms=100).validate()
        with self.assertRaises(ValueError):
            Settings(chunk_ms=100.5).validate()

    def test_unicode_paths_roundtrip_and_model_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "普通话 女声.pth"
            p.write_bytes(b"not-loaded-during-import")
            s = Settings(model=str(p))
            path = Path(directory) / "settings.json"
            s.save(path)
            self.assertEqual(Settings.load(path).validate(True).model, str(p))
            p.unlink()
            with self.assertRaises(ValueError):
                s.validate(True)


class RvcParameterTests(unittest.TestCase):
    def test_new_rvc_parameters_reach_engine(self):
        s = Settings(threshold_db=-45, f0_autotune=True, autotune_strength=.6, phase_vocoder=True, f0_method="crepe-tiny")
        engine, infer = s.engine_args(), s.inference_args()
        self.assertEqual(engine["silent_threshold"], -45)
        self.assertEqual(engine["f0_method"], "crepe-tiny")
        self.assertEqual(engine["index_path"], "")  # Applio calls .strip() on it: never None
        self.assertTrue(infer["f0_autotune"])
        self.assertEqual(infer["f0_autotune_strength"], .6)
        self.assertTrue(infer["use_phase_vocoder"])
        self.assertFalse(Settings(proposed_pitch=True, f0_autotune=True).inference_args()["proposed_pitch"])

    def test_ranges_follow_param_table(self):
        for bad in (dict(formant=2.5), dict(threshold_db=5), dict(chunk_ms=165), dict(extra_ms=6000),
                    dict(f0_method="pm"), dict(input_denoise=1), dict(crossfade_ms=150, chunk_ms=150)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Settings(**bad).validate()
        Settings(chunk_ms=1500, extra_ms=5000, crossfade_ms=150, formant=-2).validate()

    def test_latency_matches_rvc_formula(self):
        self.assertEqual(Settings(chunk_ms=160, crossfade_ms=60).algorithm_latency_ms(), 230)
        self.assertEqual(Settings(chunk_ms=160, crossfade_ms=60, input_denoise=True).algorithm_latency_ms(), 270)

    def test_old_settings_file_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"pitch": 9, "chunk_ms": 165, "crossfade_ms": 60}), encoding="utf-8")
            s = Settings.load(path)
            self.assertEqual((s.pitch, s.chunk_ms, s.formant, s.threshold_db), (9, 170, 0.5, -60))

    def test_live_updates_only_touch_live_keys_and_reject_bad_values(self):
        live = LiveConfig(Settings())
        live.update({"pitch": 11, "formant": .5, "chunk_ms": 400, "model": "x.pth"})
        config, version = live.get()
        self.assertEqual((config.pitch, config.formant, config.chunk_ms, config.model, version), (11, .5, 160, "", 1))
        with self.assertRaises(ValueError):
            live.update({"pitch": 99})
        self.assertEqual(live.get(), (config, 1))

    def test_wasapi_exclusive_only_for_wasapi(self):
        class FakeSd:
            @staticmethod
            def WasapiSettings(**kw):
                return kw
        self.assertEqual(extra_settings(FakeSd, {"host": "Windows WASAPI"}, True), {"exclusive": True})
        self.assertEqual(extra_settings(FakeSd, {"host": "Windows WASAPI"}), {"exclusive": False, "auto_convert": True})
        self.assertIsNone(extra_settings(FakeSd, {"host": "MME"}, True))


class BeatriceSettingsTests(unittest.TestCase):
    def test_backend_validation_and_model_checks(self):
        with self.assertRaises(ValueError):
            Settings(backend="so-vits").validate()
        for bad in (-1, 1.5, True, "0"):
            with self.assertRaises(ValueError):
                Settings(beatrice_speaker=bad).validate()
        with tempfile.TemporaryDirectory() as directory:
            ckpt = Path(directory) / "checkpoint_latest.pt.gz"
            ckpt.write_bytes(b"x")
            s = Settings(backend="beatrice", beatrice_model=str(ckpt))
            self.assertIs(s.validate(require_model=True), s)  # RVC model not needed for Beatrice
            self.assertFalse(s.has_model("rvc"))
            with self.assertRaises(ValueError):
                Settings(backend="beatrice", beatrice_model=str(Path(directory) / "beatrice_paraphernalia.toml")).validate(True)
            with self.assertRaises(ValueError):
                Settings(backend="rvc", beatrice_model=str(ckpt)).validate(True)

    def test_formant_snaps_to_trained_steps_and_latency_adds_lookahead(self):
        self.assertEqual(Settings(formant=0.8).beatrice_formant, 1.0)
        self.assertEqual(Settings(formant=-0.2).beatrice_formant, 0.0)
        self.assertEqual(Settings(formant=0.25).beatrice_formant, 0.0)
        self.assertEqual(Settings(formant=0.3).beatrice_formant, 0.5)
        rvc = Settings(chunk_ms=160, crossfade_ms=60)
        self.assertEqual(Settings(backend="beatrice", chunk_ms=160, crossfade_ms=60).algorithm_latency_ms(),
                         rvc.algorithm_latency_ms() + 30)

    def test_backend_and_speaker_are_live(self):
        live = LiveConfig(Settings())
        live.update({"backend": "beatrice", "beatrice_speaker": 3})
        self.assertEqual((live.get()[0].backend, live.get()[0].beatrice_speaker), ("beatrice", 3))


class RuntimeTests(unittest.TestCase):
    def test_zip_traversal_and_windows_device_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for bad in ("../x", "a/../../x", "C:/x", "/Windows/x", "\\\\host\\share\\x", "x:stream", "NUL.txt", "folder./x", "folder /x"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    runtime.safe_member(root, bad)
            self.assertEqual(runtime.safe_member(root, "Applio/env/python.exe"), root / "Applio/env/python.exe")

    def test_cancel_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            cancel = threading.Event()
            cancel.set()
            with patch.object(runtime.urllib.request, "urlopen") as network:
                with self.assertRaises(runtime.Cancelled):
                    runtime.download(Path(directory), cancel=cancel)
                network.assert_not_called()

    def test_archive_size_fails_before_execution_or_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fake.zip"
            path.write_bytes(b"MZ")
            with self.assertRaises(ValueError):
                runtime.verify_archive(path)

    def test_wrong_engine_source_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "env").mkdir()
            (root / "env/python.exe").touch()
            (root / "core.py").write_text("modified", encoding="utf-8")
            with self.assertRaises(ValueError):
                runtime.verify_engine(root, {"core.py": "0" * 64})

    def test_staged_install_and_cancel_leave_no_partial_runtime(self):
        import hashlib
        import zipfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "test.zip"
            entries = {"Applio/env/python.exe": b"stub", "Applio/rvc/realtime/core.py": b"core"}
            with zipfile.ZipFile(archive, "w") as z:
                for name, content in entries.items():
                    z.writestr(name, content)
            manifest = {"rvc/realtime/core.py": hashlib.sha256(b"core").hexdigest()}
            with patch.object(runtime, "verify_archive"):
                result = runtime.install(archive, root / "installed", manifest)
                self.assertEqual(result, (root / "installed").resolve())
                self.assertEqual((result / "env/python.exe").read_bytes(), b"stub")
                with self.assertRaises(ValueError):
                    runtime.install(archive, result, manifest)
                cancel = threading.Event()
                cancel.set()
                with self.assertRaises(runtime.Cancelled):
                    runtime.install(archive, root / "cancelled", manifest, cancel=cancel)
                self.assertFalse((root / "cancelled").exists())
                self.assertFalse(list(root.glob("rvc-stage-*")))


class AudioTests(unittest.TestCase):
    def test_bounded_queue_drops_stale_audio(self):
        q = LatestQueue(2)
        for n in range(100):
            q.put(n)
        self.assertEqual(q.dropped, 98)
        self.assertEqual([q.get(), q.get()], [98, 99])
        with self.assertRaises(Empty):
            q.get()

    def test_monitor_cannot_consume_main_output(self):
        main, monitor = LatestQueue(), LatestQueue()
        for q in (main, monitor):
            q.put("converted")
        self.assertEqual(monitor.get(), "converted")
        self.assertEqual(main.get(), "converted")

    def test_device_renumbering_and_disconnection(self):
        d = dict(key="Windows WASAPI | USB Mic | 12", inputs=1, outputs=0, id=12)
        self.assertEqual(resolve_device([d], "Windows WASAPI | USB Mic | 3", "inputs")["id"], 12)
        with self.assertRaises(ValueError):
            resolve_device([d], "Windows WASAPI | Other | 3", "inputs")
        with self.assertRaises(ValueError):
            resolve_device([d], d["key"], "outputs")


class WorkerProcessTests(unittest.TestCase):
    def test_probe_protocol_in_separate_process_without_cuda(self):
        # Only protocol/dependency handling is tested: these are explicit mocks,
        # not evidence of real audio conversion or GPU compatibility.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "torch.py").write_text("class cuda:\n @staticmethod\n def is_available(): return False\n", encoding="utf-8")
            (root / "sounddevice.py").write_text("def query_hostapis(): return []\ndef query_devices(): return []\nclass default: device=(-1,-1)\n", encoding="utf-8")
            (root / "rvc/realtime").mkdir(parents=True)
            (root / "rvc/__init__.py").touch()
            (root / "rvc/realtime/__init__.py").touch()
            (root / "rvc/realtime/core.py").write_text("class VoiceChanger: pass\n", encoding="utf-8")
            r = subprocess.run([sys.executable, str(STUDIO / "worker.py"), "probe", "--runtime", str(root)],
                               capture_output=True, input="", text=True, encoding="utf-8", timeout=15,
                               env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(r.returncode, 0, r.stderr)
            line = next(x for x in r.stdout.splitlines() if x.startswith("@RVC@"))
            report = json.loads(line[5:])
            self.assertEqual(report["event"], "probe")
            self.assertFalse(report["cuda"])
            self.assertEqual(report["devices"], [])


class RealtimeLoopTests(unittest.TestCase):
    """Drives worker.realtime() with fake torch / sounddevice / Applio modules (no GPU)."""

    def test_live_parameters_reach_engine_without_restart(self):
        import types
        import numpy as np
        import worker
        calls, events = [], []
        block = 160 * 48

        class Stream:
            latency = .01
            def __init__(self, callback=None, channels=1, **kw):
                self.callback, self.channels, self.active, self.thread = callback, channels, True, None
            def __enter__(self):
                def pump():
                    while self.active:
                        if self.channels and self.callback:
                            buf = np.zeros((block, self.channels), dtype=np.float32)
                            self.callback(buf, block, None, None)
                        time.sleep(.005)
                self.thread = threading.Thread(target=pump, daemon=True)
                self.thread.start()
                return self
            def __exit__(self, *exc):
                self.active = False

        sd = types.SimpleNamespace(
            query_hostapis=lambda: [{"name": "MME"}],
            query_devices=lambda: [dict(name="Mic", hostapi=0, max_input_channels=1, max_output_channels=0),
                                   dict(name="Out", hostapi=0, max_input_channels=0, max_output_channels=2)],
            InputStream=Stream, OutputStream=Stream)

        class VoiceChanger:
            def __init__(self, **kw):
                self.device = "cpu"
                self.vc_model = types.SimpleNamespace(input_sensitivity=None, reduced_noise=None, device="cpu",
                                                      pipeline=types.SimpleNamespace(tgt_sr=40000))
            def on_request(self, audio, **kw):
                calls.append(kw)
                return audio, .1, [0, 5.0, 0]

        core = types.ModuleType("rvc.realtime.core")
        core.VoiceChanger = VoiceChanger
        torch = types.ModuleType("torch")
        torch.cuda = types.SimpleNamespace(is_available=lambda: True)
        fakes = {"torch": torch, "sounddevice": sd, "rvc": types.ModuleType("rvc"),
                 "rvc.realtime": types.ModuleType("rvc.realtime"), "rvc.realtime.core": core}
        import time
        config = Settings(input_device="MME | Mic | 0", output_device="MME | Out | 1", formant=0.0, pitch=8)
        live = worker.LiveConfig(config)
        stop = threading.Event()
        with patch.dict(sys.modules, fakes), patch.object(worker, "validate_model"), \
             patch.object(worker.formant, "install"), patch.object(worker, "emit", lambda e, **d: events.append((e, d))), \
             patch.object(worker, "InputDenoiser", lambda *a: (lambda audio, enabled: audio)):
            runner = threading.Thread(target=worker.realtime, args=(live, stop))
            runner.start()
            deadline = time.monotonic() + 5
            while len(calls) < 3 and time.monotonic() < deadline:
                time.sleep(.01)
            live.update({"pitch": 12, "formant": 1.0, "threshold_db": -40})
            while not any(c["f0_up_key"] == 11.0 for c in calls) and time.monotonic() < deadline:
                time.sleep(.01)
            stop.set()
            runner.join(5)
        self.assertEqual(calls[0]["f0_up_key"], 8)
        self.assertTrue(any(c["f0_up_key"] == 11.0 for c in calls))
        self.assertEqual(worker.formant.STATE["semitones"], 1.0)
        kinds = [e for e, _ in events]
        self.assertIn("params", kinds)
        self.assertIn("stopped", kinds)
        started = dict(events)["started"]
        self.assertEqual(started["delay_ms"], 230 + 20)
        worker.formant.set_semitones(0)

    def test_ab_switch_between_loaded_engines_while_running(self):
        """Both engines loaded; switching "backend" live changes who converts each block."""
        import time
        import types
        import numpy as np
        import worker
        block = 160 * 48
        used, fed, events = [], [], []

        class Stream:
            latency = .01
            def __init__(self, callback=None, channels=1, **kw):
                self.callback, self.channels, self.active = callback, channels, True
            def __enter__(self):
                def pump():
                    while self.active:
                        self.callback(np.zeros((block, self.channels), dtype=np.float32), block, None, None)
                        time.sleep(.005)
                threading.Thread(target=pump, daemon=True).start()
                return self
            def __exit__(self, *exc):
                self.active = False

        sd = types.SimpleNamespace(
            query_hostapis=lambda: [{"name": "MME"}],
            query_devices=lambda: [dict(name="Mic", hostapi=0, max_input_channels=1, max_output_channels=0),
                                   dict(name="Out", hostapi=0, max_input_channels=0, max_output_channels=2)],
            InputStream=Stream, OutputStream=Stream)

        class Engine:
            device = "cpu"
            def __init__(self, name):
                self.name, self.label = name, name
            def apply(self, config):
                if config.beatrice_speaker > 5:
                    raise ValueError("该 Beatrice 模型只有 6 个说话人")
            def feed(self, audio):
                fed.append(self.name)
            def process(self, audio, config):
                used.append(self.name)
                return audio, .1, 3.0

        config = Settings(input_device="MME | Mic | 0", output_device="MME | Out | 1", backend="rvc")
        live = worker.LiveConfig(config)
        stop = threading.Event()
        with patch.dict(sys.modules, {"sounddevice": sd}), \
             patch.object(worker, "load_engines", lambda c, s: {"rvc": Engine("rvc"), "beatrice": Engine("beatrice")}), \
             patch.object(worker, "emit", lambda e, **d: events.append((e, d))), \
             patch.object(worker, "InputDenoiser", lambda *a: (lambda audio, enabled: audio)):
            runner = threading.Thread(target=worker.realtime, args=(live, stop))
            runner.start()
            deadline = time.monotonic() + 5
            while len(used) < 3 and time.monotonic() < deadline:
                time.sleep(.01)
            live.update({"backend": "beatrice"})
            while "beatrice" not in used and time.monotonic() < deadline:
                time.sleep(.01)
            live.update({"beatrice_speaker": 9})  # rejected by the engine: keep converting with Beatrice
            n = len(used)
            while len(used) < n + 3 and time.monotonic() < deadline:
                time.sleep(.01)
            stop.set()
            runner.join(5)
        self.assertEqual(used[0], "rvc")
        self.assertEqual(used[-1], "beatrice")
        self.assertIn("beatrice", fed)   # inactive Beatrice kept its context fresh
        self.assertIn("rvc", fed)
        texts = [d.get("text", "") for e, d in events]
        self.assertTrue(any("已切换到 Beatrice" in t for t in texts))
        self.assertTrue(any("参数未生效" in t for t in texts))
        self.assertEqual(dict(events)["started"]["engines"], ["rvc", "beatrice"])


if __name__ == "__main__":
    unittest.main()
