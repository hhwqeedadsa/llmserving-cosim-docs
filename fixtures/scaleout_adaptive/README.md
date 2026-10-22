# 自适应超时、条件界与纯协议分层验收

本组验证推理通信可靠性的机制和边界，不要求性能收益。平台仅使用固定原生RDMA ns-3，不改依赖或混链UB。

```bash
PYTHONPATH=python python tests/integration/check_rdma_adaptive.py \
  --backend-json "$COSIM_BACKEND_JSON" --protoc "$COSIM_PROTOC" --output /dev/shm/adaptive-gates
PYTHONPATH=python python -m unittest discover -s tests/unit -p 'scaleout*test.py'
g++ -std=c++17 -O2 -Wall -Wextra -Werror -Ibackend tests/formal/source_recovery_explore.cc -o /dev/shm/source-explore
timeout 30 /dev/shm/source-explore
g++ -std=c++17 -Wall -Wextra -Werror -Ibackend tests/unit/ack_rtt_estimator_test.cc -o /dev/shm/rtt-test
/dev/shm/rtt-test
```

构建与完整用户操作见[公开示例](../../examples/scaleout_rdma_adaptive/README.md)。固定模式回归使用`tests/integration/check_rdma_recovery.py`；保留旧二进制时，可加`--previous-probe`和`--previous-service`进行实际比较，不依赖历史二进制也可跑其余门。

| 检查 | 取得的证据 |
|---|---|
| 新网络与前端门 | 10 probe、3次真实LLMServingSim服务通过预期断言。覆盖无故障、尾包、最终ACK、NACK、重复丢失、持续丢失、短超时、双plane及记录0/3条。 |
| Karn排歧义 | 源包/ACK/NACK/超时记录独立重算RTT；实际发送payload、ACK、高水位和超时次数一致。短初值4KB探针发生重传，样本为0，冷启动限制保留。 |
| 有限静默界 | 重试耗尽的实际失败时刻与`Σ min(2^i×reset_rto,max_rto)`精确相等；该界不覆盖发送前或准入等待，不承诺推理成功。 |
| 源额度证据 | 新公开诊断核对持续故障下源0消息槽满、数据已齐却仍未确认的占用者；单元检查区分8192B额度下FIFO与目的端轮转的候选条件。 |
| 旧模式兼容 | 固定恢复19 probe/5服务门通过，实际旧二进制默认与当前相同；旧固定恢复报告/行为保持，原生默认不新增协议行为。 |
| 无界证明与有界探索分开 | 纸面条件证明见[形式化说明](../../docs/communication_fragility.md)。生产纯协议代码在2字节、0..8时刻、初始RTO2/上限8、一次重试下探索1602状态/28692转换，覆盖完成、失败、采样和作废。不是无限模型检查或NIC证明。 |
| 单元与非法配置 | 66项Scale-Out Python、4个纯C++程序通过；原生可执行入口的显式零采样边界也在开始仿真前拒绝。 |
| 公开完整服务 | 从新拓扑运行probe和2层4步服务，独立rdma-check通过；4波、628消息、1512节点，976709ns。包明细有界，恢复/RTT记录完整。此单例不作为一般加速结论。 |

[evidence.json](evidence.json)从实际结果提取必要摘要。原始目录`/dev/shm/moe-fragility-20261022/`保存命令、配置、成功/失败结果、资源、构建日志和有界检查结果；最终备份入口由当前任务登记。记录关闭/截断不改变消息、端口或终态估计，截断不重放缺失历史。

平台未增加单QP失败后继续运行、自适应跨QP历史、真实通信库分块或硬件校准，未跑千卡。独立MoE研究使用公开CLI，另行验收，不用本组fixture充当研究输入。
