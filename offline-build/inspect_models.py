import sys, json
from pathlib import Path

base = Path(r"C:\Users\q15y5\Doubao\chats\2026-09-28\new-chat\offline\payload\applio_src\logs")
models = {
    "ChineseFemale (v2 200ep primary)": (base/"ChineseFemale"/"ChineseFemale.pth", base/"ChineseFemale"/"ChineseFemale.index"),
    "ChineseFemale_HQ (Ov2 350ep alt)": (base/"ChineseFemale_HQ"/"ChineseFemale_HQ.pth", base/"ChineseFemale_HQ"/"ChineseFemale_HQ.index"),
}

import torch
import faiss

for label, (pth, idx) in models.items():
    print("="*70)
    print(label)
    print("pth:", pth.exists(), f"{pth.stat().st_size/1e6:.1f}MB" if pth.exists() else "")
    ckpt = torch.load(str(pth), map_location="cpu", weights_only=True)
    keys = list(ckpt.keys()) if isinstance(ckpt, dict) else type(ckpt)
    print("top-level keys:", keys)
    if isinstance(ckpt, dict):
        print("  version =", repr(ckpt.get("version")))
        print("  f0      =", ckpt.get("f0"))
        print("  has weight =", "weight" in ckpt, " has config =", "config" in ckpt)
        cfg = ckpt.get("config")
        if isinstance(cfg, list):
            # RVC config tuple: [sample_rate, inter_channels, n_layers, ...]
            print("  config (sampling_rate first) =", cfg[:8] if len(cfg)>=8 else cfg)
        else:
            print("  config =", cfg)
    print("index:", idx.exists(), f"{idx.stat().st_size/1e6:.1f}MB" if idx.exists() else "")
    if idx.exists():
        ix = faiss.read_index(str(idx))
        print("  index d =", ix.d, " ntotal =", ix.ntotal)
    # App validation logic
    ok = isinstance(ckpt, dict) and ckpt.get("version")=="v2" and "weight" in ckpt and "config" in ckpt and bool(ckpt.get("f0",1))
    print(">>> App model validation (version==v2, weight/config, f0):", "PASS" if ok else "FAIL")
    if idx.exists():
        print(">>> App index validation (d==768, ntotal>=1):", "PASS" if (ix.d==768 and ix.ntotal>=1) else "FAIL")
