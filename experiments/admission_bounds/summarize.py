"""Recompute a compact report from preserved public evidence."""
import argparse
from pathlib import Path

from experiments.admission_bounds.evaluate import condition
from experiments.admission_bounds.predict import load
from experiments.admission_bounds.prepare import save


def summarize(stage, output):
    values = {name: condition(stage, name) for name in ('baseline', 'bandwidth')}
    assert values == load(stage/'evaluation-timeline/summary.json')
    assert load(stage/'verification.json')['passed']
    output.mkdir(parents=True, exist_ok=False)
    save(output/'summary.json', values)
    before, after = values['baseline'], values['bandwidth']
    rows = []
    for name, label in (('baseline', '100G'), ('bandwidth', '200G')):
        for plane in (0, 1):
            item = values[name]['timing_bounds']['declared']['plane:'+str(plane)]
            rows.append(f"| {label} / plane {plane} | {item['positive_boundaries']}/{item['finite_boundaries']} | "
                f"{item['union_window_ns']/1000:.3f} | {item['transport_live_overlap_ns']/1000:.3f} | "
                f"{item['not_covered_by_saved_transport_work_ns']/1000:.3f} | {item['physical_transmissions_in_windows']} |")
    checks = sum(sum(v['checks'].values()) for v in values.values())
    report = f'''# 没有新任务提交，不等于可以跳过网络

2026-10-27。**已实现只使用当前回调前缀的通信准入下界计算器；已有完整通用图足以提供这些界。目前没有证据表明“找到空闲时间”能减少本场景的包级事件。** 这是对仿真加速候选的筛选，不是新协议、性能优化或千卡验证。

## 1. 这次实际做了什么

固定上一阶段冻结的576事件、164项工作、29条持久连接，在已经完成的100G和200G两份公开会话上逐边界计算。每份66个实际前后端交互边界；不执行新网络，不读取GPU性能，不用新增15份路由冒充本次输入。

计算器只读取无时刻的图、工作参数、拓扑以及截至当前的回调。普通AND前驱取最大、共享OR触发取最小；使用显式NIC绑定下的最短传播、本地工作时长及self服务成本，忽略队列争用只会放松下界。已观测回调保留真实历史时刻，尚未观测的事件不能早于当前时间。输出是“前端最早可能注册下一项工作”，不是首包时间或通信完成预测。

未来提交、最终transport状态和包捕获单独保存在truth，只供评价。将truth完全移除并放入不可解析的占位文件后，重新预测两份结果仍逐字节相同。全部{checks:,}次逐工作/回调比较均不晚于实际发生；这只验证当前保存输入，也保留了人工规则检查，不能替代对任意平台的形式证明。

## 2. 普通通用图已经能提供多大时间界

同一完整图仅用零成本因果关系，当前未完成事件的下界往往就是现在。增加拓扑传播后，100G下一次跨rank提交在15/60个可评价边界得到正下界，区间并集43.373微秒；再增加已声明的本地/self成本后，变为53/60，区间并集45.329微秒，典型单个界约0.1微秒。200G分别为15/60、42.583微秒和53/60、44.259微秒。

这体现现有成本信息对普通图的作用。完整通用图本身包含应用依赖，没有额外MoE专用算法或信息增量；不能把相对零成本图的改善说成超过ASTRA或其他完整事件图。区间已经取并集，未将重叠窗口反复累加；不同plane仍不能相加为应用收益。

## 3. 为什么这些区间还不能用来删包事件

时间单位为微秒。“无新提交区间”指在这些时段内不会发生新的前端跨rank注册；既有请求、自动生成的回复与ACK仍可继续执行。

| 条件 | 得到正界的边界 | 无新提交区间并集 | 其中有存续传输 | 未被保存传输寿命覆盖 | 区间内实际物理发送次数 |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

plane 0的候选区间全部仍有transport工作存续。plane 1未被保存传输寿命覆盖的区间约19.3微秒，其中实际物理发送数为0。没有包事件的时间段，离散事件仿真本来就能跨过；找到这些时段并不能据此承诺减少包事件或获得宿主加速。存续传输寿命也不是完整的设备状态证书，仍需考虑反馈、恢复、端点和共享资源。

当前DECIDE只提供全局drained，不能仅由应用操作状态推导某个rail已经排空。本轮未删除或合并任何包事件，已证明可省略的包级时间记为0；它表示没有交付安全省略机制，不表示对一切聚合方法的数学不可能性证明。

## 4. 一个会使简单静默判断出错的具体尾部

100G中操作1在4.028微秒完成：请求已经确认、原子返回值也已到达。返回报文自身的ACK要到6.036微秒才到发送方，仍有2.008微秒尾部。200G对应2.004微秒。独立检查在实际捕获中找到了这段ACK的物理发送及最终接收，未把最终状态字段当作唯一证据。

两条件各有{before['atomic_reply_tail_count']}项原子回复在应用操作完成后仍未收到自身ACK，最长分别{before['maximum_atomic_reply_tail_ns']/1000:.3f}/{after['maximum_atomic_reply_tail_ns']/1000:.3f}微秒。完整时间线中，100G的plane 0全部已注册操作于63.523微秒完成，网络仍延续到69.547微秒，相差6.024微秒；200G为59.468到65.480微秒，相差6.012微秒。不要把这段正常协议尾部说成可消除推理损失。

这里必须区分可见性：在实际66个物理DECIDE边界上，“plane操作全完成但还有存续传输”的直接误判次数为0；这些中间回调被已有批次机制延迟到最后交互才送达。上面的完整时间线反例使用评价记录，不伪装成前端当时已经知道的状态。这也说明已有批次机制已减少尾部同步，新增空闲检测未必还有收益。

## 5. 对研究方向的决定

不把普通依赖图、最短路时间界、静态分rail或空闲区间检测作为主创新。它们可保留为分析工具和较强基线。本轮不据此新增“空闲跳时”平台功能：暂未看到能减少现有包事件的收益，而应用完成也不是网络静默的充分条件。

若继续第二候选，应转向**有流量、有竞争的区间能否安全聚合**：保持出口串行服务顺序、ACK/反馈和接收可见，只有不会改变资源阈值及应用门槛时才合并，无法证明就回退包级。下一次研究必须先展示能省掉哪些真实事件，再实现聚合；不能只报告模拟时间跨度。收益衡量应是同等决策可靠性下的实际事件数、宿主时间和内存，仍不是被模拟推理的加速。

第一候选继续以完整通用图为强基线，检验更小表示是否足以选对网络配置；本次没有新增排序实验。两项创新仍未成立，真实匹配校准、独立路由泛化和千卡完整现代应用也未完成。

## 6. 复现与证据边界

使用[目录说明](../../README.md)中的公开离线命令。七项人工检查覆盖AND/OR、共享触发、环、重复前驱、显式NIC和历史回调；五类篡改分别删除回调界、伪造晚于实际的界、提前回复ACK、截断捕获和修改工作，均被拒绝。固定输入及四份平台源码hash不变，详细界保存在持久证据包中。

沿用四份请求的一层/一个条目、四活动rank、2KB分包、声明本地成本与MRT1功能事务。不是现代GPU/NCCL实测；本轮没有新网络、平台/依赖修改、构建、下载或push，也没有硬件收益、投稿质量或千卡加速主张。
'''
    (output/'report.md').write_text(report)
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.stage.resolve(), args.output.resolve())
    print('Reported conservative generic lookahead and residual ACKs; no packet speedup claimed')


if __name__ == '__main__':
    main()
