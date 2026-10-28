"""Render the heavy-loss inference results as a self-contained webpage and markdown report.

Reads only the committed small artifacts in results/20261028/ (analysis.json,
batch-a-preserved.json, timelines.json); never the volatile run directories.
"""
import argparse
import html
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / 'results/20261028'

# Validated palette (dataviz skill, light surface #fcfcfb):
# 3-hue all-pairs pass for co-plotted series; drops are neutral-ink X marks, not a hue series.
C_TX = '#2a78d6'    # 源端数据发送 (categorical slot 1)
C_NACK = '#eb6834'  # NACK (slot 2)
C_ACK = '#1baf7a'   # ACK (slot 3)
C_DROP = '#52514e'  # 丢弃：中性 X 标记（"消失"语义，非色相系列）
C_BAR = '#2a78d6'
C_GOOD = '#0ca30c'
C_CRIT = '#d03b3b'
INK = '#0b0b0b'
INK2 = '#52514e'
SURFACE = '#fcfcfb'


def read(p):
    return json.loads(p.read_text())


# MoE 推理通信面对网络丢包/故障时的恢复方法谱系（背景整理，非本实验证据）。
# 每行: 层次, 代表方案, 丢包处理机制, 与本实验的对应关系
LANDSCAPE = [
    ('链路/网络层：无损化',
     'InfiniBand credit 链路流控；RoCE 用 PFC（按优先级暂停）+ ECN 阈值',
     '设计上假设"不丢包"：接收 buffer 满前反向停发，把丢包当硬件错误而非常态',
     '本实验直接注入丢包，正是检验无损假设失效后的协议行为；PFC 自身有队头阻塞/死锁/风暴问题，是"无损化"路线的已知代价'),
    ('传输层重传：Go-Back-N',
     'InfiniBand RC、RoCE RNIC 基线（Mellanox/NVIDIA 传统实现）',
     '累计 ACK + NAK 指出缺口序号，发送端从缺口起整体重发；超时兜底',
     '本实验原生模式与 go_back_n 扩展都是此语义；"57% 与 86% 代价相同、3× 重传放大"是它的直接后果'),
    ('传输层重传：选择性重传',
     'IRN（SIGCOMM\'18，SACK 式只补丢包）；新型 RNIC 的乱序放置 + 选择性重发',
     '接收端记录到达位图，只重传真正丢失的包，不要求底层无损',
     '正是为消除图 2 展示的放大：86% 丢包下选择性重传只需补 6 包而非整体回退两轮'),
    ('传输层：多路径与乱序容忍',
     'MP-RDMA（NSDI\'18）；AWS SRD/EFA（NSDI\'20，可靠数据报 + 多路径喷洒 + 乱序交付）',
     '单包级多路径分散，乱序到达直接放置，丢失只重传单包',
     'MoE all-to-all 高扇出、短消息，最能从不保序中受益的形态；本平台当前为单路径保序 QP'),
    ('超时估计',
     '固定 RTO；Karn/Partridge 采样 + RFC6298 平滑；Swift（SIGCOMM\'20）精确时延测量',
     'RTO 决定"等多久才认定丢失"；过短放大重复流量，过长拖延恢复',
     '对应 recovery 扩展的 rtt_estimator=karn；本实验固定初值 40µs，恢复代价≈1 个 RTO，正比关系直接可见'),
    ('拥塞控制与丢包信号',
     'DCQCN（SIGCOMM\'15）；Timely、HPCC、PowerTCP 等',
     'ECN/时延/带内遥测降速，尽量避免丢包发生；丢包仍被视为严重信号',
     '本实验固定原生 DCQCN 不变，隔离丢包恢复单一变量'),
    ('集合通信库',
     'NCCL（通道故障切换、重试）；DeepEP（EP dispatch/combine，RDMA RC QP，低时延/高吞吐双模式）',
     '库层一般不自带恢复算法：依赖传输层可靠性，失败沿 QP 错误上抛，由框架决定重连或放弃',
     '本仓库 hybrid/GIN 系列筛选已核过 DeepEP 完成机制；本实验对应"库下层协议"的行为'),
    ('服务系统层',
     'PD 分离 KV 传输（Mooncake、NVIDIA Dynamo/NIXL、vLLM 连接器）；请求级重试/重路由；副本冗余；引擎重启',
     '单请求失败由调度层换实例重算，不要求单条连接必然恢复；用隔离域限制爆炸半径',
     '本实验中独立 EP 组（组 1）在组 0 停滞时两轮照常完成，正是隔离域价值的微观对照'),
]


def fmt_us(ns):
    return '—' if ns is None else f'{ns / 1000:,.1f}'


def esc(s):
    return html.escape(str(s))


def bar_row(label, value, maximum, note, status=None):
    """One horizontal bar; value in display units; status = 'good' | 'crit' | None."""
    width = 100.0 * value / maximum if maximum else 0
    icon = '' if status is None else ('<span class="ok">✓</span> ' if status == 'good' else '<span class="bad">✗</span> ')
    return (f'<div class="row"><span class="rlabel">{icon}{esc(label)}</span>'
            f'<div class="track"><i style="width:{width:.3f}%;background:{C_BAR}" '
            f'title="{esc(label)}：{esc(note)}"></i></div><b>{esc(note)}</b></div>')


def timeline_panel(title, subtitle, events, tmax_us, shade_from_us=None, end_mark_us=None, end_label=''):
    """SVG strip chart: x = time (us), 4 labeled lanes. Native <title> tooltips + enlarged hit area."""
    W, H = 940, 150
    pad_l, pad_r, pad_t = 110, 16, 10
    lanes = [('tx', '源端发送', C_TX, 'circle'), ('drop', '接收丢弃', C_DROP, 'x'),
             ('nack', 'NACK', C_NACK, 'triangle'), ('ack', 'ACK', C_ACK, 'square')]
    lane_y = {name: pad_t + 24 + i * 28 for i, (name, *_rest) in enumerate(lanes)}
    plot_w = W - pad_l - pad_r

    def X(t_ns):
        return pad_l + plot_w * (t_ns / 1000.0) / tmax_us

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}" class="tl">']
    if shade_from_us is not None:
        x0 = X(shade_from_us * 1000)
        parts.append(f'<rect x="{x0:.1f}" y="{pad_t}" width="{W - pad_r - x0:.1f}" height="{H - pad_t - 26}" '
                     f'fill="#f0efec"/>')
        parts.append(f'<text x="{(x0 + W - pad_r) / 2:.1f}" y="{pad_t + 12}" text-anchor="middle" '
                     f'font-size="10" fill="{INK2}">{esc(end_label)}</text>')
    for name, label, _c, _s in lanes:
        y = lane_y[name]
        parts.append(f'<text x="{pad_l - 8}" y="{y + 3}" text-anchor="end" font-size="11" fill="{INK2}">{label}</text>')
        parts.append(f'<line x1="{pad_l}" y1="{y}" x2="{W - pad_r}" y2="{y}" stroke="#e4e1dc" stroke-width="1"/>')
    for t_ns, lane, seq in events:
        if t_ns / 1000.0 > tmax_us:
            continue
        x, y = X(t_ns), lane_y[lane]
        color = dict((n, c) for n, _l, c, _s in lanes)[lane]
        tip = f'{dict((n, l) for n, l, _c, _s in lanes)[lane]} · 序号 {seq} · t={t_ns / 1000:.2f}µs'
        shape = dict((n, s) for n, _l, _c, s in lanes)[lane]
        parts.append(f'<circle cx="{x:.1f}" cy="{y}" r="9" fill="none" pointer-events="all"><title>{esc(tip)}</title></circle>')
        if shape == 'circle':
            parts.append(f'<circle cx="{x:.1f}" cy="{y}" r="3.2" fill="{color}" pointer-events="none"/>')
        elif shape == 'square':
            parts.append(f'<rect x="{x - 3:.1f}" y="{y - 3}" width="6" height="6" fill="{color}" pointer-events="none"/>')
        elif shape == 'triangle':
            parts.append(f'<path d="M{x:.1f},{y - 3.6} l3.4,6.4 h-6.8 z" fill="{color}" pointer-events="none"/>')
        else:
            parts.append(f'<path d="M{x - 3.2:.1f},{y - 3.2} l6.4,6.4 M{x + 3.2:.1f},{y - 3.2} l-6.4,6.4" '
                         f'stroke="{color}" stroke-width="1.8" pointer-events="none"/>')
    if end_mark_us is not None:
        x = X(end_mark_us * 1000)
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{H - 26}" stroke="{C_CRIT}" stroke-width="2" stroke-dasharray="5 3"/>')
        parts.append(f'<text x="{x:.1f}" y="{H - 14}" text-anchor="middle" font-size="10" fill="{C_CRIT}">{esc(end_label)}</text>')
    for tick in range(0, int(tmax_us) + 1, 100):
        x = X(tick * 1000)
        parts.append(f'<line x1="{x:.1f}" y1="{H - 24}" x2="{x:.1f}" y2="{H - 20}" stroke="{INK2}" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{H - 8}" text-anchor="middle" font-size="10" fill="{INK2}">{tick}</text>')
    parts.append('</svg>')
    return (f'<figure><figcaption><b>{esc(title)}</b><br><span class="sub">{esc(subtitle)}</span></figcaption>'
            + ''.join(parts) + '</figure>')


def execute(output):
    output = Path(output)
    assert not output.exists(), 'render output must not exist'
    analysis = read(RESULTS / 'analysis.json')
    batch_a = read(RESULTS / 'batch-a-preserved.json')
    timelines = read(RESULTS / 'timelines.json')
    rows = {r['label']: r for r in analysis['conditions']}
    a_base = batch_a['conditions']['baseline']
    a_tr = batch_a['conditions']['data-57-transient']

    # ---- numbers ----
    base_finish = rows['baseline']['finish_ns']
    base_ack = rows['baseline']['target']['ack_ns']
    tr57 = rows['data-57-transient']
    tr86 = rows['data-86-transient']
    pers = rows['data-57-persistent']
    ack57 = rows['ack-57-transient']
    size = analysis['target']['bytes']

    # ---- chart 1: bounded finish/failure times (µs); the native stall stays a status tile, never a truncated bar ----
    c1 = [
        ('基线（A/B 相同）', base_finish, 'good'),
        ('B · 57% 数据丢包×2轮', tr57['finish_ns'], 'good'),
        ('B · 86% 数据丢包×2轮', tr86['finish_ns'], 'good'),
        ('B · 57% 非最终ACK丢包', ack57['finish_ns'], 'good'),
        ('B · 57% 持续丢包', pers['finish_ns'], 'crit'),
    ]
    max1 = max(v for _l, v, _s in c1) * 1.06
    chart1 = ''.join(bar_row(l, v, max1, f'{v / 1000:,.1f}µs' + ('（显式失败）' if s == 'crit' else ''), s)
                     for l, v, s in c1)

    # ---- chart 2: source payload as multiple of message size ----
    c2 = [
        ('基线（A/B 相同）', 1.0, '1×（6,144B）', 'good'),
        ('A · 57%×2轮（原生，停滞）', a_tr['target']['source_data_payload_bytes'] / size, '2× 后无第三次尝试', 'crit'),
        ('B · 57% 数据丢包×2轮', tr57['source_payload_bytes_sent'] / size, '3×（18,432B）', 'good'),
        ('B · 86% 数据丢包×2轮', tr86['source_payload_bytes_sent'] / size, '3×（18,432B）', 'good'),
        ('B · 57% 持续丢包', pers['source_payload_bytes_sent'] / size, '5×（30,720B）后显式失败', 'crit'),
        ('B · 57% 非最终ACK丢包', ack57['source_payload_bytes_sent'] / size, '1×（6,144B）', 'good'),
    ]
    chart2 = ''.join(bar_row(l, v, 5.3, note, s) for l, v, note, s in c2)

    # ---- chart 3: timelines ----
    tl = timelines['series']
    panels = ''.join([
        timeline_panel('批次 A · 57% 数据丢包×2轮 · 原生 NACK 恢复（停滞）',
                       '两轮各丢 4 包后，全程只发出 1 个 NACK；24.1µs 后再无目标消息事件，服务空转到 5,000µs 上限',
                       tl['A:data-57-transient'], 500, shade_from_us=25,
                       end_label='此后 4,976µs 无任何恢复动作（直至 5ms 上限）'),
        timeline_panel('批次 B · 57% 数据丢包×2轮 · go_back_n（40µs 初始 RTO，3 次重试）',
                       'RTO 触发两轮整体回退重传，57.2µs 收到最终 ACK，服务 91.3µs 完成',
                       tl['B:data-57-transient'], 500, end_mark_us=57.154, end_label='最终 ACK 57.2µs'),
        timeline_panel('批次 B · 57% 数据持续丢包 · go_back_n',
                       '5 轮发送（每轮 4 包被丢）仍无 ACK 进展，444.2µs 以 recovery_retry_exhausted 显式失败',
                       tl['B:data-57-persistent'], 500, end_mark_us=444.203, end_label='显式失败 444.2µs'),
    ])

    # ---- table ----
    def trow(label, r, batch):
        t = r.get('target') or {}
        g = r['waves_finished_by_group']
        return ('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s/%s · %s/%s</td><td>%s/%s</td></tr>' % (
            esc(batch + ' · ' + label),
            ('<span class="ok">✓ 完成</span>' if r['passed'] else '<span class="bad">✗ 失败</span>'),
            fmt_us(r['finish_ns']), fmt_us(t.get('ack_ns')),
            '—' if r.get('duplicate_payload_bytes') is None else f"{r['duplicate_payload_bytes']:,}",
            esc(str(r['drop_counts'])), esc((r.get('failure') or {}).get('reason') or '—'),
            g.get('0', {}).get('finished', 0), g.get('0', {}).get('total', 0),
            g.get('1', {}).get('finished', 0), g.get('1', {}).get('total', 0),
            r['requests_finished'], r['requests_total']))
    table = ('<table><thead><tr><th>条件</th><th>结果</th><th>服务完成/终止(µs)</th><th>目标确认(µs)</th>'
             '<th>重复载荷(B)</th><th>各规则丢弃命中</th><th>失败原因</th><th>组0/组1完成波次</th><th>完成请求</th></tr></thead><tbody>')
    table += ('<tr><td>A · baseline</td><td><span class="ok">✓ 完成</span></td><td>54.4</td><td>17.7</td><td>0</td><td>[]</td><td>—</td><td>2/2 · 2/2</td><td>32/32</td></tr>')
    table += ('<tr><td>A · data-57-transient（原生）</td><td><span class="bad">✗ 停滞</span></td><td>5,000.0（上限）</td>'
              '<td>—</td><td>6,144</td><td>[2,2,2,2]</td><td>静默停滞，无显式失败</td><td>0/1 · 2/2</td><td>16/32</td></tr>')
    for label in ['baseline', 'data-57-transient', 'data-86-transient', 'data-57-persistent', 'ack-57-transient']:
        table += trow(label, rows[label], 'B')
    table += '</tbody></table>'

    legend = ('<div class="legend">'
              f'<span><i class="dot" style="background:{C_TX}"></i>源端数据发送</span>'
              f'<span><i class="x" style="color:{C_DROP}">✕</i>接收端丢弃（注入）</span>'
              f'<span><i class="tri" style="background:{C_NACK}"></i>NACK</span>'
              f'<span><i class="sq" style="background:{C_ACK}"></i>ACK</span></div>')

    # ---- landscape table: recovery methods for MoE inference over RDMA networks ----
    landscape_rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % tuple(esc(x) for x in row)
                             for row in LANDSCAPE)
    landscape_table = ('<table><thead><tr><th>层次</th><th>代表方案</th><th>丢包处理机制</th><th>与本实验的关系</th></tr></thead>'
                       '<tbody>' + landscape_rows + '</tbody></table>')

    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>推理消息 50%+ 持续丢包：原生停滞 vs 有界 Go-Back-N</title>
<style>
:root{{color-scheme:light}}
body{{font:16px/1.8 system-ui,sans-serif;max-width:1080px;margin:30px auto;padding:0 20px;background:#f4f7fb;color:{INK}}}
main{{background:{SURFACE};padding:28px;border-radius:12px}}
h1{{line-height:1.4;font-size:26px}} h2{{margin-top:34px;font-size:20px}}
.note{{background:#fff4d9;border-left:4px solid #c4832e;padding:14px 16px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}
.tile{{background:#fff;border:1px solid #dae3eb;border-radius:10px;padding:12px 14px}}
.tile b{{display:block;font-size:24px;line-height:1.2}} .tile span{{font-size:13px;color:{INK2}}}
.tile.crit b{{color:{C_CRIT}}} .tile.good b{{color:{C_GOOD}}}
.row{{display:grid;grid-template-columns:210px 1fr 170px;gap:10px;align-items:center;margin:3px 0}}
.rlabel{{font-size:13px;color:{INK2};text-align:right}}
.track{{background:#eff3f6;height:14px;border-radius:0 4px 4px 0}}
.track i{{display:block;height:100%;border-radius:0 4px 4px 0}}
.row b{{font-size:13px;font-weight:600}}
.ok{{color:{C_GOOD};font-weight:700}} .bad{{color:{C_CRIT};font-weight:700}}
table{{border-collapse:collapse;width:100%;font-size:13px;background:#fff}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid #dae3eb;white-space:nowrap}}
.scroll{{overflow-x:auto}}
figure{{margin:18px 0;background:#fff;border:1px solid #dae3eb;border-radius:10px;padding:12px}}
figcaption{{font-size:14px;margin-bottom:6px}} figcaption .sub{{color:{INK2};font-size:12.5px}}
svg.tl{{width:100%;height:auto;display:block}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:{INK2};margin:6px 0 0}}
.legend span{{display:inline-flex;align-items:center;gap:6px}}
.dot{{width:9px;height:9px;border-radius:50%;display:inline-block}}
.sq{{width:8px;height:8px;display:inline-block}}
.tri{{width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-bottom:9px solid {C_NACK};background:none!important}}
.x{{font-weight:700}}
footer{{margin-top:28px;font-size:13px;color:{INK2}}}
@media(max-width:700px){{.row{{grid-template-columns:120px 1fr 110px;font-size:12px}}}}
</style></head><body><main>
<h1>推理消息 50%+ 持续丢包：原生停滞 vs 有界 Go-Back-N</h1>
<p>2026-10-28 · 8 GPU 两组交错 EP4 ready-KV Decode（1 层 2 步，声明计算，合成路由）· 目标：实际首图 0→4 Dispatch（id 1，6,144B，7 包）· 原生 ns-3 RDMA（锁定 <code>e929264</code>）· <a href="analysis.json">analysis.json</a> · <a href="timelines.json">timelines.json</a> · <a href="batch-a-preserved.json">批次A保留证据</a></p>
<p class="note"><b>核心结论：</b>同一 57% 数据丢包（7 包中 4 个序号、每轮各丢 2 次），原生 NACK 恢复在第二轮后<b>不再有任何恢复动作</b>，服务静默空转到 5ms 上限；启用有界 go_back_n（40µs 初始 RTO、3 次重试）后，同一故障以 +39.5µs 恢复。持续丢包下 86% 与 57% 代价完全相同（Go-Back-N 整体回退），57% 非最终 ACK 丢失被累计确认完全掩盖。</p>
<div class="tiles">
<div class="tile crit"><b>5,000µs</b><span>✗ 批次A 原生恢复：57%×2轮后静默停滞（仿真上限）</span></div>
<div class="tile good"><b>+39.5µs</b><span>✓ 批次B 有界恢复：同一故障的目标确认增量（≈1 个 RTO）</span></div>
<div class="tile"><b>3×</b><span>Go-Back-N 重传放大：57% 与 86% 丢包同为 3× 全消息发送</span></div>
<div class="tile crit"><b>444.2µs</b><span>✗ 持续 57% 丢包：recovery_retry_exhausted 显式失败（5 轮发送后）</span></div>
<div class="tile good"><b>0µs</b><span>✓ 57% 非最终 ACK 丢失：与基线逐字段相同</span></div>
</div>

<h2>服务完成/终止时间（µs，共同 0 起点）</h2>
{chart1}
<p class="sub" style="color:{INK2};font-size:13px">批次 A 原生停滞为 5,000µs，超出本图坐标约 10 倍，不作截断绘制，见上方状态卡。</p>

<h2>目标消息源端发送量（全消息 6,144B 的倍数）</h2>
{chart2}

<h2>目标消息逐包时间线（0–500µs，悬停查看序号与时刻）</h2>
{legend}
{panels}

<h2>全条件结果表</h2>
<div class="scroll">{table}</div>

<h2>背景：MoE 推理通信的丢包恢复方法谱系</h2>
<p>下表整理 MoE 推理（EP all-to-all、PD 分离 KV 传输）所依赖协议栈在面对网络丢包/故障时的主流选择，以及本实验结果对应的层次。这是背景与相关工作整理，不构成新证据；引用年份为公开论文发表场合。</p>
<div class="scroll">{landscape_table}</div>
<p><b>谱系对本实验的定位：</b>本仿真器的原生 ns-3 RDMA 是表中第 1+2 行的组合（假设无损 + NACK 触发的 go-back-N，无有界超时兜底）；平台的可选恢复扩展是第 2+5 行（go-back-N + Karn 式自适应 RTO + 有界重试显式失败）。实验的三个观察分别回答了三个工程取舍：整体回退的放大代价是第 3 行（选择性重传）存在的理由；静默停滞说明第 5 行（有界超时）不是可选项而是必要件；ACK 掩盖效应说明累计确认本身已提供确认路径的容错。第 7、8 行（库层与服务层）意味着即使传输层失败，系统仍有请求级重试和隔离域两道防线——本实验中组 1 的独立完成为后者提供了微观证据。</p>

<h2>判读与边界</h2>
<p><b>1. Go-Back-N 的代价与丢包比例脱钩。</b>消息内任意位置丢包都从首个缺口整体回退：57%（4/7 包）与 86%（6/7 包）瞬态丢包的完成时间（91,297ns）、重传字节（18,432B）完全相同。代价由"连续丢包轮次数 × RTO"决定，而非丢包比例。</p>
<p><b>2. 原生 NACK 恢复的静默停滞。</b>批次 A 全程只观察到 1 个 NACK（t=11.1µs）；第二轮丢包后接收端无新包可触发 NACK，源端无有界超时兜底，24.1µs 后目标消息再无任何事件。这正是 communication_fragility.md 预言的"尾包/缺包后无新包触发 NACK"脆弱点的实测复现。有界超时把同一故障变成 +39.5µs 的成功恢复，或 444.2µs 的显式 <code>recovery_retry_exhausted</code>（失败时独立 EP 组两轮照常完成，16/32 请求受影响而非全局停摆）。</p>
<p><b>3. 确认路径天然鲁棒。</b>4/7 非最终 ACK 各丢一次（ACK 只发送一次，不重传）被后续累计 ACK 完全掩盖，服务完成时间与基线逐字段相同。数据路径与确认路径的丢包敏感性完全不对称；最终 ACK 持续丢失的停滞已由 scaleout_faults 记录。</p>
<p><b>边界：</b>丢包是单条消息上的确定性逐序号规则（"57%"=7 包中 4 个序号每轮必丢），不是全网随机丢包率；计算为声明值、路由为固定合成选择；持续丢包的"失败"只说明 5ms 窗口内未完成，不推断生产 RDMA 行为；批次 B 预期在看到批次 A 证据后登记，不作为独立事前预测。本图仅为浅色模式校验调色板（蓝/橙/水绿三色 all-pairs 通过；丢弃为中性 X 标记，不占色相系列）。</p>
<footer>生成：experiments/heavy_loss_inference/render.py · 输入仅为 results/20261028/ 已提交摘要 · 原始运行证据在易失卷 /dev/shm/moe-heavy-loss-20261028/</footer>
</main></body></html>
'''
    output.mkdir()
    (output / 'report.html').write_text(page)

    md = f'''# 重度丢包对照：报告（2026-10-28）

网页版：同目录 report.html（含逐包时间线交互图）。

| 条件 | 结果 | 服务完成/终止 | 目标确认 | 重复载荷 | 失败原因 |
|---|---|---|---|---|---|
| A · baseline | ✓ | 54.4µs | 17.7µs | 0 | — |
| A · 57%×2轮（原生） | ✗ 停滞 | 5,000µs（上限） | — | 6,144B | 静默停滞，无显式失败 |
| B · baseline | ✓ | 54.4µs | 17.7µs | 0 | — |
| B · 57% 数据×2轮 | ✓ | 91.3µs | 57.2µs（+39.5µs） | 12,288B | — |
| B · 86% 数据×2轮 | ✓ | 91.3µs | 57.2µs（+39.5µs） | 12,288B | — |
| B · 57% 持续 | ✗ 显式失败 | 444.2µs | — | 24,576B | recovery_retry_exhausted |
| B · 57% 非最终ACK | ✓ | 54.4µs | 17.7µs（+0） | 0 | — |

1. Go-Back-N 代价与丢包比例脱钩：57% 与 86% 同为 3× 发送、+39.5µs。
2. 原生 NACK 恢复两轮后静默停滞（全程 1 个 NACK，24.1µs 后零事件）；有界超时将同一故障变为 +1 RTO 恢复或 444.2µs 显式失败。
3. 57% 非最终 ACK 丢失被累计确认完全掩盖；数据/确认路径敏感性不对称。

## 背景：MoE 推理通信的丢包恢复方法谱系

| 层次 | 代表方案 | 丢包处理机制 | 与本实验的关系 |
|---|---|---|---|
'''
    for row in LANDSCAPE:
        md += '| ' + ' | '.join(row) + ' |\n'
    md += '''
本仿真器原生模式 = 无损假设 + NACK 触发 go-back-N（无有界超时）；可选恢复扩展 = go-back-N + Karn 自适应 RTO + 有界重试显式失败。三个观察分别对应三个工程取舍：整体回退放大是选择性重传（IRN 等）存在的理由；静默停滞说明有界超时是必要件；ACK 掩盖说明累计确认自带确认路径容错。库层与服务层（请求级重试、隔离域）是传输层失败之后的两道防线。

边界：单消息确定性逐序号丢包，非全网随机丢包率；声明计算、合成路由；失败仅指 5ms 窗口内未完成。详见 [protocol.md](../../protocol.md) 与 [README.md](../../README.md)。
'''
    (output / 'report.md').write_text(md)
    print('rendered', output)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    execute(a.output.resolve())
