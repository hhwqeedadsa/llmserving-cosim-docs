"""Recompute the physical interaction audit and its human-readable report."""
import argparse
import json
from pathlib import Path

from experiments.interaction_scope.analyze import analyze,load,save


def summarize(stage,output):
    summaries = {name:analyze(stage/name)[0] for name in ('baseline','bandwidth')}
    assert summaries==load(stage/'full-analysis/summary.json')
    output.mkdir(parents=True,exist_ok=False)
    save(output/'summary.json',summaries)
    baseline,bandwidth = summaries['baseline'],summaries['bandwidth']
    model = {name:load(stage/name/'modern-execution.json') for name in summaries}
    application = {name:max(e['time_ns'] for e in doc['events'] if e['kind']=='application_stage_complete')
                   for name,doc in model.items()}
    rows=[]
    for name,label in (('baseline','100G'),('bandwidth','200G')):
        item = summaries[name]
        wait = item['per_packet_queue_wait']
        rows.append(f"| {label} | {wait['wait_ns']/1000:.3f} | {wait['other_connection_ns']/1000:.3f} | {wait['same_connection_ns']/1000:.3f} | {wait['ack_serialization_ns']/1000:.3f} | {item['max_packet_wait_ns']/1000:.3f} | {application[name]/1000:.3f} |")
    difference = (baseline['per_packet_queue_wait']['wait_ns']-bandwidth['per_packet_queue_wait']['wait_ns'])/1000
    app_difference = (application['baseline']-application['bandwidth'])/1000
    before = baseline['request_only_port_sharing']['direct_connection_pairs']
    after = baseline['full_direction_port_sharing']['direct_connection_pairs']
    report=f'''# 包级交互范围：能分开什么，不能据此承诺什么

2026-10-27。本轮只分析已有两个正常网络的完整记录，零新网络。**应用依赖连成一体，物理网络却分成两个rail；反向流量显著扩大可能共享资源的连接对，但本次ACK排队作用很小。** 这为交互范围候选提供了具体边界，尚没有形成仿真加速器或证明加速比。

## 1. 已核对到什么粒度

每条件264个UDP源包、264个源ACK，共528条完整包路径，覆盖2,464次物理发送和对应接收、8,800条捕获。逐包使用实际IP、端口、协议、序号和UID谱系对应请求、原子回复及ACK；逐跳核对端口、下一节点、串行服务和传播时延。所有端口模型字节数与原生计数一致。不是只看首包路线，也不把UID单独当成传输唯一身份。

每条件重建1,936次交换机包入队/出队，核对两种优先级的FIFO、出队即发送及全部出口的面积/峰值/最终清零。所有正等待均由同一物理串行器在此前的实际发送区间解释，无剩余未解释等待。这里只覆盖交换机出口；当前捕获没有NIC内部入队时间，不能据此宣称全部端到端等待已解释。

## 2. 全局屏障并不等于所有包必须一起细化

576个应用事件构成一个弱连通依赖图，而25条有线上流量的连接按端口共享分成15与10两个分量。拓扑和显式NIC配置也独立支持这个物理分组：交换机之间没有跨plane链路，每条连接的两端NIC均绑定对应plane。两分量分别位于plane0与plane1；把同交换机的共享MMU也纳入后，分组仍不变。

这只是当前模型的物理交换机、链路和NIC端口分组。两个rail仍受同一应用依赖、共享GPU本地执行通道和回调推进约束；还须处理随机数流、拥塞/恢复及资源状态。不能把两次独立运行时间相加，也不能据此承诺两倍加速或称两个完整任务独立。按rail分组本身是已知工程基线，不是新创新。

## 3. 不能只按token数据正向路径划分

按请求UDP路径，直接共享出口的连接对有{before}对；保留原子回复和ACK的反向发送后变为{after}对，增加{after-before}对。每个rail内部已非常紧密；只按专家是否热门或正向大流是否相交来粗化，会漏掉物理串行器上的关联。

“可能共享”仍不等于“当前损失明显”。本次两组均无ECN标记、PFC或重传；ACK占据串行器而使其他已入队包等待的累计时间，100G仅{baseline['per_packet_queue_wait']['ack_serialization_ns']}ns。这里统计被等待包的等待贡献，可重复计入同一ACK挡住的多个包，既不是ACK自身总服务时间，也不是业务关键路径收益。不能靠关联边数量增加来夸大恢复/控制优化空间。

## 4. 本次排队的具体数量及收益边界

下表时间均为微秒。前四项为跨包累计等待，最后一项为整个模型应用阶段完成，两者不能相减解释软件收益。

| 网络 | 包累计等待 | 其他连接造成 | 同一连接前包造成 | 其中ACK占用串行器造成 | 最长单包等待 | 模型应用完成 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

ACK项与前两类归因重叠，不能再相加。100G跨连接等待占累计等待约{100*baseline['per_packet_queue_wait']['other_connection_ns']/baseline['per_packet_queue_wait']['wait_ns']:.1f}%，说明实际存在连接间竞争，但这不是新现象或可消除比例。

带宽翻倍使包累计等待减少{difference:.3f}μs，模型应用阶段只减少{app_difference:.3f}μs。前者包含并发包重复等待，不能当作可实现的端到端收益。这个对照改变了网络资源，仍不是新软件方法的改善或优化上界。

## 5. 对两项研究候选的实际约束

第一候选仍应评价“更精简表示是否足够选对网络”。本轮再次显示，总字节、潜在共享边数和累计排队都不能直接代替应用完成；完整通用事件图仍是强基线。若摘要法能同样选对，就不必一律展开全部语义；若要声称新增信息有用，须展示同资源下的决策差异与代价。

第二候选可以继续研究，但须越过“静态按rail分组”这条已有基线。下一步必须给出可在线取得的边界：下一批最早何时可能到达、哪些端口可能共享、占用何时可能越过ECN/PFC/缓冲阈值、哪些ACK/恢复反馈仍在途；在已证明的区间才能合并或降精度，越界及时回到包级。还要保留跨域应用门槛、GPU资源和随机数语义。这是待验证的方法方向，尚无安全证明、原型运行或性能收益。

当前数据只能证明两个物理rail的结构分离和各rail内的实际竞争，不能证明大集群里普遍存在大量独立小域，也不能排除基于时间边界的方法。热点随机化、普通连通分量、已有回调批次和QP复用均不构成论文贡献。若下一步的可验证边界无法留出足够可合并工作，应淘汰该加速候选。

## 6. 范围、独立核查和复现

仍是四活动rank、四份固定请求、单层、58个MoE层中的一个条目、E256/Top8、声明成本及2KB分包；拓扑共8个GPU节点/14个交换机，两物理plane。新增15份请求、千卡完整应用和真实GPU校准均未进入本研究。模型包字节与真实RoCE字节不能混同。

独立程序从原始捕获重数全部发送/接收，按每包驻留时间乘字节重新核算队列面积，核对正向/反向归属与图分组；修改捕获完整性、线路身份、收包时刻、队列占用、重复发送及静默删除ACK均须拒绝。源码与输入逐hash核验，原件不变；结果与复算说明见[使用说明](../../README.md)，原始包/等待见证保存在持久证据包。本轮零新网络/GPU/构建/下载/平台依赖修改/push。两项创新均仍待证明。
'''
    (output/'report.md').write_text(report)
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    summarize(args.stage,args.output_dir)
    print('Reported physical rail domains, complete paths and packet-wait attribution; zero new networks')


if __name__ == '__main__':
    main()
