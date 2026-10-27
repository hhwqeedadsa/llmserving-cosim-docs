"""Seal source/runtime/evidence and recheck restored results without networking."""
import argparse
from copy import deepcopy
import html
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile

from fixtures.peer_copy.verify import load, save, sha, verify, corruptions
from fixtures.peer_copy.accept import budget, source_check, runtime_check, normalize_libraries


def report(stage, output):
    result=load(stage/'peer/result.json');checked=load(stage/'verification.json')
    rows={r['id']:r for r in result['local_work']};remote={r['id']:r for r in result['transactions']['operations']}
    events=[('0：GPU0 → GPU2（机内）',rows[0]['start_ns'],rows[0]['completed_ns'],'peer'),
            ('1：GPU1 → GPU2（机内）',rows[1]['start_ns'],rows[1]['completed_ns'],'peer'),
            ('2：GPU2 → GPU4（跨机数据交付）',remote[2]['published_ns'],remote[2]['applied_ns'],'wire'),
            ('3：GPU4 → GPU5（机内）',rows[3]['start_ns'],rows[3]['completed_ns'],'peer'),
            ('4：GPU5 → GPU0（跨机数据交付）',remote[4]['published_ns'],remote[4]['applied_ns'],'wire')]
    timeline=''.join(f'<tr><td>{html.escape(name)}</td><td>{start:,}</td><td>{end:,}</td>'
        f'<td><div class="track"><span class="{kind}" style="margin-left:{start/result["finish_ns"]*100:.5f}%;width:{(end-start)/result["finish_ns"]*100:.5f}%"></span></div></td></tr>'
        for name,start,end,kind in events)
    text=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>机内转发与跨机网络：功能验收</title><style>
body{{font:16px/1.7 system-ui,sans-serif;background:#f4f6fa;color:#1d2c42;margin:0}}main{{max-width:1050px;margin:auto;padding:36px 24px}}
h1{{font-size:30px;line-height:1.3}}h2{{font-size:21px;margin-top:30px}}.card{{background:white;border:1px solid #dce3ed;border-radius:12px;padding:20px;margin:16px 0}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.stats strong{{display:block;font-size:27px;color:#165c92}}small{{color:#52637a}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #e1e7ef}}td:last-child{{min-width:180px}}
.track{{height:16px;background:#edf1f7;display:flex}}.track span{{display:block;min-width:2px}}.peer{{background:#17876c}}.wire{{background:#3674c7}}
.boundary{{border-left:4px solid #ae7615}}code{{background:#edf1f7;padding:2px 4px}}a{{color:#165c92}}@media(max-width:700px){{.stats{{grid-template-columns:1fr}}.scroll{{overflow:auto}}}}</style>
<main><small>2026-10-27 · 平台增量 · 人工功能例</small><h1>收到的跨机字节，可以继续走机内网络，再发回远端</h1>
<p>本次补上持久事务缺少 GPU 间机内转发的实现。机内和机外共用一个 ns-3 时钟，目标只在实际交付时写入，后续任务由完成通知推进。</p>
<div class="stats"><div class="card"><strong>4 / 4</strong>预定功能网络通过</div><div class="card"><strong>512 B</strong>机内实际转发字节</div><div class="card"><strong>2 个 / 576 B</strong>远端源数据包 / MRT1载体</div></div>
<div class="card"><h2>传递链路</h2><p>服务器 A：GPU0、GPU1 → GPU2 → 跨机网络 → 服务器 B：GPU4 → GPU5 → 跨机网络 → GPU0。</p>
<p>GPU0/1 各提供128B不同字节，GPU2 合成256B发送。GPU4收到后把同一组字节交给GPU5，GPU5再从窗口读出并回传。后续阶段没有重新生成标记。</p></div>
<div class="card scroll"><h2>实际数据就绪时刻（ns）</h2><table><thead><tr><th>工作</th><th>开始</th><th>交付</th><th>同一时间轴</th></tr></thead><tbody>{timeline}</tbody></table>
<p>首条跨机写入在{remote[2]['applied_ns']:,}ns交付，发送方在{remote[2]['completed_ns']:,}ns才收到完整ACK；机内转发与后续发送已在{rows[3]['completed_ns']:,}ns开始推进。不能把数据交付、发送完成或各段耗时简单相加。</p>
<p>最终网络排空：{result['finish_ns']:,}ns。绿色为机内转发，蓝色为跨机数据交付；表中没有把等待ACK画成数据传输。</p></div>
<div class="card"><h2>验证了什么</h2><ul><li>GPU2目的端口共享：两条128B转发分别在276/340ns完成，端口繁忙256ns、最大等待128ns。</li>
<li>独立重放20项内存事件，核对源保护、开始时捕获、完成时提交和最终字节。</li><li>逐项核对实际物理发送/接收、路径两端口、序列及ACK，共24次逐跳发送；机内转发不增加物理RDMA包。</li>
<li>直接执行与依赖前端的核心结果相同；迁移后的完整结果只允许已核实的运行库路径和枚举顺序差异。</li>
<li>7类非法输入在创建输出和启动后端前拒绝；7类证据篡改被独立核验器拒绝。</li></ul><a href="verification.json">查看机器可读核验结果</a></div>
<div class="card boundary"><h2>这一步能支持什么研究</h2><p>可以继续接入“跨机先送到某张GPU、再在机内转发”的分层通信依赖，避免把域内转发误算成机外RDMA。它补的是模型正确性，尚未建立新的仿真创新或性能收益。</p>
<p>本例为2台服务器×4张逻辑GPU，机内8Gbps、每跳10ns、64B分块，机外100Gbps、每跳1µs。GPU类型为NONE，属于声明参数；不是GPU执行、NVLink/TMA/GIN/RoCE成本校准、真实MoE调用或千卡实验。</p>
<p>新增路由数据的来源已确认为DeepSeek-R1-AWQ/MMLU；本次人工功能例没有使用这些文件。完整Hybrid的slot、屏障、计数、Combine执行及强基线收益验证仍待完成。</p></div></main></html>'''
    assert not output.exists();output.write_text(text)


def seal(repo,stage):
    budget(stage);assert load(stage/'execution.json')['state']=='completed'
    identity=load(stage/'runtime-identity.json');runtime_check(stage/'runtime',identity)
    assert all(sha(repo/n)==h for n,h in identity['sources'].items())
    final=stage/'final-source';final.mkdir()
    paths={p for p in (repo/'python/moe_ub_cosim').rglob('*.py')}
    paths.update((repo/'fixtures/peer_copy').glob('*.py'));paths.update(repo/n for n in identity['sources'])
    paths.update(repo/n for n in ('deps.lock.json','AGENTS.md','docs/development.md','docs/tasks/peer_copy_spec.md',
                                'docs/user_guide.md','docs/architecture.md','docs/record_storage.md','backend/README.md'))
    paths.update((repo/'examples/scaleout_peer_copy').glob('*'))
    paths.update(repo/'tests/unit'/n for n in ('peer_copy_test.py','static_stream_validation_test.py','scaleout_transactions_test.py','scaleout_streams_test.py'))
    for path in paths:
        if not path.is_file():continue
        dst=final/path.relative_to(repo);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dst)
    save(final/'source-manifest.json',{str(p.relative_to(final)):sha(p) for p in sorted(final.rglob('*')) if p.is_file()})
    source_check(final)
    save(stage/'source-basis.json',dict(passed=True,model='topology_intra_host_crossbar/1',
        sources={n:h for n,h in identity['sources'].items() if n.startswith('backend/rdma/')},
        upstream_native_commit=load(stage/'build/provenance.json')['native_commit'],
        source_roles=dict(crossbar='Existing Fabric::SendLocal shared per-GPU ingress/egress chunk model',
                          memory='Existing RemoteMemory read/write leases and actual window bytes',
                          completion='One native ns-3 Simulator, actual local delivery and persistent NIC receive/ACK callbacks'),
        declared_model_not_NVLink_TMA_or_GPU_coherence=True,full_Hybrid_executed=False,research_benefit_proven=False))
    files={}
    for name in ('inputs','topology','without-peer-topology','peer','session','without-peer','migrated','runtime',
                 'source','failures','syntax','syntax-v2','syntax-final'):
        for path in (stage/name).rglob('*'):
            if path.is_file(): files['run/'+str(path.relative_to(stage))]=path
    for path in final.rglob('*'):
        if path.is_file():files['source/'+str(path.relative_to(final))]=path
    for path in (stage/'monitor').rglob('*'):
        if path.is_file() and path.relative_to(stage/'monitor').parts[0] != 'seal':
            files['run/'+str(path.relative_to(stage))]=path
    for path in (stage/'build/logs').rglob('*'):
        if path.is_file():files['run/'+str(path.relative_to(stage))]=path
    for name in ('build/provenance.json','build/compiler-options.json','build/cmake/compile_commands.json','build/backend.json',
                 'source-basis.json','runtime-identity.json','preparation.json','frozen-commands.json','execution.json','verification.json',
                 'peer-verification.json','session-verification.json','without-peer-verification.json','migrated-verification.json','report.html'):
        files['run/'+name]=stage/name
    manifest=dict(schema_version='peer-copy-checkpoint/1',files={n:dict(sha256=sha(p),bytes=p.stat().st_size,
        mode=stat.S_IMODE(p.stat().st_mode)) for n,p in sorted(files.items())},networks_executed=4,
        contains_runtime=True,contains_raw_routing=False,open_administration_receipts_excluded=['monitor/seal'])
    archive=stage/'checkpoint.tar.gz';assert not archive.exists()
    with tarfile.open(archive,'w:gz') as tar:
        raw=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
        for name,path in sorted(files.items()):tar.add(path,arcname=name,recursive=False)
    save(stage/'archive.json',dict(passed=True,archive=str(archive),sha256=sha(archive),bytes=archive.stat().st_size,
        members=len(files)+1,manifest_files=len(files),hardware_calibrated=False))
    print('Sealed source, fixed runtime, original failure and completed execution evidence.')


def recover(stage):
    budget(stage);identity=load(stage/'archive.json');archive=stage/'checkpoint.tar.gz'
    assert sha(archive)==identity['sha256'] and archive.stat().st_size==identity['bytes']
    restored=stage/'recovered';assert not restored.exists();restored.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert len(members)==identity['members']
        assert all(m.isfile() and not PurePosixPath(m.name).is_absolute() and '..' not in PurePosixPath(m.name).parts for m in members)
        names=[m.name for m in members];assert len(names)==len(set(names))
        manifest=json.loads(tar.extractfile('manifest.json').read());assert manifest['schema_version']=='peer-copy-checkpoint/1'
        assert set(names)==set(manifest['files'])|{'manifest.json'}
        assert sum(m.size for m in members) < 512*2**20
        for member in members:
            raw=tar.extractfile(member).read()
            if member.name!='manifest.json':
                declared=manifest['files'][member.name]
                import hashlib
                assert len(raw)==declared['bytes'] and hashlib.sha256(raw).hexdigest()==declared['sha256']
            dst=restored/member.name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
            if member.name!='manifest.json':dst.chmod(manifest['files'][member.name]['mode'])
    source=restored/'source';run=restored/'run';source_check(source);runtime_check(run/'runtime',load(run/'runtime-identity.json'))
    assert all(sha(source/n)==h for n,h in load(run/'runtime-identity.json')['sources'].items())
    empty=restored/'empty-cwd';empty.mkdir();env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    records=[]
    for label in ('peer','session','without-peer','migrated'):
        config=run/'inputs'/('without-peer.json' if label=='without-peer' else 'work.json')
        topology=run/('without-peer-topology' if label=='without-peer' else 'topology')/'topology.json'
        out=restored/(label+'-verification.json')
        args=[sys.executable,'-B','-m','fixtures.peer_copy.verify','--result',run/label/'result.json',
              '--config',config,'--topology',topology,'--protocol',run/'inputs/rdma.json','--output',out]
        checked=subprocess.run(list(map(str,args)),cwd=empty,env=env,capture_output=True,timeout=15)
        records.append(dict(argv=list(map(str,args)),cwd=str(empty),exit_code=checked.returncode))
        (restored/(label+'.stdout')).write_bytes(checked.stdout);(restored/(label+'.stderr')).write_bytes(checked.stderr)
        save(restored/'commands.json',records);assert checked.returncode==0
        assert load(out)==load(run/'verification.json')['reports'][label]
    args=[sys.executable,'-B','-m','fixtures.peer_copy.closeout','report','--stage',run,'--output',restored/'report.html']
    checked=subprocess.run(list(map(str,args)),cwd=empty,env=env,capture_output=True,timeout=15)
    records.append(dict(argv=list(map(str,args)),cwd=str(empty),exit_code=checked.returncode));save(restored/'commands.json',records)
    assert checked.returncode==0 and (restored/'report.html').read_bytes()==(run/'report.html').read_bytes()
    save(stage/'recovery.json',dict(passed=True,archive_sha256=identity['sha256'],members_verified=len(members),
        copied_source_and_new_cwd=True,runtime_files_verified=len(load(run/'runtime-identity.json')['files']),
        four_offline_reports_equal=True,html_byte_identical=True,new_networks=0,new_builds=0,commands=records))
    print('Restored and hashed every member; four copied-source offline checks and HTML matched.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('report','seal','recover'))
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--repository',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args();stage=a.stage.resolve()
    if a.action=='report':report(stage,a.output.resolve())
    elif a.action=='seal':seal(a.repository.resolve(),stage)
    else:recover(stage)
