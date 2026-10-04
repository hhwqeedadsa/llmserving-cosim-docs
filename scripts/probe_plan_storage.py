"""Bounded storage comparison against an existing, hash-verified full plan."""
import argparse
import json
from pathlib import Path
import time

from moe_ub_cosim import generate
from moe_ub_cosim.plan_io import file_sha256
from moe_ub_cosim.resources import actual_usage


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--compact',action='store_true')
    args=p.parse_args();args.output_dir.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((args.input_dir/'generation.json').read_text())
    assert file_sha256(args.input_dir/'plan.json')==manifest['hashes']['plan.json']
    with (args.input_dir/'plan.json').open() as stream:plan=json.load(stream)
    timings={};original=generate._write_plan
    def write(*a,**kw):
        start=time.monotonic();result=original(*a,**kw)
        timings['write_seconds']=time.monotonic()-start
        return result
    generate._write_plan=write
    start=time.monotonic()
    try:directory=generate.export_plan(plan,args.output_dir/'graph',compact=args.compact)
    finally:generate._write_plan=original
    timings['validate_export_seconds']=time.monotonic()-start
    # Ordinary JSON decoding must recover every field, including source evidence.
    with (directory/'plan.json').open() as stream:assert json.load(stream)==plan
    generated=json.loads((directory/'generation.json').read_text())
    for name,digest in manifest['hashes'].items():
        if name!='plan.json':assert generated['hashes'][name]==digest,name
    result=dict(compact=args.compact,usage=actual_usage(plan),timings=timings,
        old_plan_bytes=(args.input_dir/'plan.json').stat().st_size,
        new_plan_bytes=(directory/'plan.json').stat().st_size,
        input_dir=str(args.input_dir),input_hashes=manifest['hashes'],output_hashes=generated['hashes'],
        complete_plan_equal=True,other_artifacts_identical=True)
    (args.output_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('input_hashes','output_hashes')}),flush=True)


if __name__=='__main__':main()
