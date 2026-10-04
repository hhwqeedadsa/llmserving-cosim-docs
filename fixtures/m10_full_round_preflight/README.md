# M10 完整一轮图的受限资源预检

本步测量现有 Python 构图/导出与 C++ 计划、Chakra 装载路径的宿主开销，不启动目标网络仿真。输入保持 128 rank、每 rank 八请求、H=7168、256 专家、Top-8、3 dense + 58 MoE、8192 B 分块和一个发送槽。只取已固定 1024 份轨迹的首个 Decode 记录；原主场景仍为 64 步，不把离线首轮当作请求结束。

[input.json](input.json) 固定输入和成本模板摘要。准备阶段逐文件校验完整原始内容，再提取首轮 59392 条层路由；同源/同芯片/跨芯片贡献与已有 58 层独立计数逐项一致。首轮应有 3767694 次交付（3741764 次 fabric、25930 次本地）、7655238 个节点、10058044416 B fabric 业务数据。这些是构图前计数，不是实际完成的 UB 事务。

模型结构复用 `r1-capacity.json` 中的 `declared_reference/r1-int8-mla-capacity`，对应完整 R1 张量结构；路由来源身份仍记录为 `deepseek-ai/DeepSeek-R1`。费用将小型完整模型样例的正参考值逐层展开，例如 dispatch pack 每 Token 20 ns、routed expert 每输入 100 ns、attention 每 context Token 5 ns，仅用于保留应有节点和测量分配成本。它不是目标 NPU profile，也不是已经按模型身份适配完成的真实路由服务配置。来源 AWQ4 与参考 INT8 的精度差异、采集版本未知、`unvalidated_reference` 保持。

## 测量方法

`moe_ub_cosim.full_round_probe` 是离线工具。它调用原有 `build_full_model` 和 `export_plan`，完整保留各层校验、合并、重构校验、身份分配和导出校验。仅在粗粒度函数入口/返回处记录阶段、耗时与 RSS，单元检查确认加日志前后的图完全一致，异常后恢复函数。准备时逐文件释放原始 JSON 不会修改生产服务导入器；本次构图内存不包含全量 64 步导入器驻留对象。

`cosim-plan-load-probe` 只调用服务使用的 `LoadLayerPlan`，包含 C++ 重构校验与逐 rank Chakra 校验。它不创建 UB 网络，不装载 ASTRA 活动图，不注册传输或观测来源，也不推进仿真时间。后续完整网络初始化和运行持有成本须另外测量。

宿主 `host_probe` 对每个独立进程组采样并保存命令、退出状态、RSS 和输出字节。目标构造及导出在同一进程内顺序执行，共享 900 s 上限、采样 RSS 32 GiB、单进程地址空间 40 GiB、产物 24 GiB；图 JSON 另有 20 GiB 上限。若前一阶段未产生完整合格图，后一阶段不启动。触限只清理本探针创建的进程组，日志和部分产物保留，不提高预算重跑。

RSS 为约 0.1 s 间隔采样，可能遗漏瞬时峰值，进程组共享页可能重复计入；RLIMIT_AS 是地址空间限制，输出监测可能有采样超调。这些都不是模拟 HBM、硬件校准或完整目标可运行证明。

## 复现入口

从仓库根目录执行；使用已有 Python 环境、固定本地数据和依赖。所有输出目录必须不存在。

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.host_probe \
  --output-dir runs/full-round-prepare \
  --rss-bytes 4294967296 --address-bytes 6442450944 \
  --artifact-bytes 1073741824 --wall-seconds 300 -- \
  .venv/llmservingsim-native/bin/python -m moe_ub_cosim.full_round_probe prepare \
  --input fixtures/m10_full_round_preflight/input.json \
  --output-dir runs/full-round-prepare/work

PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.host_probe \
  --output-dir runs/full-round-target \
  --rss-bytes 34359738368 --address-bytes 42949672960 \
  --artifact-bytes 25769803776 --wall-seconds 900 -- \
  .venv/llmservingsim-native/bin/python -m moe_ub_cosim.full_round_probe target \
  --input runs/full-round-prepare/work/prepared.json \
  --output-dir runs/full-round-target/work
```

小型探针与拒绝检查：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_full_round_probe.py \
  --build-dir build/m10-full-round-preflight --output-dir runs/full-round-small-check
```

小型检查实际调用新构建的 C++ 探针。只有目标图导出成功并核对计数/文件后，才能把对应 `graph/` 传给同一个装载探针并受限运行；不能把小图装载成功写成目标图装载通过。

## 实测结果与停止位置

2026-10-04，生产基线 `e148169`；独立构建 `build/m10-full-round-preflight`。完整结果、输入/代码/二进制摘要见 [result.json](result.json)，原始命令与阶段采样在 `runs/v03-full-round-preflight`。

| 对象 | 实际结果 |
|---|---|
| 首轮来源准备 | 38.144 s，采样峰值 428302336 B；1024 个文件全部校验，派生 bundle 67825336 B，仅保存在忽略目录 |
| 完整目标构图尝试 | 900.469 s 后因 `wall_limit` 终止，worker 为 SIGTERM / -15，探测命令退出 1；无残留后代 |
| 已返回的层图 | 27/58 个 MoE 层，layer_id 3～29；1753718 次交付、3526882 节点，逐层与独立计数一致 |
| 停止位置 | 正在构造 layer_id=30；全模型合并尚未开始，3 个 dense 块和首尾阶段尚未组合为完整图 |
| 目标进程采样峰值 | 5786435584 B，约 5.39 GiB；没有触及 RSS、地址空间或输出上限，未观察到 OOM |
| 完整目标导出 / C++ 装载 | 未执行，原因是没有完整构造并导出的目标图；不是导出器或加载器已失败 |

27 个返回的层图耗时为 30.795～35.586 s，中位数 33.210 s。约 356 s 时，进程累计用户 CPU 346.89 s、系统 CPU 4.01 s，证据表明此次等待主要在计算。这个结论只针对已到达阶段；未测得完整合并峰值，也不包含服务全量导入、网络对象、运行中的包、观测及累计 64 轮对象。

源码检查找到明确的后续候选：`cann.py` 的状态计数对每个 rank、每个专家都扫描完整贡献列表，本场景一次构造即 128×256×8192=268435456 次贡献检查；校验还会再次调用原构造器。`full_model.py` 后续另有逐层重构校验和深复制，但本次尚未到达这些阶段。未做函数级耗时采样，不能把全部 900 s 都归因于某一个循环。

建议下一步先把 CANN 计数和查询改为等价索引，保留消息顺序、依赖、逐 Token 来源和独立拒绝校验，再用本次输入与相同上限复测。此处只提出候选，没有实施该优化，也没有自动扩大预算。

## 验证与基线保护

七项 Python 单元检查通过：日志包装不改变完整图、异常后恢复，以及实际进程组时间/RSS/地址/输出限额和独立计数。小型四块图经生产构造、导出与新 C++ 装载得到 171 节点、39 次 fabric 写描述符、12 次本地交付描述符；修改 JSON 节点、截断 Chakra 均被真实加载器拒绝。新检查最初误写了诊断子串，加载器本身正确拒绝；保留失败日志，修正匹配后重跑通过。

复用保留的 `build/m10-shared-provenance` 执行两项小型真实 UB 回归：三 rank 的完整 61 块图完成 754 次写、986 次交付、3102 节点，结束于 315509 ns；MoE/dense/MoE 且无 shared expert 的图完成 26 次写、34 次交付、114 节点，结束于 10829 ns。依赖、计算时长、完成反馈及资源归零均检查通过；这些是小型合成结构证据，不是 R1/128 rank 执行结果。

全部 13 个递归依赖未变；原两套后端、原生启动锁和原 M9 两份文件共 872 个文件摘要未变，探针只链接新目录中的一套 ns-3-UB。没有下载数据或改生产图语义。本步的资源测量和定位已完成；完整图、网络装载、多轮累计持有、最终目标实验和硬件校准仍未完成，M10 不标为完成。
