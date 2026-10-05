# CANN 构图索引优化的验证记录

本目录记录一次保持图内容不变的构图优化。修改将 `cann.py` 中反复扫描贡献和传输的操作改为一次建立索引，减少生成图所需的宿主时间；消息顺序、字节、依赖和仿真时间保持。当前 CANN 行为说明见[模型与验证范围](../../docs/model_validation.md)。

## 修改与检查依据

状态计数按 `(src_rank, expert_id)` 累计；贡献和传输按阶段及源/目的 rank 保存有序引用；芯片和 rank 序号建立一次。索引在调用结束释放，不进入计划或跨轮缓存。Combine 排序、lane 内旋转、零计数发布、本地交付、数据就绪与发送完成依赖、分块和成本继续按原顺序生成；Python 和 C++ 独立校验均保留。

[golden.json](golden.json) 由修改前的 `f9b3751` 生成，固定旧源码、用例生成器及 24 组完整计划摘要，包含字段与列表顺序。用例覆盖 EP2/3/5、非连续 rank、多专家、不同批次、零 Token 成员、三类本地性、lane=1/2/3/48、非对齐 H、分块和一/两个发送槽。全组无 Token 仍按旧规则拒绝；空服务由服务层处理。初次基线误把全空图作为有效输入的失败记录保留。

## 测量结果

首个目标 MoE 层使用同一份已核验 bundle，旧代码从固定本地 Git 读取并核验摘要。两次都保留完整生产校验，完整计划与来源摘要相同。

| 指标 | 旧实现 | 索引实现 |
|---|---:|---:|
| 构图及原校验 | 34.851 s | 4.193 s |
| 交付，跨芯片/本地 | 64,556 / 436 | 相同 |
| 节点/依赖边 | 130,694 / 2,391,386 | 相同 |

这是一次对照，约 8.31 倍的构图速度差不代表所有输入的固定比例，也不包含摘要、读取和进程清理耗时。

随后完整首轮的 58 个 MoE 子图于 271.182 s 全部返回；首次全模型合并尚未返回，在 830.640 s 采样到 34,363,543,552 B，触及 32 GiB RSS 上限。监测器终止 worker，外层退出 1，无残留进程。停止原因是受控内存预算，未发生宿主 OOM，也未触及 900 s 时间上限。完整构图、导出和目标加载在这次测量中均未完成；后续结果见[全模型存储优化](../m10_full_model_storage/README.md)。

22 项单元检查、13 次 CANN 服务运行、非对齐向量执行及非法配置/计划拒绝通过；61 块小 EP、穿插 dense/无 shared expert、C++ 加载回归通过。三组历史小图共 15 个文件逐字节一致；依赖和旧产物摘要未变。

## 复现与证据

小型检查不需要真实路由：

```bash
PYTHONPATH=python:tests/unit python -m unittest \
  cann_index_test cann_plan_test full_model_test resource_budget_test host_preflight_test full_round_probe_test
```

`scripts/probe_cann_indexing.py baseline|indexed --output-dir ...` 使用固定旧提交和原首轮 bundle，须由 `moe_ub_cosim.host_probe` 包裹，限 300 s、RSS 8 GiB、地址空间 12 GiB、产物 1 GiB。完整轮复测沿用[前一步](../m10_full_round_preflight/README.md)的 900 s 及 32 GiB 上限。输出目录须不存在，缺少旧代码或数据时不替换输入。

[result.json](result.json) 保存代码/输入摘要、测量和停止位置；详细命令及失败日志位于 `runs/v03-cann-indexing/`。完整历史记录见[原说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_cann_indexing/README.md)，完整目标最终验收见[首轮交付记录](../m10_research_slice/README.md)。
