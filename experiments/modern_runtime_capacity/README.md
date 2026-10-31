# 真实EP32连续两调用：现代运行期容量需求

目的：推进同一128端点Clos上的真实现代连续调用，明确需要扩展哪些软件限制。不能将新计划压缩保存或静态容量通过解释为网络执行、生产R1推理或论文算法收益。本研究在平台容量入口独立验收后执行；不改原研究的冻结方法或输入。

输入固定为`experiments/route_window_network/results/20261031/inputs/modern-group-{0,1}/input.json`。两组来自既有15份不同R1-AWQ/MMLU路由，前8/后7请求，每请求层3前两个Decode步骤，两步属于顺序调用。每组EP32/E256/H7168/Top8/BF16激活表示，每rank8专家。源记录、归一化及原件核验沿用原研究固定证据；本轮只核对配置字节未变，不宣称重新下载/校准或取得新的真实请求。部署对象固定128Clos，两组映射沿用原报告；容量命令不接收物理拓扑，因此不能检验NIC配对或证明实际部署已准入。

事前判别：如果仅有宿主限时问题，全部静态结构需求应满足原上限；若完整控制和数据需求超出连接、操作、窗口或本地等待项，则必须在相关层扩展能力，不能只增加wall_seconds。真实需求小于某项上限不证明该层运行足够，也不在失败后删通知、并未来token、降EP或重配context来迁就当前实现。

程序仅通过公开`modern-deepep-native-capacity`读取两份完整配置，分别保存退出码、stdout/stderr和负报告。两组预计拒绝，不是网络失败。独立核验从原token选择按集合枚举token/目的rank关系、完整4个屏障及rank/expert计数，推导远端/本地工作；固定单token/单连接launch下独立计算所有可能连接身份，不导入平台、旧研究或测试驱动。将需求、已知软件门限及未验证项分别列出，提出后续执行扩展要求。

资源根`runs/modern_runtime_capacity_20261031`：累计360受监控秒/256MiB产物/卷留8GiB；最多3阶段，每阶段120秒/RSS1GiB/地址2GiB。零新网络、GPU、构建、安装下载或依赖改动；不提高当前平台上限、不把原生程序的未执行当作失败。不重复已有成功网络。研究与平台分别提交，仅发布user既有分支。

运行：

```bash
PYTHONPATH=python python3 -B experiments/modern_runtime_capacity/study.py --output runs/my-modern-capacity
python3 -B experiments/modern_runtime_capacity/verify.py --run runs/my-modern-capacity --output runs/my-modern-capacity-verification.json
```

执行时在外层使用公开`moe_ub_cosim.host_probe`施加上述预算。结果未取得前不预填具体容量或时延结论。
