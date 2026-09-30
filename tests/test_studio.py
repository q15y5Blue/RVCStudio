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
from worker import resolve_device


class SettingsTests(unittest.TestCase):
    def test_mandarin_preset_and_millisecond_mapping(self):
        s = Settings()
        self.assertEqual(s.engine_args()["block_frame"], 7680)
        self.assertEqual(s.engine_args()["cross_fade_overlap_size"], .06)
        self.assertEqual(s.engine_args()["extra_convert_size"], .25)
        self.assertEqual(s.inference_args()["f0_up_key"], 8)
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


if __name__ == "__main__":
    unittest.main()
