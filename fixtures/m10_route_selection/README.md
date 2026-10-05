# CM384/R1 请求来源清单与选择检查

本目录保存 `d128_b8_window64` 的 1024 个独立来源文件清单及请求映射，不包含原始专家路由。当前场景中的参数、选择算法、Token 索引和来源限制见[CM384/R1 场景](../../docs/scenarios/cm384_r1.md)。

| 文件 | 内容 |
|---|---|
| [manifest.json](manifest.json) | 来源路径、身份和 SHA-256。 |
| [requests.json](requests.json) | 请求、rank 与 64 步观察范围的映射。 |
| [selection.json](selection.json) | 固定候选顺序、文件数及获取字节预算。 |
| [audit-summary.json](audit-summary.json) | 独立逐文件检查结果。 |

原始文件位于本机忽略的 `data/`；候选目录、逐候选报告及命令位于 `runs/v03-target-route-selection/`。克隆仓库不会自动获得这些文件或访问权限。

## 本次选择结果

来源为 `core12345/MoE_expert_selection_trace` 固定修订 `27febb7b2d24169560a9f6f83d38eb53f94916cf`，子目录为 `cognitivecomputations/DeepSeek-R1-AWQ/mmlu/`。核对 14 页目录元数据，共 13,922 份 JSON；按 `sha256_seed_newline_path_v1`、seed=0 排序，未下载完整数据集。

本次最多检查前 1,152 份、总源文件 2,252,054,093 B；实际在第 1,145 份取得 1,024 份合格文件，合计读取/获取 2,237,609,796 B。排除 30 份缺层文件、91 份不足 64 个 Decode 入口的文件。只核验这一候选前缀，不能据此推断其余文件质量。

每份原始文件全部入口均检查，包括未选入观察范围的后续入口。30 份缺层均出现在最后入口，例如 anatomy/219.json 的入口 128 缺少层 12～60；前面已有足够合法步骤也不能使整份文件合格。拒绝原因和原件保留，没有截取合法前缀补数。

选择顺序第 i 个文件对应 `request_id=i+1`、`file_id=request-{i:04d}`、`rank_id=i%128`，i 从零开始。全部 arrival_ns=0，选入口 1～64，target_total_tokens=null；实际 prompt 为 1～521，合格文件有 65～128 个可用 Decode 入口。共选 3,801,088 条层记录。文件结束或观察结束均不推断 EOS。

当时容量检查按旧通信预留得到 31,325,587,712 B 条件 KV 预算；每 rank 八请求实际声明所需为 46,098,432～89,948,160 B。完整目标随后应用[通信预留修订](../m10_resource_efficiency/communication-reserve.json)，可用 KV 为 25,063,072,512 B、22,291 块。这是预算核算，不是实测 HBM。

## 复现与获取入口

使用已安装 Python 环境，从仓库根目录执行。下面命令只使用 selection.json 引用的本地固定目录和文件，输出目录必须不存在：

```bash
PYTHONPATH=python python -m moe_ub_cosim.route_selection plan \
  --input fixtures/m10_route_selection/selection.json --output-dir runs/route-plan-example

PYTHONPATH=python python -m moe_ub_cosim.route_selection select \
  --input fixtures/m10_route_selection/selection.json --output-dir runs/route-selection-example

PYTHONPATH=python python tests/integration/check_target_routes.py \
  --input fixtures/m10_route_selection/selection.json \
  --selection-dir runs/route-selection-example --output-dir runs/route-audit-example
```

重新获取目录使用 `scripts/fetch_route_catalog.py`；获取候选文件使用 `scripts/acquire_route_selection.py`，均有 `--help`。它们遵守配置的固定修订、候选前缀和字节预算；缺失早序候选、大小/摘要不符、重复内容或预算不足会失败。新目录的抓取时间可能改变 catalog.json 整体摘要，应核对 files 清单身份后在配置副本中填写新路径与摘要，不覆盖已提交证据。完整参数、目录清单摘要和无网络恢复方法见[原选择记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_route_selection/README.md)。

## 检查结果与适用范围

120 项单元检查和独立脚本通过。独立脚本重新扫描原始文件，不调用被测原始校验器，核对候选顺序、身份、拒绝原因、文件和请求对应、观察范围及 rank 映射。

另外从选择索引 0、2 派生小测试，两请求放到 rank 2，只观察入口 1～2、层 3～5。三次来源模式均完成 84 次网络写、344,064 B 业务；10 Gbit/s 的两轮边界为 146,762/293,224 ns，1 Gbit/s 为 1,425,542/2,851,084 ns，后轮等待前轮实际完成。原目标映射没有被修改。这组测试只核对路由通信片段，完整首轮结果另见[最终验收](../m10_research_slice/README.md)。

来源模型是 AWQ4，目标容量采用 INT8 参考；采集权重和推理软件修订未知，数据集修订不能替代这些信息。2048 请求连续补入场景未准备；本目录不提供硬件时序校准。
