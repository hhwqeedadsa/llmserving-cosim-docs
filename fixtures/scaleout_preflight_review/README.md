# 源确认时刻与实际消息预检修复

对应2026-10-29[全面审查](../../docs/reviews/整体项目审查与网络系统改进方案_20261029.md#9-全面复核的新增发现与实施方法)的三个最小反例。旧反例及其源码hash保留；本次为正确性修复，不是新传输算法或论文收益。

## 行为和边界

- `rdma-arrivals`从实际源端累计ACK/NACK重建首次完整确认时间，并核对消息摘要。完整数据但反馈未到时仍未确认；重复反馈不推迟完成；反馈不能确认尚未接受的数据。
- LLMServingSim和SimAI服务在实际选批、生成消息后，导图/COMMIT前共用`MessagePreflight`。检查单条逻辑消息的源字节上限、目的份额、实际端点/路径、累计消息数及跨轮消息/线端口身份。排队总字节大于活跃额度本身合法，不能误拒绝。
- 沿用`10000 + id % 50000`的既有端口和ECMP路径，不自动调整batch、拆消息或释放旧连接身份。拓扑结构预检不承诺所有未来消息合法。
- 同机crossbar按现有定义豁免RDMA额度/端口；持久连接和RC使用自己的显式线上身份，不套用旧消息端口公式。

## 验收与复核

[检查收据](verification.json)保存源码、命令、退出状态、失败原因和本机日志hash。全648项CPU单元检查通过；此前两轮开发检查的失败保留，包含测试plane设置、最小拓扑兼容和一个错误模块名，未删除或改写。

新增13项用例覆盖早/晚/缺失确认、完整数据但最终反馈未到、重复确认、同纳秒事件及虚假确认；消息跨轮碰撞、合法端点/plane分离、原子拒绝、目的份额、局部旁路、总量和两前端提交拦截。已有连通性检查改为独立pair BFS，避免比较同一实现的两个调用者。

```bash
PYTHONPATH=python:tests/unit python -m unittest scaleout_arrivals_test scaleout_messages_test
```

真实网络小例各一次：LLMServingSim四卡/两轮/50消息完成68.252µs，SimAI六十四卡/两轮/64消息完成47.603µs；自动原件/依赖/回调核验通过。两者都是版本化人工来源小例，仅用于修复后的真实运行验收。使用既有原生service二进制，零编译/下载/依赖修改。

六个保存真实来源条件经新程序重核，共426,742包事件；五个成功条件每个790消息全部确认，持续黑洞已提交92消息、确认0，继续明确失败。[结果](saved-real-audit.json)绑定原始结果/图/拓扑/协议hash，没有重跑旧网络或重写原件。CPU审核不等于新的网络执行。

```bash
PYTHONPATH=python python tests/integration/check_saved_service_messages.py \
  --run /path/to/saved-service --output /tmp/new-saved-service-audit.json
```

保存目录需要完整`result.json`、`topology.json`、`rdma.json`及`graphs/*/wave.json`，不能仅靠本页摘要复现原始网络。原始用户路由不随源码发布。新小例的实际命令见收据；一般运行步骤见[LLMServingSim小例](../../examples/scaleout_route_replay/README.md)和[SimAI小例](../../examples/simai_service/README.md)。
