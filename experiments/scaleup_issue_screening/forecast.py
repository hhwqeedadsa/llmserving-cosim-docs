"""Conservative E1 six-layer disk forecast from the completed three-layer run.

An estimate is not a hard guarantee. The unchanged host monitor remains active.
The long case retains its complete original run but is not additionally frozen.
"""
import argparse
import math
from pathlib import Path

from moe_ub_cosim.host_probe import disk_usage
from .workflow import ROOT, digest, host, read, save


def forecast(basis, archive, output):
    basis,archive,output=Path(basis),Path(archive),Path(output)
    measured=read(basis)
    reference=read(str(archive)+'.json')
    if digest(archive)!=reference['archive_sha256'] or not reference['verified']:
        raise ValueError('Reference archive is not verified')
    policy=host();seen=set();usage=dict(apparent_bytes=0,allocated_bytes=0)
    roots=sorted({Path(p).resolve() for p in policy['artifact_roots']},key=lambda p:len(p.parts))
    counted=[]
    for path in roots:
        if any(path.is_relative_to(parent) for parent in counted):continue
        counted.append(path)
        for key,value in disk_usage(path,seen).items():usage[key]+=value
    short=max(measured['run'].values())
    entries=reference['entries']
    effective=entries['run/raw/execution/effective_backend.json']['bytes']
    rotating=sum(math.ceil(value['bytes']/4096)*4096 for name,value in entries.items()
        if name.startswith('run/raw/execution/log/log.') and name.endswith('.log'))
    # Locked ASTRA Logging.cc: a 10 MiB current log plus 10 rotated files.
    # The 128 MiB reserve also covers the separate error-log cap (110 MiB).
    run_estimate=math.ceil(2.01*short)-effective+2**20-2*rotating+110*2**20
    archive_estimate=math.ceil(2.1*reference['archive_bytes'])
    reserve=128*2**20
    peak=max(usage.values())+run_estimate+archive_estimate+reserve
    result=dict(schema_version='issue-screening-disk-forecast/1',
        reference_archive_sha256=reference['archive_sha256'],basis_sha256=digest(basis),
        current_usage=usage,reference_run_charged_bytes=short,
        fixed_effective_backend_bytes=effective,reference_rotating_log_bytes=rotating,
        rotating_log_bound_bytes=110*2**20,long_run_estimate_bytes=run_estimate,
        long_archive_estimate_bytes=archive_estimate,extra_reserve_bytes=reserve,
        estimated_peak_bytes=peak,budget_bytes=policy['artifact_bytes'],fits=peak<=policy['artifact_bytes'],
        long_slice_created=False,complete_original_run_retained=True,
        assumptions=['same ranks, topology, observation coverage, native parameters and request batch',
            'at most 2.01 times the measured short-run footprint before fixed configuration/log adjustments',
            'compressed archive at most 2.1 times the short archive',
            'existing long preparation already counted in current usage',
            'estimate only; stop on actual host-policy violations'],
        logging_source='third_party/LLMServingSim/astra-sim/astra-sim/common/Logging.cc',
        logging_source_sha256=digest(ROOT/'third_party/LLMServingSim/astra-sim/astra-sim/common/Logging.cc'))
    if output.exists():raise ValueError('Forecast output must be new')
    save(output,result)
    print('fits',result['fits'],'estimated peak GiB',round(peak/2**30,3))
    if not result['fits']:raise SystemExit('Long run is outside the declared disk forecast')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--basis',type=Path,help='Previously measured short-run footprint')
    source.add_argument('--measure-run',type=Path,help='Measure a completed public run before archiving')
    p.add_argument('--reference-archive',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.measure_run:
        if a.reference_archive:
            p.error('--reference-archive applies only to --basis')
        if a.output.exists():
            p.error('Measurement output must be new')
        if not (a.measure_run/'manifest.json').is_file():
            p.error('Expected a completed public run with manifest.json')
        manifest=read(a.measure_run/'manifest.json')
        if not (manifest.get('normal_exit') is True and manifest.get('validation') is True and manifest.get('finished_utc')):
            p.error('Disk basis requires a completed, validated public run')
        save(a.output,dict(schema_version='issue-screening-disk-basis/1',
            run=disk_usage(a.measure_run),manifest_sha256=digest(a.measure_run/'manifest.json')))
    else:
        if not a.reference_archive:
            p.error('--basis requires --reference-archive')
        forecast(a.basis,a.reference_archive,a.output)


if __name__=='__main__':main()
