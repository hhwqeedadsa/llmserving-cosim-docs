"""Use the public read-only diagnosis; independently reduce its recorded times."""
import argparse
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
PINNED_RESULTS={
    'real':'e2c9e22122bf698b27233aa02de9d09b1eed644479656bec3de1e7be3231d8f2',
    'artificial':'21f3239ee94456618889ceb0c18d1842d64d2b9f3f1afd0a9435fe7ea758cbeb',
}
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    with p.open('x') as s:json.dump(value,s,ensure_ascii=False,indent=2);s.write('\n')


def reduce_rows(rows):
    """Separate from platform implementation: all rows and all ties are retained."""
    details=[]
    for row in rows:
        original=max(b['original_ready_ns'] for b in row['bundles'])
        winner=max(b['winner_ready_ns'] for b in row['bundles'])
        assert original==row['original_all_ready_ns'] and winner==row['winner_all_ready_ns']
        assert row['recorded_frontier_advance_ns']==original-winner>=0
        assert sorted(row['original_last_bundles'])==sorted(b['bundle'] for b in row['bundles'] if b['original_ready_ns']==original)
        assert sorted(row['winner_last_bundles'])==sorted(b['bundle'] for b in row['bundles'] if b['winner_ready_ns']==winner)
        saved=deepcopy(row)
        for b in saved['bundles']:
            b.pop('experts',None)
            if b['buffer_released_ns'] is not None:
                assert b['minimum_extra_hold_ns']==max(0,winner-b['buffer_released_ns'])
        details.append(saved)
    return details


def plot(selected,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows=selected['bundles'];helper=next(b for b in rows if b['winner_attempt'].startswith('helper.'))
    fig,(left,right)=plt.subplots(1,2,figsize=(13,5.2),gridspec_kw={'width_ratios':[1.05,1.2]})
    y=list(range(len(rows)))
    left.barh([i-.17 for i in y],[b['original_ready_ns']/1000 for b in rows],height=.30,color='#aab9c7',label='Original result')
    left.barh([i+.17 for i in y],[b['winner_ready_ns']/1000 for b in rows],height=.30,color='#2472a8',label='Selected result')
    frontier=selected['winner_all_ready_ns']/1000
    left.axvline(frontier,color='#ad3930',ls='--',lw=1.3,label=f'Full set: {frontier:.3f} us')
    left.set_yticks(y,[b['bundle'] for b in rows]);left.invert_yaxis()
    left.set_xlabel('Recorded result callback time (us)');left.set_title('All expected contributions')
    left.legend(loc='upper left',bbox_to_anchor=(0,-.18),ncol=2,fontsize=8,frameon=False)
    left.grid(axis='x',alpha=.18);left.set_axisbelow(True)
    ready=helper['winner_ready_ns']/1000;release=helper['buffer_released_ns']/1000
    right.broken_barh([(ready,release-ready)],(.8,.35),facecolors='#2a8a69',label='Recorded helper buffer live')
    right.plot([release,frontier],[.975,.975],ls=':',color='#a76e24',lw=2,label='Extra hold needed by future join')
    right.scatter([release,frontier],[.975,.975],color=['#2a8a69','#ad3930'],zorder=3)
    critical=next(b for b in rows if b['winner_is_last'])
    input_ready=critical['input_ready_ns']/1000;compute=critical['expert_start_ns']/1000
    right.broken_barh([(input_ready,compute-input_ready)],(.15,.35),facecolors='#c5cbd3',label='Recorded input-to-compute wait')
    right.axvline(frontier,color='#ad3930',ls='--',lw=1.3)
    right.axvline(selected['merge_start_ns']/1000,color='#38465b',ls='-.',lw=1,label='Original merge starts')
    right.text(ready,1.22,f'RX {ready:.3f}',fontsize=8)
    right.text(release,.67,f'released {release:.3f}',fontsize=8)
    right.text((release+frontier)/2,1.08,f'>= {helper["minimum_extra_hold_ns"]/1000:.3f} us extra hold',ha='center',fontsize=8)
    right.text(input_ready,.56,f'input {input_ready:.3f}',fontsize=8)
    right.text(compute,.56,f'compute {compute:.3f}',ha='right',fontsize=8)
    right.set_yticks([.325,.975],[f'{critical["bundle"]} input',f'{helper["bundle"]} winner'])
    right.set_ylim(-.05,1.5);right.set_xlim(0,selected['merge_start_ns']/1000+8)
    right.set_title('Actual buffer release and join requirement');right.set_xlabel('Time in the same execution (us)')
    right.legend(loc='upper left',bbox_to_anchor=(0,-.18),ncol=1,fontsize=8,frameon=False)
    right.grid(axis='x',alpha=.18);right.set_axisbelow(True)
    fig.suptitle('128-endpoint Clos: recorded contribution readiness and lifetime',fontsize=13)
    fig.subplots_adjust(bottom=.30,wspace=.40,top=.86)
    for ext in ('png','svg'):fig.savefig(output/('readiness.'+ext),dpi=170,bbox_inches='tight')
    plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--real-run',type=Path,required=True);p.add_argument('--artificial-run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    delivery=out/'delivery';delivery.mkdir();cases={};commands=[]
    for name,path in [('real',a.real_run),('artificial',a.artificial_run)]:
        assert sha(path/'result.json')==PINNED_RESULTS[name], 'study is pinned to the two registered buffer runs; use the public tool for other inputs'
        diagnosis=out/'diagnostics'/name
        cmd=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','contribution-frontier',
             '--run',str(path.resolve()),'--output',str(diagnosis)]
        call=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
        (out/(name+'.stdout.log')).write_text(call.stdout);(out/(name+'.stderr.log')).write_text(call.stderr)
        commands.append(dict(argv=cmd,cwd=str(ROOT),exit_code=call.returncode))
        assert call.returncode==0
        summary=load(diagnosis/'summary.json');rows=reduce_rows(load(diagnosis/'calls.json'))
        assert summary['calls']==len(rows) and summary['gpu_count']==128
        assert summary['calls_with_recorded_frontier_advance']==sum(r['recorded_frontier_advance_ns']>0 for r in rows)
        cases[name]=dict(summary=summary,calls=rows)
    selected=next(r for r in cases['real']['calls'] if any(b['winner_attempt'].startswith('helper.') for b in r['bundles']))
    helper=next(b for b in selected['bundles'] if b['winner_attempt'].startswith('helper.'))
    findings=dict(individual_result_advance_ns=helper['individual_advance_ns'],
        full_set_advance_ns=selected['recorded_frontier_advance_ns'],
        all_contributions_ready_ns=selected['winner_all_ready_ns'],
        original_merge_starts_ns=selected['merge_start_ns'],
        winner_to_merge_start_ns=selected['winner_to_merge_start_ns'],
        helper_buffer_released_ns=helper['buffer_released_ns'],minimum_extra_hold_ns=helper['minimum_extra_hold_ns'],
        conclusion='fixed-record helper frontier result, not a counterfactual measured speedup',
        decision='do not attribute an independent consumer or changed Dispatch semantics to this one helper; compare matching consumers and mature transport baselines')
    evidence=dict(schema_version='moe-consumption-value-study/1',cases=cases,findings=findings,commands=commands,
        new_networks=0,builds=0,pinned_result_sha256=PINNED_RESULTS,
        method='pilot already inspected; comprehensive post-run evaluation, not blind preregistration',
        source_sha256={str(path.relative_to(ROOT)):sha(path) for path in [Path(__file__).resolve(),ROOT/'python/moe_ub_cosim/contribution_frontier.py']})
    save(delivery/'analysis.json',evidence);plot(selected,delivery)
    key_rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [label,c['summary']['calls'],
        c['summary']['bundles'],c['summary']['calls_with_recorded_frontier_advance'],
        c['summary']['max_recorded_frontier_advance_ns'],c['summary']['helper_actual_logical_bytes']])+'</tr>'
        for label,c in [('真实R1专家选择',cases['real']),('独立人工输入',cases['artificial'])])
    table_rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [b['bundle'],
        b['original_ready_ns']/1000,b['winner_ready_ns']/1000,'是' if b['winner_is_last'] else '否',
        b['input_ready_ns']/1000,b['expert_start_ns']/1000])+'</tr>' for b in selected['bundles'])
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>备用贡献能带来多少消费收益？</title><style>body{font:16px/1.75 system-ui,sans-serif;max-width:1180px;margin:30px auto;padding:0 20px;background:#f4f7fb;color:#183149}section{background:white;padding:22px;margin:20px 0;border-radius:12px}h1{font-size:28px}h2{font-size:21px}table{border-collapse:collapse;display:block;overflow:auto;font-size:14px}td,th{padding:9px;border:1px solid #d9e3ed;text-align:left;white-space:nowrap}th{background:#edf3f9}img{width:100%;height:auto}strong{color:#9c352e}a{color:#1261a2}</style>
<h1>单个备用快了，整个请求能快多少？</h1>
<p>128端点Clos，实际网络记录的只读核查。真实输入为四份R1专家选择，两层、两步Decode；载荷为标记字节、计算为声明值，没有硬件校准。本轮零新网络、零构建。</p>
<section><h2>本轮最重要的结果</h2><p><strong>这次备用贡献提前62.002微秒，但全部贡献到齐的时间没有提前。</strong>首调用原件与赢家集合均在93.669微秒到齐，最后结果来自r68，备用修复的是r32。保持其他已记录事件不变时，新增一个消费者不能把这62.002微秒直接变成请求加速。</p>
<table><thead><tr><th>输入</th><th>层步调用</th><th>贡献包</th><th>前沿提前调用数</th><th>最大前沿提前ns</th><th>实际备用字节</th></tr></thead><tbody>'''+key_rows+'''</tbody></table>
<p>“0”仅指固定记录中的全贡献就绪差，不是移除备用后重新仿真的时延；移除工作会改变网络和GPU竞争。所有层步调用分别核对，没有把时延相加。</p></section>
<section><h2>真实调用中，每份结果何时可用？</h2><img src="readiness.png" alt="各贡献的原件与赢家时间，以及备用缓冲释放和汇合时刻">
<table><thead><tr><th>贡献包</th><th>原件结果µs</th><th>赢家结果µs</th><th>最后到齐</th><th>输入到达µs</th><th>专家实际开始µs</th></tr></thead><tbody>'''+table_rows+'''</tbody></table>
<p>健康r68的输入10.146微秒已到，专家仍等到85.504微秒才开始。原图Dispatch组门限是实际最后前驱，GPU FIFO等待为0。备用结果没有替代原始Dispatch的源确认，所以没有提前解除这道门限；这段等待不能直接归因于GPU计算不足。</p></section>
<section><h2>缓冲还有什么要求？</h2><p>备用结果26.073微秒收到，28.075微秒确认并释放；全部贡献93.669微秒才到齐。未来消费者若要在那时读取赢家，至少还需保留65.594微秒，后续合并和排队可能需要更久。现有模式没有消费者，因此这不是现有越界访问，也不是上游漏洞。当前标记内容也不能证明数值等价。</p></section>
<section><h2>哪里还有优化空间？</h2><p>全部贡献就绪93.669微秒到原merge开始99.997微秒相差6.328微秒。这只是这次记录中的间隔，包含必要依赖与同步，不是已证明可消除的时延。研究独立消费者时，必须同时提供同样消费者的无备用基线，不能把消费者自身的作用归给备用策略。</p>
<p>当前结果不支持继续宣称单份备用能获得62微秒的推理收益。下一步先针对阻塞整个EP组的原始Dispatch完成路径，比较成熟SR、尾包探测及完整早备用。只有在这些强基线之后，按通信组完成进度分配恢复预算仍能减少尾部且不拖累健康任务，才值得作为新机制候选。若只是调低RTO或通用重传更早，正常作为基线收尾。</p></section>
<section><h2>证据与复现</h2><p>公开工具先复核实际来源、图、原生回调与共享计算，再核对输入目录全部文件SHA。真实431份、人工427份保持不变。早先只看备用结果的解释在本轮被完整集合条件修正，没有删除门限或重跑网络来制造收益。</p>
<p><a href="analysis.json">派生证据</a> · <a href="readiness.svg">矢量图</a> · <a href="../../../../docs/contribution_frontier.md">公开工具与字段</a> · <a href="../../README.md">研究方法</a></p></section></html>'''
    (delivery/'report.html').write_text(page)
    print(json.dumps(dict(passed=True,cases=2,new_networks=0,findings=findings)))


if __name__=='__main__':main()
