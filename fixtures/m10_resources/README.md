# 图与观测预算的测试输入

本目录保存首次显式限制图规模和观测输出的功能输入。它在四块小型 Decode 模型上核对预算检查、逐事件写出、逐窗口导出和旧结果一致性。当前预算字段、压缩记录和读取规则见[记录与存储](../../docs/record_storage.md)，执行依赖见[后端说明](../../backend/README.md)。

## 两份预算

| 文件 | 约束范围 |
|---|---|
| [graph-budget.json](graph-budget.json) | `moe-ub-graph-resources/1`：每图和全运行的交付/节点数，以及每份 plan.json 的 UTF-8 字节数。 |
| [observation-budget.json](observation-budget.json) | 观测端口及窗口数、每端口事件字节、来源记录数量、导出字节。 |

图预算放在 configured-service/4 的 `execution.resource_budget`，同一声明绑定全部组和层。只在 layers 写预算而 execution 缺失会被拒绝；单图上限不能高于累计上限，交付累计还须满足 comm_tag 编号空间。`resource_usage`、graph-resources.json 和后端结果相互核对。超预算图不激活，已合法完成的旧图保留为执行事实。

这些计数上限不等于 RSS 限额：计划大小不包含解析后的对象、全部依赖索引和日志。宿主进程与累计磁盘另外按当前用户配置限制，不能通过放大图预算解决内存重复。

观测预算仍使用相同的整数本地时钟、半开区间、末窗口和整包分类。关闭来源输出后，业务/元数据分类仍需内部映射。该阶段采用每端口来源副本和显式零窗口输出；后续[共享来源](../m10_shared_provenance/README.md)与[资源工程](../m10_resource_efficiency/README.md)已改变持有和存储方式。历史实现的“按端口累积且不回收”不代表当前规则。

## 运行与手算预期

脚本从已提交的四块小模型派生输入，无需下载数据或运行数值内核。从仓库根目录使用已安装环境和兼容构建，输出目录必须不存在：

```bash
PYTHONPATH=python python tests/integration/check_graph_observation_resources.py \
  --build-dir build/m10-resources --output-dir runs/resource-example
```

每轮 4,716 次交付，其中 4,704 次网络写、12 次本地交付；共 9,495 节点，超过旧样例交付上限。两轮在同一网络中完成 36,864 B 业务，结束于 1,698,528 ns。旧/新记录方式、来源开关、供关联方法读取的文件、预算拒绝及状态消息计数均核对。

两个端口各声明 6,001 个原始窗口，其中各 1,699 个有流量；经本地时钟导出共 12,002 窗口，8,604 个为零。另有 17 端口、85,000 个零窗口的文件格式检查，该项没有实际执行 17 端口网络。

目标计数检查使用 128 rank、每 rank 两专家、双 rank/chip：仅 Dispatch 状态就有 32,512 次交付/层、58 层共 1,885,696 次，明确拒绝使用本目录小场景预算。这里未执行完整目标，最终目标结果见[首轮验收](../m10_research_slice/README.md)。

## 证据

历史 commands.json、各命令 *.time.json 和 summary.json 保存时间、输出量、退出状态及检查结果。旧测量使用独立计时进程的 `RUSAGE_CHILDREN.ru_maxrss`，表示等待子进程链中的最大单进程 RSS，不能解释为进程树总峰值或 NPU 用量。

完整字段表、运行命令及旧存储说明可查[原记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_resources/README.md)。本目录 JSON 保留原检查输入；修改实验规模时，按当前配置说明另建输入。
