# 完整现代原生工作量容量验收

公开操作见[使用说明](../../examples/modern_deepep_native/README.md#先检查完整工作量容量)，要求见[规范](../../docs/tasks/modern_native_capacity_spec.md)，实际命令、资源及失败记录见[核验摘要](verification.json)。

新增6项单元方法连同既有原生/批次21项通过；计划/事件/窗口/批次重放/压缩存储37项回归通过，共64项。两个完整**人工完成回调**用例与改动前源码的计划、连接编号、初始窗口、布局及完整控制器快照逐字节一致；这不是实际网络或时延比较。

公开CLI在空目录完成正例、重复、保存容量拒绝、覆盖拒绝和非法输入拒绝。超出完整远端/本地节点额度的输入由原生run在创建输出或调用监控启动器之前拒绝；布局不通过时不检查可执行程序是否存在。报告保留静态通过与实际运行未准入的区别，以及注册条数的下界性质和未核查项。

初次兼容检查错误拒绝了Git归档正常的顶层python目录，失败原件在runs/modern_native_capacity_20261031/compat及monitors/compat。只修该路径条件，改用compat-corrected新目录，未运行或重跑网络，未扩大预算。五个受监控阶段分别保留命令/工作目录/退出状态/资源。没有构建、安装、下载、GPU或依赖改动；真实EP32研究不由本功能测试代替。

复现（均为零网络）：

```bash
PYTHONPATH=python:tests/unit python3 -B -m unittest modern_native_capacity_test modern_native_test native_callback_batches_test
PYTHONPATH=python python3 -B tests/integration/check_modern_native_capacity.py --output runs/my-capacity-workflow
PYTHONPATH=python python3 -B tests/integration/check_modern_native_capacity_compat.py --baseline-commit cccdcd3773 --output runs/my-capacity-compat
```
