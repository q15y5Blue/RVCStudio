from pathlib import Path
import json
import sys
import tempfile
import zipfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "studio"))
import driver

archive = root / "vendor/vbcable" / driver.ZIP_NAME
driver.verify_package(archive)
with tempfile.TemporaryDirectory(prefix="RVC-build-verify-") as temp:
    with zipfile.ZipFile(archive) as package:
        package.extractall(temp)  # Hash-pinned official archive only.
    signatures = driver.verify_signatures(Path(temp))
    (root / "build").mkdir(exist_ok=True)
    (root / "build/bundled-driver-signatures.json").write_text(json.dumps(signatures, ensure_ascii=False, indent=2), encoding="utf-8")
print("VB-CABLE archive SHA-256 and publisher / Microsoft catalog signatures verified.")
