# 128、256、512卡Clos中的稀疏请求

一张卡没有本地请求，也可能持有别的卡需要访问的专家。本例在128/256/512个GPU端点中明确放入四个不同的人工请求，保留全部专家卡的接收、专家计算和Combine回传。它检查输入、真实LLMServingSim调度与ASTRA/native RDMA之间的联动，**不是R1真实路由、全卡满载、完整模型或硬件性能校准实验**。

## 准备与运行

使用仓库现有前端环境，设置`PYTHONPATH=python`。输出目录必须不存在；准备过程只生成和校验输入，不执行网络。`--gpus`仅接受128、256、512。

```bash
PYTHONPATH=python python examples/scaleout_sparse_clos/prepare.py \
  --gpus 128 --output /path/to/new/clos128
```

准备结果包括`service.json`、`rdma.json`、`topology/topology.json`、逐请求原件与来源清单，以及`preparation.json`中的端点、EP大小、请求数、连接和字节数。改成256/512并选择另一个新目录，可以独立核算对应配置。128使用EP128，256使用EP256，512使用两个EP256；每层256个专家均匀放置，无复制专家。

接入已构建的非UB后端与protoc，使用相同公开命令：

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new/clos128/service.json \
  --topology /path/to/new/clos128/topology \
  --protocol /path/to/new/clos128/rdma.json \
  --backend /path/from/backend-json/service --protoc /path/to/protoc \
  --output /path/to/new/run128

PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-check --run /path/to/new/run128
```

配置给出每个执行60秒、RSS1GiB、每进程地址2GiB、产物128MiB、模拟5ms、完整包最多200000条的限制，输出卷须留8GiB。失败保留原因，不自动扩限或重试。256/512准备成功不证明这两档已经完成网络执行。

## 稀疏模式怎样使用

`route_replay.request_layout`默认是`dense_per_rank/1`，继续要求每卡每slot都有独立原件。显式设为`sparse_explicit/1`后，只有`route_replay.requests`列出的请求存在，`batch_per_rank`表示每卡可用slot数和调度上限。slot允许空缺；请求ID仍是`rank_id × batch_per_rank + slot`，不会重新编号。

每个EP组至少有一个来源请求；重复slot、重复文件或相同SHA内容冒充不同原件、越界和来源窗口不足都会拒绝。没有请求的rank不创建调度请求或KV预留，本地Attention/打包/归并的token工作量为0；它仍持有专家权重，并根据实际收到的贡献执行专家计算及回传。支持有限MoE片段及现有全MoE GQA声明；这不会补齐完整R1所需的Dense/MLA模型。

采用已有三层Clos参考结构：每64个端点一个Pod，每服务器8卡，每Pod四ToR和四Agg，共四Core；单rail/plane、各层链路400Gb/s、每跳1µs。ToR和Agg各有4:1下行/上行带宽比，不能称为无阻塞网络。GPU类型仅是描述标签。同机流量在本例也经RDMA，机内旁路显式关闭；采用已有`port_dense`交换机计数存储，不改变交换网络。

只为这些固定请求实际使用的`(src,dst,plane)`集合配置持久连接，覆盖全部声明层和步数。这不提高平台4096连接上限，不是任意路由变化下的动态建连；更换路由后必须重算完整窗口的连接集合。仍由实际SEND发布和ACK/接收事件推进，准备时没有注入固定的通信时刻。

## 换成真实路由时

沿用[来源路由输入说明](../scaleout_route_replay/README.md)，保留来源模型/E/K/层号、原件SHA和未知采集信息。明确真实请求的源rank与slot，设置稀疏模式，并重新计算连接集合与限额。15份独立原件只能说明15个请求，不能声称128/256/512卡各有一个独立请求。

`frontend.json`记录实际活跃来源rank、逐轮batch请求ID和完成；`rdma-check`分别检查调度选择、原始专家选择、图依赖、逻辑字节、实际接收与源确认。既有逐专家贡献图不等于现代DeepEP的去重、flush或GPU流语义；需要先校准这些条件，才能将结果用于恢复优化研究。

## 可选的EP阶段传输完成依赖

在`service.json`顶层显式添加`"phase_completion": {"mode": "ep_transport_flush/1"}`后，仍使用上述`rdma-run/rdma-check`命令。需要用当前源码构建原生服务；旧后端会提前拒绝新增启动参数，不会自动退回旧语义。缺省不添加此字段，继续执行原有逐rank接收依赖。

此模式使每层Dispatch、Combine分别等待本EP组所有实际发送确认、接收和生产计算，再允许后续专家计算/归并；没有请求或没有数据的rank同样参与。GATE节点的等待来自本次ASTRA/native回调，不给定固定时间，也不额外运行一套网络。

**这是零协调成本的必要依赖下界，不是完整DeepEP。** 它不模拟GIN屏障信号、真实CQ成本或GPU可见性，不改变逐专家贡献的数据表示、持久连接、拥塞控制和恢复。`result.json.service.phase_completion`记录每个门限的成员到达与释放；图版本为`moe-scaleout-wave/2`。公开检查拒绝漏成员、漏本地传输前驱、漏消费依赖和提前释放。单次服务最多65536个门限、131072个成员到达，超限在前端验证时拒绝。
