"""Publish static repair coverage and a diagram, without executing packets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = Path(__file__).resolve().parent
    proof = load(a.stage/'verification.json')
    d = a.stage/'public/repair'
    assert proof['passed'] and proof['plan_sha256'] == sha(d/'plan.json')
    for src, name in [(a.stage/'verification.json', 'verification.json'),
                      (d/'plan.json', 'plan.json'), (d/'summary.json', 'summary.json')]:
        if (out/name).exists():
            assert sha(out/name) == sha(src), 'existing evidence differs from retained source'
        else:
            with (out/name).open('xb') as f:
                f.write(src.read_bytes())
    plan = load(d/'plan.json')
    row = next(r for r in plan['destinations'] if r['source'] == 132 and r['destination'] == 1)
    assert row['repair_prefix_nodes'] == [132, 129, 133]
    assert row['all_old_suffix_edges'] == [dict(src=128, src_port=2, dst=1, dst_port=1),
                                         dict(src=133, src_port=1, dst=128, dst_port=18)]
    # Keep BLAS thread stacks within the declared1GiB address-space budget.
    # No simulation or resource ceiling changes are needed to draw five nodes.
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10.6, 4.7), layout='constrained')
    points = {132: (.13, .8), 129: (.13, .25), 133: (.59, .8), 128: (.59, .25), 1: (.92, .25)}
    labels = {132: 'Agg132\nrepair origin', 129: 'ToR129\nexplicit waypoint', 133: 'Agg133\nsafe merge',
              128: 'ToR128', 1: 'GPU1\nendpoint label'}
    for node, (x, y) in points.items():
        ax.text(x, y, labels[node], ha='center', va='center', fontsize=11,
                bbox=dict(boxstyle='round,pad=.65', facecolor='#e9eff7', edgecolor='#7c91aa'), zorder=5)
    def arrow(a, b, color, style='-', curve=0):
        ax.annotate('', xy=points[b], xytext=points[a],
            arrowprops=dict(arrowstyle='-|>', color=color, linewidth=2.4, linestyle=style,
                            shrinkA=36, shrinkB=36, connectionstyle=f'arc3,rad={curve}'), zorder=2)
    arrow(132, 129, '#2873b8')
    arrow(129, 133, '#2873b8')
    arrow(133, 128, '#2b8658')
    arrow(128, 1, '#2b8658')
    arrow(132, 128, '#bd4545', '--')
    arrow(129, 132, '#bd7b24', '--', .45)
    ax.text(.35, .48, 'Failed link\n128 - 132', color='#ad3737', fontsize=10, ha='center')
    ax.text(.30, .72, 'Explicit prefix', color='#2873b8', fontsize=10)
    ax.text(.65, .57, 'All old shortest\nsuffixes safe', color='#2b8658', fontsize=10)
    ax.text(.015, .48, 'Old next hop\nmay return\nto132', color='#a36d20', fontsize=9, ha='left')
    ax.set_title('Static link-repair plan: no packet execution or latency result', fontsize=13)
    ax.set_xlim(-.08, 1.05); ax.set_ylim(.02, 1.05); ax.axis('off')
    assert not (out/'repair_path.png').exists()
    fig.savefig(out/'repair_path.png', dpi=160)
    plt.close(fig)
    rows = ''.join(f'<tr><td>{label}</td><td>{number}</td></tr>' for label, number in (
        ('不受此出口影响', 128), ('原ECMP仍有存活出口', 112), ('普通LFA可以新增保护', 0), ('需要显式修复段', 16)))
    body = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Clos唯一坏出口：为什么普通LFA不够</title>
<style>body{{font:16px/1.8 system-ui,sans-serif;max-width:1020px;margin:32px auto;padding:0 24px;background:#f5f8fc;color:#173047}}img{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #ddd}}.box{{padding:18px;background:#e2edf8;border-left:5px solid #2873b8}}code{{background:#e9edf3;padding:2px 4px}}a{{color:#2465a7}}</style>
<h1>有物理备用路径，也不能随便换一个邻居</h1><p>128端点Clos · 20交换机 · 保护128–132边 · 静态规划，不执行网络或预测收益</p>
<div class="box">故障两端×128个GPU目的，共256个转发组合。240个已有出口可用；其余16个没有普通单跳LFA，需要显式修复段。普通LFA不覆盖，并不意味着网络分区。</div>
<h2>本轮检查结果</h2><table><tr><th>覆盖情况</th><th>组合数</th></tr>{rows}</table>
<p>256是源/目的组合数，不是256卡实验，也不是故障丢包数。规划不读取真实路由或未来恢复事件，结果仅由物理图和保护边决定。</p>
<h2>为什么直接选别的出口可能绕回来</h2><p>普通LFA要求故障前距离满足严格不等式：<code>D(N,D) &lt; D(N,S)+D(S,D)</code>。等号不能接受，因为邻居可能有一条旧最短路经过S。Agg132至GPU1的原出口只有ToR128；改送ToR129时，129按旧ECMP仍可能送回132。</p>
<img src="repair_path.png" alt="静态显式修复段132到129到133，再按旧路由133到128到GPU1；坏边128到132，129可能按旧表送回132">
<p>安全计划：先明确执行132→129→133，到133后再使用旧路由133→128→GPU1。首段两个跳分别需要132 port2→129 port17、129 port18→133 port2。所有旧后缀都已核查，不能只挑一条好路径。</p>
<h2>这对当前研究意味着什么</h2><p>上一轮长故障发现ACK在Agg132被丢弃。本轮进一步说明，不能把“普通LFA”当成已足够强的绕路基线；该位置需要有显式中间节点的修复转发。下一步在同一原生网络中实际执行修复段，真实计算头部、队列和序列化成本，再比较MoE任务等待。</p>
<p>当前交付公开命令<code>scaleout repair-plan</code>、可修改输入和逐目的计划。它没有新增ns-3恢复模式，没有实现MPLS/Remote-LFA/TI-LFA，没有取得新性能收益，也不证明现有live_ecmp会产生环路；环路风险针对“直接改送任意邻居”的候选实现。</p>
<h2>证据与来源</h2><p>9项新单位检查和独立Floyd-Warshall核查全部256个组合、16个修复段及所有旧ECMP后缀；4类篡改被拒绝。没有新网络运行、构建、GPU执行或依赖修改。首版测试中的一条后缀排序断言失败已保留，修正为边集合比较；没有放宽严格不等式。</p>
<p><a href="README.md">验收与边界</a> · <a href="../../examples/scaleout_repair_plan/README.md">公开操作</a> · <a href="plan.json">全部计划</a> · <a href="verification.json">独立核验</a> · <a href="receipt.json">来源和资源</a> · <a href="https://www.rfc-editor.org/rfc/rfc5286.html#section-3.1">RFC5286</a> · <a href="https://www.rfc-editor.org/rfc/rfc7490.html#section-2">RFC7490</a></p></html>"""
    with (out/'report.html').open('x') as s:
        s.write(body+'\n')
    print(json.dumps(dict(published=True, network_executed=False, output=str(out), verification_sha256=sha(out/'verification.json'))))


if __name__ == '__main__':
    main()
