# 依赖驱动唤醒的离线判别

这是现代完整应用接入的系统前置检查，不是新网络或性能收益实验。要求和预算见[规范](../../docs/tasks/dependency_wakeup_spec.md)，结果见[报告](../../docs/reviews/依赖唤醒离线判别_20261024.md)。原生协议和公开 `modern-deepep-native` 行为保持原样，256 次上限尚未解除。

`audit.py` 重新执行 Python 控制器，输入只来自保存的实际回调。先逐项核对原行为，再检查两种条件：保持每个现代事件及时处理；只在可以提交新原生工作时处理。后者会延迟记账，不作为完全等价方案。`verify.py` 不导入控制器或条件生成器，独立核对回调条件、工作注册、全部批次/顺序/时刻、事件与工作映射摘要、完整历史，并拒绝六类篡改。四卡记录仅有失败前缀，不能补出未知的后半段。

原始阶段目录可用时，在仓库根目录执行（输出文件必须不存在）：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.dependency_wakeup.audit \
  --source /dev/shm/moe-modern-native-20261024 --output /tmp/wakeup-audit.json
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.dependency_wakeup.verify \
  --source /dev/shm/moe-modern-native-20261024 --report /tmp/wakeup-audit.json --output /tmp/wakeup-verify.json
```

以上为纯离线命令；有界执行使用 `stage.py` 和本阶段单独的零网络预算。`checkpoint.py` 保存 Python 源码、三份原始控制记录及输入、期望快照和本阶段核验结果，安全检查通过后才恢复文件。`recover.py` 在新源码根和空工作目录运行，比较两份报告的完整字节。包不含原生程序或 GPU，证明范围只是离线再现。
