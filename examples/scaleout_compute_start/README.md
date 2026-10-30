# 128端点Clos中的实际计算开始

本例检查备用计算与原计算共用GPU时的队列记录。四份不同人工路由、两个EP64完整副本、两层两步；128端点单rail Clos、每层400Gbps、每链路1us。只选这一档规模。专家单位成本显式设为50us，第一步原请求1的第一层选择专家12至19，使rank7承担原专家工作；这是一项可解释的功能压力条件，没有真实GPU成本或完整R1性能依据。

仓库根目录执行，替换示例绝对路径：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_compute_start/prepare.py \
  --output /path/to/new-input
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new-input/service-actual-observe.json \
  --protocol /path/to/new-input/rdma-transient.json --topology /path/to/new-input/topology \
  --backend /path/to/new/native-service --protoc /path/to/protoc \
  --output /path/to/new-observe
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-check \
  --run /path/to/new-observe
```

第二组将配置换成`service-actual-helper.json`，输出换为新的`/path/to/new-helper`目录，然后同样检查。两组都有GPU6前20us目的数据丢弃，只有备用策略不同。副本、完整流声明、计算成本、资源额度、阶段门限、选择性重传和参考DCQCN相同。父示例生成的其他配置仅保留为输入生成来源，本例两组使用`service-actual-*`及同一个`rdma-transient.json`。

`node_issue`是原任务提交，新增`node_compute_start`是实际FIFO开始，`node_complete`是实际完成。比较`shared_compute.tasks.ready_ns/start_ns/done_ns`，以及原贡献`compute_begin_ns`和备用`compute_begin`；排队时间不计作实际计算成本。GATE保持独立，不占计算队列。

准备只生成输入，不执行网络。新机器使用公开`scaleout rdma-build`；旧后端不识别`--execution-events-v2`时必须升级构建，不能改回旧模式掩盖差异。默认事件`/1`保留旧口径。本例不以备用使任务变快作为验收条件，也不据此宣称新恢复算法收益。
