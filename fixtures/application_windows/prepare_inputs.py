"""Produce sparse and explicit-prefix variants of the public local-view input."""
import argparse
from copy import deepcopy
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False)
    w=json.loads(a.work.read_text());w['schema_version']='moe-rdma-transactions/5'
    for v in w['windows']:
        raw=v.pop('hex');v.update(bytes=65536,initialization='zero_fill/1',segments=[dict(offset=0,hex=raw)])
    explicit=deepcopy(w);explicit['submission_model']=dict(mode='explicit_prefix/1')
    for op in explicit['operations']:
        if op['kind']=='remote':op['aggregate_requests']=False
    for name,value in [('sparse',w),('explicit',explicit)]:
        (a.output/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')


if __name__=='__main__':main()
