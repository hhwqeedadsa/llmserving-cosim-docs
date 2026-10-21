"""The public interface. Imports for expensive operations stay inside commands."""
import argparse
import json
from pathlib import Path
import sys


def parser():
    p = argparse.ArgumentParser(prog='moe-ub', description='MoE inference / ASTRA / ns-3-UB co-simulation. '
        'Configuration paths are relative to the configuration file; CLI paths to the working directory.')
    p.add_argument('--version', action='version', version='moe-ub-cosim 0.4.0')
    sub = p.add_subparsers(dest='command', required=True)
    doctor = sub.add_parser('doctor', help='Check tools, fixed dependencies and available resources; no installation')
    doctor.add_argument('--backend', type=Path, help='Optional backend executable to check')
    build = sub.add_parser('build', help='Build the locked native backend; never download dependencies')
    build.add_argument('--build-dir', type=Path, default=Path('build/default'), help='Build/output directory (default: build/default)')
    build.add_argument('--jobs', type=int, default=2, help='Parallel compiler jobs (default: 2)')
    resolve = sub.add_parser('resolve', help='Validate a structured YAML scenario and write explicit run inputs; no network execution')
    resolve.add_argument('--config', type=Path, required=True, help='Scenario YAML; paths resolve from this file')
    resolve.add_argument('--backend', type=Path, required=True, help='Locked backend used to check parameters')
    resolve.add_argument('--output', type=Path, required=True, help='New resolved input directory')
    scaleout = sub.add_parser('scaleout', help='Generate fabrics, estimate capacity, or execute independent native RDMA probes/services')
    sc = scaleout.add_subparsers(dest='scaleout_command', required=True)
    sp = sc.add_parser('prepare', help='Generate device/server/rack/spine inputs, explicit placement, paths and bandwidth cuts')
    sp.add_argument('--config', type=Path, required=True, help='Scale-Out JSON; source paths resolve from this file')
    sp.add_argument('--output', type=Path, required=True, help='New portable scenario directory; does not execute a backend')
    capacity = sc.add_parser('capacity', help='Estimate uniform MoE volume and fixed-array memory for a two-tier Clos; no latency prediction')
    capacity.add_argument('--config', type=Path, required=True, help='Capacity JSON, TP=PP=1 and one NIC/GPU; no external paths')
    capacity.add_argument('--output', type=Path, required=True, help='New directory for input and capacity.json; not simulation results')
    topology = sc.add_parser('topology', help='Generate flexible Clos/Agg/multi-rail/plane fabrics and native RDMA topology files')
    topology.add_argument('--config', type=Path, required=True, help='Topology JSON; counts, NIC rail map, layer rates and port limits')
    topology.add_argument('--output', type=Path, required=True, help='New directory for physical graph, NIC map, audit and ASTRA/SimAI text inputs')
    rb = sc.add_parser('rdma-build', help='Build independent locked ASTRA RDMA probe and online service targets; no UB')
    rb.add_argument('--output', type=Path, required=True, help='New isolated build directory on a volume with at least 8GiB free')
    rb.add_argument('--cmake', default='cmake')
    rb.add_argument('--ninja', default='ninja')
    rb.add_argument('--protobuf-prefix', type=Path, help='Existing Protobuf installation prefix; never installs dependencies')
    rb.add_argument('--resume', action='store_true', help='Explicitly reconfigure an owned build after a source/configuration correction; keeps prior logs')
    rp = sc.add_parser('rdma-probe', help='Execute bounded real RDMA messages on a generated small fabric; no frontend service yet')
    rp.add_argument('--topology', type=Path, required=True, help='Directory produced by scaleout topology')
    rp.add_argument('--config', type=Path, required=True, help='Explicit RDMA/ECN protocol JSON')
    rp.add_argument('--messages', type=Path, required=True, help='Bounded microprobe message array, not an LLM workload')
    rp.add_argument('--backend', type=Path, required=True, help='Executable returned by rdma-build')
    rp.add_argument('--output', type=Path, required=True, help='New result directory on a volume with at least 8GiB free')
    run_rdma = sc.add_parser('rdma-run', help='Run ready-KV MoE with synthetic or source-checked routes through LLMServingSim, ASTRA and native RDMA ns-3')
    run_rdma.add_argument('--config', type=Path, required=True, help='Model/workload/groups, declared compute, and optional source-checked route_replay')
    run_rdma.add_argument('--topology', type=Path, required=True, help='Generated physical topology directory')
    run_rdma.add_argument('--protocol', type=Path, required=True, help='Explicit RDMA protocol JSON')
    run_rdma.add_argument('--backend', type=Path, required=True, help='Native service executable from rdma-build')
    run_rdma.add_argument('--output', type=Path, required=True, help='New bounded run directory')
    run_rdma.add_argument('--protoc', default='protoc', help='Existing compiler for the pinned Chakra schema')
    verify_rdma = sc.add_parser('rdma-check', help='Audit actual token bytes, ASTRA dependencies, callbacks and next-wave scheduling')
    verify_rdma.add_argument('--run', type=Path, required=True, help='Completed native RDMA service directory')
    diagnose_rdma = sc.add_parser('rdma-diagnose', help='Explain packet faults and outstanding callbacks in a completed or failed native run; evaluation only')
    diagnose_rdma.add_argument('--run', type=Path, required=True, help='Native probe or service directory containing result.json, including failed runs')
    diagnose_rdma.add_argument('--output', type=Path, required=True, help='New diagnosis JSON; input run is preserved')
    segment=sub.add_parser('segment',help='Prepare or freeze finite consecutive MoE blocks with an explicit initial state')
    commands=segment.add_subparsers(dest='segment_command',required=True)
    prepare=commands.add_parser('prepare',help='Select original layer IDs and Decode step; preserve full model capacity')
    prepare.add_argument('--config',type=Path,required=True,help='Segment selection JSON referencing a public full-model run config')
    prepare.add_argument('--output',type=Path,required=True,help='New input directory with run.json')
    freeze=commands.add_parser('freeze',help='Freeze a completed finite segment; not an arbitrary simulator snapshot')
    freeze.add_argument('--from-run',type=Path,required=True)
    freeze.add_argument('--output',type=Path,required=True)
    freeze.add_argument('--storage',choices=('copy','hardlink'),default='copy')
    facts=sub.add_parser('facts',help='Export bounded selected A3 execution facts into internal truth; never observed data')
    facts.add_argument('--run',type=Path,required=True,help='Completed original configured run with sealed plans')
    facts.add_argument('--config',type=Path,required=True,help='Explicit graph/rank/layer coverage and resource budgets')
    facts.add_argument('--output',type=Path,required=True,help='New internal facts directory')
    for name, help_text in [('run', 'Run a configured service and export bounded research records'),
                            ('sample', 'Generate observations from a frozen slice without frontend/backend execution'),
                            ('replay', 'Execute frozen graphs in the real backend without scheduling or graph generation')]:
        q = sub.add_parser(name, help=help_text, description=help_text+'. Output must not exist. See docs/user_guide.md.')
        q.add_argument('--config', type=Path, required=True, help='JSON configuration; paths resolve from this file'+
                       ('; optional diagnostic_storage=gzip compresses closed text logs' if name in ('run','replay') else ''))
        q.add_argument('--output', type=Path, required=True, help='New experiment directory; existing directories are rejected')
        if name in ('sample', 'replay'):
            q.add_argument('--slice', type=Path, required=True, help='Frozen slice directory')
        if name in ('run', 'replay'):
            q.add_argument('--backend', type=Path, help='Override backend path in configuration (relative to working directory)')
    s = sub.add_parser('slice', help='Freeze or verify a portable first-round research bundle')
    ss = s.add_subparsers(dest='slice_command', required=True)
    create = ss.add_parser('create', help='Freeze a completed one-round run; no simulation or graph generation')
    src = create.add_mutually_exclusive_group(required=True)
    src.add_argument('--from-run', type=Path, help='Completed moe-ub run directory')
    src.add_argument('--import-config', type=Path, help='Explicit archive import manifest for an existing first-round run')
    create.add_argument('--output', type=Path, required=True, help='New slice directory')
    create.add_argument('--storage', choices=('copy', 'hardlink'), default='copy', help='Copy for independence (default), or immutable hardlinks on the same filesystem')
    inspect = ss.add_parser('inspect', help='Read scope and file sizes; --verify scans hashes of all bundle files')
    inspect.add_argument('--slice', type=Path, required=True)
    inspect.add_argument('--verify', action='store_true', help='Read all bundle bytes to verify integrity')
    compact = ss.add_parser('compact', help='Create a finite slice with compressed execution evidence; retain every byte and the original graph')
    compact.add_argument('--slice', type=Path, required=True, help='Verified expanded finite slice/3')
    compact.add_argument('--output', type=Path, required=True, help='New compact bundle; source is retained')
    compact.add_argument('--storage', choices=('copy','hardlink'), default='copy', help='Storage for unchanged inputs and graphs')
    validate = sub.add_parser('validate', help='Verify a completed experiment, or only public observations')
    target = validate.add_mutually_exclusive_group(required=True)
    target.add_argument('--run', type=Path, help='Completed experiment directory (reads internal evidence)')
    target.add_argument('--observed', type=Path, help='Public observation directory only (never reads truth)')
    validate.add_argument('--allow-partial-observation',action='store_true',
                          help='Validate recorded scope while explicitly accepting incomplete time coverage')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ('doctor', 'build'):
            from .user_build import doctor, build
            value = doctor(args.backend) if args.command == 'doctor' else build(args.build_dir, args.jobs)
        elif args.command == 'resolve':
            from .scenario import prepare
            _, manifest = prepare(args.config.resolve(), args.backend.resolve(), args.output.resolve())
            value = dict(passed=True, output=str(args.output.resolve()), state=manifest['state'],
                         scenario_id=manifest['scenario_id'])
        elif args.command == 'scaleout':
            if args.scaleout_command == 'rdma-diagnose':
                from .scaleout_diagnostics import diagnose
                value = diagnose(args.run, args.output)
            elif args.scaleout_command == 'rdma-check':
                from .scaleout_verify import verify
                value = verify(args.run)
            elif args.scaleout_command == 'rdma-run':
                from .scaleout_service import run
                value = run(args.config,args.topology,args.protocol,args.backend,args.output,args.protoc)
            elif args.scaleout_command == 'rdma-build':
                from .scaleout_rdma import build
                value = build(args.output, cmake=args.cmake, ninja=args.ninja,
                              protobuf_prefix=args.protobuf_prefix, resume=args.resume)
            elif args.scaleout_command == 'rdma-probe':
                from .scaleout_rdma import probe
                value = probe(args.topology, args.config, args.messages, args.backend, args.output)
            elif args.scaleout_command == 'topology':
                from .scaleout_topology import execute
                value = execute(args.config, args.output)
            elif args.scaleout_command == 'capacity':
                from .scaleout_capacity import execute
                value = execute(args.config, args.output)
            else:
                from .scaleout import prepare
                value = prepare(args.config, args.output)
        elif args.command == 'segment':
            if args.segment_command=='prepare':
                from .segment import prepare
                value=prepare(args.config,args.output)
            else:
                from .research_slice import create
                value=create(args.from_run,args.output,args.storage,segment_scope=True)
        elif args.command == 'facts':
            from .local_facts import execute
            value=execute(args.run,args.config,args.output)
        elif args.command == 'slice':
            from .research_slice import create, inspect, compact_slice
            value = (create(args.from_run, args.output, args.storage, args.import_config)
                     if args.slice_command == 'create' else compact_slice(args.slice,args.output,args.storage)
                     if args.slice_command == 'compact' else inspect(args.slice, verify=args.verify))
        elif args.command == 'validate':
            from .user_workflow import validate
            value = validate(args.run, args.observed,args.allow_partial_observation)
        else:
            from .user_workflow import execute
            value = execute(args)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0 if value.get('passed', True) else 1
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(f'moe-ub: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
