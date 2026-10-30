"""Preserve bounded live-binding evidence; no additional network execution."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_bytes())
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')


def main():
    root=ROOT/'runs/contribution_binding_20261030'
    paths=[*(root/'monitors').glob('*/probe.json'),*(root/mode/'execution/probe.json' for mode in ['normal','local'])]
    monitors={str(path.relative_to(root)):load(path) for path in paths}
    assert all(row['status']=='completed' and row['child_exit_code']==0 and not row['remaining_live_group_pids']
               and 'ended_utc' in row for row in monitors.values())
    assert sum(row['wall_seconds'] for row in monitors.values())<900
    assert 'Ran 56 tests' in (root/'monitors/related-unit/stderr.log').read_text()
    assert load(root/'legacy-workflow/verification.json')['commands']==5
    assert (root/'legacy-workflow/full/report.json').read_bytes()==(ROOT/'fixtures/contribution_attempts/example-report.json').read_bytes()
    verification=load(root/'final-independent/verification.json')
    assert verification['passed'] and verification['reference_unchanged_normal_native_graphs_and_events']
    assert verification['cumulative_budget_rejected_before_output'] and len(verification['negative_checks'])==6
    critical=verification['cases']['local']['target_dispatch']
    assert [row['time_ns'] for row in critical['source_transmissions']]==[1030,81030]
    source_hashes={}
    for mode in ['normal','local']:
        provenance=load(root/mode/'provenance.json')
        assert provenance['hardware_calibrated'] is False
        for name,digest in provenance['hashes'].items():
            assert sha(Path(name))==digest,name
        for name in ['result.json','input.json','rdma.json','contribution_calls.jsonl','contribution_summary.json','execution_events.jsonl']:
            source_hashes[mode+'/'+name]=sha(root/mode/name)
    dep=subprocess.run(['bash','scripts/check_deps.sh'],capture_output=True,text=True,timeout=20)
    assert dep.returncode==0 and 'PASS: checked 13 dependency checkouts' in dep.stdout
    names=['python/moe_ub_cosim/contribution_attempts.py','python/moe_ub_cosim/contribution_binding.py',
        'python/moe_ub_cosim/scaleout_service.py','python/moe_ub_cosim/scaleout_verify.py',
        'examples/scaleout_contribution_tracking/prepare.py','tests/unit/contribution_binding_test.py',
        'tests/integration/check_contribution_binding.py','fixtures/contribution_binding/evidence.py']
    for name in names:ast.parse((ROOT/name).read_text())
    links=[]
    for name in ['docs/contribution_binding.md','examples/scaleout_contribution_tracking/README.md','fixtures/contribution_binding/README.md']:
        path=ROOT/name
        for href in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            url=urlsplit(href)
            if url.scheme:continue
            target=(path.parent/unquote(url.path)).resolve()
            assert target.is_file() or target in [HERE/f for f in ['report.html','verification.json','receipt.json','resources.json']]
            links.append(str(target.relative_to(ROOT)))
    subprocess.run(['git','diff','--check'],check=True)
    commands=load(root/'final-independent/commands.json')
    assert len(commands)==8 and commands[0]['exit_code']==0 and all(row['exit_code']==1 for row in commands[1:])
    receipt=dict(passed=True,execution_commit=verification['execution_commit'],
        platform_source_sha256={name:sha(ROOT/name) for name in names},run_sha256=source_hashes,
        public_commands={name:row['command'] for name,row in monitors.items()},negative_workflow_commands=commands,
        prepared_input=load(root/'input/preparation-tracking.json'),tests=56,new_tests=11,related_tests=45,
        legacy_cli_commands=5,legacy_v1_report_byte_identical=True,
        native_network_executions=2,new_builds=0,dependency_changes=False,new_downloads=0,real_gpu_executions=0,
        dependency_check=dict(exit_code=dep.returncode,stdout=dep.stdout,stderr=dep.stderr),links=links,
        monitors_before_evidence=monitors,monitored_seconds_before_evidence=sum(row['wall_seconds'] for row in monitors.values()),
        sampled_peak_group_rss_bytes=max(row['peak_sampled_group_rss_bytes'] for row in monitors.values()),
        scope='actual original-attempt callback binding, declared simulation identity; no backup or optimization gain')
    shutil.copyfile(root/'final-independent/verification.json',HERE/'verification.json')
    save(HERE/'receipt.json',receipt)
    cases={mode:[json.loads(line) for line in (root/mode/'contribution_calls.jsonl').read_text().splitlines()] for mode in ['normal','local']}
    examples={mode:next(row for row in rows if row['call']=='g0.w0.l0.r0.s0') for mode,rows in cases.items()}
    table=''
    for label,field in [('输入就绪','input_ready_ns'),('专家开始','compute_begin_ns'),('专家完成','compute_done_ns'),('贡献返回','result_ready_ns')]:
        for rank in [6,8]:
            values=[examples[mode]['state']['attempts'][f'original.r{rank}'][field]/1000 for mode in ['normal','local']]
            table+=f'<tr><td>rank{rank} · {label}</td><td>{values[0]:.3f}</td><td>{values[1]:.3f}</td></tr>'
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>原专家贡献与实际执行绑定</title><style>body{{font:17px/1.75 system-ui,sans-serif;color:#163047;background:#f3f6fa;margin:0;padding:32px}}
main{{max-width:1080px;margin:auto;background:white;border-radius:12px;padding:32px}}h1{{line-height:1.3}}table{{width:100%;border-collapse:collapse}}
td,th{{padding:9px;border:1px solid #ccd5e1;text-align:left}}.note{{background:#edf5ff;border-left:5px solid #2875b5;padding:16px}}a{{color:#155da0}}</style>
<main><h1>从“包到了”进一步追到“哪个请求的专家贡献完成了”</h1>
<p class="note">原专家贡献已接入同一次ASTRA/ns-3的实时回调。当前只跟踪原尝试，没有启动备用专家，也没有新的恢复加速收益。</p>
<p>每个token按目标rank划分完整专家贡献集合。输入到达、聚合专家计算、贡献返回、发送确认与原图退出分开记录；
共享一条消息的多个贡献只引用同一次真实传输。</p>
<h2>两个128端点Clos功能条件</h2><p>16服务器×8，8ToR/8Agg/4Core，单rail/plane，各层400G、每链路1us。
两个EP64组、E256/Top8/H256、两层两步；4份不同人工来源、31个数据活跃rank。声明计算，未运行完整R1或GPU。</p>
<table><tr><th>指标</th><th>正常</th><th>GPU6前20us丢目的数据</th></tr>
<tr><td>token/层调用 · 贡献包 · 专家贡献</td><td>16 · 32 · 128</td><td>相同，全部覆盖和退出</td></tr>
<tr><td>生命周期事件 / 原生执行事件</td><td>380 / 7669</td><td>相同</td></tr>
<tr><td>消息 / 布局载荷</td><td>62 / 37696B</td><td>相同</td></tr>
<tr><td>完整包事件 / 注入丢弃包</td><td>2120 / 0</td><td>2126 / 1</td></tr>
<tr><td>任务完成 / 网络排空</td><td>103.852 / 103.852us</td><td>175.778 / 175.778us</td></tr></table>
<p>正常条件与上一轮保存的完整原生结果、图文件和事件逐项一致，没有重跑旧网络。
已有选择性重传和参考DCQCN保持原配置。局部故障只丢了一个目标数据包，不是全网100%丢包。</p>
<h2>同一请求内，受损和未受损专家为何都等待</h2><p>调用g0.w0.l0.r0.s0；下表均为同一ns-3绝对微秒。</p>
<table><tr><th>实际阶段</th><th>正常 / us</th><th>局部故障 / us</th></tr>{table}</table>
<p>rank8输入两次都在5.100us到达，但专家开始从13.134us推迟到85.060us。
这是原图整组Dispatch门限的等待传播，不是已经完成的健康专家计算被丢弃。</p>
<p>该调用的全部贡献正常17.882us、故障89.808us到齐；原图分别51.908us、123.834us才退出。
rank6的Combine接收正常15.858us、故障87.784us；源确认分别17.860us、89.786us。结果到达不提前关闭发送义务。</p>
<h2>收益与证据边界</h2><p>关键Dispatch消息2/stream0的实际源发送为1.030us，故障下再次发送为81.030us，
每次640B数据。20us故障结束不代表立即再次发送。本例额外任务等待71.926us是故障代价；
多少可以在相同资源和合法通信语义下消除，尚未验证。</p>
<p>路由JSON没有激活张量或门控权重。新增有类型的仿真来源身份，未填造input_sha256；
每rank聚合专家节点也不等于逐专家内核观测。</p>
<p>56项CPU检查通过，旧/1报告逐字节兼容；原JSON独立核对16调用/32包/128贡献。
迁移后的公开核验通过，6类篡改被拒绝，短缺预算在后端启动前拒绝。没有构建、依赖更新、下载、GPU执行或成功网络重跑。</p>
<p>下一步仍需实际可见的故障触发、合法备用执行和共享GPU/NIC竞争，再比较已有备用请求与候选机制。
当前保留单rank活动图、EP通信域、阶段门限及原连接退出约束。</p>
<p><a href="README.md">方法和边界</a> · <a href="verification.json">独立核验</a> · <a href="receipt.json">命令与版本</a> ·
<a href="resources.json">完整资源</a> · <a href="../../docs/contribution_binding.md">使用说明</a></p></main></html>'''
    with (HERE/'report.html').open('x') as stream:stream.write(html)
    print(json.dumps(dict(passed=True,tests=56,new_network_executions=2,invocations_per_condition=16,
                         bundles_per_condition=32,old_core_report_identical=True)))


if __name__=='__main__':main()
