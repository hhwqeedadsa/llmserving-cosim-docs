"""Publish checked platform evidence; never builds or runs a network."""
import argparse
from datetime import datetime
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

if not __debug__:raise RuntimeError('Summary requires assertions; do not run with -O')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--publish-dir',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve();assert not out.exists();out.mkdir(parents=True)
    repo=Path(__file__).resolve().parents[2]
    load=lambda path:json.loads(path.read_bytes());sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    save=lambda path,value:path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    evidence=load(root/'verification-v2/verification.json');assert evidence['passed']
    assert evidence['source_sha256']==sha(repo/'tests/integration/check_legacy_shared.py')
    summaries={name:load(root/(name+'-audit.json')) for name in ('old-single','shared-single','shared-two','shared-fault')}
    for name,summary in summaries.items():
        assert summary['passed']
        for path,expected in summary['input_sha256'].items():assert sha(Path(path))==expected
    monitors={path.parent.name:load(path) for path in (root/'monitors').glob('*/probe.json')}
    assert monitors['build']['status']=='child_failed'
    assert 'LegacyUsesPostingQueue' in (root/'build/compile-0/stderr.log').read_text()
    failed={'build'}
    if 'summary' in monitors:
        assert monitors['summary']['status']=='child_failed'
        assert 'AssertionError: ../../examples/legacy_shared/README.md' in (root/'monitors/summary/stderr.log').read_text()
        failed.add('summary')
    assert all(value['status']=='completed' and value['child_exit_code']==0 for name,value in monitors.items() if name not in failed)
    test_log=(root/'monitors/unit-regression/stderr.log').read_text()
    assert 'Ran 18 tests' in test_log and 'OK' in test_log
    assert all(not value['remaining_live_group_pids'] for value in monitors.values())
    assert sum(value['wall_seconds'] for value in monitors.values())<1800
    raw=load(root/'shared-fault/result.json');seen=set();duplicates=[]
    for row in raw['legacy_shared']['transport']['source_packets']:
        t,stream,src,dst,nic,sequence,size,uid=row;key=stream,sequence,size
        if key in seen:duplicates.append(dict(time_ns=t,stream=stream,sequence=sequence,bytes=size,packet_uid=uid))
        seen.add(key)
    assert sum(row['bytes'] for row in duplicates)==432
    assert all(row['stream']==6 for row in duplicates)
    save(out/'verification.json',evidence);save(out/'analysis.json',dict(conditions=summaries,duplicate_packets=duplicates,
        duplicate_scope='Repeated source carrier segments; includes 32B frame headers; not unique activation bytes or total wire traffic'))
    for name in ('normal.json','fault.json','transport.json','transport-single.json','single.json','shared.json'):
        shutil.copy2(root/'input'/name,out/name)
    retained=out/'retained-build-failure';retained.mkdir()
    shutil.copy2(root/'monitors/build/probe.json',retained/'probe.json')
    shutil.copy2(root/'build/compile-0/stderr.log',retained/'compiler-stderr.log')
    table=''
    for name in ('old-single','shared-single','shared-two','shared-fault'):
        summary=summaries[name];groups=summary.get('groups',[summary])
        cells=' / '.join(f"{group['last_kernel_complete_ns']/1000:.3f}" for group in groups)
        table+='<tr><td>'+name+'</td><td>'+cells+'</td><td>'+f"{summary['physical_finish_ns']/1000:.3f}"+'</td></tr>'
    method=os.path.relpath(repo/'examples/legacy_shared/README.md',a.publish_dir.resolve())
    healthy=summaries['shared-two']['groups'];damaged=summaries['shared-fault']['groups']
    fault_note=(f"故障条件只丢弃第一组的一包36B零token count通知。第一组调用由{healthy[0]['last_kernel_complete_ns']/1000:.3f}µs拖到"
                f"{damaged[0]['last_kernel_complete_ns']/1000:.3f}µs，第二组为{damaged[1]['last_kernel_complete_ns']/1000:.3f}µs；最终两组内存与正常条件相同。"
                '当前小例没有观察到第二组调用变慢，不能将该功能测试作为组间优化收益证据。')
    duplicate_note=(f"实际源端记录有{len(duplicates)}次重复发送，均在stream6："+
                    '；'.join(f"{row['time_ns']/1000:.3f}µs重复{row['bytes']}B" for row in duplicates)+
                    f"，总{sum(row['bytes'] for row in duplicates)}B。")
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>完整旧库的多组共享网络</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:1000px;margin:36px auto;padding:0 20px;color:#183047;background:#f5f8fc}table{width:100%;border-collapse:collapse;background:white}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd}.callout{padding:18px;background:#eaf1fb;border-left:5px solid #4377ad}a{color:#075ba6}</style>
<h1>完整旧库的多组共享网络</h1><p class="callout">本轮补齐系统能力：两个独立完整调用使用同一套交换机、时钟和可靠传输，各自保留通知、缓冲和完成条件。四个128端点Clos功能网络均通过完整审计；这还不是新算法或真实多任务性能实验。</p>
<p>每组EP2、4专家、H128、3个人工Top1 token，映射分别为[0,64]与[4,68]。只有4个端点参与双组，其余124端点无该组工作；不能称128卡满载。它们是明确的人工功能输入，不是R1真实路由。</p>
<table><tr><th>条件</th><th>各组完整调用 µs</th><th>共享物理排空 µs</th></tr>'''+table+'''</table>
<h2>系统正确性得到了哪些证据</h2><p>共享单组与原单组的全部应用事件、操作、内存及物理包记录一致；传输记录仅允许32MiB/64MiB捕获容量字段不同。双组都从0开始，真实执行区间重叠，使用不同全局stream区间；并非把两个独立网络的时间相加。</p>
<p>'''+html.escape(fault_note)+'''</p>
<h2>为什么只丢36B，却多发432B</h2><p>'''+html.escape(duplicate_note)+'''丢包数量、重复载体字节与有效激活字节不是同一口径；通知与后续数据共用持久流，不能用“丢一包就只重传一包”代替实际协议记录。这里不推断网卡硬件行为或把它称为新漏洞。</p>
<p>独立核验拒绝8类共享审计篡改；原生在Fabric前拒绝6类非法组合。18项Python相关检查通过。编译初次因抽取执行器后缺少直接头文件引用失败，已修正并保留日志，旧运行库未被改写。无下载、安装、依赖升级、GPU或ns-3重编。</p>
<p>下一步仍需独立制定真实来源双EP32及成熟恢复对照，先证明共享干扰和收益空间，再进入算法设计。本轮只验收人工双EP2功能，不预报真实推理收益。</p>
<p><a href="analysis.json">逐条件审计及重复包</a> · <a href="verification.json">独立核验</a> · <a href="receipt.json">资源与源码身份</a> · <a href="'''+html.escape(method,quote=True)+'''">公开操作</a></p></html>'''
    (out/'report.html').write_text(page)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.links=[]
        def handle_starttag(self,tag,attrs):
            self.links.extend(value for key,value in attrs if key in ('href','src'))
    parser=Links();parser.feed(page)
    receipt=dict(created_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),passed=True,
        new_network_executions=4,network_endpoints=128,synthetic_only=True,real_ep32_shared_execution_validated=False,
        monitored_wall_seconds_before_summary=sum(value['wall_seconds'] for value in monitors.values()),
        sampled_peak_group_rss_bytes=max(value['peak_sampled_group_rss_bytes'] for value in monitors.values()),
        sampled_artifact_peak_bytes=max(value['peak_sampled_artifact_bytes'] for value in monitors.values()),
        monitors={name:{key:value[key] for key in ('status','child_exit_code','wall_seconds')} for name,value in monitors.items()},
        build_identity=load(root/'build-v2/backend.json'),source_sha256=sha(Path(__file__)),
        checks=dict(python_methods=18,shared_audit_tamper_rejections=8,native_before_fabric_rejections=6),
        files={str(path.relative_to(out)):sha(path) for path in out.rglob('*') if path.is_file()},
        browser_render_validated=False,algorithm_innovation_validated=False,overall_research_complete=False)
    save(out/'receipt.json',receipt)
    for link in parser.links:
        assert (out/link).resolve().exists() or (a.publish_dir.resolve()/link).resolve().is_file(),link
    print(json.dumps(dict(passed=True,new_network_executions=0,summary_created=True,static_links=len(parser.links))))


if __name__=='__main__':main()
