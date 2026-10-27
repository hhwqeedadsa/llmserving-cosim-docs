# 看清服务器间的一份token如何转到多张卡

本例把4张逻辑GPU分到2台服务器，每台2张卡。同位置卡属于同一rail：`[0,2]`和`[1,3]`。例如rank0的token选择专家8、9、10、12；前三个专家在rank2，最后一个在rank3。Dispatch先从rank0发一份到rank2，再在服务器内分别交给rank2和rank3。它不是跨机发送四份，也不是跨机发送两份。

公开命令只需要基础Python，不需要真实数据、GPU、原生后端或旧运行目录。在仓库根目录执行：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout hybrid-deepep-plan \
  --config examples/hybrid_logical/input.json --output /tmp/hybrid-plan
PYTHONPATH=python python3 -m moe_ub_cosim scaleout hybrid-deepep-replay \
  --plan /tmp/hybrid-plan --actions examples/hybrid_logical/prefix.json \
  --output /tmp/hybrid-prefix.json
```

路径相对当前工作目录；输出必须不存在。`hybrid-plan`内的`input.json/plan.json/manifest.json`可以整体搬走，回放会核对文件hash并从保存输入重编译检查语义。修改模型、batch或专家选择时修改输入并使用新输出目录，不覆盖旧结果。

## 输入怎样改

`model`给出总专家数、Top-K、隐藏维度和BF16。专家连续均分到rank，`expert // (experts/ranks)`确定所在卡。`calls[].tokens[rank][token]`就是该token的专家ID列表；每个rank的列表长度是本次调用的本地token数，不能直接等同服务系统的请求batch。Prefill的一条请求可以产生多个token行。

`deployment.domains`显式列每台服务器的连续rank，`rails`列同位置rank。rail是逻辑连通关系，此入口不填写链路带宽或GPU型号，也不自动选择物理NIC/ECMP路径；这些要由后续拓扑和执行适配声明。`execution`声明SM数、每SM的scale-out warp数、容量和Combine归约分支。通道数为两者乘积，token按序号取模分配。

支持2～4台服务器、每台2～8张卡、总rank≤32、每rank≤16 token、1～4次调用。BF16/H必须为256倍数，专家数必须整除rank数，Top-K为1～32且不重复/不带mask。只支持fresh输入；不推断跨调用槽复用。所有资源上限显式填写，最大输入1MiB/输出16MiB/16384行，行数包含通知项。超限在创建输出前拒绝，不自动裁剪输入或放宽上限。

## 生成了什么

- `dispatch`：每token/目的服务器一条，注明源rank、同位置入口rank、rail、通道及有序的域通道slot。
- `forwards`：按目的GPU去重的域内转发，入口卡自己也有copy。`gpu_slot=null`，因为最终接收slot由多个转发器竞争，尚未执行。
- `combine`：保留所有Top-K贡献的位置和贡献卡。允许多次归约时每目标域一份；否则expanded每expert一份、非expanded每目标GPU一份。`schedule=null`，没有执行其进度、flush或数值归约。
- `count_blocks`：每源/目的服务器的rank块和expert块，包含零值的编码及GIN self-peer请求。这里的`gin_self`不是本地零成本旁路。
- `channels`：每6份数据一次周期tail，最后另有finish；0份和6的整倍数都必须finish。通知使用GIN release操作，域内self调用仍保留。

`hidden_bytes`是隐藏向量字节，`token_operand_bytes`是固定TokenLayout对齐后的数据操作数；计数块另有`operand_bytes`。它们不是线上字节或包数，也不含ACK、CNP、重传和完整控制协议。

## 为什么已有数据还会等

`prefix.json`是人为声明的逻辑动作，不是网络采集。入口rank2的转发器先轮到来源服务器1，对应rank2自身的空输入。即使rank0前6个token及tail已经可见，它也不能绕过所选来源；等rank2发来空通道finish后才转到rank0，消费第一批6条。rank0第7条仍需要之后的数据及finish。

动作只有`data_visible`、按序的`tail_visible`和`forward_step`。前两者声明可见性，后者检查一个转发器的稳定快照：若选中来源没有缓存数据或finish，读取所有来源的最新tail；仍未就绪则继续等同一来源，每次消费最多6条。不选一个更方便的来源，不以token序号填最终GPU slot。允许前缀回放；`all_forwarder_admissions_complete`只说明这个Dispatch门槛子模型消费完了。

本入口没有完整Hybrid DAG、GPU/TMA/NVLink资源、notify/barrier、最终slot和linked-list、epilogue、Combine进度、源buffer寿命、provider或包级执行。没有时延、吞吐、千卡完成或优化收益结论。完整包级功能与当前direct/native入口的能力边界分别维护，不能直接把这个plan注入旧direct桥接器。源码及验证见[验收记录](../../fixtures/hybrid_logical/README.md)。
