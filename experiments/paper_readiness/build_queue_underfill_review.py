"""Build the fixed cold-SQ/source-credit report from completed public evidence."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil


def load(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('task','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();task=args.task;out=args.output;assert not out.exists()
    summary=load(task/'analysis/summary.json');v=load(task/'checked-final/verification.json')
    feedback=load(task/'explanation/feedback.json');capacity=load(task/'input-corrected/truth/capacity.json')
    source=load(task/'input-corrected/truth/source-audit.json')
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    original=task/'input/truth/capacity.json';corrected=task/'input-corrected/truth/capacity.json'
    assert load(original)['maximum_per_sender_peer']==60 and capacity['maximum_per_sender_peer']==53
    identical=[]
    for path in sorted((task/'input/observed').iterdir()):
        assert path.read_bytes()==(task/'input-corrected/observed'/path.name).read_bytes()
        identical.append(dict(file=path.name,sha256=digest(path)))
    correction=dict(reason='Combine returns owner->origin; first static table incorrectly counted it in the Dispatch direction',
        original_capacity=dict(path=str(original),sha256=digest(original),maximum_sender_peer=60),
        corrected_capacity=dict(path=str(corrected),sha256=digest(corrected),maximum_sender_peer=53),
        global_provider_work_unchanged=452,observed_files_byte_identical=identical,
        old_producer_expression='combine_put=pair_counts[src,dst]',new_producer_expression='combine_put=pair_counts[dst,src]',
        old_checker_expression='provider[src,dst]+=2',new_checker_expression='provider[src,dst]+=1; provider[dst,src]+=1',
        superseded_checks=['checked/verification.json','checked-explanation/verification.json'],
        final_check='checked-final/verification.json',network_inputs_or_results_changed=False,network_rerun=False)
    assert not (task/'direction-correction.json').exists()
    save(task/'direction-correction.json',correction)
    assert v['verified'] and v['all_declared_completed'] and v['explanation_streams_checked']==36
    assert len(summary['cases'])==3 and summary['new_network_runs']==1 and summary['reused_successful_runs']==2
    by_depth={c['depth']:c for c in summary['cases']};full=by_depth[1024]
    lower=full['minimum_port_service_ns']
    facts=dict(new_network_runs=1,hash_verified_reused_successes=2,
        depth1024_vs1_ack_percent=100*(full['acked_ns']/by_depth[1]['acked_ns']-1),
        depth1024_vs8_ack_percent=100*(full['acked_ns']/by_depth[8]['acked_ns']-1),
        depth1024_ack_above_static_lower_bound_percent=100*(full['acked_ns']/lower-1),
        cold_provider_work_upper_bound=452,cold_sender_peer_upper_bound=capacity['maximum_per_sender_peer'],
        source_requested_sq_depth=1024,hardware_sq_readback=False,
        shallow_credit_is_best=False,depth_relation_monotonic=False,
        modern_provider_executed=False,hardware_calibrated=False,independent_load=False,
        paper_main_contribution_established=False,raw_volatile=True,persistent_backup=False)
    out.mkdir()
    for name,path in [('summary.json',task/'analysis/summary.json'),('verification.json',task/'checked-final/verification.json'),
        ('feedback.json',task/'explanation/feedback.json'),('capacity.json',task/'input-corrected/truth/capacity.json'),
        ('sources.json',task/'input-corrected/truth/source-audit.json'),('direction-correction.json',task/'direction-correction.json'),
        ('protocol.md',Path(__file__).with_name('queue_underfill_protocol.md'))]:
        shutil.copyfile(path,out/name)
    save(out/'findings.json',facts)
    rows='\n'.join('| '+' | '.join(map(str,(c['depth'],'新增' if c['kind']=='new' else '核hash复用',
        c['acked_ns'],c['finish_ns'],c['credit_delayed_work'],c['unique_destination_ce'],c['cnp_count'],
        c['peak_switch_queue_bytes'],c['minimum_rate_at_tx_bps'])))+' |' for c in summary['cases'])
    overlap='\n'.join('| '+' | '.join(map(str,(c['case'],c['cnp_before_own_last_tx'],c['cnp_after_own_last_tx'],
        c['cnp_after_global_business_ack'],c['drain_after_business_ack_ns'])))+' |' for c in feedback['cases'])
    text=f'''# 更强的发送对照改变了MoE并发结论

只改变既有180份数据写的软件发送额度，新增的1024条件比1和8都快：业务ACK为457,910ns，分别缩短约{-facts['depth1024_vs1_ack_percent']:.2f}%和{-facts['depth1024_vs8_ack_percent']:.2f}%。这否定了从此前1/8配对推导“浅并发更优”或“额度越深越慢”的建议。原1/8配对数值仍成立；新增的是一个原来缺失的、更充分供数的对照，没有修改旧网络结果。

## 本轮服务什么问题

研究MoE专家并行跨节点Dispatch中的发送准入、网络拥塞反馈和共享链路。当前平台用每条持久字节连接的软件额度决定允许发布多少份写；额度由最后覆盖该写的ACK释放。它可以研究“人工限制未确认工作的数量会怎样”，但不能自动当作现代GPU通信库的SQ（发送队列）容量、CQ（完成队列）实现或真实NIC行为。

本轮从固定缓存的一手源码核实容量和工作单位，再加1次公开网络执行，2个旧成功结果按输入/协议/拓扑/二进制/结果hash复用。没有把功能测试输入、现代离线计划或人工完成事件作为研究负载。没有新增平台能力、改依赖、构建安装下载、调拥塞参数、搜更多depth或重跑失败Prefill。

## 为什么必须增加这个对照

固定DeepEP f6aa38f的kDefaultQPDepth是1024，context把该请求传给GIN；固定NCCL 12df1a1的GDAKI把queueDepth用于qp_init_attr.sq_nwqe。该值是源码请求，不是本机实际SQ读回，也不保证所有设备/版本均相同。正常provider按WQE ticket检查旧槽可复用；小于1GiB的普通put只需一个发送WQE，inline值与signal也有各自reservation。一个14,464B put在网络上拆成15个数据包，并不占15个SQ工作条目。

对同四请求、层4、32/28/10/6共76 token，独立重建180份跨rank Dispatch put。按fresh direct、无跨rank NVLink、本地归约、无附加counter和其他作业的来源分支，把对应Combine、四次屏障和计数也算上：

| 对象 | 全组provider工作数 | 边界 |
|---|---:|---|
| 跨rank Dispatch token put | 180 | 每token按目的rank去重 |
| 本地归约后的Combine token put | 180 | 对应每个远端token副本，不逐专家重复 |
| Dispatch/Combine入口与出口signal | 64 | 四次×4×4，包含self GIN |
| rank count | 12 | self使用本地store旁路 |
| expert count block | 16 | 包括零值块与self GIN |
| 合计 | 452 | API到所选正常provider分支的条件WQE核算 |

即使保守地忽略上下文拆分，每sender-peer汇总全调用最多{capacity['maximum_per_sender_peer']}项；452项全局总量也不足1024。这里Combine从专家owner返回原来源，方向与Dispatch相反。在“有效容量确为1024、从空SQ起步、无其他工作”的条件下，当前有限调用不必因槽环绕而等CQ回收。多通道只会把同peer的这些工作继续分开。这个证明不依赖猜测动态slot顺序，但不能推广到已有大量未完成工作的热队列、跨调用累计或其他provider。

这不意味着现代库所有工作可在0ns发出。GPU准备、通道顺序、计数、屏障、源buffer租用、门铃和可见性仍会约束发布；只是不能把本负载中人为设置的depth1/8反压直接解释为默认1024 SQ被填满。当前现代plan公开入口每rank最多16 token，本轮没有抬限或拆76-token窗口伪装成同一调用；上述核算独立按源码完成，没有执行现代库或现代计划。

## 保持与改变的条件

固定四个已见R1-AWQ/MMLU请求、entry0/层4、连续等容量专家放置、H7168/E256/Top8、BF16/direct布局假设。76 token、180写、2,603,520B载荷、2,700个数据包不变。EP4位于8端点Clos的GPU0/2/4/6；接入100G、上层25G、每链路1us，12条持久QP、同NIC/线端口/ECMP路径/seed11。所有根工作0ns就绪；不含计算、Combine控制流、完整推理、训练或千卡部署。

固定AI40Mb/s、HAI50Mb/s、阈值间pmax0.01、ECN阈值32,768/131,072B、NP50us、alpha55us、恢复55us与其余原参考配置。1024条件只改软件ACK额度；数字来自源码容量审计，但该字节后端仍不等价现代GIN。每个条件内部共享一套网络时钟；三个独立条件不能拼接成一次执行。单种子闭环改变随机数调用顺序，不能声称逐包相同随机标记。

## 全部仿真观测结果

| 软件额度 | 来源 | 最后业务ACK ns | 网络排空 ns | 等额度的写数 | 目的CE包 | CNP | 队列峰值 B | 实际TX最低QP速率 bit/s |
|---|---|---:|---:|---:|---:|---:|---:|---:|
{rows}

1024条件180写均0ns发布，等额度的写为0；原1/8分别有168/93份等待。它已包含1332个首次反馈后的实际数据包，不能解释成完全不受反馈影响的瞬时发完。最低实际TX设置速率25G，明显高于depth8的390,625,000bit/s；这是状态采样，不是测得的平均吞吐。深度1无CE/CNP。

所有条件数据和必要ACK的物理出口字节完全相同，最忙出口服务下界443,791.36ns保留。1024业务ACK仅高出14,118.64ns，约{facts['depth1024_ack_above_static_lower_bound_percent']:.2f}%；这进一步削弱了“当前窗口存在大量新算法收益空间”的主张。该下界没有包含现代控制流和GPU执行，不能外推为真实库性能上限。差值也不是全部可消除的损失。

## 为什么更多队列和通知未必更慢

关键是反馈到达时，发送端还有多少工作没有发出。逐条持久连接对齐自己的最后数据TX后得到：

| 条件 | 自身最后TX前收到CNP | 自身最后TX后收到CNP | 全局业务ACK后收到CNP | 业务ACK后排空 ns |
|---|---:|---:|---:|---:|
{overlap}

三个条件均无CNP与该连接最后数据TX同纳秒的歧义。depth8有39个通知到达时本连接还在发数据；1024只有10个，另外50个已晚于该连接最后TX。这些后到通知不能限制该有限条件中已发完的数据，仍可能影响后续真实调用，因此不能据此裁剪反馈包或重置QP。

例如stream3：depth8首次CNP为33,447ns，最后数据TX为1,246,980ns，期间共8次CNP，实际TX最低390,625,000bit/s；1024首次CNP同为33,447ns，最后数据TX已在123,229ns，只有2次CNP在此前到达，实际TX最低25G。完整结果的NP/RP事件已独立重放。这里给出当前执行的时间关系和状态差异，不是把反馈删掉后的反事实收益。

1024队列峰值744,020B高于depth8的294,948B，CE/CNP也更多，但业务ACK更早；因此仅看峰值、总CE或总CNP不能预测该窗口的完成排序。更多字节先进入网络，交换机队列、后续通知与源端速率共同演化；不能只凭这些相关量给一个单因果百分比分解。

本轮首次明显区分业务完成和排空：1024的业务ACK是457,910ns，最后CNP RX和网络排空是482,849ns，差24,939ns。尾部仍有一次通知在全局业务ACK之后到达，不能把排空当业务时延，也不能因业务已完成而取消它。

## 论文判断：哪些保留，哪些收缩

保留：网络时间状态与供数方式会改变同一流量矩阵的完成；小窗和有限参数范围的旧反转是可复核的条件事实；静态端口服务量仍是有力基线。新对照把depth1、8、1024的关系显式变为非单调，不应继续泛化成“限制到浅并发更快”。

收缩：默认provider SQ容量被当前小负载耗尽、浅并发是合理最优基准、调浅额度是新MoE优化，都缺少支持。此轮1024接近强服务下界，当前冷启动单窗口的算法候选价值进一步下降。不能以再找一个更拥塞的参数、隐藏这个对照或把经典拥塞控制现象换名来维持贡献。

后续有效门槛：取得独立真实请求和实际发布轨迹；在明确的GPU通道/计数/屏障/源buffer/CQ语义下看持续调用是否仍有重要网络等待；以充分供数、来源合法的通信库和已有准入/调度方法作为强基线；用匹配NIC测量校准。未过这些门槛前不开发一个只针对人工小额度的策略。现代控制/self-peer/可见性尚未接入网络，本机硬件校准仍缺，论文主贡献未成立，A类论文目标继续进行中。

## 验证和保存

本轮11份固定源码的tree/blob/SHA256和摘录通过核对；独立枚举452项provider工作及每sender-peer上界{capacity['maximum_per_sender_peer']}。首次静态核算错误地把Combine计在Dispatch同向，旧上界60及对应旧检查保留，最终按反向修正；全局452、所有网络声明/输入hash及网络结果不变。direction-correction.json记录更正来源，input-corrected与原observed逐字节相同。网络核对8,100个数据包、8,100个ACK、114个CNP，共16,314端点UID关联、120个物理端口条件、540工作信用、12,426参考状态，另核对36条连接条件的反馈/最后TX关系。全部三个条件完成，只有1次新增网络执行，另外2次核hash复用；后续分析未重跑网络。

每阶段60秒/RSS1GiB/地址2GiB、仿真5ms；全轮128MiB/300秒，归档成员96MiB/压缩48MiB，内存盘预留8GiB。原始件与检查点位于/dev/shm/moe-queue-underfill-20261022，仅易失内存盘、没有持久备份。实际动态库、原件、固定源码和命令随归档保存；ELF保留原RPATH，不是可迁移运行镜像。源码、小报告和收据本地提交，不push。
'''
    (out/'report.md').write_text(text)
    parts=[];inside=False
    for line in text.splitlines():
        row=line.startswith('|')
        if row and not inside:parts.append('<div class="scroll"><table>');inside=True
        elif inside and not row:parts.append('</table></div>');inside=False
        if line.startswith('|---'):continue
        if row:parts.append('<tr>'+''.join('<td>'+html.escape(x.strip())+'</td>' for x in line.strip('|').split('|'))+'</tr>')
        elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.strip():parts.append('<p>'+html.escape(line)+'</p>')
    if inside:parts.append('</table></div>')
    links=''.join('<li><a href="'+html.escape(r['url'],quote=True)+'">'+html.escape(r['path'])+'</a></li>' for r in source['sources'])
    (out/'report.html').write_text('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>发送额度与真实SQ容量</title><style>body{font:17px/1.85 system-ui,sans-serif;max-width:1150px;margin:40px auto;padding:0 22px;color:#23334b;background:#f4f7fb}h1{font-size:30px;line-height:1.5}h2{font-size:23px;margin-top:36px;color:#164b7d}p{background:white;padding:14px 18px;border-radius:8px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;background:white;font-size:14px}td{border:1px solid #d3deeb;padding:8px;text-align:right;white-space:nowrap}td:first-child{text-align:left}tr:first-child{background:#e0eafa;font-weight:bold}a{color:#1462a1}li{overflow-wrap:anywhere}@media print{body{font-size:11px;background:white}table{font-size:9px}td{white-space:normal}}</style></head><body>'''+''.join(parts)+'''<p><a href="summary.json">全部指标</a> · <a href="feedback.json">按连接的反馈时间线</a> · <a href="capacity.json">SQ容量核算</a> · <a href="verification.json">独立核验</a> · <a href="protocol.md">执行前方法</a> · <a href="archive.json">归档收据</a> · <a href="final-check.json">最终检查</a></p><h2>固定版本的一手源码</h2><ul>'''+links+'</ul></body></html>')
    print(json.dumps(dict(report=True,output=str(out),conditions=3,new_network_runs=1)))


if __name__=='__main__':main()
