# Scale-Out 规模核算与主机资源证据

问题：千卡 MoE 网络研究在启动完整仿真前，需要多少逻辑工作量和交换机内存？本目录交付一个实际 ns-3 对象分配探针及可复核摘要；它不执行千卡逐包仿真，不提供训练热点测量结果。

公共容量核算及模型/batch/EP 配置见[用户示例](../../examples/scaleout_capacity/README.md)，网络路线与热点研究设计见[研究报告](../../docs/reviews/scaleout_scaling_hotspots_20261015.md)。平台代码不依赖本目录。

## 复现交换机分配

需要已存在的独立 `ns-3-alibabacloud` clone，固定提交 `3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437`。脚本不联网、不下载、不切换依赖，不修改主仓库 gitlink。只在研究 clone 中放入一个独立 scratch 程序；已有文件不同时拒绝覆盖。

仓库根目录执行，输出须为新目录：

```bash
PYTHONPATH=python python3 experiments/scaleout_scaling/run_memory_probe.py \
  --source data/research/ns-3-alibabacloud-3e0c7c1 \
  --build-dir build/scaleout-alibaba-probe \
  --output runs/scaleout-memory-new \
  --cmake /home/lry/tools/venv/bin/cmake \
  --ninja /home/lry/tools/venv/bin/ninja
```

最后两个参数是本机工具路径，可换为本机已有 CMake/Ninja；默认从 PATH 查找。构建目录专用于阿里派生库，不使用 UB 或 ASTRA ns-3 的库。输入、构建、输出分别位于 `data/`、`build/`、`runs/`，均不提交 Git。

配置限 120s、构建限 300s、各分配例限 15s；构建 RSS/地址空间 2/4GiB，短运行 512MiB/1GiB；来源、构建与本次输出累计 512MiB，磁盘保留 8GiB。失败不自动扩大限额。`provenance.json` 保存命令、版本和关键来源 SHA-256，`results.json` 保存程序输出，子目录保存资源、stdout 和 stderr。探针最多允许分配 16 个交换机，复现入口只运行 0、8。

## 证据解释

版本化摘要在 [results.json](results.json)。`sizeof` 是对象编译后大小；程序自己读取 `/proc/self/statm` 得到分配前后 RSS；外层 `host_probe` 还记录子进程 `max_waited_child_rss_kib`。短程序可能在采样间隔内完成，所以采样峰值可能漏掉内部分配，应同时读上述自测和系统等待值。

8 个对象的 RSS 增长约 258MiB，证明这些数组会实际触碰内存。64 个 Ethernet 交换机和 128 个建模 NVSwitch 的固定数组约 6.01GiB 是依据源码计算，**并未实际创建这 192 个对象**。按实际端口分配后的大小也是设计估计，尚未修改依赖实现。

`results.json` 中的核算结果是均匀路由的期望值，不是真实 MoE trace、线速字节、训练热点或网络性能测量。实际路由数据待用户提供后再做有来源约束的统计。

## 专家热点的确定性反例

[训练热点报告](../../docs/reviews/moe_training_hotspots_20261015.md)结合DeepSeek-V3论文和固定Megatron源码，解释全局/微批平衡、节点限制、drop/pad及通信split。阅读来源与失败访问记录保存于 [training-sources.json](training-sources.json)，未安装或执行训练框架。

5个手选合成例子用于区分专家频率、放置、通信模板和token窗口。仓库根目录执行，仅需Python标准库，无需GPU/原始数据：

```bash
python3 experiments/scaleout_scaling/analysis/routing_counterexamples.py \
  --config experiments/scaleout_scaling/configs/routing-counterexamples.json \
  --output runs/routing-counterexamples-new
```

输入最多1MiB、64个条件、每条件4096个token/专家，密集统计数组累计最多262144个单元，输出目录必须不存在；只核算Dispatch向量payload，不调用平台或网络后端，不生成包或时延。`vector_bytes`、`ranks_per_server`、`token_window_size`显式定义尺寸和相邻rank的服务器归属；每个条件给出专家放置、同一源rank和完整专家选择列表。配置路径相对当前目录，无外部数据引用，不支持未知训练格式或训练实测声明。

输出保存配置、输入/分析代码SHA-256及 `counterexamples.json`；[版本化结果](routing-counterexample-results.json)另保存有界运行和独立手算检查。专家频率相同，跨机逻辑字节仍可为0/64/128B；节点级聚合为0/64/64B。最终矩阵相同，4token窗口的目标rank峰值也可为32/16B。窗口无物理时间含义，不能解释为FCT或排队降低比例。

本轮实际执行限15s、RSS256MiB/地址空间512MiB，公开来源与运行目录累计16MiB，磁盘保留8GiB；命令和资源日志位于 `runs/scaleout_training_review_20261015/`。没有修改平台接口、依赖或后端行为。
