# DCQCN 报告 PPT 设计规格

## 背景

基于 `docs/export.zip` 中的 `export/dcqcn-pfc-joint-tuning-report.md` 和 `_figures/` 下现有图片，制作一份面向技术评审 / 研发听众的 DCQCN 仿真报告 PPT。内容直接取自报告正文和图，不重新绘制实验图，不重新解释为新的实验结论。

## 目标

- 用约 9 页 PPT 概括 DCQCN / PFC 机制、OpenUSim 实现映射、40G baseline、400G 参数调优和 incast scale-out 结果。
- 每页以图为主，文字控制在 1-2 个技术要点。
- 语气保持专业、中性，避免宣传式表述和未在报告中支撑的强结论。
- PPT 可用于内部技术评审，便于听众围绕机制、参数和结果追问。

## 非目标

- 不重新运行仿真。
- 不重新绘制报告中的实验曲线图。
- 不新增未在报告中出现的实验数据。
- 不把报告扩写成长篇讲稿。

## 输入来源

- 压缩包：`docs/export.zip`
- 报告正文：`export/dcqcn-pfc-joint-tuning-report.md`
- 图目录：`export/_figures/`

## 产物

PPT 文件建议输出到 `docs/dcqcn-pfc-joint-tuning-report.pptx`。生成过程中如需临时解压，应放到临时目录或受控构建目录，避免污染报告源目录。

## 页面结构

### 1. DCQCN 400G 仿真评估概览

主图：`12_incast400_summary_throughput.png`

要点：

- OpenUSim 支持 DCQCN 闭环行为复现。
- 400G 场景下需重新审视反馈、恢复与标记参数。

### 2. DCQCN 控制闭环与 PFC 协同边界

主图：PPT 内置示意图，表达 CP / NP / RP 闭环和 PFC 本地保护边界。

要点：

- DCQCN 负责端到端速率调节。
- PFC 定位为本地队列保护机制，避免队列极端冲高。

### 3. OpenUSim 中的 DCQCN / PFC 实现映射

主图：PPT 内置实现映射示意图，包含 Switch CP、Receiver NP、Sender RP、PFC pause/resume 路径。

要点：

- OpenUSim 按 DCQCN 论文角色建模 CP / NP / RP。
- PFC 阈值与 pause/resume 行为作为链路级保护机制实现。

### 4. 40G baseline：RED 与 cut-off 打标对比

主图：

- `01_paper40_cutoff_dual_axis.png`
- `02_paper40_red_dual_axis.png`

要点：

- cut-off 在阈值后集中打标。
- RED 在较低队列水位开始渐进概率打标。

### 5. 400G 场景下论文默认参数表现

主图：`03_incast400_paper_default_sender_rate.png`

要点：

- 默认参数下恢复偏慢，瓶颈链路利用率不足。
- 主要约束来自反馈时延与恢复节奏失配。

### 6. 反馈周期收紧后的收益

主图：`04_incast400_t10us_paper_default_dual_axis.png`

要点：

- 将反馈闭环缩短至 `10us` 后，吞吐显著提升。
- DCQCN 更早介入，PFC 触发压力下降。

### 7. BCT 配置对恢复过程的影响

主图：

- `05_incast400_uneven_bct10m_recovery_dual_axis.png`
- `06_incast400_uneven_bct4m_recovery_dual_axis.png`
- `07_incast400_uneven_bct1m_recovery_dual_axis.png`

要点：

- 较小 BCT 可加快恢复，但会提高速率波动。
- `4MB` 在恢复速度与稳定性之间更均衡。

### 8. ECN 标记强度的折中关系

主图：

- `08_incast400_k64k_256k_pmax01_dual_axis.png`
- `09_incast400_pmax04_dual_axis.png`

要点：

- 弱标记降低有效吞吐。
- 强标记提升响应强度，但可能放大短窗口内的速率抖动。

### 9. Incast 规模扩展下的收敛表现

主图：

- `14_incast400_4to1_tuned_dual_axis.png`
- `15_incast400_8to1_tuned_dual_axis.png`

辅助图可选：`13_incast400_summary_fairness.png`

要点：

- 4->1 与 8->1 场景仍可收敛。
- 规模越大，PFC 的保护作用越明显。

## 版式要求

- 16:9 宽屏。
- 图占每页主要面积，标题和要点保持克制。
- 每页最多 2 个要点，避免把报告正文搬到 PPT。
- 对比页优先使用并排布局；三图页使用等宽小图布局。
- 示意图页使用简洁方框和箭头，突出机制路径，不加入无关装饰。

## 数据与表述约束

- 所有定量数字只使用报告正文已有数字。
- 标题保持专业、中性，避免将“更优”“最佳”等未严格限定场景的词写入标题。
- 机制页可用示意图表达 OpenUSim 实现映射，但结论必须限定为“按论文角色建模”和“链路级保护机制实现”。

## 验证

- 解压 `docs/export.zip` 后检查所有引用图片存在。
- 生成 PPT 后检查页数为 9 页。
- 检查每页不超过 2 个要点。
- 检查所有图片正常嵌入。
- 检查标题与本规格一致。

