# 同一128端点Clos上的两个独立MoE任务

四组EP32，组0/1是目标任务的主/备用部署，组2/3是另一独立任务的主/备用部署。每rank每层8个专家，端点仍为128，源文件不克隆为独立请求。所有计算、门限、确认与定时事件共用同一个原生ns-3时钟。

仓库根目录准备新的输入，不执行网络：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_multicall/prepare.py \
  --output /path/to/new-input
```

默认使用两份不同人工原件，各call一份。已有R1-AWQ路由清单时增加`--source-manifest /path/to/manifest.json --target-files 4`，前4份用于目标，其余用于独立任务。文件顺序须在实验前固定，不按观察到的热点或时延挑选；每call最多32份独立请求，消息/连接预算还须通过准备检查。来源清单保留真实模型名、原路径、精度与未知版本，不将用户提供的未核验版本改标为固定修订。

真实模式默认覆盖原层3/4、入口1、单Decode步，H7168/E256/Top8，计算为声明成本。增加`--decode-steps 2`可准备连续两步，支持1～4步；多步使用显式replication/4，每个实际前步完成才启动同组下一步，最后一步才交付整个片段。原始文件不足时拒绝，不循环填充；所有步骤的源/目的连接在执行前规划。完整模型与等价ready-KV预先存在，未验证数值或模拟建立成本；不是完整671B推理、持续到达过程或满载128卡。

准备生成`service-multicall.json`（目标70µs、独立任务4ms）及`service-control.json`（两者4ms）。可用`--target-deadline-ns`、`--healthy-deadline-ns`和`--control-deadline-ns`修改；4ms并不是永久禁用备用，实际是否未启动必须检查输出。`rdma-multicall-normal.json`不额外丢包；`rdma-multicall-local.json`在前20µs丢弃目标初层Dispatch字节最多目的GPU的DATA，控制包保留。两配置保留相同部署、连接与实际KV预留。

运行仍使用[公开完整副本示例](../scaleout_whole_call/README.md)中的`scaleout rdma-run/rdma-check`：替换config、protocol、topology与新输出目录，后端需支持`--control-wakeup`。完整逐包和队列事件开启；原生预算为60秒/RSS1GiB/地址2GiB/模拟5ms/128MiB/完整包200000条。先登记累计实验预算，超限或失败保留，不自动扩限或重跑。

检查`whole_call_report.json`的每call结果、实际尝试与跳过状态，`frontend.standby_memory`的预留/释放，以及`result.service.control_wakeup`的最早期限替换。原始文件数、逻辑请求数、实际执行数、活跃rank与模拟端点数分开报告。
