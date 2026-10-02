"""Small steps of train-voices.ps1 that are easier in Python. Runs under the Applio Python.

  gpu-check                     torch/CUDA smoke test on the GPU (JSON on stdout)
  ensure-rvc-assets             make sure RVC pretrained G/D 40k, ContentVec and RMVPE exist
  set-precision                 training precision in Applio's assets/config.json
  collect-rvc                   copy the trained .pth (+ .index) to the RVC Studio model folder
  beatrice-config               write a beatrice-trainer config tuned for this GPU
  collect-beatrice              copy checkpoint_latest.pt.gz to the RVC Studio model folder
  configure-studio              point RVC Studio at the new models (backs up settings.json)
  compare                       convert one male test clip with every trained voice x engine,
                                plus an index.html to listen to them side by side
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

APPLIO_RESOURCES = "{endpoint}/IAHispano/Applio/resolve/main/Resources/{path}"
RVC_ASSETS = {
    "pretrained_v2/f0G40k.pth": "rvc/models/pretraineds/hifi-gan/f0G40k.pth",
    "pretrained_v2/f0D40k.pth": "rvc/models/pretraineds/hifi-gan/f0D40k.pth",
    "embedders/contentvec/pytorch_model.bin": "rvc/models/embedders/contentvec/pytorch_model.bin",
    "embedders/contentvec/config.json": "rvc/models/embedders/contentvec/config.json",
    "predictors/rmvpe.pt": "rvc/models/predictors/rmvpe.pt",
}


def log(msg):
    print(msg, flush=True)


def progress(msg):
    """For commands whose stdout is captured by train-voices.ps1: keep progress visible."""
    print(msg, file=sys.stderr, flush=True)


def gpu_check(_):
    report = {"ok": False}
    try:
        import torch
        report["torch"] = torch.__version__
        report["cuda_build"] = torch.version.cuda
        report["available"] = torch.cuda.is_available()
        if report["available"]:
            major, minor = torch.cuda.get_device_capability(0)
            report.update(name=torch.cuda.get_device_name(0), capability=f"{major}.{minor}",
                          vram_gb=round(torch.cuda.get_device_properties(0).total_memory / 1024 ** 3, 1),
                          arch_list=torch.cuda.get_arch_list())
            x = torch.randn(64, 64, device="cuda")
            float((x @ x).sum())                         # fails with "no kernel image" on unsupported GPUs
            report["ok"] = True
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc).splitlines()[0][:300]
    print(json.dumps(report, ensure_ascii=False))


def download(url, dest: Path, retries=5):
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RVCStudio-training"})
            with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as out:
                shutil.copyfileobj(r, out, 4 * 1024 * 1024)
            tmp.replace(dest)
            return
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            if attempt == retries - 1:
                raise RuntimeError(f"下载失败 {url}：{exc}") from exc
            time.sleep(3 + 5 * attempt)


def ensure_rvc_assets(args):
    runtime = Path(args.runtime)
    for remote, local in RVC_ASSETS.items():
        dest = runtime / local
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        for endpoint in (args.endpoint, "https://huggingface.co"):
            try:
                log(f"  下载 {remote} …")
                download(APPLIO_RESOURCES.format(endpoint=endpoint, path=remote), dest)
                break
            except RuntimeError as exc:
                log(f"  {exc}")
        else:
            # Without these Applio silently trains from scratch (pretrained_selector returns "").
            raise SystemExit(f"缺少 {local}，无法继续训练 RVC")
    log("  RVC 预训练底模、ContentVec、RMVPE 均已就绪")


def set_precision(args):
    path = Path(args.runtime) / "assets" / "config.json"
    template = Path(args.runtime) / "assets" / "config_template.json"
    if path.is_file():
        config = json.loads(path.read_text(encoding="utf-8"))
    elif template.is_file():
        config = json.loads(template.read_text(encoding="utf-8"))
    else:
        config = {}
    config["precision"] = args.value
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  Applio 训练精度：{args.value}")


def epoch_of(path: Path):
    m = re.search(r"_(\d+)e_(\d+)s\.pth$", path.name)
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def collect_rvc(args):
    logs = Path(args.runtime) / "logs" / args.model
    weights = sorted((p for p in logs.glob(f"{args.model}_*e_*s.pth")), key=epoch_of)
    if not weights:
        raise SystemExit(f"在 {logs} 中没有找到训练好的 {args.model}_*e_*s.pth")
    indexes = sorted(logs.glob("*.index"), key=lambda p: p.stat().st_mtime)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    pth = dest / f"{args.model}.pth"
    shutil.copy2(weights[-1], pth)
    result = {"pth": str(pth), "epoch": epoch_of(weights[-1])[0]}
    if indexes:
        idx = dest / f"{args.model}.index"
        shutil.copy2(indexes[-1], idx)
        result["index"] = str(idx)
    log(json.dumps(result, ensure_ascii=False))


def beatrice_config(args):
    trainer = Path(args.trainer)
    config = json.loads((trainer / "assets" / "default_config.json").read_text(encoding="utf-8"))
    config.update(
        use_amp=bool(args.amp),                     # fp16 AMP is very slow on Pascal (GTX 10xx)
        batch_size=args.batch,
        n_steps=args.steps,
        save_interval=min(2000, args.steps),
        evaluation_interval=min(2000, args.steps),
        num_workers=args.workers,                   # Windows spawns a process per worker
    )
    Path(args.out).write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  Beatrice 配置：batch {args.batch}，{args.steps} 步，AMP {'开' if args.amp else '关'}，{args.workers} 个读取进程")


def collect_beatrice(args):
    src = Path(args.out) / "checkpoint_latest.pt.gz"
    if not src.is_file():
        raise SystemExit(f"没有找到 {src}，Beatrice 训练可能未完成")
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"checkpoint_{args.model}.pt.gz"
    shutil.copy2(src, target)
    log(json.dumps({"beatrice": str(target)}, ensure_ascii=False))


def configure_studio(args):
    path = Path(args.settings)
    settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if path.is_file():
        shutil.copy2(path, path.with_name(f"settings.backup-{time.strftime('%Y%m%d-%H%M%S')}.json"))
    if args.pth:
        settings["model"] = args.pth
        settings["index"] = args.index or ""
    if args.beatrice:
        settings["beatrice_model"] = args.beatrice
        settings["beatrice_speaker"] = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  已写入 RVC Studio 设置：{path}")


def resumable_download(url, dest: Path, retries=40):
    """HTTP Range download that survives stalls (pip restarts a 2.9 GB wheel from zero)."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    size = None
    last = time.monotonic()
    for attempt in range(retries):
        have = tmp.stat().st_size if tmp.exists() else 0
        if size is not None and have >= size:
            break
        headers = {"User-Agent": "RVCStudio-training"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                if have and r.status != 206:
                    tmp.unlink()                      # server ignored Range: start over
                    continue
                total = r.headers.get("Content-Range", "").rpartition("/")[2] or r.headers.get("Content-Length")
                size = int(total) if total and total.isdigit() else size
                with tmp.open("ab") as out:
                    while True:
                        chunk = r.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        have += len(chunk)
                        if time.monotonic() - last > 10 and size and size > 50 * 1024 ** 2:
                            progress(f"    {have / 1024 ** 3:.2f} / {size / 1024 ** 3:.2f} GB")
                            last = time.monotonic()
            if size is None or have >= size:
                break
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise                                 # not on this server: try the next one
            if exc.code == 416 and have:              # .part already complete: the SHA-256 check decides
                size = have
                break
            progress(f"    连接中断（{exc}），{min(30, 3 + attempt)} 秒后续传…")
            time.sleep(min(30, 3 + attempt))
        except Exception as exc:  # noqa: BLE001
            progress(f"    连接中断（{exc}），{min(30, 3 + attempt)} 秒后续传…")
            time.sleep(min(30, 3 + attempt))
    if not tmp.exists() or (size is not None and tmp.stat().st_size != size):
        raise RuntimeError(f"下载不完整：{url}")
    tmp.replace(dest)


def fetch_wheels(args):
    """Download PyTorch wheels for this Python with resume, verified against pytorch.org's
    published SHA-256. Prints the local paths (one per line) for pip to install."""
    import hashlib
    import urllib.parse
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in args.pkg:
        name, version = spec.split("==")
        filename = f"{name}-{version}-{tag}-{tag}-win_amd64.whl"
        quoted = urllib.parse.quote(filename)
        page = urllib.request.urlopen(f"{args.index}/{name}/", timeout=120).read().decode("utf-8", "replace")
        m = re.search(re.escape(quoted) + r"#sha256=([0-9a-f]{64})", page)
        if not m:
            raise SystemExit(f"PyTorch 官方索引里没有 {filename}")
        expected = m.group(1)
        target = dest / filename
        for url in [f"{args.mirror}/{quoted}"] * bool(args.mirror) + [f"https://download.pytorch.org/whl/{args.index.rsplit('/', 1)[-1]}/{quoted}"]:
            if target.is_file():
                break
            progress(f"  下载 {filename}：{url.split('/')[2]}")
            try:
                resumable_download(url, target)
            except urllib.error.HTTPError as exc:
                progress(f"    {url.split('/')[2]} 没有这个文件（{exc.code}），换下一个下载源")
        if not target.is_file():
            raise SystemExit(f"无法下载 {filename}")
        h = hashlib.sha256()
        with target.open("rb") as f:
            for block in iter(lambda: f.read(1 << 22), b""):
                h.update(block)
        if h.hexdigest() != expected:
            target.unlink()
            raise SystemExit(f"{filename} 校验失败（文件损坏），已删除，请重新运行")
        progress(f"  {filename} 校验通过")
        paths.append(str(target))
    print("\n".join(paths))


HF_ENDPOINTS = ("https://hf-mirror.com", "https://huggingface.co")
COMPLETE_MARKER = ".studio-complete"


def file_sha256(path: Path):
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def hf_tree(endpoint, repo, revision):
    url = f"{endpoint}/api/models/{repo}/tree/{revision}?recursive=true"
    files = []
    while url:
        req = urllib.request.Request(url, headers={"User-Agent": "RVCStudio-training"})
        with urllib.request.urlopen(req, timeout=60) as r:
            files += [x for x in json.load(r) if x.get("type") == "file"]
            link = r.headers.get("Link", "")
        url = link.split(";")[0].strip("<> ") if 'rel="next"' in link else None
        if url and url.startswith("/"):
            url = endpoint + url
    return files


def fetch_hf_repo(args):
    """Download a Hugging Face model repo file by file over plain /resolve/ URLs.

    huggingface_hub's xet transfer can hang at 0 bytes behind hf-mirror.com, which only
    proxies the normal file URLs. Each LFS file is checked against its SHA-256 from the
    tree listing; each file falls back to the other endpoint on failure."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import urllib.parse
    dest = Path(args.dest)
    marker = dest / COMPLETE_MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == args.revision:
        log("  已下载完整，跳过")
        return
    endpoints = [args.endpoint] + [e for e in HF_ENDPOINTS if e != args.endpoint]
    files = None
    for endpoint in endpoints:
        try:
            files = hf_tree(endpoint, args.repo, args.revision)
            break
        except Exception as exc:  # noqa: BLE001
            log(f"  {endpoint} 无法获取文件列表：{exc}")
    if not files:
        raise SystemExit(f"无法获取 {args.repo} 的文件列表，请检查网络")
    if args.only:
        files = [f for f in files if any(f["path"].startswith(prefix) for prefix in args.only)]
    total = sum(f["size"] for f in files)
    log(f"  {len(files)} 个文件，共 {total / 1024 ** 2:.0f} MB")

    def done(entry, target: Path):
        if not target.is_file() or target.stat().st_size != entry["size"]:
            return False
        oid = (entry.get("lfs") or {}).get("oid")
        return oid is None or file_sha256(target) == oid

    def get(entry):
        target = dest / entry["path"]
        if done(entry, target):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        quoted = urllib.parse.quote(entry["path"])
        errors = []
        for endpoint in endpoints:
            try:
                resumable_download(f"{endpoint}/{args.repo}/resolve/{args.revision}/{quoted}", target)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{endpoint}: {exc}")
                continue
            if done(entry, target):
                return
            target.unlink(missing_ok=True)
            errors.append(f"{endpoint}: 校验失败")
        raise RuntimeError(f"{entry['path']} 下载失败（{'; '.join(errors)}）")

    finished = 0
    with ThreadPoolExecutor(args.workers) as pool:
        jobs = [pool.submit(get, f) for f in files]
        for job in as_completed(jobs):
            job.result()
            finished += 1
            if finished % 200 == 0 or finished == len(files):
                log(f"  {finished}/{len(files)}")
    if not args.only:
        marker.write_text(args.revision, encoding="utf-8")
    log("  全部文件校验通过")


MALE_SAMPLE_SPEAKER = "SSB0710"     # AISHELL-3, adult northern male: stand-in for "your voice"


def male_sample(dest: Path, endpoint):
    """Join three of the longest sentences of an AISHELL-3 male speaker into one test file."""
    import numpy as np
    import soundfile as sf
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import prepare_dataset as prep
    endpoint = prep.pick_endpoint(endpoint)
    url = f"{endpoint}/api/datasets/{prep.REPO}/tree/main/train/wav/{MALE_SAMPLE_SPEAKER}"
    entries = []
    while url:
        body, headers = prep.fetch(url)
        entries += [x for x in json.loads(body) if x.get("type") == "file"]
        link = headers.get("Link", "")
        url = link.split(";")[0].strip("<> ") if 'rel="next"' in link else None
    longest = sorted(entries, key=lambda x: -x.get("size", 0))[:3]
    parts = []
    for entry in longest:
        local = dest.parent / "_source" / Path(entry["path"]).name
        local.parent.mkdir(parents=True, exist_ok=True)
        prep.download(endpoint, entry["path"], local)
        y, sr = sf.read(local, dtype="float32", always_2d=True)
        parts += [y.mean(axis=1), np.zeros(int(0.4 * sr), dtype=np.float32)]
    sf.write(dest, np.concatenate(parts), sr, subtype="PCM_16")


def convert(runtime, worker, config: dict, source, output):
    """Run RVC Studio's worker.py once. Its stdin must stay open: EOF means "stop"."""
    import subprocess
    session = Path(output).with_suffix(".session.json")
    session.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", str(worker), "offline", "--runtime", str(runtime),
                             "--config", str(session), "--input", str(source), "--output", str(output)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    error = ""
    for line in proc.stdout:
        if line.startswith("@RVC@"):
            event = json.loads(line[5:])
            if event["event"] == "error":
                error = event["text"]
    proc.wait()
    proc.stdin.close()
    session.unlink(missing_ok=True)
    if proc.returncode or not Path(output).is_file():
        raise RuntimeError(error or f"worker.py 退出码 {proc.returncode}")


def compare(args):
    runtime, models = Path(args.runtime), Path(args.models)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    source = Path(args.source) if args.source else out / "source-male.wav"
    if not source.is_file():
        log(f"  下载 AISHELL-3 男声 {MALE_SAMPLE_SPEAKER} 作为测试输入…")
        male_sample(source, args.endpoint)
    base = {"runtime": str(runtime), "pitch": args.pitch, "formant": args.formant,
            "index_rate": 0.0, "protect": 0.33, "volume_envelope": 0.55}
    results = []
    for voice in args.voices:
        folder = models / voice
        pth, index, ckpt = folder / f"{voice}.pth", folder / f"{voice}.index", folder / f"checkpoint_{voice}.pt.gz"
        jobs = []
        if pth.is_file():
            jobs.append(("RVC", dict(base, backend="rvc", model=str(pth), index=str(index) if index.is_file() else "")))
        if ckpt.is_file():
            jobs.append(("Beatrice", dict(base, backend="beatrice", beatrice_model=str(ckpt), beatrice_speaker=0)))
        for engine, config in jobs:
            target = out / f"{voice}-{engine}.wav"
            if not target.is_file():
                log(f"  转换：{voice} / {engine}")
                try:
                    convert(runtime, args.worker, config, source, target)
                except RuntimeError as exc:
                    log(f"  失败：{exc}")
                    continue
            results.append((voice, engine, target.name))
    rows = "\n".join(f'<tr><td>{v}</td><td>{e}</td><td><audio controls preload="none" src="{n}"></audio></td></tr>'
                     for v, e, n in results)
    (out / "index.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>声音对比</title>
<style>body{{font:15px/1.6 "Microsoft YaHei",sans-serif;margin:32px;color:#18201f;background:#f3f5f4}}
table{{border-collapse:collapse}}td,th{{padding:8px 14px;border-bottom:1px solid #d6dddb;text-align:left}}</style>
<h1>RVC / Beatrice 声音对比</h1>
<p>同一段男声输入（{source.name}），音调 +{args.pitch}、共振峰 {args.formant:+g}。</p>
<table><tr><th>声音</th><th>引擎</th><th>试听</th></tr>
<tr><td>原声</td><td>—</td><td><audio controls preload="none" src="{source.name}"></audio></td></tr>
{rows}</table>""", encoding="utf-8")
    log(f"  对比页：{out / 'index.html'}（{len(results)} 个结果）")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch-wheels")
    p.add_argument("--index", required=True, help="e.g. https://download.pytorch.org/whl/cu126")
    p.add_argument("--mirror", default="", help="flat mirror tried first, e.g. https://mirrors.aliyun.com/pytorch-wheels/cu126")
    p.add_argument("--pkg", action="append", required=True, help="name==version, e.g. torch==2.8.0+cu126")
    p.add_argument("--dest", required=True)
    p.set_defaults(fn=fetch_wheels)
    p = sub.add_parser("fetch-hf-repo")
    p.add_argument("--repo", required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT", HF_ENDPOINTS[0]))
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--only", nargs="*", default=[], help="只下载这些路径前缀（调试用）")
    p.set_defaults(fn=fetch_hf_repo)
    p = sub.add_parser("compare")
    p.add_argument("--runtime", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--models", required=True)
    p.add_argument("--voices", required=True, nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--source", default="")
    p.add_argument("--pitch", type=int, default=10)
    p.add_argument("--formant", type=float, default=0.5)
    p.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"))
    p.set_defaults(fn=compare)
    sub.add_parser("gpu-check").set_defaults(fn=gpu_check)
    p = sub.add_parser("ensure-rvc-assets")
    p.add_argument("--runtime", required=True)
    p.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"))
    p.set_defaults(fn=ensure_rvc_assets)
    p = sub.add_parser("set-precision")
    p.add_argument("--runtime", required=True)
    p.add_argument("--value", required=True, choices=["fp32", "fp16", "bf16"])
    p.set_defaults(fn=set_precision)
    p = sub.add_parser("collect-rvc")
    p.add_argument("--runtime", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--dest", required=True)
    p.set_defaults(fn=collect_rvc)
    p = sub.add_parser("beatrice-config")
    p.add_argument("--trainer", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--amp", type=int, default=1)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--steps", type=int, default=10000)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=beatrice_config)
    p = sub.add_parser("collect-beatrice")
    p.add_argument("--out", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--dest", required=True)
    p.set_defaults(fn=collect_beatrice)
    p = sub.add_parser("configure-studio")
    p.add_argument("--settings", required=True)
    p.add_argument("--pth")
    p.add_argument("--index")
    p.add_argument("--beatrice")
    p.set_defaults(fn=configure_studio)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
