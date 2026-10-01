"""training/ helpers that run without a GPU, network or torch."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TRAINING = Path(__file__).resolve().parents[1] / "training"
sys.path.insert(0, str(TRAINING))
import prepare_dataset

HELPERS = TRAINING / "train_helpers.py"


def helper(*args):
    r = subprocess.run([sys.executable, str(HELPERS), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout


class TrainingHelperTests(unittest.TestCase):
    def test_collect_rvc_takes_highest_epoch_not_lexical(self):
        with tempfile.TemporaryDirectory() as d:
            logs = Path(d, "runtime", "logs", "voice")
            logs.mkdir(parents=True)
            for name in ("voice_10e_100s.pth", "voice_90e_900s.pth", "voice_200e_2000s.pth", "G_2000.pth",
                         "added_IVF1_Flat_nprobe_1_voice_v2.index"):
                (logs / name).write_text(name)
            out = json.loads(helper("collect-rvc", "--runtime", Path(d, "runtime"), "--model", "voice",
                                    "--dest", Path(d, "models", "voice")).strip().splitlines()[-1])
            self.assertEqual(out["epoch"], 200)
            self.assertEqual(Path(out["pth"]).read_text(), "voice_200e_2000s.pth")
            self.assertTrue(Path(out["index"]).is_file())

    def test_configure_studio_keeps_other_settings_and_backs_up(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d, "settings.json")
            settings.write_text(json.dumps({"pitch": 10, "model": "old.pth", "index": "old.index"}), encoding="utf-8")
            helper("configure-studio", "--settings", settings, "--beatrice", "new.pt.gz")
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["pitch"], 10)
            self.assertEqual(data["model"], "old.pth")          # no --pth: RVC model untouched
            self.assertEqual((data["beatrice_model"], data["beatrice_speaker"]), ("new.pt.gz", 0))
            self.assertEqual(len(list(Path(d).glob("settings.backup-*.json"))), 1)

    def test_beatrice_config_disables_amp_for_pascal(self):
        with tempfile.TemporaryDirectory() as d:
            assets = Path(d, "trainer", "assets")
            assets.mkdir(parents=True)
            (assets / "default_config.json").write_text(json.dumps(
                {"use_amp": True, "batch_size": 8, "n_steps": 10000, "save_interval": 2000,
                 "evaluation_interval": 2000, "num_workers": 16}), encoding="utf-8")
            helper("beatrice-config", "--trainer", Path(d, "trainer"), "--out", Path(d, "cfg.json"),
                   "--amp", 0, "--batch", 4, "--steps", 1500, "--workers", 2)
            cfg = json.loads(Path(d, "cfg.json").read_text(encoding="utf-8"))
            self.assertEqual((cfg["use_amp"], cfg["batch_size"], cfg["n_steps"], cfg["num_workers"]), (False, 4, 1500, 2))
            self.assertEqual(cfg["save_interval"], 1500)        # short runs still save a checkpoint

    def test_missing_rvc_weights_fail_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "runtime", "logs", "voice").mkdir(parents=True)
            with self.assertRaises(AssertionError):
                helper("collect-rvc", "--runtime", Path(d, "runtime"), "--model", "voice", "--dest", Path(d, "m"))


class DatasetTests(unittest.TestCase):
    def test_spread_covers_whole_corpus(self):
        files = list(range(10000))
        picked = prepare_dataset.spread(files, 30)
        self.assertLess(len(picked), 600)
        self.assertGreater(picked[-1], 9900)                  # reaches the end of the corpus
        self.assertEqual(prepare_dataset.spread(files[:300], 30), files[:300])
        self.assertEqual(prepare_dataset.spread(files, 0), files)


if __name__ == "__main__":
    unittest.main()
