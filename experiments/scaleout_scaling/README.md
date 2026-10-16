# 多 EP 副本共享三层 Clos 的规模验收

原有的阿里 ns-3 交换机内存探针、规模期望核算与专家热点反例，完整保留在[规模与热点探针说明](probes_and_hotspots.md)，对应脚本/配置/历史结果保持原用途。下面是新增的实际联合执行路线，两者的来源和结论范围分别解释。

本实验将完整形状 Qwen3-30B-A3B 的 ready-KV Decode 从百卡向 256 / 512 / 1024 卡扩展。每个 EP4 组持有一个完整模型的专家分片，集群中分别有 64 / 128 / 256 个推理副本；不是单个模型做 EP1024，也不包含训练 DP 梯度通信。每个 GPU 每轮 batch=1，运行完整 48 层及首尾图、两轮 Decode，全部组共享一个持续存在的非 UB ns-3 网络和仿真时间。

此处验证执行规模、分层路径、机内/跨机争用及资源边界；总请求量与网络规模同时改变，不计算强扩展加速比。大 EP 的通信行为另由[128 卡 EP128 基线](../scaleout_clos128/documents/README.md)及其配置覆盖。本实验不声称已经验证千卡全局单组 All-to-All。

## 已记录的等待时间

优先打开[离线 HTML 报告](../../docs/review_artifacts/scaleout_runtime_20261016/report.html)：直接双击文件即可查看，支持场景/版本筛选、等待/内存/模拟时间切换、单例详情、矩阵成本估算和 CSV/JSON 导出。全部数据和图表逻辑内嵌，不依赖网络、服务器、外部字体或 CDN。显示时间为北京时间，导出的原始时间戳保留 UTC。

[运行成本报告](results/runtime_20261016/report.md)收录两种后端表示的 8 个已结束用例，保留[趋势 PNG](results/runtime_20261016/runtime_growth.png)、[矢量 PDF](results/runtime_20261016/runtime_growth.pdf)、CSV/JSON 与可独立重绘的小型证据。256/512 卡已完成，原版 Worker 分别约 172/346 秒；两种实现的千卡均在 600 秒限额内未完成。部分作业同机重叠，报告列出具体重叠，不把观测比值当作受控复杂度或优化加速比。当前目标为记录增长趋势，不再为千卡自动加预算或继续优化。

使用 Python 标准库，从已有测量重新生成 HTML；输出及旁侧 manifest 必须尚不存在：

```bash
python3 experiments/scaleout_scaling/render_runtime_html.py \
  --input experiments/scaleout_scaling/results/runtime_20261016/timings.json \
  --output runs/runtime-report-new.html
```

页面只改变展示和估算，不改动测量或启动仿真。来源、模板、生成器和 HTML 的 SHA-256 保存在旁侧 manifest；模板为同目录 `runtime_report_template.html`。当前交互检查覆盖筛选、超时缺失值、键盘选择、估算输入和数据下载；本机没有可用浏览器，因此未生成浏览器截图。

`timing.py` 只读取公开元数据，不加载大型 `result.json`，不执行仿真。保留 Worker 墙钟、命令墙钟、独立校验、模拟时间和内存的不同口径；超时记为右删失，完成时间留空。无需 GPU，也不把设备标签当作宿主硬件。使用已有 Matplotlib 与文泉驿字体，在新目录重绘：

```bash
OPENBLAS_NUM_THREADS=1 python3 experiments/scaleout_scaling/timing.py \
  --evidence experiments/scaleout_scaling/results/runtime_20261016/evidence.json \
  --output runs/runtime-trend-new
```

采集新运行时，改用可重复的 `--case 用例名称 /绝对路径/公开运行目录`；每例需要 `input.json`、`topology.json`、`provenance.json` 与 `execution/probe.json`，有校验与命令记录时一并读取。保存来源路径、内容和哈希，报告按二进制 SHA-256 及 EP 工作量分组；只有有时间戳的命令才填写时长。建议分析限120秒、RSS1 GiB/地址空间2 GiB、产物64 MiB，既有字体/依赖不自动安装。

## 拓扑与放置

| GPU | Pod × server/Pod × GPU/server | ToR / Agg / Core | 外部链路 | EP4 副本 |
|---|---|---|---:|---:|
| 256 | 4 × 8 × 8 | 16 / 8 / 2 | 296 | 64 |
| 512 | 8 × 8 × 8 | 32 / 16 / 2 | 592 | 128 |
| 1024 | 8 × 16 × 8 | 32 / 16 / 2 | 1104 | 256 |

每 Pod 4 ToR、2 Agg，ToR 连全部本 Pod Agg，每 Agg 连分组的一个 Core。外部各层 400 Gb/s、逐边 1 μs；机内声明为每 GPU 每方向 450 GB/s、100 ns 传播、16 KiB 分块的非阻塞织构抽象，边界同[平台示例](../../examples/scaleout_clos128/README.md)。不是等成本或无超售拓扑。

每 EP 组为 `[r,r+1,r+N/2,r+N/2+1]`，其中 r 依次取第一半集群的偶数 rank。同一组有两个同服务器 GPU 对，分别处于不同 Pod；因此每个组既产生机内消息，也使用 Core 跨 Pod。rank 按实际拓扑的 server / Pod 归属核验，不仅依靠编号猜路径。

1024 卡每 ToR 32 个 GPU 接入 12.8 Tb/s，2 条上联共 0.8 Tb/s，局部下/上比为 16:1；每 Pod 全 GPU 接入 51.2 Tb/s，2 个 Agg 的 Core 出口共 0.8 Tb/s，Pod 接入/跨 Pod 出口比为 64:1。该基线用于明确的有超售规模验收，后续的容量/机制对照要另行配对，不能当作生产网络容量推荐。

## 复现与修改

使用已具备前端依赖的 Python、已有 `protoc` 和已构建的非 UB 后端。在仓库根目录指定新目录：

```bash
export PYTHONPATH="$PWD/python"
COSIM_PYTHON=/path/to/frontend/python
COSIM_BACKEND=/path/to/rdma-build/backend.json
COSIM_PROTOC=/path/to/protoc

"$COSIM_PYTHON" experiments/scaleout_scaling/run.py \
  --backend-json "$COSIM_BACKEND" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/scale-gates-new --sizes 256 512 1024
```

先只生成/审计配置可增加 `--prepare-only`；它不执行网络。`--sizes` 支持 128 / 256 / 512 / 1024，`--batch-per-rank` 支持 1..16，但更大 batch 仍须独立检查数量、时间和内存限额，不能保证任意组合通过。脚本写出 `inputs-N/{model,topology,protocol}.json`，后续改变模型、逐层带宽或 EP 放置时，复制修改这些显式输入，再用公共 `scaleout rdma-run` 到新目录运行。

脚本通过公共 CLI 执行拓扑、服务和独立 `rdma-check`，不导入 tests 或平台执行驱动。它检查实际跨 Pod 包路径为 GPU→ToR→Agg→Core→Agg→ToR→GPU、机内/跨机 payload 均非零，以及每副本两轮实际完成。保存完整命令、输入、验证、资源和运行前源码/二进制 hash；结束时检查源码未在执行期间改变。源码或后端发生变化应新开一轮，不能将运行末尾版本冒充执行版本。

单例协议显式限额：600 秒、RSS 6 GiB、地址空间 12 GiB、仿真 100 ms、结果 JSON 1 GiB、最多 100 万逻辑消息；全部用例累计产物 3 GiB、卷保留 8 GiB。建议再用公开 `moe_ub_cosim.host_probe` 包装整个脚本：2000 秒、相同内存限额与累计 3 GiB 目录预算。失败即停止并保留证据，不自动扩限、重跑或删断言。

## 分析

使用已有 Matplotlib/Pillow 与文泉驿字体的分析环境，给 `analyze.py` 传入已经通过校验的各运行目录和 128 卡实际拓扑目录。建议外层限制宿主 300 秒、RSS 4 GiB、地址空间 8 GiB和输出 64 MiB，并为原始结果所在卷保留空间。脚本逐例读取公开 JSON、输出分层线上字节/队列摘要/端口利用率、消息延迟和宿主资源，生成 CSV/JSON、4 组 PNG/矢量 PDF 与离线网页，不执行仿真。

消息 FCT p99 是本窗口内跨机逻辑消息的描述统计；端口利用率按整个有限执行窗口与单方向链路容量计算，不能当作稳定态利用率。`summary` 队列没有完整时序，峰值本身不足以归因业务长尾。模型计算及路由仍为声明参考输入；设备标签不是硬件校准。
