# 2026-10-15 参数化千卡电交换拓扑

问题：将用户两张图的多Pod、Agg、rail、双plane和普通交换Core变为可修改的物理输入，而非固定千卡示意图。范围是静态网络生成，不执行UB或原生RDMA业务，不复现完整HPN协议。

平台提交 `a879128` 新增公共 `scaleout topology`、严格配置/预算检查、GPU/NIC映射、交换机域可达性审计及ASTRA/SimAI文本导出；[五套示例](../../examples/scaleout_topology/README.md)覆盖八卡小图与四套千卡结构。[依据与参数边界](../reviews/scaleout_topology_20261015.md)区分rail、plane、Pod/Segment，并记录HPN全文访问失败。

验收独立保存在 [experiments/scaleout_topology](../../experiments/scaleout_topology/README.md)，通过公共CLI运行，平台不导入实验或测试。23项相关检查通过；5套各两遍生成的全部文件逐字节一致，版本、SHA-256、资源与原生装载未执行原因见[摘要](../../experiments/scaleout_topology/results.json)。完整命令、状态和日志位于 `runs/scaleout_topology_validation_20261015_final/validation.json` 及各子目录。

结构发现：独立8rail、每GPU单rail NIC时，跨rail的458752个无序GPU对没有直接分组路径；多NIC端点也不能被自动当作GPU转发桥。HPN参考结构共享上层可跨rail，但有ToR 2:1、Agg 4:1的下上行超售，因此不能与端口/带宽不同的Clos直接归因比较性能。Core验证需跨Pod业务。

本轮没有千卡逐包结果。原生装载因磁盘低于8GiB预留而未运行，没有扩大预算或清理未知数据。后续应先核对小图实际设备端口/带宽/延迟与跨Pod路径，再处理RDMA完成语义、选plane策略及共享网络All-to-All；原有非UB在线后端和原始路由统计仍保持各自pending。代码、配置与结果仅本地提交，不push。
