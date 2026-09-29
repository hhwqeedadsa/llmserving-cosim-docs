# M5.1 本地时钟与研究输出

`run.json` 复用 M3.3 的双组共享网络和单层计算输入，在同一次真实 ASTRA/UB 执行中观测交换机 8 的端口 0、4 以及交换机 9 的端口 4。各 rank 和交换机具有独立偏移、漂移；同一交换机的端口共用时钟和采样起点。输入、计算与通信仍为未校准参考模型。

按 [M5.1 环境记录](../../docs/environment.md#2026-09-29m5-1-observation-export) 配置并构建 `build/m5-observation`，从根目录运行；输出目录必须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.research run \
  --config fixtures/m5_observation/run.json \
  --backend build/m5-observation/output/cosim/ns3.44-cosim-service \
  --output-dir runs/my-observation
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.research validate-run \
  --run-dir runs/my-observation
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.research validate-observed \
  --observed-dir runs/my-observation/observed
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_research_export.py \
  --build-dir build/m5-observation --output-dir runs/my-observation-check
```

## 输入与时间

入口为 `moe-ub-research-run/1` JSON，仅接受 `execution.entry_mode=service`，调用已有动态服务程序。`inputs` 的 service、layer、system、network 路径相对于仓库根，也可为绝对路径。`run_id` 覆盖服务副本中的同名字段；`mode=fixture/research` 只声明用途。当前后端 seed 固定为 10、RngRun 为 1，清单记录实际值并核对；其他 seed 明确拒绝。

`observation.clocks` 必须恰好列出所有 rank 和被观测交换机，每个设备一项，字段为 `device_type, device_id, offset_ns, drift_ppb`。全局执行时间为非负整型纳秒；输出使用精确整数算式：

```text
local_ns = ((1000000000 + drift_ppb) * global_ns) // 1000000000 + offset_ns
```

`//` 向下取整，不经浮点数。offset 和本地结果均为有符号 64 位整数，允许负本地时间；drift_ppb 范围为 -999999999～1000000000，时钟速率严格为正，量化结果非递减。校验全局 0 与 `max_simulation_ns` 两端的结果，越界报错。

`observation.switches` 每个交换机一项，指定 `switch_id, port_ids, start_local_ns, end_local_ns, period_ns`。本地半开区间必须位于该设备从全局 0 到仿真上限的变换范围内；周期为正，每端口最多 4096 个窗口、总计最多 16 个端口。采样起点独立于时钟偏移；末窗口可不足一个周期。事件按本地发送开始时刻归窗，恰好在边界的包属于后一窗口。区间内没有发包的窗口显式写零，正常服务与网络提前结束后的静默区间也写零；失败运行不能导出为正常静默数据。

## 文件与访问边界

根输出包含 `manifest.json` 及 `inputs/graphs/observed/truth/metrics/logs`。只有根 `observed/` 可交给关联方法，目录内恰好包含以下三个文件：

| 文件 | 字段与口径 |
|---|---|
| endpoint_ops.jsonl | rank_id、每 rank 从 1 连续编号的 local_record_id、kind、start_local_ns、end_local_ns；kind 为 dispatch、combine 或 KV_TRANSFER |
| port_windows.csv | switch_id、port_id、direction、local_window_start_ns、local_window_end_ns、counted_packets、counted_packet_bytes；方向为 egress，在成功发送开始累计实际 Packet::GetSize 整包字节 |
| public_config.json | schema 版本、rank 列表、端口和本地采样区间、单位、计数位置/字节口径、时间分辨率与端侧字段白名单 |

端侧时间取自实际 ASTRA 操作阶段，不从首末包倒推。全局操作/传输/图身份、逐 Token 来源、全局时间和时钟参数不进入 `observed/`。`validate-observed` 只读该目录，拒绝隐藏字段、额外文件、符号链接及缺失/重复/乱序窗口；将目录单独复制出来也可检查。它校验可见数据格式，无法凭观测自身证明其与仿真内部事件一致。

内部 `truth/operation_registry.jsonl` 绑定操作和每 rank 的本地记录；`transfer_registry.jsonl` 保留业务来源、UB 任务及实际完成语义；`attribution_windows.csv` 仅写 `b(operation, port, local_window)>0` 的业务字节归属；`clock_models.json` 保存时钟参数。`record_provenance=false` 时前三份真值文件为空记录（CSV 仍有表头），归属校验状态明确为 disabled，观测不变。

`metrics/validation.json` 分开核对唯一业务交付和每端口时窗的整包分类总和，记录业务、应用元数据、协议开销、控制包与未归属字节。本阶段业务应用元数据为零；控制包没有虚构业务归属。同一数据经过多个端口会多次计入端口总量，不能与应用唯一交付直接相等。来源打开时还逐包核对任务范围和包内业务范围，缺失来源失败。

`manifest.json` 保留项目/递归依赖提交、源码及后端摘要、输入和产物摘要、实际随机参数、完成状态、计数位置、通信/计算来源和校准状态。`inputs` 保存配置和引用材料；`graphs` 保存实际生成的图。`logs/raw-run` 保存原服务、协议、ASTRA 和网络内部资料，其中旧格式的 `observed` 子目录仍是内部调试文件。`validate-run` 读取内部资料重建导出并核对摘要；它不是关联算法入口。文件夹隔离是访问约定，不是操作系统权限沙箱。

## 实际结果与限制

基准六轮仍在 264523 ns 完成，84 次真实写唯一交付 688128 字节。最终输入产生 48 条端侧记录、276 个窗口，其中 151 个零窗口。零时钟、来源关闭、重复、裁剪区间和真实包恰好落边界的运行，与不挂观测的运行保持相同业务图、实际时刻、协议及原生网络日志；来源开/关的三个观测文件完全相同。

九个正常研究运行、一个无观测对照、十五项预期失败及相关旧测试通过。普通 EP、KV 交接、空服务和 M4.3 数据输入均覆盖。真实专家路由、目标 NPU profile、完整多层执行及通信校准仍未验证；`research` 用途不会改变 `unvalidated_reference` 状态。没有关联算法、时钟估计算法或模型拟合。
