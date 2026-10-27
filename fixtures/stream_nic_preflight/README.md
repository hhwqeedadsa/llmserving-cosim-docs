# 历史阶段：持久连接唯一NIC准入

本文保留`10e71d1`阶段的限制和验收。后续[物理NIC绑定](../physical_nic_binding/README.md)已实现同plane多rail；下文“当前”均指当时版本。这里的`after`脚本断言旧限制，只能配合该阶段归档的Python源码复核，不能对现行源码运行并期待同样拒绝。原证据不改写为新功能证据。

当前拓扑生成器能表达同plane多rail，而持久流/事务/现代MoE入口的固定原生Open要求正反向都只有声明NIC。旧Python检查只验证端口存在、plane和端点连通，会接受原生必须拒绝的输入。本次在启动前补齐同一限制，未实现多rail绑定、改变选路或新增研究算法。

最小反例为双pod、双rail、单plane，0→4有两条6跳最短路径，源端口分别1/2。修复前的stream校验和ModernNativeLayout均接受；固定原生路由会得到[1,2]，Open却要求[1]。这是源码与独立路径枚举确认的必拒绝条件，**本轮未执行旧版原生失败网络**。[范围与预算](../../docs/tasks/stream_nic_preflight_spec.md)、[修复前见证](before.json)、[公开入口验收](verification.json)保留原始事实。

纯模块`scaleout_stream_routes.py`缓存每个目的/plane的无权最短跳距离，GPU不作中转；逐stream验证源/反向NIC集合。单NIC交换机内部ECMP、双plane，以及多个物理NIC中只有一个是最短路时都保留。声明端口不是唯一最短路、多候选或不连通则明确拒绝。校验链覆盖stream→transaction→modern/native session，普通SEND/RECV服务不调用本模块。

在仓库根用新目录复核公开操作：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir runs/my-nic-check-monitor --artifact-root runs/my-nic-check \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 134217728 \
  --wall-seconds 60 --min-free-bytes 8589934592 -- \
  python3 -B -m fixtures.stream_nic_preflight.checks after \
  --repository /home/lry/moe-ub-cosim \
  --previous /home/lry/moe-ub-cosim/fixtures/stream_nic_preflight/case \
  --output /home/lry/moe-ub-cosim/runs/my-nic-check
```

脚本从空cwd执行4条公共命令：topology成功；stream/transaction/modern三入口拒绝，均未创建运行输出或调用marker假后端。假后端只是负向启动探针，不返回仿真成功结果；正向双plane只检查纯准入。`before`模式用于修复前的历史源码，不是当前复核入口。

8项新单元条件包括64种四交换机子图的独立简单路径枚举（128个方向集合）及反向歧义/多物理NIC/交换机ECMP/plane/GPU中转等反例；另跑69项受影响stream、transaction、modern等回归。没有新包级网络或性能测量。完整命令、退出码和监控留在`runs/stream_nic_preflight_20261027/`，预算300秒/128MiB，失败保留、不加限。

真正支持同plane多rail，下一增量至少需要：以实际NIC身份区分端点地址和目的可达性；显式绑定发送QP/接收及ACK/CNP返回NIC；在共享Agg/MMU中转发而不复制共享资源；路由更新后保留持久QP/序号/拥塞状态，不能免费改绑。不能删除Open断言或靠端口hash凑结果，也不能把共用交换机的rail简单改成独立plane。应独立规范、构建、功能验收后，再做固定资源的网络收益对照。该缺口不是本项目创新，更不能据此声称竞品缺少多NIC支持。
