"""Inspect the actual official Windows ZIP with HTTP ranges, no full download."""
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import threading
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://huggingface.co/IAHispano/Applio/resolve/main/Compiled/Windows/ApplioV3.6.5.zip'
SIZE = 4929106166
CACHE = ROOT / 'build/zip-inspection'
CACHE.mkdir(parents=True, exist_ok=True)


def fetch(span):
    start, end = span
    path = CACHE / f'{start}-{end}.bin'
    if path.exists() and path.stat().st_size == end-start+1:
        return path.read_bytes()
    request = urllib.request.Request(URL, headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'RVCStudio-inspect'})
    with urllib.request.urlopen(request, timeout=45) as stream:
        if stream.headers.get('Content-Range') != f'bytes {start}-{end}/{SIZE}':
            raise ValueError('Unexpected range: '+str(stream.headers.get('Content-Range')))
        data = stream.read(end-start+1)
    if len(data) != end-start+1:
        raise ValueError('Short range')
    path.write_bytes(data)
    return data


class RemoteZip(io.RawIOBase):
    def __init__(self):
        self.pos = 0
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else (self.pos + offset if whence == 1 else SIZE + offset)
        return self.pos
    def read(self, n=-1):
        n = min(SIZE-self.pos, n if n>=0 else SIZE-self.pos)
        if n<=0:return b''
        spans=[(s,min(self.pos+n-1,s+262143)) for s in range(self.pos,self.pos+n,262144)]
        print('Read range',self.pos,n,flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            data=b''.join(pool.map(fetch,spans))
        self.pos+=n
        return data


with zipfile.ZipFile(RemoteZip()) as archive:
    report={'entries':len(archive.infolist()),'uncompressed_bytes':sum(x.file_size for x in archive.infolist())}
    names=archive.namelist()
    (CACHE/'entries.json').write_text(json.dumps([{ 'name':e.filename,'size':e.file_size,'compressed':e.compress_size} for e in archive.infolist()],indent=2),encoding='utf-8')
    report['required']=[n for n in names if any(n.endswith(x) for x in ('env/python.exe','predictors/rmvpe.pt','predictors/fcpe.pt','embedders/contentvec/config.json','embedders/contentvec/pytorch_model.bin'))]
    report['source_hashes']={}
    for tail in ('rvc/realtime/core.py','rvc/realtime/pipeline.py','core.py'):
        matches=[n for n in names if n.endswith('/'+tail) and n.count('/')==tail.count('/')+1]
        if not matches and tail in names:matches=[tail]
        if len(matches)!=1:raise ValueError((tail,matches))
        data=archive.read(matches[0])
        report['source_hashes'][tail]=hashlib.sha256(data).hexdigest()
        (CACHE/(tail.replace('/','_'))).write_bytes(data)
    (CACHE/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)
