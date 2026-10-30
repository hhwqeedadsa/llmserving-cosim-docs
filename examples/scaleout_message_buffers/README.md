# 128端点Clos主服务消息缓冲示例

本例独立生成人工路由并使用原生ASTRA服务：两个EP64组、四份不同人工请求、两个MoE层、两个Decode步。前20微秒丢弃发往GPU6的数据包，保留控制包；SR与参考DCQCN保持开启。源确认超时可发起一个备用贡献，原任务继续确认和退出。源/目标内容均为可校验标记，计算耗时为声明值。

在仓库根目录执行，所有输出目录需为新路径。先按当前源码构建，传入已有CMake、Ninja及Protobuf；不会安装依赖：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-build \
  --output /path/to/new-build --cmake /path/to/cmake --ninja /path/to/ninja \
  --protobuf-prefix /path/to/protobuf --compile-wall-seconds 1800
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_message_buffers/prepare.py \
  --output /path/to/new-input
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new-input/service-buffers.json \
  --protocol /path/to/new-input/rdma-buffers.json --topology /path/to/new-input/topology \
  --backend /path/to/new-build/output/moe-rdma/ns3.42-moe-rdma-service \
  --protoc /path/to/protobuf/bin/protoc --output /path/to/new-run
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-check \
  --run /path/to/new-run
```

实际服务二进制位置以构建输出 `backend.json` 的 `service` 字段为准，替换示例路径。生成器不运行网络。协议声明native宿主60秒/RSS1GiB/地址2GiB、模拟5ms、产物512MiB、JSON256MiB，全包及队列各200000条；缓冲每GPU2MiB、总32MiB、消息4096、事件200000、源包捕获32MiB。超限明确失败，不自动扩容。构建另有独立预算；完整默认流程不需要真实数据账户或旧运行目录。

已有R1路由清单时，可以在准备命令添加 `--source-manifest /path/to/manifest.json --source-base /path/to/reference-base`。相对原件路径一律以显式base解析；默认选择清单前四份不同文件并校验其SHA256，不下载文件。只支持清单声明的R1/H7168/E256/K8，两源层3、4及两个Decode步；四个EP32组每rank每层8个路由专家。故障目的端按第一组首层Dispatch输入字节最多的rank事先确定，保持前20微秒，仅作用于DATA。

可添加`--route-files 15`选择前15份不同原件，支持范围4..16。第i份原件放在rank i、slot0，四组按rank模4交错，15份时各组有4/4/4/3个不同请求。每份原件只使用一次；缺文件、重复内容、错误模型或范围不足均拒绝。超过四份时显式声明4096条消息/确认记录额度，依然使用原固定字节缓冲和包记录预算；这只是准备能力，不保证任意输入在宿主资源限额内执行。没有来源清单时仍只生成原四份人工输入，不能用数量参数将其克隆扩充。

真实的是专家选择，网络包由布局投影生成，字节仍是标记，计算仍是声明耗时。备用专家来自另一个已声明完整部署；不建模权重加载、生产KV同步或数值等价。不同EP组的请求不是克隆的全群负载。结果、容量和释放定义见[接口说明](../../docs/message_buffers.md)。
