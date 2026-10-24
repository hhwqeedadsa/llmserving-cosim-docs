# 四请求失败前缀的C++准入成本

平台固定 `fdb77c2`，规范见[有界诊断](../../docs/tasks/native_admission_cost_spec.md)。没有修改平台、依赖、编译优化或模型工作量，没有新网络执行。

同一O0配置、未修改的 `ValidateSessionProgram` 和 `SessionWakeups::Parse` 对238条实际COMMIT逐条校验一次：复制/追加0.187秒、完整候选校验2.201秒、订阅校验0.076秒，合计2.464秒。最终145节点候选完整等于实际命令，八个实际加载库hash保持。这是离线宿主测量，不能写成原运行CPU占比、网络加速、四请求完成或死锁结论。

结果削弱“整批校验单独足以解释60秒宿主超时”，下一步需要真实运行中的前端推进、原生进度/事件扫描、控制I/O和网络事件分区计时。不要删校验、提高预算或直接改编译选项。见[解释](../../docs/reviews/四请求超时与原生准入成本_20261024.md)、[独立核验](verification.json)、[恢复核对](recovery.json)和[预算收尾](closeout.json)。

`prepare.py` 固定原始initial/topology/protocol/control和编译/链接身份，`execute.py` 拒绝输入漂移或重复已有计时文件，`measure.cc` 只执行纯准入、不构造/运行Fabric或Simulator。`verify.py` 从原始COMMIT重建每个候选及最终程序，不导入现代控制器；时钟采用单调宿主时间与线程CPU时间，全部以ns记录。每个候选只测一次，不给统计置信区间。

原始计时/日志/命令、诊断二进制及源码快照保存在 `runs/native_admission_cost_20261024/`。复制源码根的核对只复算已有证据，仍核对本机固定八库；该快照不包含八库或完整上游头文件，不是独立原生构建包。完整网络和原四份路由的原件继续由前一平台检查点保存。本诊断零网络、无GPU、无安装下载或push。
