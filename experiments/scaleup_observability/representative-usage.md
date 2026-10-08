# 完整首轮代表性验证操作

本入口针对[代表性方法](representative-method.md)指定的完整来源，生成正常与 50% 出口服务的两组配置，通过平台公开 `replay` 重跑后端，再流式比较完成时间、事务和三个出口。它保留已有完整图及其依赖，不运行前端调度或重新下载路由。

需要当前后端、来源摘要匹配的首轮切片，以及已完整展开且属于该切片的共享图缓存。目录可以自选；下例是本机已有来源位置。缺少这些大型输入时先按[平台首轮操作](../../docs/user_guide.md)准备，不能用其他小输入或不完整缓存替代。

从仓库根目录执行，新的 `study_root` 应不存在：

```bash
sim_python=.venv/llmservingsim-native/bin/python
sim_backend=build/m17-send-state/output/cosim/ns3.44-cosim-service
source_slice=runs/v04-platform/m16-target-slice
source_cache=runs/mapping_value/full/graph-cache
study_root=runs/scaleup_observability/my-representative-study

"$sim_python" -m experiments.scaleup_observability prepare-representative \
  --slice "$source_slice" --cache "$source_cache" --backend "$sim_backend" \
  --role l1_up --output "$study_root"
"$sim_python" -m experiments.scaleup_observability preflight-representative \
  --study "$study_root" --output "$study_root/preflight.json"
"$sim_python" -m experiments.scaleup_observability run-representative \
  --study "$study_root" --case normal
"$sim_python" -m experiments.scaleup_observability run-representative \
  --study "$study_root" --case service50
"$sim_python" -m experiments.scaleup_observability compare-representative \
  --study "$study_root" --output "$study_root/comparison.json"
```

`--role` 支持 `l1_up`、`l2_down`、`access_down`，分别是 L1 上行、L2 下行、设备接入下行；位置由完整拓扑关系决定。本阶段依照已完成的小场景比较选择 `l1_up`。更改角色是在新目录开展另一项对照，不能覆盖本阶段冻结配置。

`prepare-representative` 检查固定来源、缓存绑定和公开切片完整性，只准备输入。`preflight-representative` 为接下来的一次运行核算剩余空间。`run-representative` 还会自行预检，依次执行公开重放、校验与流式统计；`service50` 要求正常基准成功且完成时间符合固定来源。已有运行目录不会自动重跑。

完整后端预算为 RSS 48 GiB、地址空间 56 GiB、7200 秒；其他阶段为 RSS 8 GiB、地址空间 12 GiB、1800 秒。每次预留 6 GiB，所有已登记产物、来源包和共享缓存计入累计 48 GiB，并保持至少 20 GiB 空闲。第二组是否可启动，以第一组完成后的新预检为准。

| 输出 | 用途 |
|---|---|
| `study.json`、`inputs/`、`observation.json` | 固定来源、后端、条件、方法、实现及观测范围 |
| `preflight.json`、`runs/<case>/preflight.json` | 准备时与逐次启动前的空间核算 |
| `runs/<case>/replay.json`、`run/manifest.json` | 实际平台调用配置、来源与覆盖信息 |
| `runs/<case>/analysis/summary.json` | 流式统计的事务、节点、计算与所选出口结果 |
| `runs/<case>/complete.json` | 成功执行、校验与分析的摘要绑定 |
| `comparison.json` | 同切片、同工作量的两组比较 |
| `commands/` 与各 `probe.json` | 实际命令、退出状态和资源记录 |

分析结果属于研究者核验。三个出口的聚合窗口不能代表全网络，也不能重建精确信用停发。命中字节只统计完全处于声明区间内的窗口，边界窗口不计入；业务归属来自单独的内部记录，不能直接交给被动算法。

失败时保留日志和目录，先区分后端、导出、校验或分析阶段；不要为了重新执行而删除失败目录、扩大预算或修改冻结来源。仅阅读结果或整理文档不需要再次执行上述完整运行命令。
