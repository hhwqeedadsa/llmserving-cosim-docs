"""One cached default initializer needed to bound the companion-QP count."""
import argparse
import hashlib
from pathlib import Path
from experiments.provider_binding.stage import load,save,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    name='src/include/nccl_device/host.h';cache=a.repository/'data/research/nccl_gin_20261022'
    tree=load(a.stage/'inputs/nccl-tree.json');src=cache/'sources'/name;raw=src.read_bytes()
    blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest();e=next(x for x in tree['tree'] if x['path']==name)
    assert e['sha']==blob and e['size']==len(raw)
    out=a.stage/'extra';assert not out.exists();out.mkdir();(out/'host.h').write_bytes(raw)
    save(out/'identity.json',dict(project='nccl',commit='12df1a11afad322be5a204a2db890161cbf8131d',
        path=name,blob=blob,bytes=len(raw),sha256=sha(src),reason='default ginCounterCount=0; no companion QPs in selected DeepEP initialization'))


if __name__=='__main__':main()
