# 现代direct操作计划验收

用户流程见[最小示例](../../examples/modern_deepep_partial_order/README.md)。功能验收不使用真实数据、研究脚本、GPU或历史运行结果；研究来源及源码布局另见[固定来源](../../docs/review_artifacts/modern_layout_20261022/sources.json)。操作计划尚未接入GIN队列核心或ns-3。

```bash
PYTHONPATH=python timeout 60s python3 -m unittest discover \
  -s tests/unit -p '*deepep_plan_test.py' -v
timeout 60s python3 tests/integration/check_modern_deepep_workflow.py \
  --output runs/my-modern-deepep-workflow
```

检查使用标准库；工作目录为仓库根，output为新目录。独立公共流程在新cwd执行12条CLI，保留命令、退出状态和stdout/stderr；单子进程最多15秒/地址2GiB/CPU30秒，总60秒、RSS≤1GiB、输出≤8MiB，卷预留8GiB。失败保留，不自动重跑或扩限。当前[验收记录](verification.json)对应人工四rank、两调用、1,076事件/224操作/16符号slot。相同输入三份产物重复逐字节一致，迁移后check相同。

| 核查 | 有效结论 |
|---|---|
| 独立按专家区间枚举 | Dispatch按目的rank去重；Combine本地归约后每来源token/owner返回一份；rank计数与专家计数口径不同 |
| 完整布局 | H7168/Top8的BF16 put长度14,464/14,400B，覆盖metadata/对齐，未推断包长 |
| 并发slot与绑定 | 不写死slot；相同token的两个合法slot能映射到不同声明连接；接收序号决定Combine通道 |
| 数据/计数角色 | 数据issued不等待远端计数可见；notify角色不会人为序列化全部数据 |
| flush与可见性 | 退出flush含此前信号尾部；数据local_complete可阻塞源flush；远端可见义务不会反向成为发送者可读真值 |
| 屏障/跨调用 | 入口/出口及滚动target保留；新一轮等旧Combine消费；共享buffer epoch明确 |
| 暂扣事件 | 零块、数据local_complete或visible会阻塞后继；最后一轮信号local_complete不一定直接阻塞应用结束，不证明网络排空 |
| 负例与迁移 | 未知事件、计划篡改、冒称已模拟包的manifest、不支持的hybrid均拒绝；正常副本迁移保持check一致 |

16个单元检查包含7个现代反例和9个既有v1回归。依赖闭包只检查保存表示与必要逻辑，不执行动态slot双射、通道内循环顺序、WQE、doorbell或provider事件；当前没有网络/源buffer寿命/硬件校准证据，也不构成论文贡献。
