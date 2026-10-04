# M10 目标参考实验就绪核对

2026-10-03，最初核对基线 `14c1c8f`；其后已完成 MLA/量化容量实现及小型实际接纳验证。下文区分原独立算式与当前实现状态。M10 整体未完成，场景规格仍不是可执行配置。

MLA/量化容量核算已进入现有唯一 KV 池和请求接纳流程，合同与验收见[容量说明](../fixtures/m10_mla_capacity/README.md)。[完整 Decode 参考计算图](../fixtures/m10_full_model/README.md)已通过小场景实际联合执行，含 dense/shared/首尾阶段；另验证 61 块小 EP 合成结构。[图与观测资源管理](../fixtures/m10_resources/README.md)现已加入显式预算、构图前计数和逐事件文件记录，小场景跨越旧交付/窗口限额。[二级多平面 fabric](../fixtures/m10_fabric/README.md)已完成目标全拓扑离线生成及七平面小网络真实执行。[主场景真实请求清单](../fixtures/m10_route_selection/README.md)已核验 1024 个独立文件。[宿主资源预检](../fixtures/m10_host_preflight/README.md)已实际测量全量导入与目标首层，发现全出口重复账本下界超本机容量；后续[共享来源修改](../fixtures/m10_shared_provenance/README.md)已消除该端口倍增项，并通过新旧后端小场景对照。完整图、后端/网络峰值及最终组合执行仍未验收，原生 LD/ST 仍非当前参考实验前置条件。

## 当前事务选择

场景规格 ns3_backend.transaction_profile_for_bringup 已选择 URMA_WRITE/RTP，native_ldst_reference_parameters.enabled=false，XCCL 也未启用。继续使用该明确参考选择，不修改技术规格来宣称原生 MTE/LDST 等价。

| 传输角色 | 发起者与数据方向 | 就绪、完成与依据 |
|---|---|---|
| Dispatch 专家向量 | Token 所属 rank → 专家 rank | 目标 RemoteWriteCommitted 达到实际载荷总量后满足 RECV；源 transaction_done 完成 SEND、释放发送槽。 |
| Dispatch 计数状态 | 每源 rank → 每专家所在 rank，包括零贡献 | 同一参考 WRITE/RTP，计为应用元数据；依赖本源数据发送完成。 |
| Combine 结果向量 | 专家 rank → Token 所属 rank | 同一参考 WRITE/RTP；返回方向依据业务结果，不能沿用 Dispatch 方向。 |
| Combine 完成标志 | 专家 rank → Token 所属 rank | 同一参考 WRITE/RTP，计为应用元数据，发布依赖该结果数据完成。 |

依据为固定 CANN 源码与 [M10 模板](../fixtures/m10_cann/README.md)，以及 backend/ub_write_transfer.cc:58 起的实际提交/回调。接收端不提交第二份事务。WRITE/RTP 是本平台参考映射；AIV/MTE 的指令方向没有据此改名为 URMA READ/WRITE。若未来改变实验目标选择 READ、CTP 或 native LD/ST，再单独实现其请求/数据方向、可消费完成、资源回收和观测，现有 RemoteWriteCommitted 不能代替其他模式的事件证明。

## 已固定的容量参考合同

[reference.json](../fixtures/m10_target_contract/reference.json) 是离线参考合同，显式 `runtime_configuration=false`、`formal_run_ready=false`。它补充当前实验的参考选择，不改写场景规格，也不是已加载的权重或 NPU 量化配置。

结构来自已核验的 [R1 配置固定版本](https://huggingface.co/deepseek-ai/DeepSeek-R1/blob/56d4cbbb4d29f4355bab4b9a39ccb717a14ad5ad/config.json)。本步另固定 [DeepSeek-V3 官方 inference/model.py](https://github.com/deepseek-ai/DeepSeek-V3/blob/9b4e9788e4a3a731f7567338ed15d3ec549ce03b/inference/model.py)及 LICENSE-CODE 的摘要，只读取张量形状和 absorbed MLA 缓存布局；没有运行该源码。它的原生 TP/EP 绑定方式、FP8 内核及软件版本不自动成为 R1/CANN/NPU 的实现依据。

MLA 的参考缓存保留归一化后的 512 维 latent 和共享的 64 维 RoPE key，二者均选 BF16，未量化、未添加额外缓存填充。官方源码 MLA:425–444 定义投影与两个缓存，forward:481–495 使用压缩缓存计算；不按 128 个头复制缓存。对应整个 61 层模型：

```text
KV/token = 61 × (512 + 64) × 2 = 70,272 B
KV/block = 16 × 70,272 = 1,124,352 B
window64 每请求预留 = ceil((实际 prompt P + 64) / 16) × 1,124,352 B
```

EP 只切分 routed 专家，不切分本 rank 请求的 KV。RoPE 表、临时展开/反量化矩阵及运行时元数据属于单独工作区预算，不计入上述 KV 字节，也不声称已测得这些工作区。

权重参考选择为 MLA 五个投影、dense/routed/shared gated FFN 使用对称 INT8 矩阵、每输出通道一个 FP32 scale、零点与额外存储填充为零；embedding/lm_head/norm 使用 BF16，router 及路由修正向量使用 FP32。INT8 激活临时量归工作区。此方案只是可审计的存储算式，不是原始 FP8 checkpoint、采集 AWQ4 checkpoint 或已验证 W8A8 内核。TP=PP=1，非专家权重和每层一个 shared expert 在各 rank 复制，256 个 routed 专家按每 rank 两个放置，排除 MTP。

设 H=7168、Q=1536、C=512、N=128、Dn=128、Dr=64、Dv=128，五个 MLA 投影形状（输出×输入）分别为 Q×H、N(Dn+Dr)×Q、(C+Dr)×H、N(Dn+Dv)×C、H×NDv。每层还计入两个 H 维 block norm、Q/C 维 LoRA norm。FFN 为两个 F×H 和一个 H×F 矩阵；router 为 256×H，另有 256 维修正向量。三层 dense F=18432、58 层 MoE F=2048、embedding 和独立 lm_head 均为 129280×H。

| 独立核算项 | 每 rank 或注明范围的结果 |
|---|---:|
| 完整模型唯一参数，含路由修正、排除 MTP | 671,026,419,200 |
| 当前放置的驻留参数数目 | 22,226,310,656 |
| 参考权重张量字节 | 24,400,011,264 B |
| INT8 每输出通道 FP32 scale | 24,624,384 B |
| 权重及 scale 合计 | 24,424,635,648 B |
| CANN 每层数据窗口下界，batch 上限 8 | 62,652,416 B |
| 选定每层数据/状态/staging | 64 MiB / 1 MiB / 28,928 B |
| 58 层通信窗口保守求和 | 3,954,809,344 B |
| 参考 kernel/runtime reserve | 4 GiB |
| 条件预算 64,000,000,000 B 扣除以上后的 KV 空间 | 31,325,587,712 B，即 27,861 个完整块 |

64 GB 是本次明确选定的条件预算，不是实测可分配 HBM。按该预算，八个相同长度请求、每个观察 64 步时，按块对齐允许的 prompt 上界为 55,648；16 个驻留请求各预留 128 步时为 27,728。混合 prompt 必须逐请求取整再相加，不能用该均长上界替代实际接纳，也不能将这些数值称为目标部署容量极限。实际内核可能需要额外权重副本、量化布局或更大临时空间，必须显式计入后重算。

## 实际缺项与证据

| 对象 | 当前证据 | 影响和处理边界 |
|---|---|---|
| 模型/KV 容量 | model/1 保留 GQA；新增 mla_capacity.py 的 model/2，独立逐 tensor/scale 与 latent+RoPE 算式 | 完整 R1 离线库存与原审计逐项匹配；小模型实际影响唯一 KV 池和接纳。容量通过不代表完整 R1 图或实测 HBM 已通过。 |
| 全模型计算 | 新 full_model.py/full_model_plan.cc 按 model/2 和显式成本完整组图；旧层段入口含义保留 | 小模型及 61 块小 EP 合成图已真实执行，shared/routed 汇合、上下文成本和首尾阶段通过。完整 R1/128 rank 仍未运行；阶段成本不是数值内核或硬件校准。 |
| 计算依据 | CANN 模板明确拒绝未覆盖阶段的 operator_profile；M9 A100 表为 H=F=256、FP16 GQA/FFN | 不能把该表用于 H=7168、MLA 或 INT8。可另选显式参考成本及敏感性，但不能称为 NPU 校准。 |
| 状态消息图规模 | 旧输入保留 4096 次交付限额；完整服务/CANN 图可显式声明每图及累计运行预算，Python/C++ 独立检查数量和文件字节 | 实际每轮 4716 次交付、连续两轮通过。128×(256−2)=32,512 条状态/层，其中同 chip 256、跨 chip 32,256；58 层为 1,885,696 条。计数下界仍超出小场景预算，未生成/执行目标完整图；图/传输对象未回收，数量和文件上限不是 RSS 预测。 |
| 网络 | fabric/1 生成完整 768 端点、336 L1、112 L2、7 平面，保留 640 非活动端点；scenario-run/2 与 C++ 核对物理归属、候选路由及实际 TP | 目标全拓扑离线通过；47 节点七平面真实执行，两个 L2 候选、同服务器/跨服务器路径、队列及后续迭代反馈通过。完整目标网络尚未与完整 R1/真实负载组合执行；单 TP 选平面和 HASH 选交换出口是不同步骤。 |
| 观测 | 旧模式仍限 16 端口及每端口 4096 窗口；新增 set/2 预算、事件 JSONL 落盘、稀疏原始窗口及逐窗口公共导出 | 实际两个端口各 6001 窗口、1699 个驻留非零窗口；新旧记录/来源开关的执行与包指纹相同。公共合同另验证 17 端口/85000 零窗口；后续 fabric 任务实际观测全部 168 个小网络交换机出口，来源开关的执行、逐包及队列记录一致。当前观测描述符/包来源已按运行共享，逐端口窗口和输出仍独立；目标端口/时域尚需统一测量。 |
| 真实路由数量 | 固定修订 MMLU 目录 13922 文件，seed=0 前 1145 份逐文件核验后选出 1024 份；排除 30 份末尾缺层、91 份短轨迹 | 主场景每 rank 八请求、每请求 64 步的清单已就绪，共 3801088 条选中层记录；真实小场景验证消费与 UB 反馈。原始内容合计 2237609796 B，在本轮授权内；其余目录内容和 2048 请求 refill 未核验。执行导入器仍同时保留原始对象，完整目标资源不能由逐文件选择的低 RSS 推断。 |
| 校准 | 目标 NPU/profile、实际可分配 HBM、链路绝对时序未测 | 参考实验在假设完整且功能正确后可开展；这些不是拒绝实现 MLA 或保持明确参考 WRITE 的理由。 |

2026-10-04 宿主预检补充：完整路由导入实际采样 RSS 峰值 6570782720 B；128 rank/1024 Token 的首个 MoE 层离线构图/导出峰值 545468416 B、137419998 B 计划、2391386 条依赖边。完整 64 轮按实际路由核算 241124521 次交付、489968326 节点，但未分配或执行完整图。全目标交换机出口为 16128 个；预检版本每端口两份全量描述符表，仅第一轮索引键就至少 482777358336 B，超过 64 GiB 本机限额。小网络四组对照证实连零流量端口也持有所有传输和包来源；当时据此提出共享来源账本，不能只提高预算。该下界不是实测 OOM，采样 RSS 不是 HBM，单层测量不是完整目标运行证明。

## 实际检查与停止

离线命令如下；只读本地固定输入，不联网或执行外部源码，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python scripts/audit_target_contract.py \
  --source-dir runs/v03-target-contract/source \
  --output-dir runs/target-contract-audit
```

--source-dir 下需要固定版本的 inference/model.py 和 LICENSE-CODE，URL/摘要在 reference.json；结构配置和轨迹沿用本机已核验的 M9 文件。检查输出明确 audit_only=true、formal_run_ready=false，成功退出只表示核对完成。实际结果在 runs/v03-target-contract/audit/audit.json；七项拒绝、每项权重形状和字节、输入摘要均保留。其中 MLA/INT8 两项始终针对旧 model/1，不能解读为新 model/2 也不接受；dense/shared 拒绝仍针对旧层段入口，不能套用到新增 configured-service/4；三项图/观测拒绝针对未声明新资源预算的旧输入，不代表新的预算入口仍固定为相同上限。

原合同审计曾复用 build/m10-cann-behavior 执行 CANN 小型服务，为 305 次真实写、120316 B 总载荷、131530 ns。后续容量任务复用同一后端执行七项 MLA 对照，基准数值相同，容量收紧、投影存储精度、驻留未选请求、观察退出及网络延迟均通过；102 项单元测试和五套受影响回归通过。完整 R1 仅作离线审计，未运行目标场景或声称 MLA/INT8 算子校准。命令、失败记录和产物保护结果见 [environment.md](environment.md)。上述为容量任务的历史验收。后续完整参考图任务在独立 build/m10-full-model 中通过四块服务和 61 块小 EP 实际执行；资源任务在独立 build/m10-resources 中完成上述预算/流式记录小场景验收，后续 fabric 任务在独立 build/m10-fabric 完成目标拓扑离线生成与七平面小网络执行。

2026-10-04 的路由准备任务已补足主场景真实文件，独立核对顺序、全部层/Top-K、实际 prompt、窗口和每 rank 八请求；沿用条件 MLA 预算，逐请求块对齐后的 KV 为每 rank 46098432–89948160 B。该值不是实际接纳或 HBM 测量。两份新选源文件的小片段在同一 UB 网络完成两轮、84 次写、344064 B；网络减速使下一轮提交后移，来源开关不扰动结果。完整 R1/128 rank 尚未运行，AWQ4 来源与 INT8 参考精度差异、采集权重/runtime 修订未知和未校准状态保留。随后宿主预检完成上述实测与历史账本下界核对。

共享来源修改补充：10 出口真实小场景的观测描述符从 48000 条减为一份 2400 条，包来源从 24000 条减为一份 2400 条。端口逻辑可见数量及逐端口输出不变；单次源包创建只登记一次，追加图不复制旧表，端口释放和晚到回调已检查。全部原预检计数/下界仍是对应版本的历史证据，不能再套用旧端口复制公式估算新后端。下一项候选是有上限地测量完整一轮图的构造、导出与后端装载，按实际结果决定后续优化；完整网络、多轮保留及最终目标执行仍待验证。当前步骤完成后停止，下一项由用户确认。
