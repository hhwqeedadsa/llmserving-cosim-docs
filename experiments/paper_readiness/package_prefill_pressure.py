"""Preserve the failed pressure run and its verified prefix, without network retries."""
import argparse
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
NATIVE = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/'
SOURCES = ['prefill_pressure.py','audit_prefill_pressure.py','package_prefill_pressure.py',
           'prefill_pressure_protocol.md','check_dispatch_network.py','analyze_dispatch_validation.py','README.md']


def load(path):
    return json.loads(path.read_text())


def digest(stream):
    h = hashlib.sha256()
    for block in iter(lambda:stream.read(2**20),b''):
        h.update(block)
    return h.hexdigest()


def sha(path):
    with path.open('rb') as stream:
        return digest(stream)


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def html(audit,replay):
    first,second = audit['cases']
    verified = [r for r in replay['streams'] if r['spacing_verified_without_equal_time_ambiguity']]
    floor_matches = sum(r['floor_rate_intervals_within_1ns'] for r in verified)
    example = next(r for r in replay['streams'] if r['case'] == 'layer4-depth8' and r['stream'] == 1)['largest_gap']
    metrics = [
        ('最终完成时间',f'{first["completion_ns"]/1e6:.6f} ms','未知；到5 ms仍未完成'),
        ('已ACK工作/声明工作',f'{first["acked_work"]}/344',f'{second["acked_work"]}/344'),
        ('已发出数据有效负载',f'{first["sent_payload_bytes"]:,} B',f'{second["sent_payload_bytes"]:,} B'),
        ('目的端唯一CE标记数据包',str(first['received_ce_packets']),str(second['received_ce_packets'])),
        ('物理接收钩子CE观测次数',str(first['ecn_hook_observations']),str(second['ecn_hook_observations'])),
        ('交换出口队列峰值',f'{first["max_queue_bytes"]:,} B',f'{second["max_queue_bytes"]:,} B'),
        ('PFC暂停观测',str(first['pfc_pause']),str(second['pfc_pause'])),
        ('完整工作单出口服务量下界',f'{first["minimum_full_work_port_service_ns"]/1e6:.6f} ms',f'{second["minimum_full_work_port_service_ns"]/1e6:.6f} ms'),
    ]
    rows = ''.join(f'<tr><td>{escape(name)}</td><td>{escape(left)}</td><td>{escape(right)}</td></tr>' for name,left,right in metrics)
    statuses = [
        ('层4/深度1','完成','344工作全部接收、ACK、排空'),
        ('层4/深度8','5ms时限未完成','保留终态前缀、退出码1；没有重跑'),
        ('层32/深度1','未执行','按首次网络失败停止规则'),
        ('层32/深度8','未执行','按首次网络失败停止规则'),
    ]
    status_rows = ''.join(f'<tr><td>{name}</td><td>{status}</td><td>{note}</td></tr>' for name,status,note in statuses)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>真实Prefill网络压力：保留失败的机制核查</title><style>
body{{font:16px/1.85 system-ui,sans-serif;max-width:1120px;margin:36px auto;padding:0 22px;color:#20334b;background:#fbfcfe}}
h1,h2{{line-height:1.4}}h2{{margin-top:30px}}aside{{padding:18px;background:#fff1df;border-left:4px solid #ba7421}}
table{{border-collapse:collapse;width:100%;font-size:15px}}td,th{{border-bottom:1px solid #d4dde8;padding:10px;text-align:left}}
th{{background:#edf2f7}}a{{color:#145c9d}}code{{background:#edf1f6}}.scroll{{overflow:auto}}
</style></head><body><h1>真实Prefill数据下，加深源队列没有保证更快</h1>
<p>2026-10-22 · 从03efe13开始 · 非UB原生RDMA ns-3 · 保留失败及未执行条件</p>
<aside>同一份344项数据写入，深度1在1.777657ms完成；深度8出现ECN，到5ms仍有83项未ACK。
源码与实际反馈核对显示，默认DCQCN参数是这一现象必须审查的解释；它不是硬件优化结论，也没有证明新的论文贡献。</aside>
<p><a href="audit.json">独立原件/包/工作核验</a> · <a href="rate-replay.json">源码与反馈重建</a> ·
<a href="maintenance.json">全部状态和实际命令</a> · <a href="protocol.md">执行前方法</a> · <a href="archive.json">本机完整检查点</a></p>
<h2>为什么换成这批工作量</h2><p>前几轮的小Decode窗口没有ECN/PFC，无法检验真实较大数据量下的反馈和源额度。
本轮取用户四份R1-AWQ/MMLU文件entry0的全部Prefill token，长度105/28/10/6，共149个；每个完整请求声明放在一个源rank。
连续等容量放置256专家、Top8，按目的rank去重，层4形成344份数据，每份14,464B，共4,975,616B有效负载。
没有复制原请求或把未来Decode步骤并作并发请求。</p>
<p>四个源映射到GPU0/2/4/6，跨四服务器、两个Pod；8GPU小Clos、100G接入/25G上层、seed11、12条持久连接。
所有独立写在0ns应用就绪，实际发送由ACK信用与DCQCN推进。两个条件仅改源工作队列深度1/8；
深度8是敏感性参数，未校准为真实NIC/CQ容量。同步可用与源部署也是声明条件，原数据没有到达时间。</p>
<h2>计划与实际执行分开</h2><table><tr><th>预先固定条件</th><th>实际状态</th><th>证据含义</th></tr>{status_rows}</table>
<p>没有延长5ms仿真时限、修改协议或重试网络。层32的349项输入已经生成并回查原件，但没有网络结果。
失败的深度8不能被当作5ms最终完成，也不能报“深度1加速了几倍”。宿主执行不是超时杀死：native正常写出5ms前缀后返回未完成错误。</p>
<h2>同一数据的实际观察</h2><div class="scroll"><table><tr><th>指标</th><th>深度1</th><th>深度8（截于5ms）</th></tr>{rows}</table></div>
<p>CE表示显式拥塞通知ECN的标记位。深度8收到2,293个唯一CE数据包；9,662是逐物理接收钩子的观测次数，同一包可重复计数，不能当9,662个唯一拥塞包。
无PFC暂停或故障注入丢弃。最忙端口服务量下界在两条件相同，它只含必要字节与速率，不包含反馈降速、源信用或传播，因此不能直接预测完成时间。</p>
<h2>先区分源长度不均与热门专家</h2><p>149个token中，最长请求占105个，约70.47%；四源远端数据份数为236/67/25/16，
源峰/均为{first['source_copy_max_to_mean']:.3f}；目的rank远端入量为38/95/89/122，峰/均为{first['destination_copy_max_to_mean']:.3f}。
同层单专家选择次数峰/均为{first['expert_peak_to_mean']:.3f}，各目的rank专家计算贡献为335/342/212/303。</p>
<p>这些指标的对象不同：单专家计算、源端发出、目的rank去重后的网络入量不能混成一个“热点程度”。
当前源映射中请求长度明显不均，不能把所有网络压力归因于热门专家；也没有执行重分配或证明改源映射可以免费消除差距。</p>
<h2>查到的参数与反馈机制</h2><table><tr><th>当前固定源码默认值</th><th>数值</th><th>作用</th></tr>
<tr><td>MinRate</td><td>100 Mb/s</td><td>降速的下限</td></tr>
<tr><td>RateDecreaseInterval</td><td>4 μs</td><td>检查并执行降速</td></tr>
<tr><td>AlphaResumInterval / EwmaGain</td><td>55 μs / 1/16</td><td>更新反馈权重</td></tr>
<tr><td>RPTimer</td><td>1500 μs</td><td>速率恢复定时器</td></tr>
<tr><td>RateAI / RateHAI</td><td>5 / 50 Mb/s</td><td>后续增加幅度</td></tr></table>
<p>Fabric没有覆盖这些属性。固定ReceiveUdp在每个包的ACK中依据ECN设置反馈标志，持久连接收到该ACK后调用DCQCN更新。
本次按真实数据接收、累计ACK序号和ACK接收时刻重建反馈，再按固定源码推进速率状态，未重新执行网络。</p>
<p>24条“条件×连接”的重建中，{len(verified)}条没有同纳秒外部事件/定时器顺序歧义，发送间隔都满足重建的速率约束。
其中{floor_matches}个实际发送间隔与100Mb/s下的包间隔在1ns内相符。
例如深度8/连接1，从{example['start_ns']:,}ns到{example['next_ns']:,}ns间隔{example['observed_gap_ns']:,}ns，
对应1036B模型帧在100Mb/s下的82,880ns。另一连接存在1次同纳秒采样歧义，明确排除其精确间隔认证。</p>
<p>最小重建速率可能发生在连接已无新包之后，因此JSON另外记录“实际发包时的最小重建速率”。
重建不是直接采样NIC内部状态，不证明已解释每一ns的整段业务时间；它支持“反馈降速与慢恢复是需要校准的机制解释”，
不能将1500μs默认值称为真实设备参数，更不能据此设计号称适用于生产的新算法。</p>
<h2>核验范围与保留的失败</h2><p>回查4份原件及4个已准备条件；实际核对9,094个源数据包、36,373个GPU端点事件，
核对源序号连续、数据长度、实际连续接收/ACK、slot复用及未发布项。完整条件40个物理端口的数据和ACK计数精确一致；
失败前缀40端口采用已送达/已注入路径界限，不能冒充全程逐跳捕获。捕获只选择4个活动GPU，交换机队列积分来自原生汇总观测，未独立逐事件重建。</p>
<p>第一次只读检查错误地假定尚未发布的工作已有字节区间，断言失败。源码确认这些区间在实际发布时才赋值，
检查器据此分开验证未发布状态；原失败、原脚本和修正记录均保留，没有修改平台或网络。
最终审计重复两份JSON逐字节一致，重复的只是只读核验。</p>
<h2>接下来应改什么、论文还缺什么</h2><ol>
<li>把DCQCN的生效参数、覆盖来源和必要状态显式记录，避免小场景无反馈时隐藏默认值。先验收观测不改变包和时间，再开展独立研究。</li>
<li>补匹配硬件/库分支的反馈、恢复和源额度测量；以未参与拟合的大小与并发验证。当前没有校准依据，不通过挑更快的定时器制造收益。</li>
<li>在完整现代数据/计数/屏障/可见性语义下检查多EP组共享网络；本轮只有数据写入，没有计算、Combine或完整DeepEP/GIN。</li>
<li>补独立请求和更大部署。当前四请求、一个已执行层和声明同时就绪不能证明生产热点或泛化。</li></ol>
<p>根卷打包前空间检查失败，未创建大文件；完整检查点改放内存盘，原打包失败及源码也保留。
内存盘检查点是易失的，尚未持久备份。小型报告进入本地Git，原始记录也仍留在内存盘。</p>
<p>本轮得到的是新的网络压力反例与参数证据，已改变后续检查方向；已知拥塞反馈现象本身不构成新的A类论文贡献。论文目标继续进行中。</p>
</body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('task','output','checkpoint','prior_archive','prior_members'):
        parser.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    parser.add_argument('--monitors',nargs='+',type=Path,required=True)
    args = parser.parse_args()
    task,out,checkpoint = (p.resolve() for p in (args.task,args.output,args.checkpoint))
    assert out.is_relative_to(ROOT) and checkpoint.is_relative_to(task) and checkpoint.is_relative_to(Path('/dev/shm'))
    assert not out.exists() and not checkpoint.exists()
    # Root-volume preflight failed and is preserved. Keep large new artifacts
    # on tmpfs, as in the earlier bounded group-capacity study; only the small
    # versioned review is written in the repository.
    assert shutil.disk_usage(checkpoint.parent).free >= 8*2**30+32*2**20
    for name in ('audit.json','rate-replay.json'):
        assert (task/'prefix-v3'/name).read_bytes() == (task/'prefix-repeat'/name).read_bytes()
    audit,replay = load(task/'prefix-v3/audit.json'),load(task/'prefix-v3/rate-replay.json')
    assert audit['passed'] and audit['network_conditions_complete'] == 1 and audit['network_conditions_time_censored'] == 1
    monitors = [load(p/'probe.json') for p in args.monitors]
    assert len(monitors) == 7 and all(not m['remaining_live_group_pids'] for m in monitors)
    assert [m['status'] for m in monitors] == ['completed','child_failed','child_failed','completed','completed','completed','child_failed']
    assert sum(m['wall_seconds'] for m in monitors) < 240
    prior = load(ROOT/'docs/review_artifacts/dispatch_validation_20261022/archive.json')
    assert sha(args.prior_archive) == prior['sha256']
    members = load(args.prior_members)
    with tarfile.open(args.prior_archive,'r:gz') as archive:
        assert len(archive.getnames()) == len(members) == prior['files']
        assert set(archive.getnames()) == set(members)
        for member in archive:
            assert member.isfile() and member.size == members[member.name]['bytes']
            assert digest(archive.extractfile(member)) == members[member.name]['sha256']
    pinned = load(ROOT/'docs/review_artifacts/dispatch_validation_20261022/packet-verification.json')
    for case in audit['cases']:
        result = load(task/'run'/case['case']/'result.json')
        for library in result['loaded_libraries']:
            if 'libns3' in library:
                assert sha(Path(library)) == pinned['native_libraries_sha256'][library]
    out.mkdir()
    for name in ('audit.json','rate-replay.json'):
        shutil.copyfile(task/'prefix-v3'/name,out/name)
    shutil.copyfile(ROOT/'experiments/paper_readiness/prefill_pressure_protocol.md',out/'protocol.md')
    save(out/'maintenance.json',dict(monitors=monitors,commands=load(task/'run/commands.json'),
        platform_start_commit='03efe13',package_start_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256={name:sha(ROOT/'experiments/paper_readiness'/name) for name in SOURCES},
        source_correction=load(task/'analysis-maintenance.json'),
        storage_correction=load(task/'storage-maintenance.json'),
        interpretation='1 completed network, 1 5ms-censored failure, 2 not started; network, first checker and root-disk packaging failures retained; only audit repeated',
        no_install_build_download_dependency_or_platform_change=True,goal_complete=False,
        package_probe_excluded_while_running=True))
    (out/'report.html').write_text(html(audit,replay))
    files = {}
    def tree(root,prefix):
        for p in sorted(root.rglob('*')):
            if p.is_file():
                assert not p.is_symlink()
                files[prefix+'/'+str(p.relative_to(root))] = p
    tree(task,'raw')  # checkpoint does not exist yet; do not include itself
    tree(out,'review')
    for directory in args.monitors:
        tree(directory,'monitors/'+directory.name)
    for name in SOURCES:
        files['source/'+name] = ROOT/'experiments/paper_readiness'/name
    native_sources = list(audit['source_hashes'])+[
        NATIVE+'point-to-point/model/qbb-net-device.cc',NATIVE+'point-to-point/model/point-to-point-net-device.cc',
        NATIVE+'network/utils/data-rate.cc',NATIVE+'network/utils/data-rate.h']
    for name in native_sources:
        files['repository/'+name] = ROOT/name
    files['source/current_task.md'] = ROOT/'docs/tasks/current.md'
    files['source/deps.lock.json'] = ROOT/'deps.lock.json'
    files['baseline/checkpoint.tar.gz'] = args.prior_archive
    files['baseline/members.json'] = args.prior_members
    identities = {name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(files.items())}
    assert sum(r['bytes'] for r in identities.values()) <= 96*2**20
    checkpoint.mkdir()
    target = checkpoint/'checkpoint.tar.gz'
    with tarfile.open(target,'w:gz') as archive:
        for name,p in sorted(files.items()):
            archive.add(p,arcname=name,recursive=False)
    assert target.stat().st_size <= 32*2**20
    with tarfile.open(target,'r:gz') as archive:
        assert len(archive.getnames()) == len(identities) and set(archive.getnames()) == set(identities)
        for member in archive:
            assert member.isfile() and member.size == identities[member.name]['bytes']
            assert digest(archive.extractfile(member)) == identities[member.name]['sha256']
    save(checkpoint/'members.json',identities)
    save(out/'archive.json',dict(path=str(target),sha256=sha(target),bytes=target.stat().st_size,files=len(files),
        all_member_hashes_verified=True,failed_network_and_checker_preserved=True,
        prior_archive_embedded=True,prior_archive_sha256=prior['sha256'],prior_member_hashes_verified=len(members),
        raw_requests_and_native_runtime_in_prior_archive=True,storage='tmpfs_volatile_not_persistent_backup',
        running_package_probe_and_self_manifest_excluded=True,
        restore='Extract into new directories; read-only audit needs the four manifest request paths and recorded native/runtime paths restored and hash checked. Prior archive supplies them; original RPATH is retained, not a portable runtime image.'))
    class Links(HTMLParser):
        def handle_starttag(self,tag,attrs):
            if tag == 'a':
                for key,value in attrs:
                    if key == 'href':
                        assert (out/value).is_file(),value
    Links().feed((out/'report.html').read_text())
    assert sum(p.stat().st_size for p in out.iterdir()) <= 2*2**20
    assert shutil.disk_usage(checkpoint).free >= 8*2**30
    print(json.dumps(dict(passed=True,files=len(files),archive_bytes=target.stat().st_size,output=str(out))))


if __name__ == '__main__':
    main()
