"""Interactive-widget checks using isolated application data (no audio hardware)."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ["RVC_STUDIO_DATA"] = str(ROOT / "build/gui-test-data")
sys.path.insert(0, str(ROOT / "studio"))
from app import Studio
from tkinter import ttk

app = Studio()
app.update()
books = []


def find(widget):
    if isinstance(widget, ttk.Notebook):
        books.append(widget)
    for child in widget.winfo_children():
        find(child)


find(app)
book = books[0]
report = {"tabs": []}
for i in range(4):
    book.select(i)
    app.update()
    tab = book.nametowidget(book.tabs()[i])
    clipped = []

    def check(widget):
        if isinstance(widget, ttk.Button) and widget.winfo_ismapped():
            if (widget.winfo_rooty() + widget.winfo_height() > tab.winfo_rooty() + tab.winfo_height()
                    or widget.winfo_rootx() + widget.winfo_width() > tab.winfo_rootx() + tab.winfo_width()):
                clipped.append(widget.cget("text"))
        for child in widget.winfo_children():
            check(child)

    check(tab)
    report["tabs"].append({"name": book.tab(i, "text"), "clipped_buttons": clipped})
    assert not clipped, clipped

with tempfile.TemporaryDirectory() as td:
    source = Path(td) / "普通话 女声.pth"
    source.write_bytes(b"test-payload-not-a-real-model")
    app.variable("index").set("previous.index")
    with patch("app.filedialog.askopenfilename", return_value=str(source)):
        app.import_model()
    deadline = time.monotonic() + 5
    while app.busy and time.monotonic() < deadline:
        app.update()
        time.sleep(.02)
    assert not app.busy
    copied = Path(app.variable("model").get())
    assert copied.read_bytes() == source.read_bytes()
    assert app.variable("index").get() == ""
    report["import_copy_and_clear_old_index"] = "passed"
app.defaults()
assert app.collect().pitch == 8
report["preset_reset"] = "passed"
(ROOT / "build/gui-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
app.destroy()
env = os.environ.copy()
env["RVC_STUDIO_DATA"] = str(ROOT / "build/exe-smoke-data")
r = subprocess.run([str(ROOT / "build/app/RVCStudio.exe"), "--smoke-test", str(ROOT / "build/exe-smoke.json")],
                   env=env, cwd=r"C:\Windows\System32", timeout=30)
assert r.returncode == 0
print((ROOT / "build/exe-smoke.json").read_text(encoding="utf-8"))
