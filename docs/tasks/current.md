# 当前任务与交接

状态：`completed`。2026-10-16，按用户要求将时间趋势报告整理为[离线 HTML](../review_artifacts/scaleout_runtime_20261016/report.html)，支持场景/版本筛选、指标切换、运行详情、矩阵成本估算和数据下载，保留超时与同机重叠说明。已核对8例嵌入数据与源文件一致、无外部资源、Node交互和确定性生成；未进行浏览器截图。此前测量与归档保持原状，本次不启动新仿真。只本地修改/提交，不 push。

## 本轮结果与证据

| 项目 | 结果与入口 |
|---|---|
| 实际运行成本 | [8 例运行成本报告](../../experiments/scaleout_scaling/results/runtime_20261016/report.md)，6 例完成且校验通过、2 例千卡超时；保留 CSV/JSON、PNG/矢量 PDF 与可重绘证据 |
| 规模增长 | 原版 EP4 多副本：256/512 卡 Worker 171.66/345.93 秒；完整命令与独立校验分别另列。部分运行重叠，不作为受控复杂度或优化加速比 |
| 千卡终态 | 两版均到 600 秒墙钟限额；原版采样 RSS 5.68 GiB、新版 4.45 GiB，仅中止前峰值。无最终模拟完成时间，不记成千卡验收通过 |
| 模型与网络 | 完整 Qwen3-30B-A3B 48 层两轮 Decode，B=1、context=2048、声明计算/合成路由；同一非 UB ns-3 时钟内机内织构与跨 Pod RDMA |
| 平台扩展 | 提交 `b8b965c`，完整形状、显式预算、机内抽象与百卡因果执行；[验收证据](../../fixtures/scaleout_scaled/README.md) |
| 数值观测表示 | 提交 `3ca1622`；旧 8 卡、完整 16/128 卡的结果和 36/34/258 个图文件逐字节一致；[证据](../../fixtures/scaleout_numeric_observer/README.md) |
| 方法审阅 | [v0.4 十页 PDF](../review_artifacts/ScaleOut实验方法_审阅稿_v0.4_20261016.pdf)及[原文](../../experiments/scaleout_clos128/documents/method_review_v0.4.md)；文档整理不启动仿真 |

## 时间口径与复现

Worker 墙钟包括初始化、构图、后端执行和写出，不是纯 Simulator CPU 时间。公共命令墙钟还含准备与自动校验；独立检查命令单独记录，未采集处留空。模拟完成时间来自通过核验的结果；超时为右删失，不补最终时间或节点/消息总数。

报告按 EP=N 单副本、EP4 多副本及 service 二进制哈希分组。已核对 128/512 卡旧版重叠约197.62秒、新版128/1024卡重叠约158.79秒，未证明其他时段宿主独占。每点一次测量，不能宣称稳定统计或受控加速比。

[规模实验入口](../../experiments/scaleout_scaling/README.md)保留公开 CLI 运行、分析、时间采集与证据重绘。原阿里交换机内存探针、规模估算及训练热点反例完整移至[探针说明](../../experiments/scaleout_scaling/probes_and_hotspots.md)，未改变其原用途。[百卡实验](../../experiments/scaleout_clos128/README.md)保存完整模型结果和复现入口。

单例扩展预算为600秒、RSS6 GiB/地址空间12 GiB、模拟100 ms；专用目录累计3 GiB、卷保留8 GiB。后续数值观测运行沿用同一预算，保留两次失败，不自动重跑。分析不加载大型结果 JSON，实际来源哈希和全部状态保存在报告证据中。

## 检查与持久保存

时间 CSV/JSON、说明和 PNG/PDF 从保存证据重绘后逐字节一致；已独立核对8个原始来源、6次校验、2次删失和已知重叠。当前四个数值观测源码哈希与128卡执行前来源一致，13个锁定依赖检查通过。初始归档审计误以为旧包仅有campaign目录，实际还含同级监控/预检/分析；修正目录映射后逐成员比对，不改变源包或略过内容检查。

原始证据位于本地忽略目录：`runs/scaleout_clos128_20261016/evidence.tar.gz`、`runs/scaleout_scaling_20261016/campaign-01.tar.gz` 和 `numeric-observer-01.tar.gz`。两份规模归档共10705个成员逐文件hash与来源一致，[归档摘要](../../experiments/scaleout_scaling/results/runtime_20261016/archives.json)已随实验保存；不提交大型原始记录、构建对象或依赖源码。

## 边界与历史

真实 HF 原始路由仍按用户“先做别的”暂缓；没有 H100 实机校准、完整 NVLink/PCIe 模型、训练 DP 或生产 HPN 复现。当前千卡是多 EP4 副本共享有超售三层网络；不能外推千卡全局 All-to-All 或将扩规模本身当机制创新。旧 v0.3 方法稿中的128卡失败是滚动日志覆盖的历史状态，已通过完整节点日志修复，旧稿不覆盖改写。

上轮小规模服务实验见[历史快照](../iterations/20261015_scaleout_e2e_status.md)与[原结果](../../experiments/scaleout_e2e/conclusions.md)。本轮记录增长趋势完成后不自动进入新矩阵或算法开发。
