# Scale-Out字节准入、队列积分与阶段诊断功能验收

2026-10-22平台检查；不以研究策略收益为验收条件。原生依赖仍为`3964b1d`，不修改依赖或链接UB。

复核命令（从主仓执行，先构建当前版本原生后端，使用项目Python环境）：

```bash
PYTHONPATH=python python tests/integration/check_rdma_structure.py \
  --backend-json "$COSIM_BACKEND_JSON" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/structure-gates
PYTHONPATH=python python -m unittest discover -s tests/unit -p 'scaleout*_test.py'
g++ -std=c++17 -Wall -Wextra -Werror -Ibackend tests/unit/communication_admission_test.cc -o /dev/shm/admission-test
/dev/shm/admission-test
g++ -std=c++17 -Wall -Wextra -Werror -Ibackend tests/unit/queue_integral_test.cc -o /dev/shm/queue-test
/dev/shm/queue-test
```

本轮实际根目录：`/dev/shm/moe-structure-20261022/`。详细命令和退出状态在各功能门`commands.json`，宿主预算记录在各运行`execution/probe.json`。摘要由真实输出复制至本目录`evidence.json`，可按同一脚本再生；共享内存原件随机器重启消失。

| 检查 | 结果与边界 |
|---|---|
| 新结构门 | 11个probe、1个执行前超额拒绝、3次实际LLMServingSim服务通过。FIFO不越过队首；轮转只跨目的端选可容纳队首；双plane共享字节额度；同机crossbar排除。 |
| 独立队列核算 | 正常/最终ACK故障下，逐端口状态积分、逐包大小×驻留时间及原生常量空间汇总一致；故障末态也积分到实际停止时刻。 |
| 详细记录与summary | 完整包记录、仅3条、无包记录得到相同汇总及执行；summary模式`max_queue_events=1`不限制积分。 |
| 原准入回归 | 11probe、9服务通过；默认结果与本次修改前真实二进制一致，透传观测等价，双plane同纳秒ACK资源计数仍正确。 |
| 原故障回归 | 11probe、3服务通过，保留数据尾包、最终ACK、NACK丢失的失败，不假造恢复。 |
| 单元检查 | 55项Scale-Out Python检查与2个纯C++检查通过，涵盖非法配置、字节所有权、队列时钟和不完整生命周期。 |
| 公开流程 | `bytes8192.json`完成4波、628消息、1512图节点，423082 ns；详细队列事件关闭，积分保留。 |

未运行千卡、完整Scale-Up网络或新增硬件测量。本功能仅提供软件准入基线与执行可观测性，不证明性能收益或硬件精度。
