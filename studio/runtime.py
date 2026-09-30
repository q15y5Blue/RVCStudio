"""Pinned official runtime download and staged installation. No driver mutations."""
from __future__ import annotations

import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import time
import urllib.request
import zipfile

VERSION = "3.6.5"
ARCHIVE_NAME = "ApplioV3.6.5.zip"
ARCHIVE_SIZE = 4929106166
ARCHIVE_SHA256 = "0d6d777a2668a6b16e83d7648f227961088a54cfcb93025f63099cd1088e1ff2"
ARCHIVE_URL = "https://huggingface.co/IAHispano/Applio/resolve/main/Compiled/Windows/" + ARCHIVE_NAME
SOURCE_FILES = ("rvc/realtime/core.py", "rvc/realtime/pipeline.py", "core.py")


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise Cancelled("操作已取消；下载进度保留，可以继续")


def sha256(path: Path, progress=lambda text: None, cancel=None):
    digest = hashlib.sha256()
    total = path.stat().st_size
    read = 0
    last = 0
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            check_cancel(cancel)
            digest.update(block)
            read += len(block)
            if time.monotonic() - last > .5:
                progress(f"校验下载文件 {read / max(total, 1):.0%}")
                last = time.monotonic()
    return digest.hexdigest()


def verify_archive(path: Path, progress=lambda text: None, cancel=None):
    if path.stat().st_size != ARCHIVE_SIZE:
        raise ValueError(f"运行环境 ZIP 大小不匹配，需要官方 {ARCHIVE_NAME}")
    if sha256(path, progress, cancel) != ARCHIVE_SHA256:
        raise ValueError("运行环境 SHA-256 校验失败，文件可能损坏；请重新下载")


def download(cache: Path, progress=lambda text: None, cancel=None) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    final = cache / ARCHIVE_NAME
    part = cache / (ARCHIVE_NAME + ".part")
    if final.exists():
        try:
            verify_archive(final, progress, cancel)
            return final
        except ValueError:
            final.unlink()
            progress("下载缓存损坏，正在重新下载")
    for attempt in range(4):
        check_cancel(cancel)
        offset = part.stat().st_size if part.exists() else 0
        if offset == ARCHIVE_SIZE:
            break
        if offset > ARCHIVE_SIZE:
            part.unlink()
            offset = 0
        if shutil.disk_usage(cache).free < ARCHIVE_SIZE - offset + 512 * 1024 ** 2:
            raise ValueError("下载盘剩余空间不足，需要约 5 GB")
        headers = {"User-Agent": "RVCStudio/0.2", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            request = urllib.request.Request(ARCHIVE_URL, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                if offset and response.status == 200:
                    offset = 0
                elif offset and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("服务器未正确返回续传范围")
                mode = "ab" if offset else "wb"
                last = 0
                with part.open(mode) as output:
                    while block := response.read(64 * 1024):
                        check_cancel(cancel)
                        output.write(block)
                        offset += len(block)
                        if offset > ARCHIVE_SIZE:
                            raise ValueError("服务器文件长度超出固定版本大小")
                        if time.monotonic() - last > .5:
                            progress(f"下载运行环境 {offset / ARCHIVE_SIZE:.1%} · {offset / 1e9:.2f} / 4.93 GB")
                            last = time.monotonic()
            if offset != ARCHIVE_SIZE:
                raise OSError("下载提前结束")
            break
        except (OSError, TimeoutError, IncompleteRead) as exc:
            if attempt == 3:
                raise OSError(f"下载失败，缓存已保留，请重试：{exc}") from exc
            progress(f"网络中断，正在续传（{attempt + 1}/3）")
    try:
        verify_archive(part, progress, cancel)
    except ValueError:
        part.unlink()
        raise ValueError("运行环境校验失败，已清除损坏的下载缓存，请重试下载") from None
    part.replace(final)
    return final


CHUNK_PREFIX = "engine.bin."


def reassemble_chunks(chunk_dir: Path, dest: Path, progress=lambda text: None, cancel=None,
                      consume: bool = False) -> Path:
    """Merge the offline installer's sub-2GB engine chunks into the pinned ZIP.

    The merged file must match the official size and SHA-256 exactly. When
    ``consume`` is set, each chunk is deleted after it is appended, which keeps
    the peak temporary-space footprint low during installation.
    """
    chunk_dir = Path(chunk_dir)
    chunks = sorted(p for p in chunk_dir.glob(CHUNK_PREFIX + "*") if p.is_file())
    if not chunks:
        raise ValueError("未找到内置引擎分片，请使用完整的离线安装包重新安装")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    merged = dest.with_name(dest.name + ".merged")
    if merged.exists():
        merged.unlink()
    done = 0
    try:
        with merged.open("wb") as out:
            for chunk in chunks:
                check_cancel(cancel)
                with chunk.open("rb") as src:
                    while block := src.read(8 * 1024 * 1024):
                        check_cancel(cancel)
                        out.write(block)
                        done += len(block)
                        if done % (256 * 1024 * 1024) < 8 * 1024 * 1024:
                            progress(f"合并内置引擎分片 {done / ARCHIVE_SIZE:.1%}")
                if consume:
                    try:
                        chunk.unlink(missing_ok=True)
                    except OSError:
                        # 来源目录可能只读（如挂载的 ISO）；无法删除分片不影响安装。
                        pass
                if done > ARCHIVE_SIZE:
                    raise ValueError("内置引擎分片总大小超出官方版本，请使用完整的离线安装包")
        if done != ARCHIVE_SIZE:
            raise ValueError("内置引擎分片不完整，请使用完整的离线安装包重新安装")
        verify_archive(merged, progress, cancel)
        merged.replace(dest)
    finally:
        merged.unlink(missing_ok=True)
    return dest


def safe_member(root: Path, name: str) -> Path:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or any(p in ("..",) or ":" in p or p.endswith((".", " ")) for p in path.parts):
        raise ValueError(f"不安全的压缩路径：{name}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(p.split(".")[0].upper() in reserved for p in path.parts):
        raise ValueError(f"压缩包包含设备路径：{name}")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("压缩路径超出目标目录")
    return target


def locate(root: Path) -> Path:
    candidates = [root] + [p for p in root.iterdir() if p.is_dir()]
    matches = [p for p in candidates if (p / "env/python.exe").is_file() and (p / "rvc/realtime/core.py").is_file()]
    if len(matches) != 1:
        raise ValueError("未找到唯一 Applio Windows 运行环境（env/python.exe）")
    return matches[0]


def verify_engine(root: Path, manifest: dict):
    if not (root / "env/python.exe").is_file():
        raise ValueError("运行环境不完整：缺少 env/python.exe")
    for file, expected in manifest.items():
        p = root / file
        # Official Windows bundles use CRLF while GitHub source uses LF.
        if not p.is_file() or hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != expected:
            raise ValueError(f"仅支持已适配的 Applio {VERSION} 原版；文件不匹配：{file}")


def install(archive: Path, target: Path, manifest: dict, progress=lambda text: None, cancel=None) -> Path:
    verify_archive(archive, progress, cancel)
    target = target.resolve()
    if target.exists():
        raise ValueError("目标目录已存在，请选择新的空目录，或直接使用已有运行环境")
    target.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    stage = Path(tempfile.mkdtemp(prefix="rvc-stage-", dir=target.parent))
    try:
        with zipfile.ZipFile(archive) as package:
            required = sum(e.file_size for e in package.infolist())
            if shutil.disk_usage(stage).free < required + 1024 ** 3:
                raise ValueError(f"解压空间不足，需要 {required / 1024**3 + 1:.1f} GB 可用空间")
            done = 0
            last = 0
            for entry in package.infolist():
                check_cancel(cancel)
                dest = safe_member(stage, entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("不接受含符号链接的运行环境")
                if entry.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, dest.open("wb") as output:
                    while block := source.read(1024 * 1024):
                        check_cancel(cancel)
                        output.write(block)
                        done += len(block)
                        if time.monotonic() - last > .5:
                            progress(f"安装运行环境 {done / max(required, 1):.0%}")
                            last = time.monotonic()
        engine = locate(stage)
        verify_engine(engine, manifest)
        engine.rename(target)
        progress("运行环境文件安装完成；下一步检测依赖和显卡")
        return target
    finally:
        # stage is created by this operation, never a user-supplied directory.
        if stage.exists() and stage.parent == target.parent and stage.name.startswith("rvc-stage-"):
            shutil.rmtree(stage)
