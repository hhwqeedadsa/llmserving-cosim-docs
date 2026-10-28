# SimAI前端与320卡真实专家路由实验

已完成四次实际联合运行：Clos/HPN启发结构各正常与指定数据/ACK丢失一次。固定平台实现`44b55c1`、执行前方法`0c8b268`；[离线网页](results/20261028-final/report.html)包含9组科学图、8份本次320卡曲线、双端/逐消息/逐包CSV及输入来源。不是复用EP4曲线，也不只是拓扑生成。

| 条件 | 服务完成/µs | 首Dispatch有效收齐/µs | 捕获事件 |
|---|---:|---:|---:|
| Clos正常 | 87.346 | 10.420 | 67,216 |
| Clos数据与ACK丢失 | 216.239 | 141.126 | 67,304 |
| HPN启发正常 | 91.815 | 12.281 | 62,736 |
| HPN启发数据与ACK丢失 | 221.413 | 142.064 | 62,824 |

每次320端点/40服务器、15个真实源请求、221个实际数据参与GPU、942消息、13,619,200有效字节。专家选择来自用户提供的15份完整R1-AWQ/MMLU原件；原层3—4、Decode entry1—2，EP256+EP64，每rank每层1/4专家。源rank和专家放置是声明部署，不是运行时采集的真实位置。上游SimAI/Vidur实际调度与KV资源生命周期执行，网络回调推进下一轮；计算是声明参考，初始状态是已有KV。

三次指定丢包均命中并恢复；两种结构内正常/故障的全部wave逐字节一致。这是GBN恢复功能与影响对照，不是新算法或50%丢包实验。两种拓扑资源不相等，HPN单消息按plane发送，本输入正常HPN启发更慢，不将差异称为等成本拓扑收益。没有完整R1、模型权重、GPU数值计算、硬件校准、生产负载保证或完整现代DeepEP。完整范围和预算见[执行前方法](protocol.md)。

复现使用公开CLI，不依赖测试、历史fixture或旧运行目录。需准备本仓库Python及`simai`依赖、现有`protoc`与独立原生`rdma-build`的service程序；从[SimAI来源说明](../../examples/simai_frontend/README.md)准备固定runtime。将来源清单指定的原始文件放到`data/moe-trace-small/`，内容hash必须与接收清单一致，不能用随机数据或重复请求补规模。以下从仓库根执行，设置`SIMAI_RUNTIME/NATIVE_SERVICE/PROTOC`为已有真实路径：

```bash
PYTHONPATH=python python experiments/simai_scaleout/run.py prepare --root runs/my-simai-320
PYTHONPATH=python python experiments/simai_scaleout/run.py run --root runs/my-simai-320 --runtime "$SIMAI_RUNTIME" --backend "$NATIVE_SERVICE" --protoc "$PROTOC"
PYTHONPATH=python python experiments/simai_scaleout/run.py audit --root runs/my-simai-320
PYTHONPATH=python python experiments/simai_scaleout/verify.py --root runs/my-simai-320 --output runs/my-simai-320/independent-verification.json
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 python experiments/simai_scaleout/render.py --stage runs/my-simai-320 --output runs/my-simai-320/web
PYTHONPATH=python python experiments/simai_scaleout/audit_report.py --stage runs/my-simai-320 --report runs/my-simai-320/web --output runs/my-simai-320/web-check.json
```

输出路径必须全新。`run --case clos-normal`可显式只执行一个尚未执行的条件；同名运行及已有命令记录拒绝覆盖。实际网络由平台资源探针限制；本次总额度/监控入口与资源明细见[收尾记录](results/closeout.json)，不自动扩大限额或重跑失败网络。命令中的`python`应属于已安装依赖的环境，当前机器使用`.venv/frontend/bin/python`。

[独立原件到包审核](results/20261028-final/verification.json)枚举60个token/层源记录、260,080条包事件、4,632个曲线点与2,560个端点行；完整比较曲线和CSV，不仅核总量。[网页核验](results/report-verification.json)检查72个hash、72个资源链接、8份曲线、1,400条SVG物理边和内嵌筛选脚本；PNG人工查看通过，没有声称浏览器布局自动测试。所有原件和原生结果保留在`runs/simai_scaleout_real_20261028/`，不提交大型原始路由。

核验阶段首次CSV读取未将闲端点的空Counter字段按0处理，失败保留后修正离线审核；首次网页脚本检查把大JS放命令参数触及系统参数长度，改为标准输入后通过。均未重跑网络。首版全量图难显示最后少量字节的恢复，最终图增加同数据末尾20KiB放大视图；首版保存在运行根`render-first-output/`，不混作最终交付。
