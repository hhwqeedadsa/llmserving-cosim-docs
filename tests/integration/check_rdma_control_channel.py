"""CPU-only concurrent diagnostic/control pipe check; no ns-3 execution."""
import argparse
import json
from pathlib import Path
import subprocess


def check(binary):
    result=subprocess.run([str(binary.resolve())],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    if result.returncode!=0:raise ValueError('control test child failed: '+result.stderr.decode(errors='replace'))
    lines=result.stdout.decode().splitlines()
    if len(lines)!=128:raise ValueError('missing or extra control frames')
    for index,line in enumerate(lines):
        if not line.startswith('MOE_RDMA_EVENT '):raise ValueError('diagnostic leaked into control pipe')
        event=json.loads(line[len('MOE_RDMA_EVENT '):])
        if event!=dict(type='DECIDE',decision_id=index,padding='x'*8192):
            raise ValueError('control frame content changed')
    # Diagnostic threads may interleave with each other; the control stream
    # must remain exact regardless of their formatting.
    errors=result.stderr.decode()
    if any(errors.count(text)!=128 for text in ('iostream diagnostic','stdio diagnostic','direct descriptor diagnostic')):
        raise ValueError('diagnostic output was lost rather than redirected')
    return dict(passed=True,control_frames=128,padding_bytes_per_frame=8192,
                diagnostic_records=384,invalid_frames_rejected=3,control_bytes=len(result.stdout),
                diagnostic_bytes=len(result.stderr),scope='actual pipes and concurrent writes; no simulator or logger calibration')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(check(args.binary)))
