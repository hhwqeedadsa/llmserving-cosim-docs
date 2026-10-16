"""Capture public run metadata and plot measured host scaling costs.

This never executes a simulation or loads the large result.json. A captured
evidence JSON is sufficient to regenerate the report after tmpfs runs disappear.
Wall-limit cases are right censored, not successful 600-second observations.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform


def read(path):
    return json.loads(path.read_text())


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


def command_time(commands, name):
    matches = [c for c in commands if c['name'] == name]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('ambiguous command: ' + name)
    command = matches[0]
    if 'started_utc' not in command or 'ended_utc' not in command:
        return None
    duration = timestamp(command['ended_utc']) - timestamp(command['started_utc'])
    if duration < 0:
        raise ValueError('negative UTC command interval')
    return duration


def capture_case(identity, run):
    run = run.resolve()
    files = {}
    documents = {}
    paths = {'input': run/'input.json', 'probe': run/'execution/probe.json',
             'run_provenance': run/'provenance.json',
             'verification': run/'verification.json',
             'commands': run.parent/'commands.json',
             'campaign_start': run.parent/'provenance.start.json'}
    for name, path in paths.items():
        if not path.exists():
            if name in ('input', 'probe', 'run_provenance'):
                raise ValueError('required evidence missing: ' + str(path))
            continue
        documents[name] = read(path)
        files[name] = dict(path=str(path), sha256=digest(path))
    topology_path = run/'topology.json'
    topology = read(topology_path)
    counts = Counter(n['kind'] for n in topology['nodes'])
    topo = dict(counts=dict(counts), links=len(topology['links']),
                pods=len({n['pod'] for n in topology['nodes'] if n['kind']=='gpu'}),
                servers=len({n['server'] for n in topology['nodes'] if n['kind']=='gpu'}))
    files['topology'] = dict(path=str(topology_path), sha256=digest(topology_path))
    hashes = documents['run_provenance']['hashes']
    backend_hashes = {v for k, v in hashes.items() if k.endswith('ns3.42-moe-rdma-service')}
    if len(backend_hashes) != 1:
        raise ValueError('missing or ambiguous service executable hash')
    return dict(id=identity, run_path=str(run), source_files=files,
                documents=documents, topology_summary=topo,
                service_sha256=next(iter(backend_hashes)))


def machine_snapshot():
    cpu = next((l.split(':', 1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines()
                if l.startswith('model name')), 'unknown')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    data = dict(captured_utc=datetime.now(timezone.utc).isoformat(), cpu_model=cpu,
                platform=platform.platform(), available_cpu_ids=sorted(os.sched_getaffinity(0)),
                mem_total_kib=int(memory['MemTotal'].split()[0]),
                scope='snapshot during analysis; no historical CPU load or exclusive-host guarantee')
    for filename in ('cpu.max', 'memory.max'):
        path = Path('/sys/fs/cgroup')/filename
        if path.exists():
            data[filename] = path.read_text().strip()
    return data


def summarize(case):
    docs = case['documents']
    model, probe = docs['input'], docs['probe']
    groups = model['parallel']['groups']
    ep_sizes = sorted({len(g) for g in groups})
    if len(ep_sizes) != 1:
        raise ValueError('mixed EP sizes require a separate study')
    gpus = sum(map(len, groups))
    if len({r for g in groups for r in g}) != gpus or gpus != case['topology_summary']['counts']['gpu']:
        raise ValueError('rank set/topology mismatch')
    verification = docs.get('verification', {})
    completed = probe['status']=='completed' and probe['child_exit_code']==0
    verified = completed and verification.get('passed') is True
    censored = probe['status']=='wall_limit'
    if not probe.get('ended_utc') or probe.get('remaining_live_group_pids'):
        raise ValueError('only terminal runs can enter the report')
    if censored and verification.get('passed'):
        raise ValueError('timeout with stale successful verification')
    commands = docs.get('commands', [])
    ep = ep_sizes[0]
    row = dict(id=case['id'], implementation=case['service_sha256'][:12],
               service_sha256=case['service_sha256'],
               family='single_EP_equals_N' if len(groups)==1 and ep==gpus else f'fixed_EP{ep}_replicas',
               gpus=gpus, ep=ep, replicas=len(groups),
               batch_per_rank=model['workload']['batch_per_rank'],
               context_tokens=model['workload']['context_tokens'],
               decode_steps=model['workload']['decode_steps'], layers=model['model']['layers'],
               global_tokens_per_step=gpus*model['workload']['batch_per_rank'],
               model=model['model']['name'], gpu_label=model['model']['gpu_type'],
               compute_source=model['compute']['source'],
               route_source=docs['run_provenance']['route_source'],
               hardware_calibrated=docs['run_provenance']['hardware_calibrated'],
               status=probe['status'], child_exit_code=probe['child_exit_code'],
               independently_verified=verified, right_censored=censored,
               worker_wall_seconds=probe['wall_seconds'],
               wall_limit_seconds=probe['limits']['wall_seconds'],
               command_wall_seconds=command_time(commands, f'service-{gpus}'),
               independent_check_wall_seconds=command_time(commands, f'check-{gpus}'),
               sampled_peak_rss_bytes=probe['peak_sampled_group_rss_bytes'],
               rss_limit_bytes=probe['limits']['sampled_group_rss_bytes'],
               sampled_peak_artifact_bytes=probe['peak_sampled_artifact_bytes'],
               started_utc=probe['started_utc'], ended_utc=probe['ended_utc'],
               simulation_finish_ns=verification.get('finish_ns') if verified else None,
               messages=verification.get('messages') if verified else None,
               verified_nodes=verification.get('verified_nodes') if verified else None,
               overlap_with=[], overlap_seconds={}, **case['topology_summary'])
    # Total graph nodes and switches are different measures.
    row['switches'] = sum(row['counts'].get(tier, 0) for tier in ('tor', 'agg', 'core'))
    return row


def add_overlap(rows):
    for left in rows:
        for right in rows:
            if left['id']==right['id']:
                continue
            seconds = min(timestamp(left['ended_utc']), timestamp(right['ended_utc'])) - max(
                timestamp(left['started_utc']), timestamp(right['started_utc']))
            if seconds > 0:
                left['overlap_with'].append(right['id'])
                left['overlap_seconds'][right['id']] = seconds


def fmt(value, decimals=2):
    return '—' if value is None else f'{value:.{decimals}f}'


def render(evidence, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties

    output.mkdir(parents=True, exist_ok=False)
    rows = [summarize(case) for case in evidence['cases']]
    add_overlap(rows)
    write(output/'timings.json', dict(rows=rows, definitions=evidence['definitions'],
                                     machine=evidence['machine']))
    with (output/'timings.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list,dict)) else v
                             for k,v in row.items()})
    font_path = Path('/usr/share/fonts/truetype/wqy/wqy-microhei.ttc')
    if not font_path.exists():
        raise ValueError('existing CJK font required; no automatic installation')
    font = FontProperties(fname=str(font_path))
    plt.rcParams.update({'pdf.fonttype':42, 'axes.unicode_minus':False})
    implementations = sorted({r['implementation'] for r in rows})
    colors = {version: color for version, color in zip(implementations, ['#14628a','#cf7031','#54854f'])}
    if len(implementations)>len(colors):
        raise ValueError('too many implementations for this comparison plot')
    families = sorted({r['family'] for r in rows})
    fig, axes = plt.subplots(2, len(families), figsize=(12.8,8), squeeze=False)
    for column, family in enumerate(families):
        family_rows = [r for r in rows if r['family']==family]
        title = '固定 EP，多副本共享网络' if family.startswith('fixed_EP') else '单副本，EP 随卡数增加'
        for version in implementations:
            points = sorted([r for r in family_rows if r['implementation']==version], key=lambda r:r['gpus'])
            if not points:
                continue
            done = [r for r in points if r['independently_verified']]
            if done:
                axes[0,column].plot([r['gpus'] for r in done], [r['worker_wall_seconds'] for r in done],
                                    'o-', color=colors[version], label=version)
            else:
                axes[0,column].plot([], [], '^', mfc='white', mec=colors[version], label=version)
            for row in points:
                x = row['gpus']
                if row['right_censored']:
                    y = row['wall_limit_seconds']
                    axes[0,column].plot(x,y,'^',mfc='white',mec=colors[version],ms=9)
                    axes[0,column].annotate('',(x,y+80),(x,y),
                                            arrowprops=dict(arrowstyle='->',color=colors[version]))
                    # Separate same-x timeout annotations vertically.
                    offset = 16 if version==implementations[0] else -22
                    axes[0,column].annotate(f">{y:.0f}s ({version[:6]})",(x,y),
                                            xytext=(-125,offset),textcoords='offset points',fontsize=9,
                                            color=colors[version])
                else:
                    offset = (2,-16 if version==implementations[0] else 7)
                    if family=='single_EP_equals_N' and x==min(r['gpus'] for r in family_rows) and version==implementations[0]:
                        offset = (25,0)
                    axes[0,column].annotate(f"{row['worker_wall_seconds']:.1f}s",(x,row['worker_wall_seconds']),
                                            xytext=offset,
                                            textcoords='offset points',fontsize=9)
                # Censored RSS is only the observed partial-run peak; no fitted memory line.
                axes[1,column].plot(x,row['sampled_peak_rss_bytes']/1024**3,
                                    '^' if row['right_censored'] else 'o',
                                    color=colors[version],mfc='white' if row['right_censored'] else colors[version],ms=8)
                axes[1,column].annotate(f"{row['sampled_peak_rss_bytes']/1024**3:.2f}",
                                        (x,row['sampled_peak_rss_bytes']/1024**3),
                                        xytext=(-28 if version==implementations[0] else 5,8),
                                        textcoords='offset points',fontsize=9)
        axes[0,column].set_title(title, fontproperties=font, fontsize=13)
        for axis in axes[:,column]:
            axis.set_xticks(sorted({r['gpus'] for r in family_rows}))
            axis.grid(alpha=.22)
            axis.set_xlabel('GPU 数',fontproperties=font)
            axis.set_xlim(0, max(r['gpus'] for r in family_rows)*1.12)
        axes[0,column].set_ylabel('Worker 墙钟时间 / 秒',fontproperties=font)
        axes[1,column].set_ylabel('进程树采样峰值 RSS / GiB',fontproperties=font)
        axes[0,column].set_ylim(bottom=0,top=max(r['worker_wall_seconds'] for r in family_rows)*1.26)
        axes[1,column].set_ylim(bottom=0,top=max(r['sampled_peak_rss_bytes']/1024**3 for r in family_rows)*1.22)
        axes[0,column].legend(title='service SHA-256', fontsize=8)
    fig.suptitle('实际等待时间随规模增长：按工作量与实现版本分组',fontproperties=font,fontsize=16)
    fig.text(.04,.025,'空心三角与箭头：超时未完成；内存仅为中止前峰值。点间连线辅助阅读；部分作业同机重叠，不能解释为受控加速比。',
             fontproperties=font,fontsize=9.5)
    fig.tight_layout(rect=[0,.055,1,.95])
    fig.savefig(output/'runtime_growth.png',dpi=160)
    fig.savefig(output/'runtime_growth.pdf',metadata={'CreationDate':None,'ModDate':None})
    plt.close(fig)
    lines=['# Scale-Out 仿真实际等待时间与规模增长','',
           '本报告只读取已经结束的运行产物，没有新增仿真。Worker 时间含初始化、构图、后端执行和写出；不是纯 ns-3 CPU 时间。', '',
           '![运行成本](runtime_growth.png)','',
           '| 卡数 | EP / 副本 | 实现 SHA 前缀 | Worker 秒 | 完整命令秒 | 独立校验秒 | 峰值 GiB | 仿真完成 ms | 状态 |',
           '|---:|---|---|---:|---:|---:|---:|---:|---|']
    for row in rows:
        status = '超过600秒未完成' if row['right_censored'] and row['wall_limit_seconds']==600 else row['status']
        if row['independently_verified']:
            status='完成且独立校验通过'
        simulation = None if row['simulation_finish_ns'] is None else row['simulation_finish_ns']/1e6
        wall = f">{row['wall_limit_seconds']:.0f}" if row['right_censored'] else fmt(row['worker_wall_seconds'])
        lines.append(f"| {row['gpus']} | {row['ep']} / {row['replicas']} | {row['implementation']} | {wall} | "
                     f"{fmt(row['command_wall_seconds'])} | {fmt(row['independent_check_wall_seconds'])} | "
                     f"{row['sampled_peak_rss_bytes']/1024**3:.2f} | {fmt(simulation,6)} | {status} |")
    workload_labels = sorted({f"{r['model']}，{r['layers']} 层，batch/rank={r['batch_per_rank']}，context={r['context_tokens']}，Decode {r['decode_steps']} 轮，GPU 标签 {r['gpu_label']}，计算来源 {r['compute_source']}，路由 {r['route_source']}，硬件校准={r['hardware_calibrated']}"
                              for r in rows})
    lines += ['', '空白时间没有相应采集，不能用零补齐。命令时间与 Worker 时间之差混合准备、退出和自动校验，不能全部称为纯校验开销。', '',
              '## 可比性与已知同机重叠', '',
              '工作量配置：'+'；'.join(workload_labels)+'。宿主时间不是 GPU 实机运行时间。', '',
              'EP=N 单副本与 EP4 多副本改变了不同工作量，分别绘图。实现版本由运行时记录的 service 二进制 SHA-256 识别，不能用当前 HEAD 代替当时版本。', '']
    for row in rows:
        if row['overlap_with']:
            detail=', '.join(f"{identity}：{seconds:.2f}s" for identity,seconds in row['overlap_seconds'].items())
            lines.append(f"- {row['id']} 的已知重叠：{detail}。")
    lines += ['', '重叠只在本报告收录的 Worker 间核算；未证明其他时段宿主独占。单次样本不支持稳定分位数、严格复杂度或可靠优化加速比。', '',
              '## 实际增长与使用建议', '']
    comparable = [r for r in rows if r['family']=='fixed_EP4_replicas' and r['independently_verified']]
    pairs = [(a,b) for a in comparable for b in comparable if a['gpus']==256 and b['gpus']==512
             and a['service_sha256']==b['service_sha256']]
    for a,b in pairs:
        ratio=b['worker_wall_seconds']/a['worker_wall_seconds']
        matrix=(b['command_wall_seconds']+b['independent_check_wall_seconds'])*36/3600
        lines += [f"256→512 卡的本次 Worker 等待从 {a['worker_wall_seconds']:.2f}s 增至 {b['worker_wall_seconds']:.2f}s，观测比值 {ratio:.3f}。512 卡含同机重叠，不能据此认定线性复杂度。", '',
                  f"以本次 512 卡完整命令＋独立校验 {b['command_wall_seconds']+b['independent_check_wall_seconds']:.2f}s 粗算，36 个同等成本用例顺序执行约 {matrix:.2f} 小时；不含编译、失败重试，其他 batch/模型不能照搬。", '']
    lines += ['超时只能确定未在预算内完成，无法得到完整业务模拟时间和最终内存峰值。本轮按已声明预算保留终态，不因超时继续加预算或追加优化。', '',
              '优先在百卡或 256/512 卡做网络机制对照，用少量目标规模点核查趋势；先估计总用例数×单例预算，再决定是否展开矩阵。保持模型、EP、副本、层数、batch、拓扑和实现版本可追踪。', '',
              '## 复核入口', '',
              'timings.csv / timings.json 保存全部时钟、预算、状态、规模及已知重叠。evidence.json 保存公开小文件的内容、原路径与 SHA-256，可在原始 tmpfs 不存在时重绘；原始完整结果另行归档。', '',
              f"宿主 CPU：{evidence['machine']['cpu_model']}；系统内存 {evidence['machine']['mem_total_kib']/1024**2:.1f} GiB。硬件与可用 CPU 为分析时快照，无历史独占负载记录。", '']
    (output/'report.md').write_text('\n'.join(lines))
    write(output/'evidence.json',evidence)
    write(output/'manifest.json',dict(script_sha256=digest(Path(__file__)),
        simulation_executed=False,files={p.name:digest(p) for p in sorted(output.iterdir()) if p.is_file()}))
    print(json.dumps(dict(cases=len(rows),output=str(output),simulation_executed=False),ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--case',action='append',nargs=2,metavar=('ID','PUBLIC_RUN_PATH'))
    source.add_argument('--evidence',type=Path,help='Replot a previously captured evidence.json')
    parser.add_argument('--output',type=Path,required=True,help='New report directory')
    args=parser.parse_args()
    if args.case:
        if len({identity for identity,_ in args.case})!=len(args.case):
            parser.error('case identities must be unique')
        evidence=dict(schema='moe-scaleout-runtime-evidence/1',
            definitions=dict(worker_wall='monotonic host wall time: initialization + graph + backend + writeout',
                command_wall='UTC start/end of public rdma-run; includes automatic checks and preparation',
                independent_check='UTC start/end of a separate rdma-check invocation',
                rss='sampled owned process-tree peak; timeout is partial-run peak only',
                censoring='wall_limit is right censored at declared limit; no final simulation time',
                cpu_time='not captured',repetitions='one measurement per case; no fitted complexity'),
            machine=machine_snapshot(),cases=[capture_case(identity,Path(path)) for identity,path in args.case])
    else:
        evidence=read(args.evidence)
        if evidence.get('schema')!='moe-scaleout-runtime-evidence/1':
            parser.error('unsupported evidence schema')
    render(evidence,args.output.resolve())


if __name__=='__main__':
    main()
