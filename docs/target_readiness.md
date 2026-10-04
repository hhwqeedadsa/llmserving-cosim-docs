# M10 目标参考实验就绪核对

2026-10-03，最初核对基线 `14c1c8f`；其后已完成 MLA/量化容量实现及小型实际接纳验证。下文区分原独立算式与当前实现状态。M10 整体未完成，场景规格仍不是可执行配置。

MLA/量化容量核算已进入现有唯一 KV 池和请求接纳流程，合同与验收见[容量说明](../fixtures/m10_mla_capacity/README.md)。[完整 Decode 参考计算图](../fixtures/m10_full_model/README.md)已通过小场景实际联合执行，含 dense/shared/首尾阶段；另验证 61 块小 EP 合成结构。下一首要缺项是**目标规模图管理与观测**；目标拓扑和足够真实路由仍待完成，原生 LD/ST 仍非当前参考实验前置条件。

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
| 状态消息图规模 | 当前 MAX_TRANSFERS=4096，Python/C++ 单层及多层均检查 | 128×(256−2)=32,512 条非同 rank Dispatch 状态；其中同 chip 256、跨 chip 32,256，单层仅状态载荷就有 1,032,192 B 跨 chip。58 层为 1,885,696 条状态交付，尚未计向量/Combine。不能直接提高上限后宣称规模已通过。 |
| 网络 | 场景要求保留完整 fabric、7 平面和非活动端点；现有 CANN 验收是小型共享瓶颈 | 需生成与编号一致的节点、端口、路由、预建通道并验证实际路径和队列；多路径开关本身不产生多平面。 |
| 观测 | Python 及 C++ 限 16 端口、每端口 4096 窗口，原始包/来源账本保留在内存 | 本步实际触发 17 端口及 4097 窗口拒绝。1 μs 采样下超过 4.096 ms 的连续单段窗口会触限；先定目标端口/时域及资源预算，再验证输出/内存成本。当前不声称必然 OOM。 |
| 真实路由数量 | 固定 manifest 中仅两文件，prompt 为 26/37，各 128 个 Decode 步；14,848 条层记录再核对通过 | 两文件均满足 window64，但目标需 1024 个独立请求，补入负载需 2048。只审计该 manifest，未扫描完整数据集；不得重复文件凑数。扩大下载另需授权。 |
| 校准 | 目标 NPU/profile、实际可分配 HBM、链路绝对时序未测 | 参考实验在假设完整且功能正确后可开展；这些不是拒绝实现 MLA 或保持明确参考 WRITE 的理由。 |

## 实际检查与停止

离线命令如下；只读本地固定输入，不联网或执行外部源码，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python scripts/audit_target_contract.py \
  --source-dir runs/v03-target-contract/source \
  --output-dir runs/target-contract-audit
```

--source-dir 下需要固定版本的 inference/model.py 和 LICENSE-CODE，URL/摘要在 reference.json；结构配置和轨迹沿用本机已核验的 M9 文件。检查输出明确 audit_only=true、formal_run_ready=false，成功退出只表示核对完成。实际结果在 runs/v03-target-contract/audit/audit.json；七项拒绝、每项权重形状和字节、输入摘要均保留。其中 MLA/INT8 两项始终针对旧 model/1，不能解读为新 model/2 也不接受；dense/shared 拒绝仍针对旧层段入口，不能套用到新增 configured-service/4；三项图/观测资源限制仍在。

原合同审计曾复用 build/m10-cann-behavior 执行 CANN 小型服务，为 305 次真实写、120316 B 总载荷、131530 ns。后续容量任务复用同一后端执行七项 MLA 对照，基准数值相同，容量收紧、投影存储精度、驻留未选请求、观察退出及网络延迟均通过；102 项单元测试和五套受影响回归通过。完整 R1 仅作离线审计，未运行目标场景或声称 MLA/INT8 算子校准。命令、失败记录和产物保护结果见 [environment.md](environment.md)。上述为容量任务的历史验收。后续完整参考图任务在独立 build/m10-full-model 中通过四块服务和 61 块小 EP 实际执行；当前停在该任务完成，不自动开始目标规模任务。
