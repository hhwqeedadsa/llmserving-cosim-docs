# 源端恢复历史、目的端份额与统一实验入口验收

平台验收与研究效果分开。本组验证协议/资源/接口的正确性，不要求策略一定加速。完整输入与用户命令见[公开示例](../../examples/scaleout_rdma_resilience/README.md)，证明见[通信脆弱性](../../docs/communication_fragility.md)。

本轮从`a329e19`继续，外部依赖未改。已通过：72项Scale-Out Python检查；5个纯C++程序；纯SourceRecovery有界探索1602状态/28692转换；当前二进制固定恢复19 probe/5服务、自适应10 probe/3服务；新增历史与份额11 probe/4服务。固定模式与保存的真实旧二进制比较，不能用模拟输出代替。

新增检查覆盖：零有效样本时从真实退避给后续QP提供初值；源、目的、plane隔离；每源独立容量/LRU、严格到期边界；记录关闭/截断不改变执行；单目的端故障资源保留而健康网络工作完成；正常服务四组合的真实前端/任务图一致。整体失败仍停止共享运行，不宣称请求级故障隔离。

统一入口已执行普通用户的五条件清单，覆盖模型形状、batch和50G/100G接入变化。另验证预期失败后继续执行下一条件、非预期失败保持非零、总时间预算到期时终态为`execution_stopped`，不遗留“运行中”作为最终状态。预算停止日志保留；测试最初误读宿主记录字段，已依据真实`status=wall_limit`更正并对保留输出离线重验，无需重跑仿真。旧二进制比较最初因复制未保留可执行权限在启动前拒绝，修正权限后在新目录完成，原失败未删除。

```bash
PYTHONPATH=python python tests/integration/check_rdma_resilience.py \
  --backend-json "$COSIM_BACKEND_JSON" --protoc "$COSIM_PROTOC" --output /dev/shm/resilience-gates
PYTHONPATH=python python tests/integration/check_scaleout_sweep.py \
  --backend "$COSIM_NATIVE_SERVICE" --protoc "$COSIM_PROTOC" --output /dev/shm/sweep-gates
```

第二个检查可加`--audit-existing`只核对原实际运行，明确记录该模式，不宣称再次执行。功能输入来自本组示例，研究从自己的配置出发，经公开命令使用平台。

来源hash、摘要与实际运行入口见[evidence.json](evidence.json)。构建300秒/2并发/RSS2GiB/地址4GiB；单例60秒/RSS1GiB/地址2GiB/仿真5ms，功能累计256MiB，运行卷预留8GiB。预期失败、准备失败和检查脚本失败分别说明，不自动抬高预算或隐藏记录。
