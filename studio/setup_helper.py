"""Console helper used by the single installer. Engine runs without elevation."""
from __future__ import annotations
import argparse
import configparser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback

import driver
import runtime
from settings import Settings, data_dir

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# Bundled natural Mandarin female voices (folder -> (pth, index)).
BUNDLED_MODELS = {
    "ChineseFemale": ("ChineseFemale.pth", "ChineseFemale.index"),
    "ChineseFemale_HQ": ("ChineseFemale_HQ.pth", "ChineseFemale_HQ.index"),
}
PRIMARY_MODEL = "ChineseFemale"


def deploy_bundled_models(root: Path):
    """Copy the models embedded in the offline helper into the user data dir.

    Returns {folder: dest_dir} for every model actually deployed, or None when
    no models are bundled (e.g. the online build or unit-test environment).
    """
    src_base = HERE / "models"
    if not src_base.is_dir():
        return None
    models_root = root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    deployed = {}
    for folder, (pth_name, idx_name) in BUNDLED_MODELS.items():
        src = src_base / folder
        if not (src / pth_name).is_file():
            continue
        dest = models_root / folder
        dest.mkdir(parents=True, exist_ok=True)
        for name in (pth_name, idx_name):
            sp = src / name
            if sp.is_file():
                dp = dest / name
                if not dp.exists() or dp.stat().st_size != sp.stat().st_size:
                    shutil.copy2(sp, dp)
        deployed[folder] = dest
    readme = src_base / "模型说明.txt"
    if readme.is_file():
        shutil.copy2(readme, models_root / "模型说明.txt")
    return deployed


def result_file(path, **data):
    ini = configparser.ConfigParser(interpolation=None)
    ini["Result"] = {key: str(value).replace("\n", " ").replace("\r", " ") for key, value in data.items()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-16") as out:
        ini.write(out, space_around_delimiters=False)


def progress(text):
    # ASCII transport avoids console-codepage ambiguity in the Inno callback.
    if "合并" in text:
        stage = "MERGE"
    elif "下载运行环境" in text:
        stage = "DOWNLOAD"
    elif "校验" in text:
        stage = "VERIFY"
    elif "安装运行环境" in text:
        stage = "EXTRACT"
    else:
        stage = "PREPARE"
    match = re.search(r"([\d.]+)%", text)
    percent = int(float(match.group(1))) if match else 0
    print(f"RVC|{stage}|{percent}", flush=True)


class FileCancellation:
    def __init__(self, path):
        self.path = Path(path) if path else None
    def is_set(self):
        return bool(self.path and self.path.exists())


def prepare_engine(root: Path, cancel, archive=None, chunk_dir=None):
    manifest = json.loads((HERE / "engine-manifest.json").read_text(encoding="utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    target = root / "runtime" / ("Applio-" + runtime.VERSION)
    offline_zip = None
    if target.exists():
        runtime.verify_engine(target, manifest)
    else:
        if chunk_dir:
            cache = root / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            offline_zip = cache / runtime.ARCHIVE_NAME
            print("RVC|MERGE|0", flush=True)
            package = runtime.reassemble_chunks(chunk_dir, offline_zip, progress, cancel, consume=True)
        elif archive:
            package = Path(archive)
        else:
            package = runtime.download(root / "cache", progress, cancel)
        runtime.install(package, target, manifest, progress, cancel)
        # Offline install: reclaim the ~4.9 GB merged ZIP once extraction succeeded.
        if offline_zip is not None:
            offline_zip.unlink(missing_ok=True)
    runtime.check_cancel(cancel)
    print("RVC|DEPENDENCIES|0", flush=True)
    # Runs in the unprivileged installer process. Import the actual engine, not a stub.
    # Isolate from this frozen helper: never let its _MEIPASS OpenSSL/Python DLLs shadow
    # the Applio conda env (otherwise "DLL load failed while importing _ssl").
    env = os.environ.copy()
    envdir = target / "env"
    prepend = [envdir, envdir / "Library" / "bin", envdir / "Library" / "mingw-w64" / "bin",
               envdir / "Library" / "usr" / "bin", envdir / "Scripts", target]
    existing = [str(p) for p in prepend if p.exists()]
    here_norm = os.path.normcase(os.path.normpath(str(HERE)))
    kept = []
    for part in env.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        norm = os.path.normcase(os.path.normpath(part))
        if norm == here_norm or norm.startswith(here_norm + os.sep) \
                or os.path.basename(norm).lower().startswith("_mei"):
            continue
        kept.append(part)
    env["PATH"] = os.pathsep.join(existing + kept)
    env["PYTHONHOME"] = str(envdir)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    check = ("import os,sys;sys.path.insert(0,os.getcwd());"
             "import torch,sounddevice,soundfile,faiss;from rvc.realtime.core import VoiceChanger;"
             "print('RVC_ENGINE_IMPORT_OK')")
    if getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        check_result = subprocess.run([str(target / "env/python.exe"), "-c", check],
              cwd=target, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
              timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        if getattr(sys, "frozen", False):
            ctypes.windll.kernel32.SetDllDirectoryW(str(HERE))
    (root / "engine-setup.log").write_text(check_result.stdout + "\n" + check_result.stderr, encoding="utf-8")
    if check_result.returncode or "RVC_ENGINE_IMPORT_OK" not in check_result.stdout:
        raise RuntimeError("变声引擎依赖检测失败，安装未完成。详情见数据目录 engine-setup.log：" + check_result.stderr[-800:])
    runtime.check_cancel(cancel)
    config = Settings.load(root / "settings.json")
    config.runtime = str(target)
    # Deploy bundled offline voices and preselect the primary natural female model.
    deployed = deploy_bundled_models(root)
    if deployed and PRIMARY_MODEL in deployed:
        primary_dir = deployed[PRIMARY_MODEL]
        pth_name, idx_name = BUNDLED_MODELS[PRIMARY_MODEL]
        # Never overwrite a model the user already chose on repair/reinstall.
        if (not config.model) or (not Path(config.model).is_file()):
            config.model = str(primary_dir / pth_name)
            config.index = str(primary_dir / idx_name)
    config.save(root / "settings.json")
    print("RVC|READY|100", flush=True)
    return {"runtime": str(target), "dependencies": True,
            "bundled_models": sorted(deployed.keys()) if deployed else []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("engine", "driver", "driver-status", "verify-driver", "self-test"))
    parser.add_argument("--data-root")
    parser.add_argument("--archive")
    parser.add_argument("--engine-chunk-dir", dest="chunk_dir")
    parser.add_argument("--result", required=True)
    parser.add_argument("--cancel-file")
    args = parser.parse_args()
    try:
        if args.command == "engine":
            details = prepare_engine(Path(args.data_root) if args.data_root else data_dir(),
                                     FileCancellation(args.cancel_file), args.archive, args.chunk_dir)
        elif args.command == "driver":
            details = driver.install(HERE / driver.ZIP_NAME)
        elif args.command == "driver-status":
            details = driver.status()
        elif args.command == "verify-driver":
            driver.verify_package(HERE / driver.ZIP_NAME)
            details = {"package_hash_valid": True}
        else:
            details = {"helper_ready": True, "manifest": (HERE / "engine-manifest.json").is_file(),
                       "driver_bundled": (HERE / driver.ZIP_NAME).is_file()}
        result_file(args.result, status="ok", reboot="1" if details.get("reboot_required") else "0",
                    details=json.dumps(details, ensure_ascii=False))
        return 0
    except Exception as exc:
        result_file(args.result, status="cancelled" if isinstance(exc, runtime.Cancelled) else "error", error=str(exc))
        traceback.print_exc()
        return 2 if isinstance(exc, runtime.Cancelled) else 1


if __name__ == "__main__":
    raise SystemExit(main())
