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
    pf = sc.add_parser('rdma-preflight', help='Check physical per-switch headroom/reserve and optional transaction input; no network execution or backend required')
    pf.add_argument('--topology',type=Path,required=True,help='Generated physical topology directory')
    pf.add_argument('--protocol',type=Path,required=True,help='Explicit native RDMA protocol JSON')
    pf.add_argument('--transactions',type=Path,help='Optional full prospective or empty initial transaction config; existing dependency/address checks')
    pf.add_argument('--output',type=Path,required=True,help='New JSON report; capacity failure is saved and exits 1')
    dp = sc.add_parser('deepep-plan', help='Compile an offline pinned DeepEP low-latency application partial order; no network/GPU execution')
    dp.add_argument('--config', type=Path, required=True, help='Explicit choices, default compute-stream-wait/no-hook API and logical single-NIC RDMA-only mode')
    dp.add_argument('--output', type=Path, required=True, help='New directory for input, operation/event plan and hash manifest')
    dc = sc.add_parser('deepep-check', help='Check plan integrity and untimed event reachability; withholding is not packet loss simulation')
    dc.add_argument('--plan', type=Path, required=True, help='Directory from deepep-plan')
    dc.add_argument('--output', type=Path, required=True, help='New JSON file with dependency closure and blocked device kernel completions')
    dc.add_argument('--withhold', action='append', default=[], help='Exact event ID to withhold from closure; repeatable')
    mp = sc.add_parser('modern-deepep-plan', help='Compile pinned modern direct BF16 operations with symbolic Dispatch slots; no network/GPU execution')
    mp.add_argument('--config', type=Path, required=True, help='Fresh direct RDMA-only, expanded/local reduction and explicit launch/context declarations')
    mp.add_argument('--output', type=Path, required=True, help='New directory for input, symbolic operation/event plan and hash manifest')
    mc = sc.add_parser('modern-deepep-check', help='Check modern symbolic plan integrity and untimed closure; does not resolve slots or execute queues')
    mc.add_argument('--plan', type=Path, required=True)
    mc.add_argument('--output', type=Path, required=True, help='New JSON file')
    mc.add_argument('--withhold', action='append', default=[], help='Exact event ID; repeatable logic probe, not packet loss')
    mr = sc.add_parser('modern-deepep-replay', help='Validate caller event transcripts with dynamic slots/channel order; no provider, queue or network simulation')
    mr.add_argument('--config', type=Path, required=True, help='Same input as modern-deepep-plan')
    mr.add_argument('--actions', type=Path, required=True, help='Explicit event IDs in occurrence order; full or prefix transcript')
    mr.add_argument('--output', type=Path, required=True, help='New JSON file containing execution log, resolved slots and enabled next events')
    mw = sc.add_parser('modern-deepep-window-replay', help='Check caller events against relative windows, source leases, count resets and rolling signals; no transport')
    mw.add_argument('--config', type=Path, required=True, help='Same bounded input as modern-deepep-plan')
    mw.add_argument('--actions', type=Path, required=True, help='Explicit event IDs; callbacks are declarations, not network observations')
    mw.add_argument('--output', type=Path, required=True, help='New JSON file with execution, relative window map and shadow state')
    mn = sc.add_parser('modern-deepep-native', help='Drive the complete modern functional graph with actual native transaction callbacks and synthetic operands; no GPU/GIN timing claim')
    for name in ('topology','protocol','config','backend','output'):
        mn.add_argument('--'+name,type=Path,required=True)
    callback_mode=mn.add_mutually_exclusive_group()
    callback_mode.add_argument('--callback-wakeups',action='store_true',help='Subscribe to actual callbacks needed by the next modern event; requires a session backend supporting wake_on')
    callback_mode.add_argument('--callback-batches',action='store_true',help='Boundedly buffer original callback batches and process their original times; requires a supporting session backend')
    mn.add_argument('--host-timing',action='store_true',help='Write bounded worker/native host phase traces, including flushed failure prefixes; requires an instrumented session backend')
    rb = sc.add_parser('rdma-build', help='Build independent locked ASTRA RDMA probes and online service targets; no UB')
    rb.add_argument('--output', type=Path, required=True, help='New isolated build directory on a volume with at least 8GiB free')
    rb.add_argument('--cmake', default='cmake')
    rb.add_argument('--ninja', default='ninja')
    rb.add_argument('--protobuf-prefix', type=Path, help='Existing Protobuf installation prefix; never installs dependencies')
    rb.add_argument('--resume', action='store_true', help='Explicitly reconfigure an owned build after a source/configuration correction; keeps prior logs')
    rb.add_argument('--compile-wall-seconds', type=int, default=300,
                    help='Explicit compile time budget, 60..1800 seconds (default 300); never increased automatically')
    rp = sc.add_parser('rdma-probe', help='Execute bounded real RDMA messages on a generated small fabric; no frontend service yet')
    rp.add_argument('--topology', type=Path, required=True, help='Directory produced by scaleout topology')
    rp.add_argument('--config', type=Path, required=True, help='Explicit RDMA/ECN protocol JSON; optional switch_links/1 failures with declared route-update delay')
    rp.add_argument('--messages', type=Path, required=True, help='Bounded microprobe message array, not an LLM workload')
    rp.add_argument('--backend', type=Path, required=True, help='Executable returned by rdma-build')
    rp.add_argument('--output', type=Path, required=True, help='New result directory on a volume with at least 8GiB free')
    rt = sc.add_parser('rdma-transaction-probe', help='Execute native MRT1 transactions; v4 guarded copy, v5 sparse/prefix and explicit self-peer service, optional NIC registrations/local views; not GIN/RoCE timing')
    rt.add_argument('--topology', type=Path, required=True)
    rt.add_argument('--protocol', type=Path, required=True)
    rt.add_argument('--config', type=Path, required=True)
    rt.add_argument('--backend', type=Path, required=True, help='transaction_probe executable from rdma-build')
    rt.add_argument('--output', type=Path, required=True, help='New bounded result directory')
    session = sc.add_parser('rdma-transaction-session', help='Submit v4/v5 work after actual native callbacks; optional NIC registrations/local views, v5 sparse/prefix and explicit self-peer service; not modern GIN/GPU timing')
    for name in ('topology', 'protocol', 'config', 'backend', 'output'):
        session.add_argument('--'+name, type=Path, required=True)
    context = sc.add_parser('rdma-context-plan', help='Bind explicit communicator/context/peer identities to NIC-bound persistent MRT1 connections; self-peer unsupported, not full GIN')
    for name in ('config','topology','protocol','output'):
        context.add_argument('--'+name,type=Path,required=True)
    context_check = sc.add_parser('rdma-context-check', help='Verify a context plan against actual native transaction packets and connection reuse; requires complete endpoint/wire capture')
    for name in ('plan','run','output'):
        context_check.add_argument('--'+name,type=Path,required=True)
    causal = sc.add_parser('rdma-causal-plan', help='Lower saved public wave DAGs to persistent byte/local work; not ASTRA scheduling equivalence or modern GIN')
    for name in ('config', 'topology', 'protocol', 'output'):
        causal.add_argument('--'+name, type=Path, required=True)
    rs = sc.add_parser('rdma-stream-probe', help='Execute persistent native byte writes; optional source_rate_capture or dcqcn_reference; not GIN/GPU visibility or hardware calibration')
    rs.add_argument('--topology', type=Path, required=True, help='Generated physical RDMA topology directory; no intra-host bypass')
    rs.add_argument('--protocol', type=Path, required=True, help='Native DCQCN, ACK interval 1; optional switch_links/1; transport_recovery belongs in work config, legacy recovery/admission excluded')
    rs.add_argument('--config', type=Path, required=True, help='NIC-bound causal work; v1 fixed 16/64 counts, v2/v3 up to 128/8192, v4 up to 4096 streams/65536 work with explicit link-transmission budget')
    rs.add_argument('--backend', type=Path, required=True, help='stream_probe executable returned by rdma-build')
    rs.add_argument('--output', type=Path, required=True, help='New result directory; maximum 60s/64MiB artifacts; v1-v3 RSS capped at 1GiB, v4 uses protocol host limits')
    run_rdma = sc.add_parser('rdma-run', help='Run ready-KV MoE with synthetic or source-checked routes through LLMServingSim, ASTRA and native RDMA ns-3')
    run_rdma.add_argument('--config', type=Path, required=True, help='Model/workload/groups, declared compute, and optional source-checked route_replay')
    run_rdma.add_argument('--topology', type=Path, required=True, help='Generated physical topology directory')
    run_rdma.add_argument('--protocol', type=Path, required=True, help='Explicit RDMA protocol JSON')
    run_rdma.add_argument('--backend', type=Path, required=True, help='Native service executable from rdma-build')
    run_rdma.add_argument('--output', type=Path, required=True, help='New bounded run directory')
    run_rdma.add_argument('--protoc', default='protoc', help='Existing compiler for the pinned Chakra schema')
    run_rdma.add_argument('--host-timing', action='store_true', help='Write optional worker/native host wall and process-CPU phase traces; not simulated GPU/network time')
    ht = sc.add_parser('host-timing-check', help='Read optional host phases, including flushed timeout prefixes; does not assert service success')
    ht.add_argument('--run', type=Path, required=True, help='rdma-run directory with worker_host.jsonl and/or native_host.jsonl')
    ht.add_argument('--output', type=Path, required=True, help='New summary JSON; never add nested or cross-process wall times')
    verify_rdma = sc.add_parser('rdma-check', help='Audit actual token bytes, ASTRA dependencies, callbacks and next-wave scheduling')
    verify_rdma.add_argument('--run', type=Path, required=True, help='Completed native RDMA service directory')
    diagnose_rdma = sc.add_parser('rdma-diagnose', help='Audit recovery/RTT, conditional timeout bounds, source credits and callbacks; completed or failed runs, evaluation only')
    diagnose_rdma.add_argument('--run', type=Path, required=True, help='Native probe or service directory containing result.json, including failed runs')
    diagnose_rdma.add_argument('--output', type=Path, required=True, help='New diagnosis JSON; input run is preserved')
    sweep=sc.add_parser('sweep',help='Run an explicit bounded case list; vary model/batch/topology/protocol, retain failures and compare common graphs')
    sweep.add_argument('--config',type=Path,required=True,help='Sweep JSON; base paths resolve from its directory; object patches merge, null deletes optional keys')
    sweep.add_argument('--backend',type=Path,required=True,help='Native service executable from rdma-build')
    sweep.add_argument('--protoc',default='protoc',help='Existing pinned-schema compiler')
    sweep.add_argument('--output',type=Path,required=True,help='New output directory with aggregate wall/memory/disk limits')
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
            if args.scaleout_command == 'modern-deepep-native':
                from .modern_native import run
                value=run(args.topology,args.protocol,args.config,args.backend,args.output,callback_wakeups=args.callback_wakeups,host_timing=args.host_timing,callback_batches=args.callback_batches)
            elif args.scaleout_command == 'rdma-preflight':
                from .scaleout_fabric_buffers import preflight
                value=preflight(args.topology,args.protocol,args.output,args.transactions)
            elif args.scaleout_command == 'host-timing-check':
                from .host_timing import check
                value = check(args.run,args.output)
            elif args.scaleout_command == 'rdma-transaction-probe':
                from .scaleout_transactions import probe
                value = probe(args.topology, args.protocol, args.config, args.backend, args.output)
            elif args.scaleout_command == 'rdma-transaction-session':
                from .transaction_session import run
                value = run(args.topology, args.protocol, args.config, args.backend, args.output)
            elif args.scaleout_command == 'rdma-context-plan':
                from .context_transactions import execute
                value = execute(args.config,args.topology,args.protocol,args.output)
            elif args.scaleout_command == 'rdma-context-check':
                from .context_transactions import check
                value = check(args.plan,args.run,args.output)
            elif args.scaleout_command == 'rdma-causal-plan':
                from .scaleout_causal_waves import execute
                value = execute(args.config, args.topology, args.protocol, args.output)
            elif args.scaleout_command == 'rdma-stream-probe':
                from .scaleout_streams import probe
                value = probe(args.topology, args.protocol, args.config, args.backend, args.output)
            elif args.scaleout_command == 'modern-deepep-replay':
                from .modern_deepep_runtime import replay
                value = replay(args.config, args.actions, args.output)
            elif args.scaleout_command == 'modern-deepep-window-replay':
                from .modern_deepep_windows import replay
                value = replay(args.config, args.actions, args.output)
            elif args.scaleout_command == 'modern-deepep-plan':
                from .modern_deepep_plan import execute
                value = execute(args.config, args.output)
            elif args.scaleout_command == 'modern-deepep-check':
                from .modern_deepep_plan import check
                value = check(args.plan, args.output, args.withhold)
            elif args.scaleout_command == 'deepep-plan':
                from .deepep_plan import execute
                value = execute(args.config, args.output)
            elif args.scaleout_command == 'deepep-check':
                from .deepep_plan import check
                value = check(args.plan, args.output, args.withhold)
            elif args.scaleout_command == 'sweep':
                from .scaleout_sweep import run
                value=run(args.config,args.backend,args.output,args.protoc)
            elif args.scaleout_command == 'rdma-diagnose':
                from .scaleout_diagnostics import diagnose
                value = diagnose(args.run, args.output)
            elif args.scaleout_command == 'rdma-check':
                from .scaleout_verify import verify
                value = verify(args.run)
            elif args.scaleout_command == 'rdma-run':
                from .scaleout_service import run
                value = run(args.config,args.topology,args.protocol,args.backend,args.output,args.protoc,host_timing=args.host_timing)
            elif args.scaleout_command == 'rdma-build':
                from .scaleout_rdma import build
                value = build(args.output, cmake=args.cmake, ninja=args.ninja,
                              protobuf_prefix=args.protobuf_prefix, resume=args.resume,
                              compile_wall_seconds=args.compile_wall_seconds)
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
