import sys
from pathlib import Path

proj = Path(r"C:\Users\q15y5\Desktop\RVCV2")
sys.path.insert(0, str(proj / "studio"))
import runtime

chunks = proj / "offline-build" / "engine-chunks"
out = proj / "offline-build" / "merged-test.zip"
if out.exists():
    out.unlink()


def p(t):
    print(t, flush=True)


result = runtime.reassemble_chunks(chunks, out, p, None, consume=False)
assert result == out
assert out.stat().st_size == runtime.ARCHIVE_SIZE
# reassemble_chunks already ran verify_archive (size + official SHA-256) internally.
print("REASSEMBLE_OK size=%d sha-verified=True" % out.stat().st_size, flush=True)
out.unlink()
print("cleaned merged test file", flush=True)
