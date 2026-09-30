import configparser
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "studio"))
import driver
import runtime
import setup_helper
from routing import cable_pair, restore_device_key


class IntegrationTests(unittest.TestCase):
    def test_corrupt_completed_download_can_be_retried(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            partial = cache / (runtime.ARCHIVE_NAME + '.part')
            partial.write_bytes(b'bad!')
            with patch.object(runtime, 'ARCHIVE_SIZE', 4), \
                 patch.object(runtime, 'ARCHIVE_SHA256', hashlib.sha256(b'good').hexdigest()):
                with self.assertRaisesRegex(ValueError, '已清除'):
                    runtime.download(cache)
                self.assertFalse(partial.exists())
                response = io.BytesIO(b'good')
                response.status = 200
                response.headers = {}
                with patch.object(runtime.urllib.request, 'urlopen', return_value=response):
                    self.assertEqual(runtime.download(cache).read_bytes(), b'good')

    def test_partial_download_resumes_at_saved_offset(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / (runtime.ARCHIVE_NAME + '.part')).write_bytes(b'go')
            response = io.BytesIO(b'od')
            response.status = 206
            response.headers = {'Content-Range': 'bytes 2-3/4'}
            with patch.object(runtime, 'ARCHIVE_SIZE', 4), \
                 patch.object(runtime, 'ARCHIVE_SHA256', hashlib.sha256(b'good').hexdigest()), \
                 patch.object(runtime.urllib.request, 'urlopen', return_value=response) as request:
                self.assertEqual(runtime.download(cache).read_bytes(), b'good')
                self.assertEqual(request.call_args.args[0].get_header('Range'), 'bytes=2-')

    def test_official_windows_crlf_matches_pinned_engine(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "env").mkdir()
            (root / "env/python.exe").touch()
            (root / "core.py").write_bytes(b"import os\r\nprint('ok')\r\n")
            runtime.verify_engine(root, {"core.py": hashlib.sha256(b"import os\nprint('ok')\n").hexdigest()})

    def test_pair_prefers_wasapi_and_ignores_cable_a(self):
        def dev(n, name, host, inputs, outputs):
            return dict(id=n, name=name, host=host, inputs=inputs, outputs=outputs, key=f"{host} | {name} | {n}")
        devices = [dev(1, "CABLE-A Input (VB-Audio Cable A)", "Windows WASAPI", 0, 2),
                   dev(2, "CABLE Input (VB-Audio Virtual Cable)", "MME", 0, 2),
                   dev(3, "CABLE Input (VB-Audio Virtual Cable)", "Windows WASAPI", 0, 2),
                   dev(4, "CABLE Output (VB-Audio Virtual Cable)", "Windows WASAPI", 2, 0)]
        render, capture = cable_pair(devices)
        self.assertEqual((render["id"], capture["id"]), (3, 4))
        self.assertEqual(restore_device_key(devices, "Windows WASAPI | CABLE Input (VB-Audio Virtual Cable) | 9", "outputs"), render["key"])
        self.assertEqual(cable_pair(devices[:1]), (None, None))

    def test_corrupt_driver_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "driver.zip"
            path.write_bytes(b"incorrect")
            with self.assertRaises(ValueError):
                driver.verify_package(path)

    def test_driver_missing_device_is_not_success(self):
        with patch.object(driver.ctypes.windll.shell32, "IsUserAnAdmin", return_value=1), \
             patch.object(driver, "status", return_value={"installed": False, "healthy": False, "devices": [], "reboot_required": False}), \
             patch.object(driver, "verify_signatures", return_value=[]), \
             patch.object(driver.subprocess, "run") as run:
            run.return_value.returncode = 0
            with self.assertRaisesRegex(RuntimeError, "未发现设备"):
                driver.install(ROOT / "vendor/vbcable/VBCABLE_Driver_Pack45.zip")

    def test_existing_driver_is_reused(self):
        with patch.object(driver.ctypes.windll.shell32, "IsUserAnAdmin", return_value=1), \
             patch.object(driver, "status", return_value={"installed": True, "healthy": True, "devices": [{}], "reboot_required": False}), \
             patch.object(driver.subprocess, "run") as run:
            result = driver.install(ROOT / "vendor/vbcable/VBCABLE_Driver_Pack45.zip")
            self.assertFalse(result["changed"])
            run.assert_not_called()

    def test_ini_result_handles_unicode_and_percent(self):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "result.ini"
            setup_helper.result_file(result, status="error", error="下载 50% 失败\n请重试")
            ini = configparser.ConfigParser(interpolation=None)
            ini.read(result, encoding="utf-16")
            self.assertEqual(ini.get("Result", "error"), "下载 50% 失败 请重试")

    def test_failed_dependency_check_does_not_mark_engine_ready(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime" / ("Applio-" + runtime.VERSION)).mkdir(parents=True)
            with patch.object(runtime, "verify_engine"), patch.object(setup_helper.subprocess, "run") as run:
                run.return_value.returncode = 1
                run.return_value.stdout = ""
                run.return_value.stderr = "missing torch DLL"
                with self.assertRaisesRegex(RuntimeError, "依赖检测失败"):
                    setup_helper.prepare_engine(root, setup_helper.FileCancellation(None))
            self.assertFalse((root / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
