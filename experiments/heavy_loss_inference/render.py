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


# 仿真能力覆盖（与本实验相关口径；✓=已有实测证据，✗=已知缺口）。
# 每行: 实验类别, 状态, 能跑什么 / 缺口是什么, 证据锚点
COVERAGE = [
    ('推理全链路（Scale-Out RDMA）', 'ok',
     'LLMServingSim 真实组批 → ASTRA → 原生 ns-3 逐包执行；8 GPU、两组交错 EP4、batch/层/步数可调、合成或源校验路由回放；DCQCN/PFC/ECN',
     'scaleout_e2e 30 次主对照；本实验 7 个条件'),
    ('Scale-Up（UB，ns-3-ub 独立入口）', 'ok',
     'M0–M5 已交付：cosim/1 联合仿真、P/D 分离 KV handoff、端口观测三分类、守恒/隔离/可复现套件；CM384/R1 128 端点首轮历史参考',
     'README 当前能力节；docs/tasks/current.md'),
    ('故障注入', 'ok',
     '精确丢包规则（数据/ACK/NACK × 消息 × 序号 × 时间窗 × 次数，≤32 条）；drop_once；永久断链 + 延迟路由更新',
     'scaleout_faults、extreme_fault_stress、本实验两批次'),
    ('丢包恢复对照', 'ok',
     '原生 NACK go-back-N vs 可选 go_back_n（固定或 Karn 自适应 RTO、有界重试、显式失败、静默区间证书）',
     'inference_fragility、loss_recovery_cost、本实验：静默停滞 vs +39.5µs/444.2µs'),
    ('准入与发送资源', 'ok',
     '软件准入（每源消息/字节额度、每目的端份额轮转）、生命周期捕获',
     'scaleout_admission 7 组对照、inference_resilience 24 条件'),
    ('RC 事务与探针', 'ok',
     '单/多 QP WRITE/fetch-add/Atomic ACK 共享 fabric、MRT1 事务会话、上下文绑定、因果 DAG 下沉、持久流',
     'rc_concurrent、rc_packet_probe 等 fixtures'),
    ('DeepEP/Hybrid 计划编译', 'ok',
     '离线偏序编译、GDAKI 资源绑定、回放核验；不跑网络、不含 GPU 时序',
     'deepep-plan / hybrid-deepep-* / modern-deepep-* 入口'),
    ('拓扑与容量', 'ok',
     '两/三层 Clos、multi-rail、双平面静态生成（含 1024 GPU 配置）；容量下界核算与预检',
     'scaleout_topology、scaleout_capacity_scope（进行中，15 份真实轨迹）'),
    ('全网随机比率丢包', 'gap',
     '缺口：FaultModel 只有精确规则（≤32 条），无法表达"随机丢 50%"',
     '本实验协议已声明；需比率型规则 + 原生重编译'),
    ('选择性重传 / 多路径乱序', 'gap',
     '缺口：只有 go-back-N 语义；无 SACK 位图、无单包级多路径（MP-RDMA/SRD 形态）',
     '本实验 57%/86% 同代价即该缺口的量化证据'),
    ('单 QP 错误隔离', 'gap',
     '缺口：恢复耗尽即整体停止；不能单 QP 终止 + 服务层重路由',
     'communication_fragility.md 列为未实现后续项'),
    ('千卡逐包 MoE 执行', 'gap',
     '缺口：1024 只有静态拓扑/存储与合成短任务检查；完整千卡 MoE 逐包执行未通过',
     'README 当前能力节明确声明'),
    ('GPU 数值计算 / 硬件校准 / 生产 SLO', 'gap',
     '缺口：计算为声明成本，无 GPU 前向、无实机校准、无生产到达过程',
     'AGENTS.md 与各实验边界声明'),
]
ROADMAP = [
    ('全网比率型随机丢包',
     '回答"DCQCN+PFC 在真实丢包率（0.1%→50%）下的表现"，支持丢包率扫描找拐点',
     'FaultModel 增加 rate 规则 + 一次原生重编译；规则预算从 32 条精确匹配改为比率模型',
     '本实验 README/protocol 已声明该缺口；当前 57% 是单消息确定性规则'),
    ('将已有选择性重传接入本推理服务路径',
     '与有界 Go-Back-N 做同输入、同故障和明确缓冲成本的对照，不预报节省包数或时延',
     'PersistentStreams/事务路径已有 selective_repeat/1；rdma-run 的 recovery 目前只支持 go_back_n，不能直接改一个字段',
     '本实验未执行选择性重传；两轮各丢6包不能当成只需补6包，也不能把已有算法当创新'),
    ('推理服务中的单 QP 错误传播与隔离',
     '恢复耗尽只终止该 QP 并让服务层重路由，而非整体停止共享仿真',
     '事务/持久流路径已有 on_exhausted=isolate_stream；rdma-run 仍整体停止，尚缺请求依赖清理与重新调度',
     '组1在全局停止前已完成；这不证明错误后的其他业务仍能持续服务'),
    ('Karn 自适应 RTO 的重度丢包对照',
     '固定 40µs 初值 vs 自适应采样在 50%+ 丢包下的恢复代价与重复流量差异',
     'Karn估计器是扩展的可选项；在本实验条件族中，先以当前固定初值配置作对照',
     'communication_fragility.md 命题 3 已证明采样无歧义；缺重度丢包场景实测'),
    ('多路径/乱序交付（MP-RDMA/SRD 语义）',
     'MoE all-to-all 短消息高扇出，最能从单包级多路径与不保序放置受益',
     '拓扑生成已支持 multi-rail/多平面；需 QP 乱序放置与逐包路径选择',
     '谱系表第 4 行；本平台当前为单路径保序 QP'),
    ('PD 分离 KV handoff 大流量 + 丢包',
     '检验长 KV 消息中的回退放大、流控与服务等待是否改变，不能由消息变长直接断言损失更大',
     'M4 已有 kv_handoff 提交类型与容量账本；复用本实验的故障注入即可',
     '本实验仅测6KB消息；长流的窗口、反馈及丢包位置需要另立对照'),
]
LANDSCAPE = [
    ('链路/网络层：无损化',
     'InfiniBand credit 链路流控；RoCE 常配 PFC，并结合 ECN/DCQCN 控制拥塞',
     '尽量减少拥塞丢包；PFC不是重传机制，也不是所有RoCE部署的必选条件，RC仍须处理错误',
     '本实验直接注入丢包，正是检验无损假设失效后的协议行为；PFC 自身有队头阻塞/死锁/风暴问题，是"无损化"路线的已知代价'),
    ('传输层重传：Go-Back-N',
     'RC可靠服务的经典有序接收/整体回退实现；不代表所有RNIC都采用同一算法',
     '累计 ACK + NAK 指出缺口序号，发送端从缺口起整体重发；超时兜底',
     '本实验两种模式均整体回退；同首包缺口、同两轮丢失和相同RTO下代价相同，不能推广到任意丢包位置'),
    ('传输层重传：选择性重传',
     'IRN（SIGCOMM\'18）等选择性恢复方案；设备是否支持须核对型号/固件/驱动',
     '接收端记录到达位图，只重传真正丢失的包，不要求底层无损',
     '本项目另一事务路径已有选择性重传；本轮未测，不能从GBN结果直接推算其重发量或时延'),
    ('传输层：多路径与乱序容忍',
     'MP-RDMA（NSDI\'18）；AWS SRD/EFA（NSDI\'20，可靠数据报 + 多路径喷洒 + 乱序交付）',
     '单包级多路径分散，乱序到达直接放置，丢失只重传单包',
     '这些是已有方法；本轮没有逐包多路径对照，不能声称MoE收益最大'),
    ('超时估计',
     '固定 RTO；Karn/Partridge 采样 + RFC6298 平滑；Swift（SIGCOMM\'20）精确时延测量',
     'RTO 决定"等多久才认定丢失"；过短放大重复流量，过长拖延恢复',
     '扩展可选 rtt_estimator=karn，但本轮未启用；固定初值40µs。RFC6298是TCP参考，不是RoCE协议规范'),
    ('拥塞控制与丢包信号',
     'DCQCN（SIGCOMM\'15）；Timely、HPCC、PowerTCP 等',
     'ECN/时延/带内遥测降速，尽量避免丢包发生；丢包仍被视为严重信号',
     '本实验固定原生 DCQCN 不变，隔离丢包恢复单一变量'),
    ('集合通信库',
     'NCCL/DeepEP及其具体网络provider；行为须按所用分支核对',
     '库层一般不自带恢复算法：依赖传输层可靠性，失败沿 QP 错误上抛，由框架决定重连或放弃',
     '本仓库 hybrid/GIN 系列筛选已核过 DeepEP 完成机制；本实验对应"库下层协议"的行为'),
    ('服务系统层',
     'PD 分离 KV 传输（Mooncake、NVIDIA Dynamo/NIXL、vLLM 连接器）；请求级重试/重路由；副本冗余；引擎重启',
     '单请求失败由调度层换实例重算，不要求单条连接必然恢复；用隔离域限制爆炸半径',
     '组1在共享仿真停止前完成两轮；它不是失败后仍继续服务的证据'),
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
                       'RTO 触发两轮整体回退重传（21 次发送 = 3×7 包，8 次丢弃），57.2µs 收到最终 ACK，服务 91.3µs 完成',
                       tl['B:data-57-transient'], 500, end_mark_us=57.154, end_label='最终 ACK 57.2µs'),
        timeline_panel('批次 B · 86% 数据丢包×2轮 · go_back_n',
                       '12 次丢弃（比 57% 多 4 次）但发送次数、NACK、完成时刻与 57% 完全相同——Go-Back-N 从首个缺口整体回退，多丢的包不产生额外代价',
                       tl['B:data-86-transient'], 500, end_mark_us=57.154, end_label='最终 ACK 57.2µs（与 57% 相同）'),
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

    # ---- roadmap table ----
    roadmap_rows = ''.join('<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>' % tuple(esc(x) for x in row)
                           for row in ROADMAP)
    roadmap_table = ('<table><thead><tr><th>新增方向</th><th>解决什么</th><th>依赖与工作量</th><th>证据锚点</th></tr></thead>'
                     '<tbody>' + roadmap_rows + '</tbody></table>')

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
.toc{{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0 4px;font-size:13.5px}}
.toc a{{color:#286aa6;text-decoration:none}}
.toc a:hover{{text-decoration:underline}}
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
<nav class="toc"><a href="#conclusion">结论</a><a href="#c-finish">完成时间</a><a href="#c-resend">重传放大</a><a href="#c-timeline">逐包时间线</a><a href="#c-table">结果表</a><a href="#landscape">方法谱系</a><a href="#cycle">本周期工作</a><a href="#roadmap">新增方向</a><a href="#limits">边界</a></nav>
<p>2026-10-28 · 8 GPU 两组交错 EP4 ready-KV Decode（1 层 2 步，声明计算，合成路由）· 目标：实际首图 0→4 Dispatch（id 1，6,144B，7 包）· 原生 ns-3 RDMA（锁定 <code>e929264</code>）· <a href="analysis.json">analysis.json</a> · <a href="timelines.json">timelines.json</a> · <a href="batch-a-preserved.json">批次A保留证据</a></p>
<p class="note" id="conclusion"><b>核心结论：</b>同一 57% 数据丢包（7 包中 4 个序号、每轮各丢 2 次），原生 NACK 恢复在第二轮后<b>不再有任何恢复动作</b>，服务静默空转到 5ms 上限；启用有界 go_back_n（40µs 初始 RTO、3 次重试）后，同一故障以 +39.5µs 恢复。持续丢包下 86% 与 57% 代价完全相同（Go-Back-N 整体回退），57% 非最终 ACK 丢失被累计确认完全掩盖。</p>
<div class="tiles">
<div class="tile crit"><b>5,000µs</b><span>✗ 批次A 原生恢复：57%×2轮后静默停滞（仿真上限）</span></div>
<div class="tile good"><b>+39.5µs</b><span>✓ 批次B 有界恢复：同一故障的目标确认增量（≈1 个 RTO）</span></div>
<div class="tile"><b>3×</b><span>Go-Back-N 重传放大：57% 与 86% 丢包同为 3× 全消息发送</span></div>
<div class="tile crit"><b>444.2µs</b><span>✗ 持续 57% 丢包：recovery_retry_exhausted 显式失败（5 轮发送后）</span></div>
<div class="tile good"><b>0µs</b><span>✓ 57% 非最终 ACK 丢失：与基线逐字段相同</span></div>
</div>

<h2 id="c-finish">服务完成/终止时间（µs，共同 0 起点）</h2>
{chart1}
<p class="sub" style="color:{INK2};font-size:13px">批次 A 原生停滞为 5,000µs，超出本图坐标约 10 倍，不作截断绘制，见上方状态卡。</p>

<h2 id="c-resend">目标消息源端发送量（全消息 6,144B 的倍数）</h2>
{chart2}

<h2 id="c-timeline">目标消息逐包时间线（0–500µs，悬停查看序号与时刻）</h2>
{legend}
{panels}

<h2 id="c-table">全条件结果表</h2>
<div class="scroll">{table}</div>

<p><b>比例口径：</b>57%/86%指前两次故障发送各丢4/7、6/7包。把全部故障运行的成功发送也算入后，目标数据包实际丢弃比例分别为8/21=38.1%、12/21=57.1%；持续57%条件为20/35=57.1%。各比例针对一条消息，均不是全网随机丢包率。<a href="verification.json">逐包复核与分母</a>。</p>
<p>常见RoCE配置使用RC可靠连接；设备会按QP的超时/重试设置进行可靠传输，但具体乱序接收与重放行为依硬件和版本而异。RoCE部署常把PFC/ECN/DCQCN用于减少拥塞丢包，不能把链路暂停或拥塞降速称为重传。verbs的RC建链属性见 <a href="https://man7.org/linux/man-pages/man3/ibv_modify_qp.3.html">ibv_modify_qp手册</a>；选择性恢复代表工作IRN见 <a href="https://doi.org/10.1145/3230543.3230557">SIGCOMM 2018论文</a>。</p>
<h2 id="landscape">背景：MoE 推理通信的丢包恢复方法谱系</h2>
<p>下表整理 MoE 推理（EP all-to-all、PD 分离 KV 传输）所依赖协议栈在面对网络丢包/故障时的主流选择，以及本实验结果对应的层次。这是背景与相关工作整理，不构成新证据；引用年份为公开论文发表场合。</p>
<div class="scroll">{landscape_table}</div>
<p><b>谱系对本实验的定位：</b>原生 ns-3 模型按序接收；乱序包触发 NACK，发送端从累计确认位置回退重发，默认未给此服务扩展配置有界 RTO。可选扩展添加固定初值 RTO、指数退避和重试上限；Karn 估计器虽可配置，本轮未启用。设备、固件和 QP 参数会改变实际 RNIC 行为，不能把这里的原生模型推广为所有 RoCE/InfiniBand 实现。实验说明了此构造中整体回退、RTO 与累计确认的影响；没有证明选择性重传的收益，也没有验证生产设备的错误处理。事务路径已有单流隔离选项，但本次 rdma-run 耗尽重试会停掉共享仿真；另一 EP 组在停机前完成，只能证明该时点已完成。</p>

<h2 id="cycle">本周期工作与丢包处理现状</h2>
<p><b>本周期（2026-10-28）使用的环境能力：</b><code>rdma-run</code> 调用固定版本的 LLMServingSim 配置调度器及其批次/完成方法（初始化由本项目适配），本项目据服务进度生成 Chakra 图，ASTRA 按图执行，固定原生 RDMA ns-3 传包并以真实网络完成事件推进下一步；网络配置含 DCQCN/PFC/ECN。模型与 GPU 计算使用声明值，本例没有 GPU 上运行模型。<code>rdma-build</code> 使用锁定的原生 ns-3 提交 <code>e929264</code>；<code>rdma-diagnose</code> 从已保存运行中检查失败并捕获逐包事件。两条 UB/非 UB 后端仍隔离。</p>
<p><b>丢包注入现状：</b>后端 <code>FaultModel</code> 精确规则（节点/端口/消息 ID/包类型/序号/时间窗/次数，≤32 条、每条 ≤10,000 次），另有 <code>drop_once</code> 与永久断链 <code>link_failures</code>。<b>已知缺口：不支持全网随机丢包率</b>（如"随机丢 50%"），需要比率型规则与一次原生重编译；本实验的"57%"是单消息 7 包中 4 个序号的确定性规则，已在边界中声明。</p>
<p><b>丢包恢复现状（本次运行实测）：</b>① 默认原生配置下，累计 NACK 触发从最早未确认位置重发；本例重复丢失后只有 1 个 NACK，之后无计时器兜底，仿真到 5ms 上限。代码默认 NACK 生成间隔为500µs，长于本次5ms窗口中的首段停滞时段。② 启用 <code>recovery.mode=go_back_n</code> 后，固定初值40µs、上限160µs、最多3次重试；本轮未启用 Karn RTT 估计。短暂故障确认增加39.481µs，持续故障在444.203µs以 <code>recovery_retry_exhausted</code> 显式结束。③ 耗尽时停止整个共享仿真，不合成成功回调或提前归还额度；组1先于停止完成两轮，但第二轮比同组基线提前332ns，不能据此说流量完全不干扰，更不能称实现了故障后隔离。持久事务路径已有流隔离选项，尚未接入本次服务执行路径。</p>

<h2 id="roadmap">新增能力方向（由本实验证据驱动）</h2>
<p>以下每项都对应本实验的一个具体观察或已声明缺口，按"证据锚点"列可追溯；这是方向清单，不是已排期的承诺。</p>
<div class="scroll">{roadmap_table}</div>

<h2 id="limits">判读与边界</h2>
<p><b>1. 同一缺口下的回退量。</b>本目标消息中序号0始终丢失；Go-Back-N 每轮均重发完整6144B，所以故障轮各丢4/7或6/7包，最终都需相同重发。有限丢包两例各发送3遍全消息，服务均为91,297ns。不能据这两个固定位置条件概括所有位置/概率下代价只由 RTO 决定。</p>
<p><b>2. 原生模式停滞。</b>批次A观察到1个NACK（约11.1µs），14.1µs后第二轮故障发送，其后缺口继续被丢；默认500µs NACK限频和本配置没有RTO兜底使消息到5ms仍未确认。可选40µs RTO在57,154ns确认该目标，但整个服务到91,297ns才完成；持续失败在444,203ns报告重试耗尽。</p>
<p><b>3. 非最终 ACK 的掩盖。</b>本规则丢4/7个非最终累计 ACK，每个指定序号只丢一次；后续更大的累计ACK覆盖缺口，服务和目标确认时间与基线相同。最终ACK丢失是另一种条件，不能由此推出。<b>边界：</b>一条6144B消息、确定性丢包、合成专家路由、声明计算；5ms内失败仅表示该仿真窗口未完成，不代表生产RNIC。批次B在看到批次A失败后登记，结果是机制对照而非独立盲测。</p>
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

1. Go-Back-N 在这两组首包缺失条件下：57% 与 86% 同为 3× 发送、+39.5µs。
2. 原生 NACK 恢复两轮后静默停滞（全程 1 个 NACK，24.1µs 后零事件）；有界超时将同一故障变为 +1 RTO 恢复或 444.2µs 显式失败。
3. 57% 非最终 ACK 丢失被累计确认完全掩盖；数据/确认路径敏感性不对称。

比例口径：57%/86%指前两次发送各丢4/7、6/7包；包含最终成功轮后，目标消息实际丢弃比例分别为8/21=38.1%、12/21=57.1%；持续条件为20/35=57.1%。不是全网随机丢包率。逐包分母见 verification.json。

## 背景：MoE 推理通信的丢包恢复方法谱系

| 层次 | 代表方案 | 丢包处理机制 | 与本实验的关系 |
|---|---|---|---|
'''
    for row in LANDSCAPE:
        md += '| ' + ' | '.join(row) + ' |\n'
    md += '''
本仿真器原生模式 = 无损假设 + NACK 触发 go-back-N（无有界超时）；可选恢复扩展 = go-back-N + 有界重试显式失败（Karn可选，本轮未启用）。三个观察分别对应三个工程取舍：整体回退放大是选择性重传（IRN 等）存在的理由；静默停滞说明有界超时是必要件；ACK 掩盖说明累计确认自带确认路径容错。库层与服务层（请求级重试、隔离域）是传输层失败之后的两道防线。

## 本周期工作与丢包处理现状

- 环境能力（本周期范围）：Scale-Out 推理全链路 `rdma-run`（固定LLMServingSim调度/完成逻辑（初始化适配）→ 项目Chakra图 → ASTRA → 原生RDMA逐包执行，含 DCQCN/PFC/ECN）；`rdma-build` 锁定原生 `e929264` 现场构建（本周期修复其构建登记的 str/Path 哈希 bug）；`rdma-diagnose` 诊断与全节点逐包捕获。未触及 UB 侧。
- 丢包注入：`FaultModel` 精确规则（≤32 条、每条 ≤10,000 次）+ `drop_once` + 永久断链；已知缺口：不支持全网随机丢包率，需比率型规则与原生重编译。
- 丢包恢复（实测）：原生 = NACK go-back-N 无超时兜底（两轮丢包后静默停滞至 5ms）；可选 `go_back_n` = 固定初值RTO + 有界重试显式失败（目标确认+39.5µs，或444.2µs终止）；另一EP组在全局停止前完成；事务路径已有流隔离，本rdma-run尚未接入。

## 新增能力方向（由本实验证据驱动）

| 新增方向 | 解决什么 | 依赖与工作量 | 证据锚点 |
|---|---|---|---|
'''
    for row in ROADMAP:
        md += '| ' + ' | '.join(row) + ' |\n'
    md += '''
边界：单消息确定性逐序号丢包，非全网随机丢包率；声明计算、合成路由；失败仅指 5ms 窗口内未完成。详见 [protocol.md](../../protocol.md) 与 [README.md](../../README.md)。
'''
    (output / 'report.md').write_text(md)
    print('rendered', output)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    execute(a.output.resolve())
