"""Bounded source/evidence archive and Chinese review for the fixed study."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

ROOT=Path(__file__).resolve().parents[2]


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('task','output','checkpoint'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();task,out,checkpoint=(getattr(a,n).resolve() for n in ('task','output','checkpoint'))
    assert not out.exists() and not checkpoint.exists() and checkpoint.parent==task
    assert str(task).startswith('/dev/shm/') and shutil.disk_usage(task).free>=8*2**30
    v=read(task/'verification-final.json');s=read(task/'analysis-fixed/summary.json')
    assert v['verified'] and v['toy_permutations']==24 and v['checked_events']==18006
    assert v['cases']['real']['allocations']==1 and v['cases']['real']['incorrectly_dropping_local_tokens']['changed_profile_cells']==39
    out.mkdir();checkpoint.mkdir()
    for name,source in [('verification.json',task/'verification-final.json'),('summary.json',task/'analysis-fixed/summary.json'),
        ('sources.json',task/'input/observed/sources.json'),('scope.json',task/'input/observed/scope.json')]:shutil.copyfile(source,out/name)
    monitors=[]
    for p in sorted(task.glob('*-monitor/probe.json')):
        r=read(p);monitors.append(dict(path=str(p.relative_to(task)),status=r['status'],exitcode=r['child_exit_code'],
            wall_seconds=r['wall_seconds'],peak_rss_bytes=r['peak_sampled_group_rss_bytes'],command=r['command'],cwd=r['cwd']))
    assert all(r['status']=='completed' or r['path']=='analysis-monitor/probe.json' for r in monitors)
    save(out/'maintenance.json',dict(base_commit='ee1f3c9',monitors=monitors,failed_analysis_preserved=True,
        analysis_correction='Remove extra requirement that the real-case schedules must differ; require the predeclared toy witness, report real negative result',
        first_analysis_implementation_preserved=False,event_execution_rerun_after_failure=False,
        real_event_orders_modified=False,network_or_GPU_execution=False,dependency_changes=False))
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>槽位顺序与网络需求的约简边界</title>
<style>body{font:17px/1.85 system-ui,sans-serif;background:#f1f5f9;color:#182c43;margin:0}main{max-width:1000px;margin:40px auto;padding:40px;background:#fff;border-radius:14px}h1{font-size:30px}h2{font-size:22px;margin-top:32px}.note{padding:18px;background:#eff6ff;border-left:4px solid #2563eb}.negative{padding:18px;background:#fff5e9;border-left:4px solid #c77716}table{width:100%;border-collapse:collapse;font-size:15px}td,th{padding:12px;border-bottom:1px solid #dae2ed;text-align:left}code{background:#edf2f7;padding:2px 5px}a{color:#1455a5}small{color:#52657a}@media(max-width:700px){main{margin:0;padding:20px}table{font-size:13px}}</style><main>
<small>2026-10-23 · Scale-Out研究 · 固定源码与声明事件</small><h1>总通信量能算准，具体token仍不能随意合并</h1>
<p class="note">固定现代direct布局下，不必枚举每一种token接收槽位，就能精确核算Combine每条声明连接的token操作数。但token身份、地址和前后依赖仍然可能变化；总量相同不能推导完成时间相同。</p>
<h2>为什么成立</h2><p>同一原始rank发给同一专家owner的去重token，占据一段连续的接收序号。Combine把这些序号连续分给执行通道。每条连接的操作数，是两段区间的交集长度；改变哪一个token占哪个槽位，不改变区间长度。固定H7168/Top8时，每次跨rank返回put操作数为14,400B。</p>
<h2>本轮结果</h2><table><tr><th>核对</th><th>实际结果</th></tr><tr><td>人工4-token全部24个合法分配顺序</td><td>每连接始终2次返回操作；同一个token会换连接，4个token均有见证。</td></tr><tr><td>四份已见路由的48-token前缀</td><td>149个source→owner条目，32个本地旁路、117个跨rank返回put；后者操作数1,684,800B。</td></tr><tr><td>独立核验</td><td>6份固定源码、48份原始token选择、18,006个事件、543个Combine绑定；两次分析5产物一致。</td></tr></table>
<p class="negative">保留的负结果：真实输入的三种就绪策略，实际只产生一种槽位分配。它们没有形成有效的顺序对照，不能称三种独立验证。没有事后增加策略找差异。首次分析因额外要求真实输入也必须出现换连接见证而失败，随后仅修正分析断言，保留原事件记录。</p>
<h2>对网络建模更直接的提醒</h2><p>本地旁路token不发跨机包，但仍占接收序号。如果先删掉它们再计算远端token的通道，跨机总量仍为117次，声明context/connection需求表却有39个单元变化，至少20份token操作数的需求质量被重新分配。</p><p>这是故意采用错误计数规则的静态对照，现有平台计划已经保留本地token，未发现当前平台有这39处错误。此结果不等于网口利用率、线上包或时延改善。</p>
<h2>为什么不能直接合并成整块流量</h2><p>纯代数反例：四个等长写的前处理工作量为9、9、1、1，两个串行通道各处理两个token。不同分配可形成18/2或10/10的通道工作量，但两通道都各有两次写。单位是抽象工作量，不是GPU时间或网络实验。它说明计数相同不足以保证一般有异质成本时的完成时间相同。</p>
<h2>下一步的实现约束</h2><p>静态需求生成与核验可以采用区间公式，动态执行仍须保留token身份、地址、源buffer、归约、实际就绪和完成。现代完整通信、self-peer服务、WQE/CQ、硬件校准、独立请求与规模验证没有由此完成。当前尚无A类论文主贡献，本轮只缩小了哪些量可精确约简的范围。</p>
<h2>证据和复核</h2><ul><li><a href="../../reviews/并发槽位与连接需求可约简边界_20261023.md">完整说明、公式与证明条件</a></li><li><a href="../../../experiments/paper_readiness/README.md#并发槽位与静态连接需求">公开复核命令</a></li><li><a href="verification.json">独立核验</a> · <a href="summary.json">结果摘要</a> · <a href="sources.json">固定源码</a> · <a href="maintenance.json">执行及失败记录</a></li><li><a href="archive.json">检查点</a> · <a href="replay.json">换源码根恢复</a> · <a href="closeout.json">资源收尾</a></li></ul><p><small>所有事件是无时间声明，没有网络/GPU性能测量。实际SM/共享内存/NIC未读回，声明设备号不是可部署硬件配置。原始件仅在/dev/shm/moe-slot-projection-20261023/，易失无持久备份。</small></p></main></html>'''
    (out/'report.html').write_text(page)
    sources={p for p in (ROOT/'python').rglob('*.py')}
    for n in ['slot_projection.py','check_slot_projection.py','package_slot_projection.py','slot_projection_protocol.md','modern_backend_contract.md','README.md']:
        sources.add(ROOT/'experiments/paper_readiness'/n)
    sources.add(ROOT/'docs/reviews/并发槽位与连接需求可约简边界_20261023.md');sources.add(ROOT/'deps.lock.json')
    files={str(Path('source')/p.relative_to(ROOT)):p for p in sources}
    for p in task.rglob('*'):
        if p.is_file() and not p.is_symlink() and p.relative_to(task).parts[0] not in ('checkpoint','recovery','package-monitor'):
            files[str(Path('raw')/p.relative_to(task))]=p
    for p in out.iterdir():files[str(Path('review')/p.name)]=p
    manifest={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(files.items())}
    assert sum(r['bytes'] for r in manifest.values())<48*2**20
    save(checkpoint/'members.json',manifest)
    archive=checkpoint/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for n,p in sorted(files.items()):t.add(p,arcname=n,recursive=False)
    assert archive.stat().st_size<16*2**20
    with tarfile.open(archive,'r:gz') as t:
        assert len(t.getmembers())==len(manifest)
        for m in t:
            data=t.extractfile(m).read();assert len(data)==manifest[m.name]['bytes']
            assert hashlib.sha256(data).hexdigest()==manifest[m.name]['sha256']
    restored=task/'recovery';restored.mkdir()
    with tarfile.open(archive,'r:gz') as t:
        for m in t:
            if m.name.startswith('source/') or m.name.startswith('raw/input/'):
                assert m.isfile() and '..' not in Path(m.name).parts
                target=restored/m.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(t.extractfile(m).read())
    receipt=dict(verified=True,path=str(archive),sha256=sha(archive),members=len(manifest),source_files=len(sources),
        member_bytes=sum(r['bytes'] for r in manifest.values()),compressed_bytes=archive.stat().st_size,
        persistent_backup=False,network_runtime_required=False)
    save(task/'archive.json',receipt);save(out/'archive.json',receipt);print(json.dumps(receipt))


if __name__=='__main__':main()
