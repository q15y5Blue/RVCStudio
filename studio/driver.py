"""Install the unmodified, pinned VB-CABLE package through its official setup."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

ZIP_SHA256 = "b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb"
ZIP_NAME = "VBCABLE_Driver_Pack45.zip"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def powershell(script, env=None, timeout=45):
    system = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(system), "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; $ErrorActionPreference='Stop'; " + script],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Windows 检测失败")
    return result.stdout.strip().lstrip("\ufeff")


def status():
    # Match the actual service from the vendor INF, not a user-editable friendly name.
    data = powershell("$d=@(Get-CimInstance Win32_PnPEntity -Filter \"Service='VBAudioVACMME'\"); "
        "ConvertTo-Json -Compress -InputObject @($d | Select-Object Name,PNPDeviceID,ConfigManagerErrorCode)")
    devices = json.loads(data or "[]")
    if isinstance(devices, dict):
        devices = [devices]
    return {"installed": bool(devices), "healthy": bool(devices) and all(d["ConfigManagerErrorCode"] == 0 for d in devices),
            "devices": devices, "reboot_required": any(d["ConfigManagerErrorCode"] == 14 for d in devices)}


def verify_package(archive: Path):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ZIP_SHA256:
        raise ValueError("内置 VB-CABLE 驱动包校验失败，已拒绝安装")


def verify_signatures(folder: Path):
    env = os.environ.copy()
    env["RVC_CABLE_SIGNATURE_DIR"] = str(folder)
    text = powershell("$p=$env:RVC_CABLE_SIGNATURE_DIR; "
        "$items=@('VBCABLE_Setup_x64.exe','vbaudio_cable64_win10.cat'); "
        "$r=@(foreach($n in $items){$s=Get-AuthenticodeSignature -LiteralPath (Join-Path $p $n); "
        "[pscustomobject]@{Name=$n;Status=[string]$s.Status;Signer=$s.SignerCertificate.Subject}}); "
        "ConvertTo-Json -Compress -InputObject $r", env=env)
    signatures = json.loads(text)
    if len(signatures) != 2 or any(s["Status"] != "Valid" for s in signatures):
        raise RuntimeError("Windows 未能验证 VB-CABLE 官方数字签名，已停止安装")
    catalog = next(s for s in signatures if s["Name"].endswith(".cat"))
    if "Microsoft Windows Hardware Compatibility Publisher" not in catalog["Signer"]:
        raise RuntimeError("VB-CABLE 驱动目录没有预期的微软签名")
    return signatures


def install(archive: Path):
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError("安装虚拟麦克风需要在 Windows 管理员授权窗口中选择“是”")
    verify_package(archive)
    before = status()
    if before["installed"]:
        if before["healthy"] or before["reboot_required"]:
            return {**before, "changed": False}
        raise RuntimeError("已有 VB-CABLE 设备处于错误或禁用状态；请先在设备管理器中修复，避免覆盖现有驱动")
    from runtime import safe_member
    with tempfile.TemporaryDirectory(prefix="RVCStudio-Cable-") as td:
        folder = Path(td)
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                dest = safe_member(folder, entry.filename)
                if entry.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(package.read(entry))
        signatures = verify_signatures(folder)
        result = subprocess.run([str(folder / "VBCABLE_Setup_x64.exe"), "-i", "-h"],
                                cwd=folder, timeout=240, creationflags=NO_WINDOW)
        after = status()
        # Vendor exit codes alone are not evidence that a PnP device was installed.
        if not after["installed"]:
            raise RuntimeError(f"VB-CABLE 安装后未发现设备（安装器退出码 {result.returncode}）。请重新运行本软件安装包重试。")
        if not after["healthy"] and not after["reboot_required"]:
            raise RuntimeError(f"VB-CABLE 设备异常：{after['devices']}；请重启后重新检测")
        return {**after, "changed": True, "reboot_required": True,
                "installer_exit": result.returncode, "signatures": signatures}
