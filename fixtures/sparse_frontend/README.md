# 稀疏请求接入与Clos规模核验

本轮补齐了LLMServingSim回放入口在128、256、512卡Clos中的稀疏请求表达。以前要求每卡填满batch，15份真实原件无法直接对应128卡；现在只为明确列出的请求创建调度/KV状态，其余卡继续承担专家计算和回传。默认每卡完整batch保持兼容，未提高连接上限、重试次数或资源预算。

## 实际网络验收

平台执行固定于`c4cc145dbe`，使用现有原生程序和锁定依赖。只执行一个新的128卡Clos功能条件：4份不同的人工路由、EP128、每层256专家/Top8、两层两步、400Gb/s各层链路、无人工故障。GPU名称为描述标签，声明计算未经校准。

结果：252条消息，129,024有效字节，8,496条完整包事件，2,552个ASTRA节点完成核验；有限窗口结束65,696ns。72个rank实际执行专家，其中69个没有本地请求；1,488个此类rank的本地token计算节点均为0。独立核算从原始JSON重建贡献矩阵，并用实际节点issue/callback核验专家工作，没有仅比较两次调用同一个构图函数。

该时延是人工功能用例的结果，**不是R1推理速度或优化收益**。本轮没有真实路由的新网络执行，也没有256/512卡的新网络执行。

## 15份真实原件的离线核验

原件来自用户确认的R1-AWQ/MMLU目录；17份中保留原15份合格输入，899/1479仍排除。全部重新核对本地SHA及完整原始层结构，不复制原件填满端点；混合清单对远端revision明确记为未知。选原始层3、4及Decode入口1、2，H7168/E256/Top8，激活每元素2字节为假设。

| Clos端点 | EP分组 | 每rank专家数 | 独立请求 | 有网络数据的rank | 其中无本地请求 | 持久连接 | 消息 | 有效字节 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 128 | EP128 | 2 | 15 | 114 | 99 | 804 | 926 | 13,676,544 |
| 256 | EP256 | 1 | 15 | 181 | 166 | 894 | 958 | 13,733,888 |
| 512 | 2×EP256 | 每组1 | 15 | 264 | 249 | 892 | 956 | 13,705,216 |

三档均通过来源、专家部署、独立原JSON贡献矩阵、完整窗口持久连接和消息限额检查，**仅准备、未执行网络**。源rank按预先声明的等间隔方式放置，数据集本身不提供原集群位置或到达时间。512卡中只有264卡有上述窗口的网络数据，不能称为512卡满载；消息差异来自专家归属、同卡贡献和聚合方式，不能当作网络扩容收益。

只分配固定窗口实际需要的连接，未创建稠密全互联；最大单连接累计43,008/28,672/28,672B，小于1MiB参考SR窗口。这不解决任意动态路由的连接扩展或现有稠密EP256线端口限制。

## 复核与复现

公开准备、运行、协议与预算见[示例](../../examples/scaleout_sparse_clos/README.md)。本机原件、完整日志与命令保留在`runs/clos_sparse_frontend_20261030/`。`native128/execution/probe.json`记录实际工作目录、命令、退出状态和资源；`native-attempt.json`绑定全部执行输入及原7个程序/8个库的身份检查。

```bash
PYTHONPATH=python python fixtures/sparse_frontend/audit_real.py \
  --intake experiments/route_coverage/results/local_additions_20261027/intake.json \
  --routes-root /path/to/originals \
  --source-manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --prepared-root /path/to/three-prepared-clos-directories \
  --output /path/to/new/real-audit

PYTHONPATH=python python fixtures/sparse_frontend/verify.py \
  --root runs/clos_sparse_frontend_20261030 --output /path/to/new/verification.json
```

`prepared-root`下需要按公开示例生成`clos128`、`clos256`、`clos512`。第一条只读用户原件，复制必要输入并离线检查，不启动网络；第二条针对本轮保存的目录结构核验实际结果，不是研究运行器。真实数据不进入Git。发布的[独立核验摘要](verification.json)及[执行身份与检查收据](receipt.json)便于审阅，完整包仍留运行目录。

49项不同的相关CPU检查通过，含真实Scheduler的请求推进/KV释放、空slot保留、专家rank参与、全MoE GQA的零本地KV/计算、快照迁移、克隆拒绝，以及实际batch缺失/重复/错rank篡改拒绝。原有SimAI、消息预检与终态检查通过。

首次新增测试混入下一层接收依赖，修正测试的层过滤后通过；256/512首次准备因旧dense交换机计数存储超过静态RSS预算被拒绝，显式使用已有`port_dense`后通过。真实核算脚本首次有括号语法错误，之后又将历史manifest来源误当作缺少用户确认，按输入manifest的原路径与SHA身份修正；失败日志和部分输入保留。所有后续处理均离线，未重跑网络、增加预算或清理其他目录。

## 对研究的作用及下一步

这次收益是输入和系统表达更准确：能够在大Clos结构中研究少量真实请求怎样访问分散专家，且不虚构并发。它不是通信算法创新，也未测量优化百分比。下一步先校准目标通信库的Dispatch完成、flush和计算流依赖，再在三档Clos内冻结真实路由的正常/局部短时故障对照，追踪受损与未受损任务是否实际竞争同一出口。
