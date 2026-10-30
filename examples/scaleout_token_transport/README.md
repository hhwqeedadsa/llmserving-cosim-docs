# 128卡Clos的按目的卡去重示例

四份不同人工路由、两个EP64组，保持E256/Top8、每卡每层4专家。特意把同一token的多个专家放在同一卡，检查Dispatch只传一份、本地专家仍全部计算，以及Combine的两种已有实现选择。这是功能输入，不是R1实测或算法收益。

从128/256/512中只选择128。准备不依赖旧运行目录或真实数据账户；需要已安装的frontend环境及当前平台原生服务。公开输入与公式见[传输规范](../../docs/tasks/token_transport_spec.md)。

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_token_transport/prepare.py \
  --output /path/to/new-inputs --combine rank_reduced/1 --local-reduce-ns-per-extra 100

PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new-inputs/service.json --protocol /path/to/new-inputs/rdma.json \
  --topology /path/to/new-inputs/topology --backend /path/to/current/native-service \
  --protoc /path/to/protoc --output /path/to/new-run

PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-check \
  --run /path/to/new-run
```

替换为`--combine expanded_per_expert/1 --local-reduce-ns-per-extra 0`，在另一个新目录准备逐专家Combine；Dispatch仍按目的rank去重。100ns是明确的人工单位成本，0也只是声明下界，均不代表GPU测量。不要因传输单元变少而减少专家计算份数。

准备输出`preparation.json`保存布局载荷、专家份数及总声明归并耗时；实际结果仍来自SEND/RECV、阶段门限和真实网络回调。`rdma-check`独立检查传输单元、专家工作、归并时长及必要前驱。源确认、接收接受、应用窗口完成和网络排空继续分列。命令执行前须按[开发说明](../../docs/development.md)选择宿主时间/RSS/磁盘预算；本例协议内原生执行限60秒、1GiB RSS、2GiB地址、5ms仿真和128MiB产物。

新模式只投影direct BF16数据布局；没有GIN计数交换/屏障包、真实CQ/GPU可见性、FP8/SF或hybrid转发。布局载荷包含元数据/填充，`rdma-arrivals`的累计接受曲线因此也包含这些字节，不能全部解释为激活张量。缺省旧模式不改变。
