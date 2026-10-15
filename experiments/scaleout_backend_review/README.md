# 非 UB 后端源码研究与可复现探针

研究问题及网络架构结论见[源码报告](../../docs/reviews/scaleout_ns3_backends_20261015.md)。本目录保存固定来源、独立探针与实际结果，平台代码不导入本目录。

## 这次验证了什么

SimAI `a57d765` 的 `SendFlow()` 将已用 ns 表示的 `send_lat` 再乘 1000，传入 `Time(integer)`。其锁定的 Alibaba `3e0c7c1` core 默认时间刻度是 ns。此探针用**该库实际编译的 core**分别调度 `Time(7200*1000)` 和 `NanoSeconds(7200)`，检查实际事件时刻。

2026-10-15 的真实输出，退出码 0：

```json
{"default_resolution_is_ns":true,"intended_ns":7200,"raw_constructor_ns":7200000,"explicit_constructor_ns":7200,"events_ns":[7200,7200000]}
```

这里的 7.2µs 与 7.2ms 差异来自单位构造，不是网络拥塞。探针没有调用 SimAI `SendFlow()`，没有链接 ASTRA 或 UB，没有运行 RDMA 流，**不能据此宣称完整 SimAI 的性能误差、网络完成语义或在线推理服务已经验证**。本轮审阅的前端未发现改变为 PS 的调用；整套构建仍需单独验证启动路径。

## RDMA 消息边界结果

另有 `rdma_callbacks.cc`，直接调用阿里 `RdmaDriver`，在两端点 100Gbps、1µs 传播的 QBB 链路上执行 8 个 QP。CC 选择模式 1、无 INT、窗口为 0（该库语义为不设窗口上限），每条消息间隔 100µs，仿真限定 1ms。它没有交换机、ASTRA、UB 或服务调度，也没有人为丢包；不用于验证拥塞控制性能。8 条消息 ACK 完成都恰好一次，但原库发送完成 trace 如下：

| payload / 消息字节 | 发送完成回调次数 |
|---|---:|
| 1000 / 999、1000、1001 | 各 1 |
| 1000 / 3000 | **3** |
| 9000 / 8999、9000、9001、27000 | **各 0** |

该结果证实包长判断不是可靠的 QP 发送完成事件；完整事件时刻保存在 `results.json`。探针在全部 ACK 完成时返回 0，以便保存负面研究结果；`all_send_callbacks_once=false` 明确表示发送完成语义检查失败，不能把进程正常退出当成语义通过。

## 统一脚本复现

准备下述固定研究 clone 后，在主仓库根运行：

```bash
PYTHONPATH=python python experiments/scaleout_backend_review/run_probes.py \
  --source data/research/ns-3-alibabacloud-3e0c7c1 \
  --build-dir build/scaleout-alibaba-probe \
  --output runs/scaleout-probes
```

可用 `--cmake /path/to/cmake --ninja /path/to/ninja` 指定已有工具；不安装、不下载依赖。脚本先核对提交和关键来源哈希，只允许使用本仓 `data/` 或 `downloads/` 中独立的研究 clone；拒绝不同内容的已有 scratch 与已有输出目录。它用公共资源探针依次配置、编译、执行两个小例，保存实际命令和结果。已有同源探针 build 可复用；专用 fresh build 也使用相同 512MiB 累计产物预算与 2 路编译，不自动重试或扩限。

脚本已经通过真实完整流程，日志为 `runs/scaleout_ns3_review_20261015/reproduce-final/`；与前次 `reproduce/` 的探针结果一致，包括负面发现。实际首次 RDMA 构建为 `rdma-build-probe/`。下面保留 core-only 时间探针的独立复现步骤，便于只检查时间构造，无需编译网络栈。

## 时间探针独立复现

在主仓库根目录，准备 `cmake`、`ninja`、C++ 工具链和本仓 Python 环境，不需要 GPU、模型权重、protobuf 或 ASTRA 构建。下载固定来源，勿替换本仓 gitlink。HTTPS 不可用但已配置 GitHub SSH 时，可用对应 SSH 地址。

```bash
git clone --depth 1 https://github.com/aliyun/ns-3-alibabacloud.git data/research/ns-3-alibabacloud-3e0c7c1
git -C data/research/ns-3-alibabacloud-3e0c7c1 rev-parse HEAD
```

必须确认 HEAD 等于 `3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437`。若远端已前移，显式 fetch 此提交后 checkout，再核对 `sources.json` 中该库的文件哈希；不要继续使用另一个 HEAD。研究 clone 约 181MiB，网络获取应设外部时限，失败保留具体错误，不无限重试。

只修改这个研究副本的 scratch，原子模块不变：

```bash
cp experiments/scaleout_backend_review/time_units.cc data/research/ns-3-alibabacloud-3e0c7c1/simulation/scratch/scaleout-time-units.cc
python -m moe_ub_cosim.host_probe \
  --output-dir runs/scaleout-ns3-configure \
  --rss-bytes 2147483648 --address-bytes 4294967296 \
  --artifact-bytes 536870912 --wall-seconds 120 \
  --artifact-root build/scaleout-alibaba-probe \
  --artifact-root data/research/ns-3-alibabacloud-3e0c7c1 \
  --min-free-bytes 8589934592 -- \
  cmake -S data/research/ns-3-alibabacloud-3e0c7c1/simulation \
  -B build/scaleout-alibaba-probe -G Ninja \
  -DNS3_ENABLED_MODULES=core -DCMAKE_BUILD_TYPE=Release \
  -DNS3_OUTPUT_DIRECTORY="$PWD/build/scaleout-alibaba-probe/output" \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_PRECOMPILE_HEADERS=OFF \
  -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF \
  -DNS3_PYTHON_BINDINGS=OFF
python -m moe_ub_cosim.host_probe \
  --output-dir runs/scaleout-ns3-build \
  --rss-bytes 2147483648 --address-bytes 4294967296 \
  --artifact-bytes 536870912 --wall-seconds 300 \
  --artifact-root build/scaleout-alibaba-probe \
  --artifact-root data/research/ns-3-alibabacloud-3e0c7c1 \
  --min-free-bytes 8589934592 -- \
  cmake --build build/scaleout-alibaba-probe \
  --target scratch_scaleout-time-units --parallel 2
python -m moe_ub_cosim.host_probe \
  --output-dir runs/scaleout-ns3-time-run \
  --rss-bytes 268435456 --address-bytes 536870912 \
  --artifact-bytes 536870912 --wall-seconds 15 \
  --artifact-root build/scaleout-alibaba-probe \
  --artifact-root data/research/ns-3-alibabacloud-3e0c7c1 \
  --min-free-bytes 8589934592 -- \
  build/scaleout-alibaba-probe/output/scratch/ns3.36.1-scaleout-time-units
```

输出目录必须未存在。预算为进程树采样 RSS 2GiB、单进程地址空间 4GiB、研究源码与构建合计 512MiB，磁盘保留 8GiB；最后运行使用更小内存预算。配置、构建和运行分别限制 120/300/15 秒。预算失败时检查原因，不自动加限额。编译只选择 core，不能当作网络模块构建通过。

## 本次证据

完整命令、起止时刻、退出状态、日志与资源位于 `runs/scaleout_ns3_review_20261015/`；版本化摘要为[results.json](results.json)。首次配置因 Ninja 不在 PATH 失败；保留 `configure-probe/`，随后显式指定已有 Ninja 路径，以相同预算通过，未安装工具、未增加限额。

时间探针产物为 `build/scaleout-alibaba-probe/output/scratch/ns3.36.1-scaleout-time-units`；首次 core-only 构建的 `ldd` 仅包含本次 `libns3.36.1-core.so` 与系统库。RDMA 探针为同目录 `ns3.36.1-scaleout-rdma-callbacks`，链接本次 3.36.1 的 point-to-point、applications、internet、network 等库。两者均不链接 UB、ASTRA 或另一套 ns-3。来源清单 `sources.json` 含 4 个仓库、26 个关键文件的 SHA256；它是审阅证据索引，不是更换依赖的锁文件。
