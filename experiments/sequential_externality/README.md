# 两步完整备用与独立健康任务

[结果网页](results/20261030/report.html)、[结果摘要](results/20261030/README.md)、[事前方法](protocol.md)。研究的是完整备用额外流量是否拖慢独立MoE任务；这是成熟机制的固定参数对照，未证明新算法收益。

## 准备和执行

从仓库根目录使用现有前端环境、匹配的原生RDMA后端、protoc和已校验本地原件。没有自动下载、安装或构建。数据清单包含原四份与十一份用户提供原件；后者远端固定修订仍未知。`prepare.py`逐文件校验SHA并拒绝两份缺层输入，再调用公开[双任务准备入口](../../examples/scaleout_multicall/README.md)的`--decode-steps 2`，规划全部实际步骤和可能备用的连接/消息。完整准备例：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/sequential_externality/prepare.py \
  --known-manifest data/moe-trace-small/manifest.json \
  --intake experiments/route_coverage/results/local_additions_20261027/intake.json \
  --backend runs/delayed_whole_20261030/build/ns3.42-moe-rdma-service \
  --output runs/sequential_whole_20261030/study
```

随后根据`frozen.json`的四份`cases`映射，分别用公开`scaleout rdma-run`执行，每个输出目录必须不存在。以control-normal为例：

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config runs/sequential_whole_20261030/study/input/service-control.json \
  --protocol runs/sequential_whole_20261030/study/input/rdma-multicall-normal.json \
  --topology runs/sequential_whole_20261030/study/input/topology \
  --backend runs/delayed_whole_20261030/build/ns3.42-moe-rdma-service \
  --protoc /home/lry/tools/protobuf/bin/protoc \
  --output runs/sequential_whole_20261030/study/control-normal
```

treatment改用`service-multicall.json`；local改用`rdma-multicall-local.json`。保持四条件各一次，执行前落实事前方法的累计预算和原生限制。成功网络不重跑；公开`rdma-check --run <目录>`只核验保存原件，不执行网络。本次首份网络成功但旧检查器后检查失败，修复后只做离线检查并补存报告；原始失败记录保留，见[平台修复依据](../../fixtures/sequential_whole/verification-correction.json)。

历史目录已经存在时，复现应使用新的准备、网络和分析输出目录，不能覆盖本次证据。换机器需要原始文件、匹配依赖与后端、protoc及有效本地路径，重新准备并冻结；不能仅改历史冻结JSON的绝对路径。原始路由和完整原生大文件不随网页发布。

## 独立分析与网页

`analyze.py --frozen <冻结文件> --output <新分析目录>`从原件逐步重建消息矩阵、声明计算与KV，核对全部图、门限、源/接收字节、实际确认及原生定时/控制前缀。它独立推导每组下一步图名，不使用策略消费者生成期望。完整包与队列钩子按入队后/出队前语义还原操作后占用，逐端口核对最终0。

`mechanism.py --frozen <冻结文件> --analysis <分析目录> --output <新机制目录>`从局部故障两份实际结果中枚举健康第二步每个门限的全部最晚成员和紧约束前驱，追踪其真实源确认、最后数据包、逐跳驻留和原有余量。结果不是用所有包累计等待代替业务损失，也不输入运行策略。

`plot.py --analysis <分析目录> --output <新图目录>`读取CSV生成两幅PNG/SVG。`report.py --analysis <分析目录> --mechanism <机制目录> --figures <图目录> --frozen <冻结文件> --output <新网页目录>`组装本地可打开网页、JSON及12份CSV。绘图使用本机已有Matplotlib，没有新增依赖；SVG固定hash salt并去时间戳/行尾空格，CSV归一LF。

各入口只使用公开平台接口或读取保存数据，不导入测试驱动、不依赖上一轮结果产生本轮流量。准确实际命令、平台/方法提交、后检查修复、各原生终态、哈希和资源见结果收据；预算失败保留，不自动扩限。
