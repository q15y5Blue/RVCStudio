"""Download a Mandarin female voice and prepare the same clean clips for RVC and Beatrice.

Runs under the Applio Python (numpy / soundfile / librosa are already there).

--source aishell3  one AISHELL-3 speaker (Apache-2.0), read file by file from the
                   shenyunhang/AISHELL-3 Hugging Face mirror: the official AISHELL/AISHELL-3
                   mirror only holds the first 100 files of every folder. hf-mirror.com is
                   tried first (fast in mainland China), then huggingface.co.
--source csmsc     DataBaker (标贝) CSMSC / BZNSYP single female speaker, the official 2.3 GB
                   RAR from DataBaker's OSS bucket (non-commercial use only). Extracted with an
                   installed 7-Zip / WinRAR, else an official portable 7-Zip (hash-pinned).

Output (under --out):
  wav/<voice>/*.wav           44.1 kHz mono clips, silence-trimmed, level-matched
                              (RVC: --dataset-path <out>/wav/<voice>;
                               Beatrice: a folder holding only <voice>/, see train-voices.ps1)
  manifest-<voice>.json       source, clip count, total minutes
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "shenyunhang/AISHELL-3"
ENDPOINTS = ("https://hf-mirror.com", "https://huggingface.co")
CSMSC_URL = "https://weixinxcxdb.oss-cn-beijing.aliyuncs.com/gwYinPinKu/BZNSYP.rar"
CSMSC_SIZE = 2293620016
SEVEN_ZIP = {"7zr.exe": "https://www.7-zip.org/a/7zr.exe", "7z-x64.exe": "https://www.7-zip.org/a/7z2301-x64.exe"}
SEVEN_ZIP_SHA256 = {"7zr.exe": "ad4c82fadcbdf93c03b4fc440f300509c7d60c5c2f4d183e35d9d70d6957037d",
                    "7z-x64.exe": "26cb6e9f56333682122fafe79dbcdfd51e9f47cc7217dccd29ac6fc33b5598cd"}
TARGET_RMS_DB = -20.0
PEAK = 0.89                     # about -1 dBFS
MIN_SECONDS = 0.8
SENTENCE_SECONDS = 4.5          # rough average, used to spread the pick over a corpus


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "RVCStudio-training"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers


# ---------------------------------------------------------------- AISHELL-3

def list_folder(endpoint, path):
    """All file paths under a dataset folder (the tree API pages 100 entries at a time)."""
    url = f"{endpoint}/api/datasets/{REPO}/tree/main/{path}"
    files = []
    while url:
        body, headers = fetch(url)
        files += [x["path"] for x in json.loads(body) if x.get("type") == "file"]
        link = headers.get("Link", "")
        url = link.split(";")[0].strip("<> ") if 'rel="next"' in link else None
        if url and url.startswith("/"):
            url = endpoint + url
    return files


def pick_endpoint(preferred):
    order = [preferred] if preferred else []
    order += [e for e in ENDPOINTS if e != preferred]
    for endpoint in order:
        try:
            fetch(f"{endpoint}/api/datasets/{REPO}/tree/main", timeout=30)
            return endpoint
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next one"
            log(f"  {endpoint} 不可用：{exc}")
    raise RuntimeError("Hugging Face 与 hf-mirror.com 都无法访问，请检查网络或代理后重试")


def download(endpoint, path, dest: Path, retries=6):
    if dest.is_file() and dest.stat().st_size > 1000:
        return dest
    url = f"{endpoint}/datasets/{REPO}/resolve/main/{path}"
    tmp = dest.with_suffix(".part")
    for attempt in range(retries):
        try:
            body, _ = fetch(url, timeout=120)
            if not body.startswith(b"RIFF"):
                raise RuntimeError("返回内容不是 WAV")
            tmp.write_bytes(body)
            tmp.replace(dest)
            return dest
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                raise RuntimeError(f"下载失败 {path}：{exc}") from exc
            time.sleep(2 + 3 * attempt)


def collect_aishell3(args, raw: Path):
    endpoint = pick_endpoint(args.endpoint)
    log(f"[1/3] 从 {endpoint} 获取 AISHELL-3 {args.speaker} 的文件列表…")
    files = []
    for split in ("train", "test"):
        try:
            files += list_folder(endpoint, f"{split}/wav/{args.speaker}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    files = sorted(f for f in files if f.endswith(".wav"))
    if not files:
        raise SystemExit(f"在 AISHELL-3 中找不到说话人 {args.speaker}")
    if args.max_files:
        files = files[: args.max_files]
    log(f"      共 {len(files)} 句")
    log(f"[2/3] 下载原始录音到 {raw} …（可中断，重跑会跳过已下载的文件）")
    raw.mkdir(parents=True, exist_ok=True)
    done = 0
    with ThreadPoolExecutor(args.workers) as pool:
        jobs = [pool.submit(download, endpoint, f, raw / Path(f).name) for f in files]
        for job in as_completed(jobs):
            job.result()
            done += 1
            if done % 25 == 0 or done == len(files):
                log(f"      {done}/{len(files)}")
    return [raw / Path(f).name for f in files]


# ---------------------------------------------------------------- CSMSC (标贝)

def download_large(url, dest: Path, size, retries=60):
    """Resumable download of one big file (HTTP Range)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == size:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = time.monotonic()
    for attempt in range(retries):
        have = tmp.stat().st_size if tmp.exists() else 0
        if have >= size:
            break
        req = urllib.request.Request(url, headers={"User-Agent": "RVCStudio-training", "Range": f"bytes={have}-"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                if have and r.status != 206:
                    raise RuntimeError("服务器没有按断点续传返回")
                with tmp.open("ab") as out:
                    while True:
                        chunk = r.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        have += len(chunk)
                        if time.monotonic() - last > 10:
                            log(f"      {have / 1024 ** 3:.2f} / {size / 1024 ** 3:.2f} GB")
                            last = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            log(f"      连接中断（{exc}），{5 + attempt} 秒后续传…")
            time.sleep(5 + attempt)
    if not tmp.exists() or tmp.stat().st_size != size:
        raise RuntimeError("BZNSYP.rar 下载不完整，请重新运行以续传")
    tmp.replace(dest)
    return dest


def sha256(path: Path):
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def portable_7zip(tools: Path):
    """Full 7-Zip (with RAR support) without installing: the official x64 installer is a
    7z archive, which the official stand-alone 7zr.exe can unpack. Both downloads are pinned."""
    seven = tools / "7z" / "7z.exe"
    if seven.is_file():
        return seven
    tools.mkdir(parents=True, exist_ok=True)
    for name, url in SEVEN_ZIP.items():
        dest = tools / name
        if not dest.is_file():
            log(f"      下载 {url}")
            body, _ = fetch(url, timeout=120)
            dest.write_bytes(body)
        if sha256(dest) != SEVEN_ZIP_SHA256[name]:
            dest.unlink()
            raise RuntimeError(f"{name} 的校验值与预期不符（7-Zip 可能已发布新版本）。"
                               "请从 https://www.7-zip.org 安装 7-Zip 后重新运行")
    subprocess.run([str(tools / "7zr.exe"), "x", "-y", f"-o{tools / '7z'}", str(tools / "7z-x64.exe"),
                    "7z.exe", "7z.dll"], check=True, stdout=subprocess.DEVNULL)
    return seven


def extract_rar(archive: Path, dest: Path):
    """BZNSYP.rar stores its WAVs with WinRAR's audio filter. Windows' tar.exe (libarchive
    3.5) cannot decode that and silently writes all-zero files, so it is never used."""
    dest.mkdir(parents=True, exist_ok=True)
    candidates = [shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe",
                  r"C:\Program Files\WinRAR\UnRAR.exe", r"C:\Program Files (x86)\WinRAR\UnRAR.exe"]
    tool = next((Path(c) for c in candidates if c and Path(c).is_file()), None)
    if tool is None:
        log("      没有找到 7-Zip / WinRAR，下载官方 7-Zip（免安装）…")
        tool = portable_7zip(dest.parent / "tools")
    log(f"      解压工具：{tool}")
    if tool.name.lower() == "unrar.exe":
        cmd = [str(tool), "x", "-y", str(archive), str(dest) + os.sep]
    else:
        cmd = [str(tool), "x", "-y", f"-o{dest}", str(archive)]
    if subprocess.run(cmd).returncode != 0:
        raise RuntimeError("解压 BZNSYP.rar 失败：文件可能不完整，删除后重新运行即可重新下载")
    import soundfile as sf
    sample = next(dest.rglob("*.wav"), None)
    if sample is None or sf.info(sample).frames == 0:
        raise RuntimeError("解压出的音频无效，请安装 7-Zip（https://www.7-zip.org）后重新运行")


def collect_csmsc(args, work: Path):
    archive = Path(args.archive) if args.archive else work / "BZNSYP.rar"
    if not args.archive:
        log(f"[1/3] 下载标贝 CSMSC（2.3 GB，可中断续传）到 {archive}")
        download_large(CSMSC_URL, archive, CSMSC_SIZE)
    extracted = work / "BZNSYP"
    if any(extracted.rglob("*.wav")):
        log("[2/3] 已解压，跳过")
    else:
        log(f"[2/3] 解压到 {extracted}（约 2.6 GB）")
        extract_rar(archive, extracted)
    files = sorted(extracted.rglob("*.wav"))
    if args.max_files:
        files = files[: args.max_files]
    log(f"      共 {len(files)} 句")
    return files


# ---------------------------------------------------------------- common

def clean(src: Path, dest: Path):
    """Trim edge silence, match loudness, keep 44.1 kHz mono. Returns seconds or 0 if dropped."""
    import librosa
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(src, dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    if sr != 44100:
        y = librosa.resample(y, orig_sr=sr, target_sr=44100)
        sr = 44100
    y, _ = librosa.effects.trim(y, top_db=40, frame_length=2048, hop_length=512)
    pad = int(0.05 * sr)
    y = np.pad(y, (pad, pad))                    # keep a short natural lead-in/out
    if len(y) < MIN_SECONDS * sr or np.abs(y).max() > 0.999:
        return 0.0                               # too short, or clipped in the source
    rms = np.sqrt(np.mean(y ** 2)) + 1e-9
    y = y * (10 ** (TARGET_RMS_DB / 20) / rms)
    if np.abs(y).max() > PEAK:
        y = y * (PEAK / np.abs(y).max())
    sf.write(dest, y, sr, subtype="PCM_16")
    return len(y) / sr


def spread(files, max_minutes):
    """An even spread over the corpus (text styles are grouped), about max_minutes long."""
    if not max_minutes:
        return files
    want = int(max_minutes * 60 / SENTENCE_SECONDS * 1.3)   # margin for dropped / short clips
    if want >= len(files):
        return files
    step = len(files) / want
    return [files[int(i * step)] for i in range(want)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["aishell3", "csmsc"], default="aishell3")
    ap.add_argument("--speaker", default="SSB0565", help="AISHELL-3 说话人编号，如 SSB0565")
    ap.add_argument("--voice", default=None, help="输出声音名（默认 aishell3_<speaker> / csmsc_baker）")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT", ENDPOINTS[0]))
    ap.add_argument("--archive", default=None, help="已下载的 BZNSYP.rar 路径（csmsc，可选）")
    ap.add_argument("--max-minutes", type=float, default=30, help="最多保留多少分钟（0 = 全部）")
    ap.add_argument("--max-files", type=int, default=0, help="只取前 N 个文件（调试用）")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    if args.source == "csmsc":
        voice = args.voice or "csmsc_baker"
        sources = collect_csmsc(args, args.out / "raw" / "csmsc")
        label = "DataBaker CSMSC / BZNSYP (non-commercial use only)"
    else:
        voice = args.voice or f"aishell3_{args.speaker}"
        sources = collect_aishell3(args, args.out / "raw" / args.speaker)
        label = f"{REPO} (AISHELL-3 {args.speaker}, Apache-2.0)"

    wav = args.out / "wav" / voice
    if wav.exists():
        shutil.rmtree(wav)               # rebuild: a smaller --max-minutes must not keep old clips
    wav.mkdir(parents=True)
    log(f"[3/3] 去首尾静音、统一响度，写入 {wav}")
    total, kept = 0.0, []
    for src in spread(sources, args.max_minutes):
        seconds = clean(src, wav / src.name)
        if seconds:
            total += seconds
            kept.append(src.name)
        else:
            (wav / src.name).unlink(missing_ok=True)
        if args.max_minutes and total >= args.max_minutes * 60:
            break
    manifest = dict(source=label, voice=voice, clips=len(kept), minutes=round(total / 60, 1), dataset=str(wav))
    (args.out / f"manifest-{voice}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"      保留 {len(kept)} 句，共 {total / 60:.1f} 分钟")
    if total < 10 * 60:
        log("      注意：不足 10 分钟，训练效果可能偏差")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
