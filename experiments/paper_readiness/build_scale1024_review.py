"""Render the bounded/censored audit; no simulator or raw-log imports."""
import argparse
from html import escape
import json
from pathlib import Path
import shutil


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary-directory',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();assert not a.output.exists();a.output.mkdir()
    summary=json.loads((a.summary_directory/'summary.json').read_text())
    verify=json.loads((a.summary_directory/'verification.json').read_text());assert verify['passed']
    for name in ('summary.json','verification.json','historical_source.json'):
        shutil.copy2(a.summary_directory/name,a.output/name)
    rows=[]
    for name,v in summary['cases'].items():
        event=v['tail_last_complete_node_record'];usage=v['usage']
        rows.append('<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in (
            name,f"{usage['wall_seconds']:.2f}",f"{usage['peak_sampled_group_rss_bytes']/2**30:.2f}",
            v['saved_completion_notifications'],f"{v['tail_max_tick']/1e6:.6f}",
            event['graph_id']+' / '+event['name']))+'</tr>')
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>Scale-out执行、日志与扩展能力核查</title><style>
body{font:17px/1.75 system-ui,sans-serif;max-width:1120px;margin:35px auto;padding:0 22px;color:#172534;background:#f7f9fc}
article{background:white;padding:30px;border:1px solid #dce3ed;border-radius:12px}h1{font-size:29px}h2{font-size:22px;margin-top:30px}
table{border-collapse:collapse;width:100%;font-size:15px}th,td{border:1px solid #dce3ed;padding:9px;text-align:left}
th{background:#edf3f9}.note{border-left:4px solid #ab6a18;padding:12px;background:#fff6e6}code{font-size:14px}a{color:#075d99}
</style><article><h1>Scale-out执行、日志与扩展能力核查</h1>
<p>本轮完成两个平台修复，并从已有千卡失败产物中取得有限但具体的阶段证据。论文主贡献尚未成立，完整千卡推理仍未验证。</p>
<h2>本轮已交付的代码</h2>
<table><tr><th>问题</th><th>修改</th><th>实际验收</th></tr>
<tr><td>持久入口逐包扫描连接、逐ACK扫描历史工作；16/64固定计数</td><td>端口索引、累计前缀、按序号二分定位；真实回调释放后继。v2明确声明最多128连接/8192工作</td><td>固定旧源码重建基线；12连接/48工作与乱序就绪两例完整结果相同。8GPU上24连接/192工作/672实际源包，重复相同；24条事务回归通过</td></tr>
<tr><td>worker已处理控制行可能仍在Python块缓冲，超时日志落后于执行</td><td>stdout按行写出，在前端决策前提交完整行给操作系统</td><td>8GPU人工service的628消息/4轮/1512节点；新旧result和ASTRA日志逐字节相同。SIGKILL缓冲机制小例：0B对49B</td></tr></table>
<p>本地平台提交：<code>5264d11</code>、<code>0d8ee06</code>，均未push。见<a href="../../../fixtures/indexed_streams/README.md">持久路径验收</a>及<a href="../../../fixtures/stdout_capture/README.md">日志修复验收</a>。持久probe与历史service不是同一个入口，不能宣称前一修改解决了旧千卡超时。</p>
<h2>旧千卡实验实际执行到了哪里</h2>
<p>固定Qwen3-30B-A3B、48层、ready-KV Decode两轮、每GPU batch1。1024GPU承载256个独立EP4副本，共享一套原生ns-3；不是EP1024、训练DP或一个全局Alltoall。路由为合成，H100为声明标签，计算参考值未经硬件校准。原拓扑有明显上层超售，不能当生产网络推荐。</p>
<table><tr><th>旧版本</th><th>宿主秒（截尾）</th><th>采样峰值GiB</th><th>已保存首轮通知</th><th>末尾已记录模拟毫秒</th><th>最后完整节点记录</th></tr>'''+''.join(rows)+'''
</table><p>两份stdout中的222条控制事件相同，普通日志头的宿主时间戳不同，不能称整个stdout逐字节相同。两者均没有最终result；已记录tick是下限，不是最终完成时间。宿主存在已知同机重叠，不能由这个表计算可信加速比。</p>
<p>baseline已写出的完整摘要包含256个w0图，其issue与callback全部配平；还出现全部256个w1图。固定历史前端源码只有处理w0完成后才创建同组w1。因而不能把stdout仅保存221条通知理解成“剩下35组没有完成第一轮”；控制记录至少落后于节点执行。</p>
<p>另一处易误读是层进度：baseline所有w1图都记录了第47层的issue，但最高callback层只在8—11之间。历史生成器给RECV设置空依赖，允许提前投递未来层的接收等待。第47层RECV被投递不等于第47层计算已执行；215,060项issue/callback前缀差额也不能直接当作堵塞消息或死锁证据。</p>
<h2>哪些结论仍然不成立</h2>
<ul><li>不能把超时归因成拓扑生成、网络CPU、前端生成或日志中的任何单项。已有第二轮活动排除了“始终只在初始化”的解释，尚无宿主分阶段计时。</li>
<li>不能从没有callback、缺末尾通知或最终文件不存在推导丢包、PFC死锁、硬件通信长尾或现代DeepEP性能。</li>
<li>不能把512/1024不同网络负载、EP4多副本和EP128单组混成同一规模曲线，也不能将现有四请求复制成千卡生产数据。</li></ul>
<h2>下一步应先做的判别</h2>
<table><tr><th>待检验解释</th><th>最小观测/对照</th><th>完成标准</th></tr>
<tr><td>宿主时间主要耗在何处</td><td>分别记录拓扑初始化、ET生成/加载、DECIDE往返、事件执行、日志/报告阶段的宿主单调时钟与CPU时间；避免每包调用宿主时钟</td><td>小例能对齐阶段总时间，网络结果不变，再预注册目标规模预算</td></tr>
<tr><td>未来RECV是否主要增加宿主对象与事件成本</td><td>先统计已注册接收、实际可发送消息、活跃QP和ns-3事件数；检查与层数、batch、EP的关系</td><td>保留原发送依赖和完成语义；不能直接删未来接收来获得快结果</td></tr>
<tr><td>网络共享与路径是否改变策略排序</td><td>在已验收的持续网络上比较实际瓶颈出口、队列积分、ECMP路径与ACK完成，配固定带宽/路径种子及留出请求</td><td>先证明差异稳定且有意义，再谈新策略；需要公平强基线</td></tr>
<tr><td>现代通信语义能否支撑时延主张</td><td>将事件/窗口与真实传输/provider反馈相连，明确self-peer与完成资源；取得匹配硬件条件</td><td>未校准前保留功能模型限定，不用MRT1字节代替GIN成本</td></tr></table>
<h2>失败、范围与复核</h2>
<p class="note">双案例全量审计没有完成。首版解析器拒绝被截断的图名；更正后发现普通日志头时间戳不同，原“整个stdout相同”断言错误；继续保存逐项前缀差集时触及32MiB输出上限。失败文件和命令均保留，没有抬高限额或重跑千卡网络。最终从已经写出的baseline摘要恢复完整计数，numeric只做1MiB尾部和完整小型stdout核查。全量分析器已改为32项样本输出，但没有重新执行，不能当已通过验收。</p>
<p>独立检查核对全部stdout事件、两份1MiB尾部和历史源码的commit/blob/SHA；baseline全量计数仅作内部一致性检查，未独立重枚举。原始节点日志仍留旧目录，没有复制大日志。证据：<a href="summary.json">范围与结果</a>、<a href="verification.json">独立检查</a>、<a href="historical_source.json">历史源码身份</a>、<a href="archive.json">本机检查点</a>。</p>
</article></html>'''
    (a.output/'report.html').write_text(document)
    print(a.output/'report.html')


if __name__=='__main__':main()
