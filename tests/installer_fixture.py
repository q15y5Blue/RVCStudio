"""ONLY linked into a separate test installer; never shipped in dist."""
import argparse
import configparser
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--result", required=True)
parser.add_argument("--data-root")
parser.add_argument("--cancel-file")
parser.add_argument("--archive")
args = parser.parse_args()
ini = configparser.ConfigParser(interpolation=None)
ini["Result"] = {"status": "ok", "reboot": "0", "details": "MOCK COMPONENTS - INSTALLER FLOW TEST ONLY"}
if args.command == "engine":
    for stage in ("DOWNLOAD", "VERIFY", "EXTRACT", "DEPENDENCIES", "READY"):
        print(f"RVC|{stage}|100", flush=True)
        time.sleep(.1)
    if args.archive:
        ini["Result"] = {"status": "error", "error": "模拟下载失败：50% ，测试失败不继续安装"}
with Path(args.result).open("w", encoding="utf-16") as out:
    ini.write(out, space_around_delimiters=False)
raise SystemExit(1 if ini["Result"]["status"] == "error" else 0)
