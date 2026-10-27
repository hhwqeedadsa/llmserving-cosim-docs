# 查看多个通道和调用实际共享哪些发送资源

本例先把4张逻辑GPU分到2台服务器，每台2张卡，再将两个MoE调用中的Dispatch请求绑定到声明的GIN资源。它帮助后续网络执行保留跨通道共享，而不是把每个token都新建一条连接。只需基础Python，不需要GPU、后端、原始数据集或旧运行目录。

在仓库根目录执行，两个输出目录必须不存在：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout hybrid-deepep-plan \
  --config examples/hybrid_bindings/input.json --output /tmp/hybrid-logical
PYTHONPATH=python python3 -m moe_ub_cosim scaleout hybrid-deepep-bind \
  --plan /tmp/hybrid-logical --provider examples/hybrid_bindings/provider.json \
  --output /tmp/hybrid-bound
```

输入给出E16/Top4/H256/BF16、2SM×2个scale-out warp和两次调用。这是人工功能输入，逐rank的token列表长度不是整个服务系统的batch。修改专家选择、token数量和部署后先生成新的logical plan；不要直接修改已有bindings文件。source→目标域去重与域内转发方式见[分层逻辑示例](../hybrid_logical/README.md)。

## provider怎样改

`devcomm`表示一个持续存在的设备通信器。同一输入中的所有调用共享它，不按层重建资源。`call_contexts`是本次调用可用的context索引数量，`requested_contexts`是通信器请求分配数量；`connections`为provider连接数。示例中请求3个context、2个connection，分配向上对齐为4；notify用索引0，2个SM的数据分别用1和2。

context index先按connection数拆成connection及connection内context，再绑定 `local_devices[rank]` 中对应设备。设备编号是声明ID，不是从本机发现的网卡或自动选路。示例中的10、11等不表示已具备这些物理设备。

`gin_connection_stride`必须填写实际GIN connection使用的rank步长，支持1或每台服务器GPU数。它不是请求rail连接时就一定得到的值：NCCL的底层comm可能是full连接，也可能已经按rail连接。world rank3在stride1时对应provider peer3，在stride2时为peer1；输出同时保留world、rail与provider三个编号。

`nic_handler`必须显式为 `gpu_sm_db` 或 `gpu_sm_no_dbr`。两者此处具有相同的逻辑前缀规则，物理门铃次数/成本没有建模。AUTO、CPU proxy、free-flow和其他provider拒绝，不做静默替代。`local_devices`每rank为1～4个不重复整数，connection多于设备时按序循环使用。

逻辑入口保留SM1的抽象示例；该绑定入口按固定host要求拒绝多域Hybrid的SM1。声明SM/warp没有校验具体GPU的共享内存容量，也不证明真实host会选择这一形状。

## 输出怎么看

`hybrid-bound`包含四个文件，可整体移动：

| 文件 | 内容 |
|---|---|
| `input.json` | 已准入的原始Hybrid逻辑输入 |
| `provider.json` | 原始资源声明 |
| `bindings.json` | 持久资源、每项请求绑定、计数顺序及逐peer数据/tail顺序 |
| `manifest.json` | 三输入/结果文件的SHA256及版本、范围声明 |

示例生成111项绑定、16个活跃资源身份。它们包含32个计数块、15份按目标服务器去重的token数据和64次finish；空通道也有finish。数量不是报文、物理门铃、吞吐或优化收益。

`resources`的身份包括devcomm、源rank、provider connection、connection内context、本地设备及provider peer。`bindings[].resource`相同表示共享该资源，**不表示已经同时发生拥塞**。同资源会出现在不同通道和两个call中，不能按输出数组位置推断其预约顺序。

`transport=gin`中，rank-count是聚合put，expert-count是普通put；同rank计数仍保留GIN self。远端token put是聚合请求，远端tail/finish是非聚合signal add。本地token是 `local_copy`，本地tail/finish是 `local_release_atomic`，后两者没有QP，不生成假RDMA包。`add_value`保留tail增量及高32位finish标志。

`data_streams`保存每源/目标/channel的本地API先后，不代表远端完成顺序，也不包含跨peer warp同步的完整DAG；`count_sequences`只表示rank-count先于expert-count的API调用。数据提前发布、目标可见和允许转发仍是不同事件。输出没有预先确定多个通道的WQE ticket或门铃交错。

公开读取接口会同时核hash和从保存输入重算语义，改了结果再重算hash仍不能绕过检查：

```python
from moe_ub_cosim.hybrid_deepep_bindings import load_bindings
bound, logical_input, provider_input = load_bindings('/tmp/hybrid-bound')
```

这个入口没有执行网络、GPU或完整Hybrid，Combine通道、notify/barrier的完整调度、最终slot、源数据寿命及provider实际时间成本仍需补齐。不能把bindings直接喂给现有direct/native桥，也不能据此声称现代千卡推理已完成。源码边界和平台验收见[规范](../../docs/tasks/hybrid_binding_spec.md)及[验收记录](../../fixtures/hybrid_bindings/README.md)。
