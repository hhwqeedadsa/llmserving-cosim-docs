# 当前任务与交接

既有 **Scale-Up 正常业务问题筛选**本轮状态为 `completed`。已按 2026-10-14 用户授权，连续完成独立目录迁移、三类机制核对、正常分析、定向服务验证、诊断及采集成本比较、固定规则复核和最终研究判断。没有新建顶层任务，没有进入 E3/E4 或测量算法开发。

## 目标与入口

本轮围绕本地竞争与服务不足、下游阻塞及排队传播、多平面推进差异，回答等待在哪里、哪些实际业务条件受到影响，以及有限采集成本下保留什么证据。唯一要求见[实验方案](../../experiments/scaleup_issue_screening/plan.md)，研究判断见[结论](../../experiments/scaleup_issue_screening/conclusions.md)。

开始时已将远端 `main` 快进同步至 `cb9c116`，读取根指令、开发维护与实验文档，并检查主仓和依赖。当前后续改动仅本地保存，未推送。历史执行保留原版本绑定，新分析另存来源与规则摘要。

## 验收结果

| 内容 | 状态 | 结果与证据入口 |
|---|---|---|
| 分析代码、测试、配置及历史结果迁移 | `completed` | 157 个文件迁移；112 份配置/历史机器结果逐字节一致，前后各 59 项检查、32 个入口及 2156 条窗口判断等价；[迁移验收](../../experiments/scaleup_issue_screening/tests/evidence/migration.json) |
| 三类机制与正常记录覆盖 | `completed` | 四档正常来源、2688 个声明交换出口及预选六例；新精确补采共覆盖 3584 端口，接收消费反压明确未建模；[方法](../../experiments/scaleup_issue_screening/method.md)与各主题结果 |
| 平台被动事实补充 | `completed` | 有界实际队列、序列化、信用变化与真实 CBFC 判断；222 项单元检查、信用几何 C++ 检查、公开小例及重放通过；[平台验收](../../fixtures/network_facts/validation.json) |
| 正常补采与定向服务验证 | `completed` | 固定八项全部执行、导出、校验和分析通过；两档正常补采各 2688 出口报文/时刻及 8082 份原记录等价；全部控制保持图、计划传输、计算成本、资源与选路条件；[本地服务](../../experiments/scaleup_issue_screening/results/local_service/README.md) |
| 实际等待及完整完成条件 | `completed` | 每项检查所有操作最晚消费者及并列条件，继续沿实际回调和资源占用者展开；有限图内未解释的就绪至启动差为零，原生提交至推进的混合边界明确保留；[三类结果入口](../../experiments/scaleup_issue_screening/README.md) |
| 较强基线、字段作用及采集成本 | `completed` | 19 个固定条件、完整信息消融、直方图及同规则本地过滤；实际/最坏上报突发、状态、读取、更新与共同软件成本分别核算；[152 行比较表](../../experiments/scaleup_issue_screening/results/measurement_budget/metrics.csv) |
| 冻结规则复核与最终取舍 | `completed` | 256 请求正常与持续服务复核，旧 512 请求及较长前序的聚合界限复核；24 项相关检查及完整来源比较通过；[比较验收](../../experiments/scaleup_issue_screening/tests/evidence/diagnostic-comparison.json)及[最终导出等价](../../experiments/scaleup_issue_screening/tests/evidence/diagnostic-native-lines.json)，最终研究候选数为零 |

## 研究结果与适用边界

正常短时出口竞争得到确认。直接、共享及持续服务对照分别使 1024 请求最终通信完成增加 7062、729、394263 ns，256 请求持续服务复核增加 371080 ns；完整依赖核验支持真实业务作用，不能将累计包等待直接相加成可消除损失。

预选低发送量点没有实际阻塞，八项捕获均无失败信用检查；接收应用消费反压属于未覆盖。多平面正常进度差主要在到达时已存在，两侧服务对照保留实际生效及有限业务作用，不能用整段进度代替服务或必要完成条件。

事件触发比稀疏取窗保留更多短事件，但相对完整聚合加本地过滤仍有漏检、服务证据遗漏和持续采集成本。保留机制事实及较强诊断基线，未得到足以支持新增测量算法或完整映射机制的候选。否定与未覆盖分开，完整依据在结论和结果页，本页不重复方法定义。

事实准备 `/3` 修正服务耗时的可知时刻，规则 `/3` 将额度改为预先声明的 1 ms；旧准备及评价保留，不再作为最终因果采集结果。最终规则在两项新 256 请求运行及标签打开前冻结，来源相关性明确披露。旧批控制器因冻结校验停止的原因留在运行目录，未重跑目标后端，也没有按评价样本调整门槛。

## 资源与来源

新增目标后端执行 **8/16**，编号 `r00`～`r07`，均完成；没有额外目标启动或失败重试。每次用途及启动前登记见 `runs/scaleup_issue_screening/diagnostic-study-20261014/target-runs.json`。

| 资源 | 原批准上限 | 本轮实测摘要 |
|---|---|---|
| 目标后端 | 进程组采样 RSS 48 GiB；地址空间 56 GiB；7200 秒 | 峰值 RSS 7.531 GiB，单次后端约 273～491 秒 |
| 准备、导出、分析和校验 | RSS 8 GiB；地址空间 12 GiB；1800 秒 | 已留存分析峰值 RSS 3.488 GiB；单次目标导出约 290～560 秒 |
| 累计产物 | 64 GiB；最低空闲 20 GiB | 收尾约 59.23 GiB，16 个原计量根及新增产物一并核算；空闲约 5939 GiB |

宿主资源证据见 [resources.json](../../experiments/scaleup_issue_screening/results/measurement_budget/resources.json)，这些数值不是芯片采集成本。旧来源保持；本轮 `r00`～`r03` 的展开副本经逐文件核验后无损归档并释放，索引和原始内容仍在 `diagnostic-study-20261014/archives/`。不同时解压全部历史资料，公开恢复命令继续执行预算检查。

主要本地入口为 `runs/scaleup_issue_screening/diagnostic-study-20261014/`：`target-runs.json` 是运行清单，`final-comparison-lf.json` 是完整私有研究比较，`prepared-v3/`、`judged-final/`、`evaluated-final/` 是最终分析来源，`business/`、`deep-v3/`、`feedback/` 是业务与机制证据。详细命令、退出状态及资源日志留在对应运行目录；最终 `study.json`、`comparison.json` 和 CSV 聚合副本在四类 results 子目录，不含逐包身份和受限路由条目。操作见[使用说明](../../experiments/scaleup_issue_screening/usage.md)。

## 提交与停止位置

目录重构独立提交 `d778ae0`；平台主仓提交 `582fb8f`，对应 ns-3-UB 本地提交 `fee9fbf`；实验实现及规则修正分批提交，最终比较工具提交 `3305c62`，导出换行等价修正提交 `196b65a`。实验结果与研究判断另行本地提交，不与平台语义修改混合。依赖检出及锁定关系通过 13 项核对；该检查不冒充编译或仿真验证。

本轮授权范围已完成，停在完整研究交付。真实路由、拓扑、计算基线、请求划分、额度、核内同步与 C1 冻结条件保持；不自动开启 E3/E4、独立时间同步、接收消费模型或新的测量机制。未经新的明确授权，不推送本轮后续实现及结果。
