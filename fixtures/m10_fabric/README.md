# 二级多平面网络的测试输入与结果

本目录验证 `moe_ub_cosim.fabric` 生成的二级交换网络。每个 die 对应一个 DEVICE，分别接入各平面；每服务器、每平面一个 L1，同平面 L1 与全部 L2 连接。不参与请求的端点仍保留节点、端口、链路和路由。同芯片不同 rank 的交付由本地事件处理，没有芯片内部网络链路。

通用编号、路由规则及目标参数见[CM384/R1 场景](../../docs/scenarios/cm384_r1.md)，后端参数和完成事件见[后端说明](../../backend/README.md)。本目录保留网络实例、独立核对和小型运行证据。最初只离线生成完整目标；后续完整首轮已通过，见[最终验收](../m10_research_slice/README.md)。

## 输入实例

| 文件 | 网络与活动成员 |
|---|---|
| [target.json](target.json) | 48 服务器、384 芯片、768 die、七平面，每平面 16 个 L2；服务器 0～7 的 128 rank 活动，其余 640 端点保留。 |
| [small.json](small.json) | 三服务器、每服务器两芯片/四 die、七平面、每平面两个 L2；两组三 rank，六个非活动端点，第三服务器全部空闲。 |
| [defaults.json](defaults.json) | 224 Gbit/s、20 ns 链路延迟、1 ns 仲裁、0 ns 输入处理，均为未校准参考值。 |

完整目标应生成 1,216 节点、10,752 双向物理链路、21,504 端口、47,152 压缩路由行和 56,448 行双向预建通道；交换机为 336 个 L1、112 个 L2。通道只覆盖活动组内的跨芯片端点对，不为同芯片或组间创建业务通道。

`moe-ub-fabric/1` 的数量预算在构造表前检查，不能代替宿主内存预算。逻辑 rank 与 UB 节点允许不同编号，服务每组、每层的映射和部署必须与 fabric 一致。`scenario-run/2` 使用 `network.fabric_file`，其路径相对场景 YAML；`nodes/links` 只覆盖已有对象的时间或速率，不改变连通关系。旧 `scenario-run/1` 的单候选 CSV 规则保留。

## 离线生成与小场景检查

从仓库根目录使用已安装的 Python 环境；生成和检查输出目录必须不存在，`--audit-existing` 用于重新核对已生成目录：

```bash
PYTHONPATH=python python -m moe_ub_cosim.fabric \
  --input fixtures/m10_fabric/target.json --defaults fixtures/m10_fabric/defaults.json \
  --output-dir runs/target-fabric-example

PYTHONPATH=python python -m moe_ub_cosim.fabric \
  --input fixtures/m10_fabric/target.json --defaults fixtures/m10_fabric/defaults.json \
  --output-dir runs/target-fabric-example --audit-existing

PYTHONPATH=python python tests/integration/check_target_fabric.py \
  --build-dir build/m10-fabric --output-dir runs/fabric-check-example
```

前两条只生成和检查输入，不运行模型或网络。集成脚本需要兼容的原生构建，从四块小模型派生服务，并通过场景入口实际执行同一套 ns-3-UB 网络。

## 小网络验证结果

小网络有 47 节点、126 链路，观测全部 168 个交换机出口。原生实际查询的 1,344 项路由、28 行双向通道对应的 56 个本端 TP 均匹配。七平面全部出现业务，跨服务器实际使用两种 L2 候选；111 个业务包的 UID 路径均符合编号和平面规则。同服务器经过一个交换机出口，跨服务器经过三个；同芯片交付不产生网络来源记录。

基准、来源关闭、1 Gbit/s 扰动和同服务器对照均完成五轮、305 次网络写、123 次本地交付。基准结束于 33,689 ns，1 Gbit/s 对照为 1,999,784 ns；ep0 下一轮提交从 12,152 ns 推迟到 764,064 ns，出口队列总等待从 432 ns 增至 260,792 ns。

跨服务器 113,664 B 唯一业务各经过三个出口，端口访问总量为 340,992 B；同服务器对照为 113,664 B。两种端口总量不能都解释为唯一交付。逐端口字节与原生计数器相同；关闭来源输出后，结果、包时刻指纹和逐包/队列文件不变。

错误物理归属、缺链路、跨平面路由/TP、错误链路归属、预算不足、缺节点和服务映射漂移均被拒绝。该阶段保留已完成图与传输记录，后续[资源工程](../m10_resource_efficiency/README.md)另验证释放。实际命令、宿主测量和开发失败可查[原记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_fabric/README.md)。
