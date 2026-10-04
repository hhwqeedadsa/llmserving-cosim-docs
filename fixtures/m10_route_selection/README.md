# M10 主场景真实请求清单

已为 `d128_b8_window64` 核验 1024 个独立真实文件。[manifest.json](manifest.json) 保存来源路径与 SHA256，[requests.json](requests.json) 保存请求/rank/窗口映射，[selection.json](selection.json) 固定选择策略及本次预算，[audit-summary.json](audit-summary.json) 保存独立检查结果。这些文件只含来源元数据，不含原始专家路由；原始文件保留在本地忽略的 `data/`，完整候选目录、逐候选报告与运行日志在 `runs/v03-target-route-selection/`。

## 选择与失败规则

来源为 `core12345/MoE_expert_selection_trace`，固定修订 `27febb7b2d24169560a9f6f83d38eb53f94916cf`，子目录 `cognitivecomputations/DeepSeek-R1-AWQ/mmlu/`。完整目录经 14 页元数据核对，共 13922 份 JSON；没有下载完整数据集。按 `(sha256(UTF8(str(seed) + "\n" + 仓库相对路径)), 路径)` 升序，seed=0；算法名为 `sha256_seed_newline_path_v1`，不依赖目录返回次序或 Python 随机数实现。

本轮用户授权最多检查前 1152 份、源文件合计 2252054093 B，获得 1024 份合格文件立即停止。实际停在第 1145 份，合计 2237609796 B；排除 30 份缺层文件及 91 份不足 64 个 Decode 入口的文件。只对这一前缀做了内容核验，不能据此判断其余目录文件的合格率。

每份文件先核对目录大小和 Git blob/LFS 身份，再核对完整 JSON 的 61 层、前三层 null、其余 58 层 Top-8、专家范围 0–255 与唯一性、跨层一致的实际 Prefill 长度，以及每个 Decode 入口一行 Token。检查包含未选入窗口的后续入口。缺层等格式问题会排除整份文件并保留原件和原因，不取合法前缀补数；合法的 Prefill-only 文件也只能判为不足。缺少顺序中更早的候选、大小/摘要不符、重复路径/内容或预算耗尽则明确失败，不生成可运行目标清单。

本次 30 份缺层均发生在最后一个入口，例如 anatomy/219.json 的入口 128 缺少层 12–60；文件前面超过 64 步合法也不使整份文件合格。具体位置见 audit-summary，全部拒绝与选中项见本地 `selected/report.json`。源采集精度仍是 AWQ4，与目标 INT8 参考方案不匹配；采集权重和 runtime 修订仍未知，不能由数据集修订号替代。

## 请求与 Token 索引

选择顺序中的第 i 个文件（i 从 0 开始）对应 request_id=i+1、file_id=`request-{i:04d}`、rank_id=i%128，因此每 rank 八个请求。全部 arrival_ns=0，使用入口 1–64，target_total_tokens=null。实际 prompt 为 1–521，合格文件可用 Decode 入口为 65–128。

设实际 prompt 为 P：第一步输入 Token 索引 P，最后一步 P+63，窗口 KV 预留 P+64，窗口完成后的 tokens_reached=P+65。文件结尾和观察结束均不推断 EOS。1024×64×58 共选择 3801088 条 Decode 层记录；本步没有把这些记录整体导入目标执行图。

沿用已固定的 MLA 容量参考，逐请求 KV 为 `ceil((P+64)/16)×1124352 B`。本清单每 rank 八个请求合计 46098432–89948160 B，低于条件 KV 预算 31325587712 B。这只是容量算式检查，不是目标调度接纳、实测可用 HBM 或宿主 RAM 证明。

## 已运行的入口

从主仓库根目录运行，输出目录须不存在。下面使用本机已有、摘要固定的目录和原始文件，仅生成元数据清单：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.route_selection plan \
  --input fixtures/m10_route_selection/selection.json --output-dir runs/route-plan-example

PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.route_selection select \
  --input fixtures/m10_route_selection/selection.json --output-dir runs/route-selection-example

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_target_routes.py \
  --input fixtures/m10_route_selection/selection.json \
  --selection-dir runs/route-selection-example --output-dir runs/route-audit-example
```

其他机器不会因克隆本仓库就拥有这些原始文件或访问权限。`scripts/fetch_route_catalog.py --source-manifest fixtures/m9_evidence/published-r1-manifest.json --output-dir 新目录` 只读取固定版本目录元数据。完整 files 清单按 `json.dumps(files,sort_keys=True,separators=(',',':')).encode()` 计算的 SHA256 应为 `2e1a2c055a3b2254d49aa87c0861fb6860bfc865ff4fcdce91c75bd10e070905`；元数据抓取时间和页链接会使 catalog.json 整体摘要变化。重建时核对清单身份，再在本地 selection 配置副本中填写新的 catalog 路径与整体摘要，不覆盖已提交的本次证据。

`scripts/acquire_route_selection.py --input 配置 --output-dir 新目录 --max-download-bytes 2252054093 --workers 4` 是本轮实际用过的获取入口，遵守配置的候选前缀、逐文件大小与合计预算。使用本机已有 HF 登录，跨主机重定向不转发令牌；原文件不覆盖。并行批次最多取尚缺的合格文件数，获得所需数量后无额外预取。失败后可用新输出目录恢复，已有原件仍重新核验；报错不记录签名 URL 或凭据。下载权限按根 AGENTS.md 和会话授权判断，清单不是对其他数据或扩大范围的授权。

## 实际验证及范围

120 项单元测试通过，包含乱序目录、换种子、末尾缺层、Prefill-only、摘要篡改、重复内容、早序候选缺失、数量/字节不足，以及下载停止和无网络恢复。独立检查脚本重新逐文件扫描来源，不调用被测原始校验器；核对全部候选顺序、身份、拒绝原因、1024 个文件与请求、窗口和 rank 映射。

独立脚本另外从选中清单的索引 0、2 派生小场景，它们各有 128 个 Decode 入口：仅为已有 UB 测试将两个请求放到 rank 2、观察入口 1–2 和层 3–5，保留源文件/请求身份。执行命令为 `tests/integration/check_published_routes.py --build-dir build/m10-fabric --selection runs/v03-target-route-selection/audit/smoke-selection.json --output-dir 新目录`（同样使用上述 Python 环境与 PYTHONPATH）。原目标请求映射没有被覆盖。

小场景逐条核对两文件的 14848 条 Decode 层记录，实际消费 12 条，每次完成 84 次真实 UB 写、344064 B 业务。来源开启/关闭/无观测三次结果相同；10 Gbit/s 时两轮边界为 146762/293224 ns，1 Gbit/s 为 1425542/2851084 ns，下一轮等待真实完成。原 M9 两文件回归另通过四次执行及三项非法选择拒绝。测试驱动仅验证 routed 通信片段，不代表完整模型、CANN 目标负载、原生 Scheduler 或唯一 KV 池已按目标规模运行。

文件逐个准备实测 37.872 s、最大 RSS 49804 KiB，独立复核 32.285 s、49028 KiB；计时定义及命令日志见 [environment.md](../../docs/environment.md)。这不是完整执行导入器的内存测量：现有 `load_manifest/select_routes` 仍保留整份 manifest 的原始对象与选中记录。完整目标导入、全图和来源账本的宿主资源，以及最终 R1/CM384 组合执行仍待验证。2048 请求的 refill 场景本步未准备，NPU/MLA/量化内核及网络绝对时序仍未校准。
