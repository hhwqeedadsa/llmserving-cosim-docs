# 按目的卡去重的显式传输投影

## 目标与依据

现有缺省图按每个token的每个专家贡献传输H×元素字节，Dispatch/Combine相同。这是历史激活量抽象，不能视作固定DeepEP direct的完整表示。固定`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`中，`dispatch.cuh`按目的rank去重；`combine.cuh`在expanded且不允许多级归并时逐专家发送hidden，允许本rank归并时先计算再发送TokenLayout。来源清单及SHA核验保存于本轮fixture；不升级依赖或调用GPU。

## 公开输入

在有限或完整形状服务的顶层显式设置：

```json
{
  "phase_completion": {"mode": "ep_transport_flush/1"},
  "token_transport": {
    "mode": "deepep_direct_bf16/1",
    "combine": "rank_reduced/1",
    "local_reduce_ns_per_extra": 100
  }
}
```

`combine`为`rank_reduced/1`或`expanded_per_expert/1`。前者每token每专家所在rank回传一次，必须显式声明每个额外专家贡献的本地归并纳秒成本；后者逐专家回传，成本必须为0，拒绝未生效参数。0成本只表示声明的零成本下界，不是实测。只支持BF16激活（`activation_bytes=2`），hidden字节需32B对齐，不支持FP8/SF、负数专家、专家副本或hybrid两级转发。要求显式阶段完成模式，避免把去重配置与未核库的旧重叠混为一谈。未知字段/模式、布尔耗时及越界在运行前拒绝。

还要求显式配置持久`transport`；缺少该配置时在运行前拒绝。两种Combine模式共用既有持久连接与恢复方式，不把逐消息旧传输当作新模式的执行载体。

缺省不添加`token_transport`，原配置、图/消息字段及字节保持不变。新模式沿用wave/2（阶段完成图），新增`token_transport`说明及逐消息`transport_tokens`，不改变原有任务ID、QP映射或选路种子。

## 逻辑和成本

每层先保留每token的全部Top-K专家与所在rank，专家工作计数仍按每专家贡献累计。Dispatch对**同一个token**的目的rank去重；不同token、slot、请求、层和步不互相去重。原`contributions`保持该src/dst对的专家贡献数，`transport_tokens`说明实际传输单元数，消息字节不再由`contributions × H × 2`直接推出。

BF16无SF的固定TokenLayout投影：TMA对齐32B、RDMA对齐64B。Dispatch元数据为`K×8+(1+K)×4`B，按32B对齐后与hidden相加，再对齐64B。rank合并Combine元数据为`K×8`B，使用同样对齐；expanded逐专家Combine只传hidden字节。H7168/K8对应每单元14464/14400/14336B。该字节包含布局元数据/填充，仍不是Ethernet线上字节，后端另加其模型包头。

rank合并新增每rank每层`COMP L*.combine_reduce`，等待专家工作；耗时为该rank各token的`(本地专家数-1)`正部之和乘显式单位成本，包含本rank源token的本地专家贡献。随后Combine发送才可启动，阶段完成门限也等待该计算，空rank仍有零成本节点。不同token不能共用归并结果。expanded模式不插入该节点。接收等待、发送确认、图退休和排空沿用现有规则。

## 核验及边界

检查器从逐token路由独立核算专家份数、传输单元和字节，核对卡内合并依赖/时长；拒绝少算专家、跨token去重、漏合并前驱、丢门限或篡改字节。默认图与既有实际执行图离线比较；CPU边界后在128卡Clos进行两个模式的真实前后端功能执行，报告完整包和收发守恒。来源15原件另作离线核算，不将功能小例写成真实R1性能。

该模式只投影固定direct BF16的数据布局和必要计算依赖，仍没有GIN计数交换、屏障信号、实际CQ/流/可见性、多context映射、数值合并、硬件耗时与包合并校准。即使字节更准确，也不是完整现代强基线或新通信算法。没有证明旧故障成本可以按减少字节的比例消除。
