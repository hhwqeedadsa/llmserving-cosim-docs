# M10 二级多平面 fabric

`moe_ub_cosim.fabric` 生成固定形状的二级交换网络：每个 die 一个 DEVICE，每个 DEVICE 在每平面各有一个端口；每服务器、每平面一个 L1；同平面的所有 L1 与该平面的全部 L2 相连。全部物理设备、端口、交换机和链路保留，包括不参与请求的端点。没有芯片内部链路，同芯片交付继续由已有本地模型执行。

[target.json](target.json) 对应场景规格的 48 服务器、384 芯片、768 die、7 平面、每平面 16 个 L2，活动组为服务器 0–7 的 128 rank。[small.json](small.json) 保留相同结构，缩为 3 服务器、每服务器 2 芯片/4 die、7 平面和每平面 2 个 L2。两组共 6 个活动 rank，另有 6 个非活动端点，第三服务器全部空闲。目标全拓扑仅做离线核对；真实联合执行使用小网络。

## 输入与编号

fabric/1 输入严格包含 schema_version、shape、groups、budget。shape 的字段为 servers、chips_per_server、dies_per_chip、planes、l2_per_plane。groups 每项填写 group_id、rank_ids、rank_to_ub_node，rank 是独立逻辑身份，不必等于 UB node；不同组的 rank 和端点不得重叠。所有计数为正整数，rank/端点为非负整数。

设 D=chips_per_server×dies_per_chip，N=servers×D，P=planes，H=l2_per_plane，编号从零开始：

| 对象 | 编号/端口 |
|---|---|
| 设备 | node=server×D+local_die；chip=node//dies_per_chip；die=node |
| L1 | node=N+server×P+plane；下行口=local_die；上行口=D+spine |
| L2 | node=N+servers×P+plane×H+spine；端口=server |
| 平面 | DEVICE 的 port=plane；两级交换机和链路均只属于该 plane |
| 层配置中的物理身份 | die_id=`die-{node}`，chip_id=`chip-{chip}`，server_id=`server-{server}` |

`budget` 的 max_nodes、max_links、max_route_rows、max_channels 在构造表之前检查；links 按双向物理链路计数，channels 按一行双向预建 TP 计数。当前入口另限总节点不超过 4096、每种 shape 参数不超过 128、L1 端口 D+H 不超过 128。预算是数量约束，不代表宿主 RSS 上限。链路和节点时序来自 [defaults.json](defaults.json)：224 Gbit/s、20 ns 链路延迟、1 ns 仲裁、0 ns 输入处理，仅为未校准参考值。

路由目标用原生支持的连续 node 区间压缩，不把目标逐个展开成数百万行。每个 DEVICE 在每个目标平面的出口固定为该平面端口；同服务器经过一个 L1，跨服务器在源 L1 的所有 H 个上行口间保留等长候选，再经 L2 和目标 L1。所有设备都有可达路由，包括非活动设备。通道只覆盖活动组内的跨芯片端点对，每对每平面一行、双向 TPN 各自在本端唯一；同芯片及组间不建业务通道。

完整目标的清单为 1216 节点、10752 链路、21504 端口、47152 压缩路由行和 56448 双向通道；768 设备中 128 活动、640 非活动，交换机为 336 L1 和 112 L2。

## 场景和实际后端

新场景使用 `moe-ub-scenario-run/2`，其余字段沿用 [场景入口](../m6_scenario_backend/README.md)，network 改为：

```yaml
network:
  fabric_file: small.json
  defaults: {rate_bps: 224000000000, delay_ns: 20, allocation_ns: 1, forward_ns: 0}
  nodes: []
  links: []
```

文件路径相对于场景 YAML。需要显式多组/层段声明（configured-service/3、/4 或对应 evidence-service）；各组、各层的 rank 映射与 deployment 必须和 fabric 完全一致。不会根据模型名字推断物理部署。`nodes/links` 仍只覆盖已有节点时序及已有链路速率/延迟，不能改变连通关系。旧 scenario-run/1 保留 case_path、plane_id 和单候选 CSV 校验，不会隐式升级。

解析器生成 CSV，保存 fabric-audit.json、输入/生成器摘要，以及新增 fabric 字段的 `moe-ub-backend-config/2`。C++ 独立核对几何连接、预算及服务部署；在激活任何业务前，读取所有实际节点/链路、各设备/交换机的最短路候选、所有端点 TP、双向 TPN 与端口，包括非活动设备。在 effective-backend/2 的 fabric_audit 中保存查询数及实际通道绑定。每份动态计划再次核对物理归属。旧 backend-config/1 行为不变。

固定 UB 的选路分两步：`UbApp::ExecuteTraffic` 调用 `TpConnectionManager::GetTpns`，后者在 EnableMultiPath=false 时由 `ReconstructTPs` 用原生随机数从最短 TP 候选中选一个，因而一次 WQE 使用一个平面；交换机的 `UbRoutingProcess` 再以 HASH 选择该平面内的最短出口。seed/run 沿用场景显式值。不把多平面改写为多 TP 同时传输，不启用 packet spray，不按内部 operation 来源选路。WRITE/RTP、优先级 7、CBFC/SP、关闭 CC/重传/故障等约束不变。

observation 仍接受原始后端配置（port/1、set/1、set/2）或 null；研究 clocks/switches 配置须先经 `backend_observation()` 转换。超过旧 16 端口限制时使用 [观测预算](../m10_resources/README.md)及完整模型图预算。`resolve` 只生成并核验输入，`run` 才进行真实联合执行。生成器的 target_scale_executed=false 不充当运行证明。

## 已验证入口

从主仓库根目录运行；输出目录必须不存在，audit-existing 除外。离线命令不加载模型、Chakra 图或 UB 网络：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.fabric \
  --input fixtures/m10_fabric/target.json --defaults fixtures/m10_fabric/defaults.json \
  --output-dir runs/target-fabric-example

PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.fabric \
  --input fixtures/m10_fabric/target.json --defaults fixtures/m10_fabric/defaults.json \
  --output-dir runs/target-fabric-example --audit-existing

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_target_fabric.py \
  --build-dir build/m10-fabric --output-dir runs/fabric-check-example
```

集成脚本从已提交的四块小模型派生服务和层配置，保存 reference.yaml 等可重放输入；实际通过 scenario run 调用新后端。每次只有一个共享 UB 网络。基准、来源关闭、1 Gbit/s 扰动和同服务器对照均完成 5 轮、305 次 UB 写和 123 次本地交付。

小网络有 47 节点、126 链路，观测全部 168 个交换机出口。原生实际路由查询 1344 项、28 行双向通道的 56 个本端 TP 对象均匹配；七个平面全部出现业务，跨服务器实际使用两种 L2 候选。同服务器业务包只经过一个交换机出口，跨服务器经过三个；111 个业务包的 UID 路径均满足编号与平面约束。同芯片交付不出现在 fabric 来源记录中。

基准结束于 33689 ns，1 Gbit/s 对照结束于 1999784 ns；ep0 下一轮提交从 12152 ns 推迟到 764064 ns。出口队列总等待从 432 ns 增至 260792 ns。逐端口包字节与原生计数器相同；跨服务器业务字节出口访问总量为 340992 B，即 113664 B 独立交付各经过三个出口。同服务器对照为 113664 B，不能把两种访问计数都当成独立业务量。来源关闭后，执行结果、每端口包/时刻指纹、逐包及队列文件不变。

错误物理归属、缺链路、跨平面路由/TP、错误链路归属、通道预算不足、缺节点和服务映射漂移均被拒绝。所有图完成，发送槽与待处理事件归零，网络排空且 KV 释放；图/传输记录仍驻留，预建 TP 保留供服务复用。命令、RSS/墙钟、开发期失败及旧路径回归见 [environment.md](../../docs/environment.md)。完整 R1 图的宿主资源、足够独立真实请求和最终目标组合验收仍待后续，模型与硬件校准状态不变。
