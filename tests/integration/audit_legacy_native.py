"""Compatibility test entrypoint; complete checks live in the public module."""
import argparse
import json
from pathlib import Path
from moe_ub_cosim.legacy_audit import audit as _audit, decode, verify_complete_ids, verify_packet_bytes


def audit(work,result,protocol):
    summary=_audit(work,result,protocol)
    summary['real_routes']=summary.pop('route_source_verified')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();assert not args.output.exists()
    load=lambda p:json.loads(p.read_bytes())
    summary=audit(load(args.run/'execution.json'),load(args.run/'result.json'),load(args.protocol))
    args.output.write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
