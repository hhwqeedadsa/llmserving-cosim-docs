# 推理路由的离线热点分析

研究结论、数据结构、原论文已完成的工作及Scale-Out接入位置见[研究说明](../../docs/reviews/moe_trace_dataset_hotspots_20261018.md)。本目录的 `analyze.py` 只做统计，不下载文件、不运行模型或网络，也不修改当前Scale-Out服务。结果按模型、原始层号、Prefill/Decode分别保存。

要把来源选择转为实际网络任务，使用独立的[Scale-Out回放示例](../../examples/scaleout_route_replay/README.md)。该例也可用作无原始数据时的离线CLI检查：将下面manifest换为`examples/scaleout_route_replay/manifest.json`并省略`--file-id`，即可分析4个明确标为人工构造的请求；其统计不代表真实数据分布。

在仓库根目录，用包含现有来源校验器所需依赖的Python运行。先对已授权、已在本地的真实manifest选少量文件：

```bash
PYTHONPATH=python python3 experiments/moe_trace_hotspots/analyze.py \
  --manifest fixtures/m10_route_selection/manifest.json \
  --file-id request-0000 --file-id request-0001 \
  --window 16 --top-m 8 \
  --output runs/my-real-route-hotspots.json
```

当前clone缺少这些原文件时，该命令会明确失败。没有自动获取、自动接纳条款或用合成数据补齐；请使用已获授权且hash一致的原件。文件路径沿用manifest，按仓库根目录解析。可通过新manifest显式列出自己的来源文件，必须遵循 `moe-ub-route-manifest/1`，不能篡改来源标签。

参数：`--file-id` 可重复；省略时分析manifest全部文件，不自动抽样。默认最多16文件、总输入64 MiB、单文件16 MiB、最多128个MoE层、1024专家和Top-16。默认窗口为每请求16个Decode token，Top-m为8；并限制1600万共选事件、200万估算输出单元及64 MiB结果。大输入需显式选择更少文件；不会静默截断或跳过坏文件。当前要求每份文件包含至少一个完整Decode入口，拒绝Prefill-only文件。

输出JSON包括：

- `aggregate`：每层每阶段的token加权统计，包含全部专家（含零负载）。`activation_probability`的和为Top-K；`selection_share`的和为1。
- `requests`：逐请求各层的频率、原始共选计数及token窗口Top-m。
- `prefill_decode_top_jaccard`：两阶段Top-m集合Jaccard，不是预测准确率。
- `decode_token_windows`：单请求连续token窗口及相邻窗口热点Jaccard；没有跨请求拼接，也没有纳秒时间含义。末尾窗口可以不足指定长度，并记录实际token数。
- `source`、`selected_files`、`manifest_sha256`：来源、原件SHA-256和使用范围。`real_trace`沿用manifest来源声明，不表示新增了采集身份或硬件校准证明。

排名只包含正计数专家，同频按专家ID排序；小窗口排名有离散性和并列敏感性，需改变窗口/Top-m检查。共选统计只是原始频次，尚未控制单专家的边际热度。工具不输出时间相关的网络性能，不把EOF当EOS。

以下只验证程序行为，使用仓库现有人工功能输入，不能当真实研究结果：

```bash
PYTHONPATH=python python3 experiments/moe_trace_hotspots/check_analysis.py
PYTHONPATH=python python3 experiments/moe_trace_hotspots/analyze.py \
  --manifest fixtures/m9_evidence/manifest.json --file-id 201 \
  --window 1 --top-m 1 --output runs/my-synthetic-hotspot-check.json
```

公开目录元数据摘要、原论文及访问失败的来源哈希分别保存在 `metadata-summary.json`、`sources.json`。约8.8 MB原始目录响应和论文HTML保留在忽略目录 `runs/moe_trace_dataset_review_20261018/`，没有提交原始路由文件。siblings列表恰好100000项，摘要中的可见数量不代表完整总体。
