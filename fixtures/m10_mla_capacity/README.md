# MLA 容量核算的测试输入与结果

本目录检查 `moe-ub-model/2` 的 MLA 权重存储和压缩 KV 字节核算，并用小型服务验证核算结果进入原生 Scheduler 的同一个 KV 块池。当前公式、支持的存储格式和模型限制见[模型与验证范围](../../docs/model_validation.md)。容量核算不创建数值算子或证明硬件能够部署。

## 输入的分工

| 文件 | 用途 |
|---|---|
| [r1-capacity.json](r1-capacity.json) | 原始 R1 条件容量输入，独立离线核算，保留当时通信预算。 |
| [toy-capacity.json](toy-capacity.json) | 可手算的小模型，用于逐项权重、scale 和 KV 常数对照。 |
| [service.json](service.json) | 合成 MLA 模型，执行既有 CANN 三层 MoE 片段，验证容量与接纳、等待、释放。 |
| [evidence.json](evidence.json)、[manifest.json](manifest.json)、`routes/` | 人工专家序列和显式观察终点；标记 synthetic_test，不是真实采集。 |

`r1-capacity.json` 采用 61 块、128 rank，每 MoE 层每 rank 两个 routed expert、一个复制的 shared expert。矩阵为 INT8/FP32 scale，embedding/head/norm 为 BF16，router/修正为 FP32；这些是参考存储选择。原始 FP8 模型、AWQ4 路由来源和此处 INT8 容量分别记录。该文件不能直接运行服务。

## R1 条件容量

| 每 rank 项目 | 数值 |
|---|---:|
| 权重张量 | 24,400,011,264 B |
| scale | 24,624,384 B |
| 总权重 | 24,424,635,648 B |
| BF16 latent+RoPE KV/token | 70,272 B |
| 每个 16 Token 块 | 1,124,352 B |
| 原通信预算 | 3,954,809,344 B |
| 原条件完整 KV 块数 | 27,861 |

原容量为每 rank 64,000,000,000 B，另含 4 GiB workspace。完整服务已应用[通信预留修订](../m10_resource_efficiency/communication-reserve.json)：通信预留增至 10,217,324,544 B，剩余 KV 为 25,063,072,512 B，可容纳 22,291 块。原 r1-capacity.json 仍用于核对旧独立算式，不能把其输出当作修订后的运行预算。

## 独立预期与服务检查

toy-capacity 使用 H=8、V=16、N=2、Q=4、C=2、Dn=Dr=Dv=2，两层分别为 dense F=6 与 MoE F=4，三个 routed expert 按 [2,7,7] 放置，shared F=2、每 rank 一个。独立计数中投影与 norm 每层 316 B，dense FFN 224 B，每 routed expert 160 B，shared 96 B，router/修正 108 B，embedding/head/final norm 528 B。两 rank 结果为 1,748/1,908 B，其中 scale 448/512 B；KV/token 为 16 B，四 Token 块为 64 B。浮点 KV、绑定头和非法输入另有常数预期。

小型 service 每 rank 权重 10,408,520 B、KV/token 384 B、块 1,536 B，workspace 4,096 B、三层通信预算 6,304,512 B。默认八块；收紧为三块后 A/B 不能同时驻留，释放 A 后才接纳 B。两请求各需两块，不能合并成三块。固定设备预算，只将 MLA 投影存储从 BF16 改为 INT8+scale，可用块由 3 变为 1,849，首轮实际从 A 变为 A/B；参考计算耗时不变。

七组网络对照验证事务/节点完成、72 份专家贡献、容量回收，以及未选入本轮的驻留请求仍占 KV。基准为 305 次写、113,664 B 业务、131,530 ns；三块预算变为 377 次写、143,142 ns，额外轮次产生额外计数消息，业务量不变。1 Gbit/s 对照为 1,214,814 ns。观察输入结束时按观察终点释放，未推断 EOS。

102 项单元检查、旧功能回归及七项启动前拒绝通过。此处 service 使用早期层段入口，dense 和 shared expert 只计库存；完整块执行另由[完整 Decode 图用例](../m10_full_model/README.md)验证。

## 复现入口

从仓库根目录使用已安装环境，文件输出和运行目录必须不存在：

```bash
mkdir -p runs
PYTHONPATH=python python -m moe_ub_cosim.capacity_audit \
  --input fixtures/m10_mla_capacity/r1-capacity.json --output runs/mla-r1-capacity.json

PYTHONPATH=python python tests/integration/check_mla_capacity.py \
  --build-dir build/m10-cann-behavior --output-dir runs/mla-capacity-check
```

容量命令只核算库存，输出 `execution_scope=capacity_only` 和 `communication_budget_basis=declared_not_graph_validated`；后者说明通信缓冲尚未按该次执行图检查。集成脚本需要兼容后端。实际命令、失败与结果见[整理前记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_mla_capacity/README.md)，完整目标当前范围见[CM384/R1 场景](../../docs/scenarios/cm384_r1.md)。
