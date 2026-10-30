# 按目的卡去重与Combine表示的验收

该增量补齐固定DeepEP direct BF16的一部分传输表示，不是新算法。Dispatch按同一token的目的rank去重；Combine可逐专家回传，也可在本rank合并后回传，后者的成本显式声明。原专家工作、阶段依赖、持久传输和默认历史图保留。

## 来源

固定DeepEP提交为`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`。`verify.py`逐一核对[来源清单](../../docs/review_artifacts/invocation_semantics_20261023/sources.json)的19份缓存长度、SHA-256与Git blob身份，未下载或升级依赖。

| 固定源码 | 对应行为 |
|---|---|
| `impls/ep/dispatch.cuh:103–105, 333–386` | 目的rank计数/slot去重；每token每目的rank发送一次 |
| `impls/ep/combine.cuh:43, 127–175, 180–236` | expanded逐专家发送hidden；可选本rank归并后发送TokenLayout |
| `layout/ep/token.cuh:18–40` | BF16无SF时的metadata、hidden与最终对齐 |
| `common/compiled.cuh:67–68` | TMA 32B、RDMA 64B对齐 |

Python默认`allow_multiple_reduction=True`且`allow_hybrid_mode=True`；本配置明确限定direct，不把它称为生产默认hybrid。没有GIN计数/屏障包、真实CQ或GPU可见性、FP8/SF、多context与数值合并校准。字段和公式见[规范](../../docs/tasks/token_transport_spec.md)。

## 检查方法

新增7项CPU检查覆盖token/slot身份、原专家工作、BF16布局、两种Combine、显式放置、非法输入及9种图篡改；另检查38项受影响回归。小型CPU输入不执行网络。

`verify.py --prior-run ... --output ...`离线重建保存的默认实际图，逐字段比较；同时从15份真实原JSON独立计算三种表示的单位数、字节和专家工作，不导入历史实验驱动。它不执行这些真实输入的网络，也不根据字节比例推算时延或收益。

两个新功能条件使用[公开示例](../../examples/scaleout_token_transport/README.md)，四份不同人工原件、128端点Clos、两个EP64组、两层两步。两模式各只执行一次，通过公开`rdma-run/rdma-check`；独立`verify.py --native ... --output ...`核对原JSON、实际提交字节、收发守恒、原生归并节点时长与后继开始、实际门限成员/释放。此输入刻意共置专家，不能代表真实R1热点概率或生产时延。

根目录`runs/token_transport_20261030`累计预算900受监控秒/512MiB，卷保留8GiB。每native60秒、RSS1GiB、地址2GiB、模拟5ms、产物128MiB、结果32MiB、完整包200000条；零构建/下载/安装/GPU/依赖修改。失败保留，不扩限或重跑成功网络。实际结果及资源收据在完成核验后单独保存。
