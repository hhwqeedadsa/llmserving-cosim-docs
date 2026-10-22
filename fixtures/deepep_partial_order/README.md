# DeepEP离线操作计划检查

输入和公开命令见[示例](../../examples/deepep_partial_order/README.md)。人工输入不代表真实路由或网络测量。

九项单元反例检查：`PYTHONPATH=python python3 -m unittest discover -s tests/unit -p deepep_plan_test.py -v`。公开新目录流程：`python3 tests/integration/check_deepep_workflow.py --output /dev/shm/new-deepep-workflow`。本次实际流程产物为`/dev/shm/moe-deepep-platform-accepted-20261022/`，8条命令含2项预期拒绝，160操作/728事件，迁移检查一致。

仅验证操作关系、保存表示与无时间闭包；不说明真实GPU/NIC事件、源buffer释放或模型精度。源码身份和检查摘要见[verification.json](verification.json)。
