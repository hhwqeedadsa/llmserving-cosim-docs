# 可配置的千卡电交换网络

`moe-ub scaleout topology` 生成 GPU—ToR—Agg—Core 的物理连接、NIC 端口表、带宽及连通性审计，并导出 ASTRA 原生 ns-3 和 SimAI/Alibaba 的文本拓扑。ToR 是接入交换机，Agg 是 Pod 内汇聚交换机，Core 是跨 Pod 的普通电交换机。用户第二张图中的 OCS（光路交换机）在这里用 Core 替换，没有光路重构过程。

仅需 Python 3.10+ 标准库，无需 GPU、模型权重或原生构建。在仓库根目录使用一个新输出目录：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_topology/small-clos.json \
  --output runs/topology-small-new
```

安装基础包后可把前面的 Python 调用换为 `moe-ub`。CLI 路径相对工作目录；JSON 没有外部路径、继承配置或隐藏默认值。修改配置后用另一个输出目录重跑，已有目录拒绝覆盖。未知字段或违反 NIC 带宽、端口、节点/边预算时直接拒绝。

本命令生成物理输入，**不执行通信，也没有把非 UB 网络接入 `moe-ub run`**。`generated_not_executed` 是实际状态，不能据此报告千卡仿真延迟、拥塞或吞吐。旧 `scaleout prepare` 是 UB 输入，`scaleout capacity` 是单 NIC/两层均匀通信量核算；三者不能互换配置。

## 配置入口

| 文件 | GPU / 服务器 | ToR / Agg / Core | 平面 / rail | 链路数 | 任意 GPU 对有分组网络路径 |
|---|---:|---:|---:|---:|---|
| [small-clos.json](small-clos.json) | 8 / 4 | 4 / 4 / 2 | 1 / 1 | 20 | 是 |
| [1024-leaf-spine.json](1024-leaf-spine.json) | 1024 / 128 | 32 / 0 / 32 | 1 / 1 | 2048 | 是 |
| [1024-clos.json](1024-clos.json) | 1024 / 128 | 128 / 128 / 64 | 1 / 1 | 3072 | 是 |
| [1024-hpn-inspired.json](1024-hpn-inspired.json) | 1024 / 128 | 64 / 32 / 8 | 2 / 8 | 2688 | 是 |
| [1024-multirail.json](1024-multirail.json) | 1024 / 128 | 32 / 64 / 32 | 1 / 8 | 1408 | 否；同 rail 可通 |

千卡运行只需把 `--config` 换为表中路径。四套输入都是不同结构的研究配置，端口、带宽和资源不同，**不是等成本性能对照**。修改数量时需要同时检查端口与超售关系。

三层 Clos 示例采用 k=16 的连接关系：16 Pod，每 Pod 8 ToR、8 Agg，64 个跨 Pod Core，每 Agg 连同一分组的8个 Core；每个 ToR 接一台八卡服务器。链路均为400G，ToR/Agg 的下上行容量比均为1。它生成1024个 GPU 端点，不模拟服务器内 NVLink。

HPN 参考示例有2 Pod、每 Pod 2 Segment、每 Segment 32台八卡服务器。8 rail 对应 GPU 槽位，双平面每个 NIC 各出200G，总预算400G；每 Pod/plane 8 Agg、每 plane 跨 Pod 4 Core。ToR 下/上容量比2，Agg 比值4，存在超售；不等于原论文的生产配置。

独立 multi-rail 示例每个 GPU 只有其槽位对应的一张 rail NIC，上层也按 rail 隔离。1024 GPU 分成8个各128 GPU的网络域，458752个无序 GPU 对没有直接分组路径。全局 All-to-All 需要增加 NIC，或显式建模服务器内传输和 GPU 中继；本生成器不会把 GPU 当交换机补出路径。若改为 `upper_tier_scope=shared`，不同 rail 的 ToR 可经共享 Agg/Core 互通，这仍与独立 rail 的容量/竞争结构不同。

## 参数及作用范围

schema 固定为 `moe-scaleout-topology/1`；所有字段必填，下面给出单位与约束。

| 对象 | 字段 | 含义 |
|---|---|---|
| `deployment` | `pods`, `segments_per_pod`, `servers_per_segment`, `gpus_per_server` | 正整数；同层采用相同规模，GPU数是四者乘积。Segment 是 Pod 内的接入分区，不另增交换层 |
| `deployment` | `gpu_type` | `A100/A800/H100/H800/NONE`，仅用于锁定 SimAI reader 的分类标签；不生成计算 profile 或验证显存 |
| `fabric` | `rails`, `planes` | rail 为 NIC/ToR 分组，plane 为独立交换机网络副本，二者分别配置；每 NIC 每 plane 一个端口 |
| `fabric` | `tors_per_segment_per_rail` | **每 plane、每 Segment、每 rail** 的 ToR 数 |
| `fabric` | `upper_tier_scope` | `shared`：每 Pod/plane 共享 Agg，跨 Pod 共享 Core；`rail`：Agg/Core 也分别归属 rail |
| `fabric` | `aggs_per_pod_per_group` | 每 Pod/plane/group 的 Agg 数；0时直接 ToR→Core。group 在 shared 时为1，在 rail 时为 rail 数 |
| `fabric` | `cores_per_group` | 每 plane/group 的跨 Pod Core 数，**不乘 Pod 数**；0时只有 ToR→Agg |
| `fabric` | `tor_uplinks` | 每 ToR 的上联数；有 Agg 时循环均衡连接 Agg，否则连接 Core |
| `fabric` | `agg_uplinks` | 每 Agg 的 Core 上联数；无 Agg 或无 Core 时必须为0 |
| `fabric` | `core_wiring` | `full_mesh/grouped/cyclic/none`，详见下文 |
| `nic` | `rails_by_gpu_slot` | 长度等于每服务器 GPU 数；每行是该 GPU 的 NIC rail 列表，如 `[[0],[1]]` 或 `[[0,1],[0,1]]`。每行不能重复 rail，所有 rail 必须被使用 |
| `nic` | `total_rate_bps` | **每张 NIC** 全部 plane 端口速率之和的预算，非每 GPU 所有 NIC 的总和 |
| `links` | `access/tor_agg/agg_core/tor_core` | 四份显式链路配置，每份 `rate_bps`（整数bit/s）、`delay_ns`（整数ns）；未使用的层不生成边。速率按每个方向解释；损失率固定0 |
| `port_limits` | `gpu/tor/agg/core` | 每类节点的逻辑链路/设备端口上限，1..1024；不能直接当物理 cage 或 breakout 数 |
| `budget` | `max_nodes/max_links/max_switches` | 生成前检查的数量上限；硬上限4096 GPU、8192节点、131072边。不是 ns-3 仿真内存限额 |

设 P=Pod 数、S=每 Pod 的 Segment 数、H=每 Segment 服务器数、G=每服务器 GPU 数、R=rail 数、L=plane 数、T=每 Segment/rail 的 ToR 数、A=每 Pod/group 的 Agg 数、C=每 group 的 Core 数，Q 在 shared 时为1、rail 时为R：

- GPU = P×S×H×G；ToR = P×S×R×T×L。
- Agg = P×Q×A×L；Core = Q×C×L。
- access 边 = 服务器数×每服务器全部 GPU 的 NIC 数×L。
- 总边 = access 边 + ToR数×`tor_uplinks` + Agg数×`agg_uplinks`。

GPU ID 为0到GPU数减1，按 Pod、Segment、服务器、GPU槽编号，同一服务器的 GPU 连续。服务器在 Segment 内编号模 T 决定接入哪一个 ToR；NIC rail 决定 rail，plane 决定网络副本。本版不支持逐台异构接线覆写或同节点对平行链路，后者会覆盖固定 reader 的 `nbr2if` 映射。

Core 接线在每个 plane/group 内进行，每个 Pod 重复相同规则。lower index 是 Agg 的索引；无 Agg 时，shared 下是 Segment→rail→ToR 的展开索引，rail 下是 Segment→ToR 索引。设每个 Pod/group 的 lower 数为N、上联数为U：

- `full_mesh`：U必须等于C，lower连全部Core。
- `grouped`：C必须等于N×U，第i个lower连 `[i×U, (i+1)×U)` 的Core；用于标准分组 Clos。
- `cyclic`：第i个lower连 `(i×U+j) mod C`，j从0到U减1；可用于非标准数量。仍须审计连通性，不保证无阻塞。
- `none`：仅用于无Core的ToR—Agg；多 Pod 此时默认互不相连。

ToR→Agg 同样按循环分配；上联数等于A时即全互连。少量上联可能使某些交换机闲置或网络分裂，程序保留实际结构并输出警告，不自动改线以满足“可通”。

## 怎样改带宽和规模

例如将 HPN 参考配置的每平面 GPU 接入从200G改为100G，修改 `links.access.rate_bps=100000000000`；两平面端口合计200G，不会自动用满NIC的400G预算。要改为每平面400G，须同时把 `nic.total_rate_bps` 明确提高到至少800G，否则拒绝生成。不同层的速率与延迟分别修改，接入速率不会自动覆盖 Agg/Core 链路。

增大 Pod 数会同时增加 GPU、ToR、Agg，但保留 Core 数；增大 `cores_per_group` 只增加 Core。使用 grouped 时还须保持分组等式；使用 full_mesh 时需同时更新对应上联数。端口上限不会被自动提高。

多 NIC 的 `total_rate_bps` 按 NIC 分开计算，GPU 可能有多份预算；当前没有 GPU PCIe 或 NIC 共享队列的运行时服务模型。双平面交换机之间没有跨平面链路，GPU 是两平面的共同端点；可达不等于已经实现分流、故障切换或自适应选路。

## 输出与检查

| 文件 | 用途 |
|---|---|
| `config.json` | 输入原文，保留有效参数 |
| `topology.json` | 带 Pod/Segment/rail/plane 位置的节点、物理边、两端逻辑端口与 NIC 映射 |
| `gpu_nic_ports.csv` | GPU→NIC→rail→plane→ToR 及端口，便于检查接线或制作后续rank映射 |
| `audit.json` | 数量、NIC预算、每交换机端口及下/上带宽、交换机连通域、不可达GPU对、固定数组开销、警告和能力边界 |
| `topology.astra.txt` | 原生 ASTRA reader 的三字段头：节点数、交换机数、边数；下一行交换机ID，再每行五字段边 |
| `topology.simai.txt` | SimAI/Alibaba reader 的六字段头：节点数、每服务器GPU数、NVSwitch数=0、交换机数、边数、GPU类型；后续交换机ID与边 |
| `manifest.json` | 输入、生成器与所有其他输出文件的 SHA-256；不含机器路径和时间，便于逐字节复现 |

边格式为 `src dst rate_bpsbps delay_nsns error_rate`，实际例如 `0 8 400000000000bps 1000ns 0`。端口从1编号，预留原生 InternetStack 的 loopback设备0；这些是按安装顺序推导的预期编号，尚需原生装载核对。

连通性在**交换机子图**内核算，两个 GPU 需附着同一分组交换网络域；不能经第三个 GPU 转发。审计的 `down_to_up_ratio` 仅为各交换机容量之和的比值，Core无上联时为null；它不是测得吞吐，也不是拓扑的精确二分带宽。

确定性小图、独立路径检查、非法输入、两个导出格式及千卡数量检查：

```bash
PYTHONPATH=python python3 -m unittest discover -s tests/unit -p 'scaleout*_test.py' -v
```

结构依据、两图对应关系和验证边界见[拓扑说明](../../docs/reviews/scaleout_topology_20261015.md)。
