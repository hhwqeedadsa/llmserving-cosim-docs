# 检查原/备用贡献与资源退出

本例验证同一贡献只采用一次，结果到齐不提前释放原尝试资源。它是人工回调的CPU功能例，不运行MoE、GPU、ASTRA或ns-3，不提供恢复性能数据。rank20/21是相同专家40–43的声明副本，没有创建小卡数网络。

仓库根目录运行，不需要后端、历史结果或真实数据：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout contribution-check \
  --config examples/contribution_attempts/input.json \
  --output runs/contribution-example
```

也可使用已安装的`moe-ub scaleout contribution-check`，输出目录须不存在。`report.json`显示：

- 专家40、42、80到齐；40/42是不可拆贡献包，备用`backup`获胜。
- 人工标签7时逻辑到齐，原尝试租约到12才关闭、13退休；14到达的旧结果为`late_retired`。数字是人工纳秒标签，不是仿真测量或收益。
- `logical_complete_ns`与`declared_call_retirable`分开；合法前缀也可退出0，仍可能缺贡献或未完成资源退出。

可修改专家划分、版本和事件顺序。改变invocation或专家集合后，用公共`ContributionAttempts(spec).bundle_key(bundle_id)`重新生成声明身份；旧key将被拒绝。字段和服务接入限制见[接口说明](../../docs/contribution_attempts.md)。全`a/b/c`指纹为人工占位身份，不对应R1权重或真实激活，没有认证或张量正确性证明。
