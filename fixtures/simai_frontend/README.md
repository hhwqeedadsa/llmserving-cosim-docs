# 固定SimAI前端CPU验收

[核验结果](verification.json)来自`tests/integration/check_simai_frontend.py`通过公开`scaleout simai-prepare`执行；原件`runs/simai_scaleout_20261028/scheduler-check/`，监控`scheduler-check-monitor/probe.json`。

固定165份源码/模型配置/许可文件，两个包初始化文件使用显式延迟导入，其余功能源码保持原Git blob；实际SimAI Cluster建立EP64，原生Orca调度三个声明ready-KV请求，batch上限2，完成四次批次并释放全部块。记录五个实际上游类的源路径和hash。再次加载同一运行目录通过，功能文件改变与额外模块均在执行前拒绝。没有使用LLMServingSim调度器、sklearn、GPU或网络。

该验收不证明SimAI/ns-3已经联动、320卡执行、真实路由实验或论文收益；后续要求见[联合执行范围](../../docs/tasks/simai_scaleout_spec.md)。准备过程未安装依赖，使用已有frontend环境的networkx。
