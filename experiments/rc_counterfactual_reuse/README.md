# RC反事实复用原型与强解析基线

研究“只变化一次故障时，能否复用已执行网络、只重算受影响尾部”。[冻结方法与预算](protocol.md)、[结果网页](results/20261028/report.html)、[文字说明](results/20261028/report.md)、[核验](results/20261028/verification.json)。

从1472真实路由的第一个prefill token生成单层跨机Dispatch数据/计数片段：EP4、三条连接、每目的14,336B、共43,008B；数据内容为功能字节，不是完整CCL、LLMServingSim服务或GPU。输入准备不导入平台tests/fixtures；网络通过公开`scaleout rc-fabric-probe`调用。

正常/最终原子回复丢失/写入中间包丢失三个新网络各一次。前两者排空28.245/112.041μs。基于正常缓存封存的复用预测与故障结果、旧值及所有尾部时刻一致，但普通RTO/空闲路径解析也准确，因此没有额外预测创新或完整宿主加速结论。数据丢失事先拒绝复用，完整结果113.363μs；拒绝不是预测正确。

源程序：`study.py`负责独立输入及有停止账目的公开执行，`predict.py`仅读observed生成反事实，`verify.py`独立解析实际包并评价，`report.py`输出中文报告，`checkpoint.py`保存/离线恢复。预测hash在两故障网络之前封存；已有人工小例参与了公式开发，不称完全盲测。初次核验后加固缓存身份与ECN适用性，保存原源码，离线预测与原封存字节一致，不重跑网络。

本轮执行额度关闭，原件位于`runs/rc_counterfactual_reuse_20261028/`。核验现有结果可从仓库根目录在明确预算下执行，输出须为新路径：

```bash
PYTHONPATH=python:. python3 -B -m experiments.rc_counterfactual_reuse.verify \
  --stage runs/rc_counterfactual_reuse_20261028 --output /tmp/new-reuse-verification.json
```

归档与复制源码/空cwd恢复见[恢复收据](results/20261028/recovery.json)及[关闭](results/20261028/closeout.json)。恢复只重算预测、核验与报告，不执行网络、构建或下载；原始数据/运行时只在本地归档，不提交Git。`prepare/healthy/ack_loss/data_loss`保存首次研究的实际方法，不是追加已关闭条件的授权。

下一研究门槛是有重叠流量、后继依赖或拥塞反馈时的有效范围，以及相对普通增量/惰性执行的完整成本；不能把经典缓存、RTO或少量通过条件当作两项主创新。
