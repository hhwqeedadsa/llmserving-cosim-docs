"""Build a readable offline report from saved checked runs, never run networks."""
import argparse
import ast
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')


def monitors(run,include_evidence):
    paths=sorted((run/'monitors').glob('*/probe.json'))
    if not include_evidence:paths=[p for p in paths if not p.parent.name.startswith('evidence')]
    paths += [run/mode/'execution/probe.json' for mode in ('normal','local','helper')]
    rows={str(p.relative_to(run)):load(p) for p in paths}
    assert all(r['status'] in ('completed','child_failed') and r['ended_utc'] and not r['remaining_live_group_pids'] for r in rows.values())
    assert sum(r['wall_seconds'] for r in rows.values())<1200
    assert {name for name,r in rows.items() if r['child_exit_code']!=0}=={'monitors/regression/probe.json','monitors/independent/probe.json'}
    return rows


def collect(run,verification_dir):
    verification=load(verification_dir/'verification.json');assert verification['passed']
    assert all(sha(Path(path))==value for path,value in verification['original_hashes'].items())
    build=load(run/'build/backend.json')
    assert all(sha(ROOT/path)==value for path,value in build['source_sha256'].items())
    assert sha(run/'build/ns3.42-moe-rdma-service')==build['service_sha256']
    assert all(sha(Path(path))==value for path,value in load(run/'build/original-hashes.json').items())
    assert 'Ran 83 tests' in (run/'monitors/regression-fixed/stderr.log').read_text()
    assert load(run/'monitors/fifo/stdout.log')['passed']
    probes=monitors(run,False)
    source=['backend/rdma/service.cc','backend/rdma/inflight_helpers.hh','backend/rdma/shared_compute.hh',
        'backend/rdma/source_ack_watchdog.hh','python/moe_ub_cosim/inflight_helpers.py',
        'python/moe_ub_cosim/contribution_binding.py','python/moe_ub_cosim/native_control.py',
        'python/moe_ub_cosim/scaleout_service.py','python/moe_ub_cosim/scaleout_verify.py',
        'python/moe_ub_cosim/service_stream_records.py','python/moe_ub_cosim/service_stream_verify.py',
        'python/moe_ub_cosim/service_transport.py','python/moe_ub_cosim/source_ack_watchdog.py',
        'examples/scaleout_inflight_helpers/prepare.py','tests/unit/inflight_helpers_test.py',
        'tests/integration/check_inflight_helpers.py','fixtures/inflight_helpers/build.py','fixtures/inflight_helpers/evidence.py']
    for path in source:
        if path.endswith('.py'):ast.parse((ROOT/path).read_text())
    dep=subprocess.run(['bash','scripts/check_deps.sh'],cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert dep.returncode==0 and 'PASS: checked 13 dependency checkouts' in dep.stdout
    shutil.copyfile(verification_dir/'verification.json',HERE/'verification.json')
    save(HERE/'receipt.json',dict(passed=True,execution_commit=verification['execution_commit'],
        source_sha256={path:sha(ROOT/path) for path in source},backend=build,
        commands=load(verification_dir/'commands.json'),build_commands=load(run/'build/commands.json'),
        monitors_before_collector=probes,dependency_check=dep.stdout,distinct_unit_tests=83,new_unit_tests=9,
        new_network_executions=3,dependency_changes=False,dependency_builds=0,gpu_executions=0,downloads=0,
        negative_checks=6,native_pre_fabric_rejections=5,
        preserved_failures=['old packet input missing service field; fixed default access',
            'overstrong old/new packet-identity assumption; shared FIFO changes equal-time event order, report exact deltas'],
        scope='actual auxiliary lifecycle and shared resources; application recovery remains incomplete'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    cases=verification['cases'];attempt=cases['helper']['attempts'][0];states=attempt['lifecycle']
    fig,axes=plt.subplots(2,1,figsize=(10,7),constrained_layout=True)
    labels=['Normal','Local fault + SR','Local fault + SR + helper'];modes=['normal','local','helper']
    for i,mode in enumerate(modes):
        axes[0].barh(i,cases[mode]['application_finished_ns']/1000,color=['#2f756e','#b56936','#3b70a4'][i])
        axes[0].text(cases[mode]['application_finished_ns']/1000+2,i,f"{cases[mode]['application_finished_ns']/1000:.3f} us",va='center')
    axes[0].set(yticks=range(3),yticklabels=labels,xlabel='Simulated completion time (us)',xlim=(0,205),title='Application gain: 0 us in this functional case')
    axes[0].invert_yaxis();axes[0].grid(axis='x',alpha=.2)
    points=[('Watchdog / helper admitted',states['accepted']),('Helper result at source',states['result_ready']),
        ('Helper fully retired',states['retired']),('Original EP Dispatch gate',cases['helper']['dispatch_gate_ns']),
        ('Original rank6 result',attempt['original_result_ns']),('All token contributions',attempt['call_logical_complete_ns']),
        ('Original wave retired',attempt['original_graph_retired_ns'])]
    for i,(label,ns) in enumerate(points):
        axes[1].hlines(i,0,ns/1000,color='#dce4e8');axes[1].plot(ns/1000,i,'o',color='#3b70a4' if i<3 else '#b56936')
        axes[1].text(ns/1000+1.5,i,f'{ns/1000:.3f}',va='center')
    axes[1].set(yticks=range(len(points)),yticklabels=[p[0] for p in points],xlabel='Simulated time (us)',xlim=(0,142),title='Earlier contribution does not bypass remaining dependencies')
    axes[1].invert_yaxis();axes[1].grid(axis='x',alpha=.2)
    fig.savefig(HERE/'timing.png',dpi=160);fig.savefig(HERE/'timing.svg');plt.close(fig)
    rows=''.join(f'<tr><td>{label}</td><td>{cases[m]["application_finished_ns"]/1000:.3f}</td><td>{cases[m]["actual_messages"]}</td><td>{cases[m]["packet_events"]}</td><td>{cases[m]["compute_tasks"]}</td><td>{cases[m]["declared_payload_bytes"]}</td></tr>' for label,m in zip(['正常','局部故障','相同故障＋一次备用'],modes))
    (HERE/'report.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>共享资源备用执行：为什么还没有业务收益</title><style>
    body{font:17px/1.8 system-ui,sans-serif;background:#f3f6f8;color:#183449;margin:0}main{max-width:1000px;margin:auto;padding:35px 22px}h1{font-size:32px;line-height:1.4}h2{font-size:23px}section{background:white;padding:22px 26px;margin:22px 0;border-radius:12px}table{border-collapse:collapse;width:100%;font-size:15px}td,th{padding:9px;border-bottom:1px solid #dce4e8;text-align:left}.callout{border-left:5px solid #b56936;background:#fff4e9;padding:16px}a{color:#1c638a}img{width:100%}code{overflow-wrap:anywhere}small{color:#566c7b}.table{overflow:auto}</style><main>
    <small>128端点Clos · 人工功能路由 · 同一GPU/NIC/网络资源 · 执行版本 '''+html.escape(verification['execution_commit'][:10])+'''</small>
    <h1>备用贡献提前62.002微秒，<br>整项任务仍没有变快</h1>
    <p class="callout"><strong>已证明：</strong>备用输入、计算、归并、回传和ACK真实执行，共享原来的资源。<strong>尚未实现：</strong>备用贡献替代原图必要依赖、推动推理请求提前完成。不能把单项提前当作整体恢复收益。</p>
    <section><h2>这轮做了什么</h2><ol><li>在确认超时处加入同一ns-3时刻的同步决策回复，避免前端把恢复动作回填到过去。</li><li>普通计算和备用计算共用每GPU非抢占FIFO；备用消息通过原ServiceTransport、NIC和Clos传输。</li><li>将真实备用生命周期绑定到贡献的一次提交、迟到结果判重和全部资源退休；保存完整逐包记录与独立审核。</li></ol></section>
    <section><h2>三组实际结果</h2><div class="table"><table><tr><th>条件</th><th>任务完成/us</th><th>实际消息</th><th>逐包事件</th><th>计算任务</th><th>布局载荷/B</th></tr>'''+rows+'''</table></div>
    <p>三组原图均3708节点、62消息、2560计算任务，四份独立人工路由、31个数据活跃rank。局部故障仅在[0,20us)丢弃发往GPU6的DATA，实际丢1包，控制包保留；已有选择性重传SR和参考DCQCN保持开启。两组EP64完整副本均已声明，三组均声明相同108条持久流，不临时增加GPU或独立网络。</p>
    <img src="timing.png" alt="三组任务完成时间及备用贡献、门限、原图退休的时间点"><p>图中SR表示选择性重传；时间为仿真时钟，不是宿主运行时间。布局载荷不含协议头和ACK，逐包事件数包含逐跳发送接收，不能当作源端包数。</p></section>
    <section><h2>为什么先返回也没用</h2><p>示例token需要原rank6的专家12—15，也需要rank8的专家16—19。0→6的原Dispatch丢包后，前端于21.030us收到超时并请求rank7执行同一专家集合。备用输入23.058us到达，专家计算400ns、归并300ns，结果25.782us回到源端；原rank6结果87.784us才回到源端，单包贡献提前62.002us。</p>
    <p>可是rank8虽然5.100us就收到输入，专家计算仍须等整个EP组Dispatch门限85.060us释放。rank8的结果89.808us才返回。所以同一token全部专家到齐时间仍为89.808us，原首轮图123.834us退休，完整两层两步服务175.778us结束。原消息继续真正确认，迟到的rank6结果被记为冗余，没有伪造ACK或跳过原连接。</p>
    <p class="callout">当前实测任务收益0%，新增1216B布局载荷（640B输入＋576B回传，约为原37696B的3.23%），新增700ns声明计算。健康组本例未观察到完成时间恶化；不能推广为繁忙集群下备用无干扰。</p></section>
    <section><h2>发现与下一步</h2><p>只增加重传、超时或备用请求还不够。下一步应检查通信接口能否允许已具备完整输入的贡献独立计算，并让真实结果到达驱动token级归并，同时把原传输排空放到后台。这需要明确执行语义和实际资源成本，不能简单删除现有EP门限或伪造原任务完成。若实际通信库必须等待整个调用完成，就应把优化放在调用内部，或承认该部署下备用策略没有收益。</p>
    <p>当前尚无经过验证的新恢复算法或真实R1收益。功能用例通过后仍须使用真实专家路由、合理故障范围和同副本预算的成熟备用基线，再测SLO、尾延迟、额外带宽/计算及健康任务影响；不为追求正收益削弱基线或制造任意热点。</p></section>
    <section><h2>验证与边界</h2><p>83项单元检查通过，含9项新检查；无网络CPU检查独立验证6任务FIFO时序和3类非法输入。三个新128端点Clos条件各运行一次，完整记录和公开rdma-check均通过。6类记录篡改、5类原生非法声明被拒绝；迁移后检查通过。没有依赖重编、升级、下载或真实GPU执行。</p>
    <p>与旧非共享队列结果离线比较：两组完成时间不变。新正常条件的同纳秒事件顺序改变，两条ACK回调和三个节点时刻最大差1ns；不能声称旧新原件逐字节一致。局部故障原包、消息和节点时刻一致，原始事件序列顺序仍有差异。当前故障对照与备用条件使用相同共享队列，其原执行事件逐字节一致。</p>
    <p>首次CPU回归发现旧输入缺service字段，已修复；首次离线审核错误要求新旧包完全相同，已保留失败并改为明确报告真实差异。两次均未重跑网络。功能例不是完整DeepSeek-R1，没有激活/权重数值验证，H100标签和声明成本没有硬件校准。</p>
    <p><a href="verification.json">独立核验</a> · <a href="receipt.json">源码、命令与监控</a> · <a href="resources.json">资源汇总</a> · <a href="final-audit.json">最终核查</a> · <a href="../../docs/inflight_helpers.md">接口文档</a> · <a href="../../examples/scaleout_inflight_helpers/README.md">公开复现示例</a></p></section></main></html>''')
    print(json.dumps(dict(passed=True,report=str(HERE/'report.html'),application_gain_ns=0)))


def close(run):
    probes=monitors(run,True);verification=load(HERE/'verification.json');receipt=load(HERE/'receipt.json')
    assert all(sha(Path(path))==value for path,value in verification['original_hashes'].items())
    assert all(sha(ROOT/path)==value for path,value in receipt['source_sha256'].items())
    all_files=[p for p in run.rglob('*') if p.is_file()]
    apparent=sum(p.stat().st_size for p in all_files);allocated=sum(p.stat().st_blocks*512 for p in run.rglob('*'))
    assert max(apparent,allocated)<768*2**20
    save(HERE/'resources.json',dict(passed=True,monitored_commands=len(probes),
        cumulative_monitored_seconds=sum(r['wall_seconds'] for r in probes.values()),
        peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in probes.values()),
        actual_run_files=len(all_files),apparent_bytes=apparent,allocated_bytes=allocated,
        remaining_live_group_pids=[],preserved_failed_commands=2,new_network_executions=3,
        monitored_commands_including_failures=probes,scope='nested monitors counted as registered monitored seconds, not elapsed turn time'))
    links=[]
    for path in [HERE/'README.md',HERE/'report.html',ROOT/'docs/inflight_helpers.md',ROOT/'examples/scaleout_inflight_helpers/README.md']:
        text=path.read_text();targets=re.findall(r'\]\(([^)]+)\)',text)+re.findall(r'(?:href|src)="([^"]+)"',text)
        for link in targets:
            if '://' in link or link.startswith('#'):continue
            target=(path.parent/link).resolve()
            assert target.is_file() or target==HERE/'final-audit.json',str(target)
            links.append(str(target.relative_to(ROOT)))
    save(HERE/'final-audit.json',dict(passed=True,source_and_original_hashes_match=True,terminal_resources_verified=True,
        independent_verification_sha256=sha(HERE/'verification.json'),links=links,
        platform_auxiliary_execution_verified=True,application_recovery_complete=False,application_gain_ns=0,
        scope='auxiliary input/compute/result and retirement verified; whole research objective remains active'))
    print(json.dumps(dict(passed=True,resources_closed=True,application_recovery_complete=False)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--close',action='store_true');parser.add_argument('--verification',type=Path)
    args=parser.parse_args()
    if args.close:close(args.run.resolve())
    else:
        parser.error('--verification is required for collection') if args.verification is None else None
        collect(args.run.resolve(),args.verification.resolve())
