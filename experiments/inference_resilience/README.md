# 恢复历史与目的端份额的独立推理对照

研究目标是判断两项改进在推理通信中什么时候有用、什么时候带来代价。功能检查不作为研究收益证据；本目录维护自己的输入，只使用公开`scaleout sweep`，不导入平台私有驱动、tests或功能fixture。

固定8GPU、两组交错EP4、H1024/FFN4096、16专家/Top2、1层2步ready-KV Decode，100G三层Clos；每源3条消息/16384B逻辑额度，策略均为目的端轮转。计算为声明值，路由为同一合成选择，不改热点制造收益。启用份额时每目的端1条/8192B，总源资源不增加；历史容量每源16、证据有效期300μs。

`design.json`显式列出24条件：batch/rank1或4、正常40μs或过短2μs初值、历史开/关、份额开/关的16组无故障组合，以及batch4正常初值下最终ACK丢失一次/持续丢失的8组。短初值上限64μs/8次重试；正常160μs/3次重试；持续故障允许2次重试。不是性能调优搜索，不选最有利参数作为默认配置。

先通过公开入口运行一个正式基准条件，从实际首图唯一选出0→4 Dispatch作为故障目标，再执行其余23条件。这个先行条件是24组之一，不重复计数，不从测试记录推定消息编号或字节。共同任务图和实际首包路径由独立分析核对；不同batch分别比较，不把失败中减少的提交量当作网络节省。

预先区分的解释：

1. 历史的收益应对应后续QP初值、超时次数和重复payload变化；超时更新不计为有效RTT。初批无历史不能被“自动修复”。
2. 同一目的端份额可能限制健康并发，也可能阻止故障占用扩大。必须核对所有者和实际准入，而不是把全部业务差异归给保护。
3. 正常初值下若已有足够安全余量，历史可能没有收益；故障下保守历史也可能推迟恢复/明确失败。这些都是保留的正常结果。
4. 接收数据齐、源确认、资源释放、业务图完成、尾部网络排空分别记录。持续故障不能当作推理加速，独立副本也不等于训练DP同步。

```bash
PYTHONPATH=python python experiments/inference_resilience/run.py \
  --backend "$COSIM_NATIVE_SERVICE" --protoc "$COSIM_PROTOC" --output /dev/shm/inference-resilience
```

本机需先激活包含平台依赖的Python环境；`python`指该环境的解释器。正式24组结束后，在同一研究预算内补采四个含歧义/负结果的条件，再运行离线分析：

```bash
PYTHONPATH=python python experiments/inference_resilience/capture.py \
  --experiment /dev/shm/inference-resilience \
  --backend "$COSIM_NATIVE_SERVICE" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/inference-resilience/captures
python experiments/inference_resilience/analyze.py \
  --input /dev/shm/inference-resilience --output /dev/shm/inference-resilience-analysis
```

分析需要完整24组和四组包记录，不允许缺失时默默省略机制核验。补采仍走公开sweep，只增加被动记录；要求除packet_capture外整个result逐字段相同、所有任务图字节相同。补采从研究256MiB剩余额度中分配，最多128MiB，并额外留16MiB；不在原预算外增加额度。它们是观测一致性证据，不算额外独立性能样本。

分析从原始result、frontend、任务图与diagnosis独立重算指标，核对24条件、来源hash、图与实际路径、历史重建、持续失败界和资源保留。输出`summary.json`、`results.md`、`report.html`；对两策略分别保存24个成对有符号差值，不要求收益为正。

2026-10-22的结果见[审阅HTML](../../docs/review_artifacts/inference_resilience_20261022/report.html)。短初值下历史减少重复流量，但batch4业务反而晚313ns；包级确认反馈与源额度链已解释。静态份额在单最终ACK丢失时让同目的端健康消息等待，业务慢12856ns。两者仍缺省关闭；持续故障仍停止共享运行，不具备请求级容错。

需先按[用户示例](../../examples/scaleout_rdma_resilience/README.md)构建原生后端。单例60秒/RSS1GiB/地址2GiB/仿真5ms，总产物256MiB分为先行64MiB与其余192MiB；每次清单300秒、卷预留8GiB，失败保留、不自动重试或抬高预算。完整命令、平台版本、输入/source hash与资源采样随运行输出。

若要改变模型、batch、链路、rail或已实现策略，优先复制公开`sweep.json`修改嵌套配置，并显式声明条件/预算；它比修改本研究的固定问题定义更适合新场景。模型标签与声明成本不提供硬件精度，千卡和真实专家热点需要单独验证。
