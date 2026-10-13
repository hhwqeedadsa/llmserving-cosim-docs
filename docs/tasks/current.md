# 当前任务与交接

既有 **Scale-Up 正常业务问题筛选 / C1 的 E2 机制核验**，本次授权范围已完成，状态 `completed`。已从干净工作区同步远端 `main` 至 `c3abc36`，先补查已有两组记录的完整消费者依赖，再完成固定总额度的队列组织对照。没有新建任务或里程碑，没有进入 E3/E4；按用户后续明确授权，已将本次平台与实验提交推送至 GitHub。

## 目标与边界

区分请求总容量与请求组织方式的影响。固定真实路由、128 rank、256 请求、原始第 6～8 层、完整部署容量、物理网络、放置、STORE/STORE、线性计算成本及全部核内同步。保留数据栅栏、状态到下一份准备的依赖和缓冲复用条件。

统一 FIFO 与按通信核轮转共用每端 32 个在途 STORE 额度、256 项请求编号池、一个 worker 和 4096 B 请求粒度。轮转是用于检查模型假设的参考策略，默认关闭，不代表已经确认的 A3 硬件组织。不追加容量扫描、状态优先级、同步改造、测量算法或完整 61 块/64 轮执行。

## 状态与验收

| 内容 | 状态 | 结果与证据 |
|---|---|---|
| 平台 P：M18～M21 | `completed` | 原验收保留，不重开清单；见 [M21](../../fixtures/m21_platform/README.md) |
| E1/E2 原对照与上轮 32→64 核验 | `completed` | [E1](../../experiments/scaleup_issue_screening/results/e1/README.md)、[E2](../../experiments/scaleup_issue_screening/results/e2/README.md)，旧记录与归档保留 |
| 已有最晚消费者完整依赖补查 | `completed` | [FIFO/32](../../experiments/scaleup_issue_screening/results/e2/c1-fifo32-consumers.json)、[FIFO/64](../../experiments/scaleup_issue_screening/results/e2/c1-fifo64-consumers.json)：均由状态到达后归约决定，保留本核发送、全部归约及并列条件；没有为补查重跑仿真 |
| 固定总额度参考的平台实现 | `completed` | [独立验收](../../fixtures/m19_a3_resources/native-queue-validation.json)：旧 FIFO、单核退化、多核轮转、额度与编号池、STORE/LOAD、静态核身份、全部请求范围、旧切片重放及无损压缩检查通过，提交后公开构建通过 |
| 目标 FIFO 等价基线 | `completed` | [等价报告](../../experiments/scaleup_issue_screening/results/e2/c1-organization-equivalence.json)：8082 个旧记录文件及 2688 个出口包内容/时刻指纹一致 |
| 唯一一次轮转/32 参考 | `completed` | [效应核验](../../experiments/scaleup_issue_screening/results/e2/c1-organization-effect.json)：全图请求范围、计算和固定资源一致，实际消费者重新追踪；22 项实验工具检查通过 |
| C1 去留判断 | `completed` | [结论](../../experiments/scaleup_issue_screening/conclusions.md)：保留模型内现象，降低直接将请求排队分量提出为真实测量问题的优先级，暂停新增测量研究 |
| E3、E4 | `pending` / `conditional` | 未执行，不在本次授权范围 |

## 主要结果

128 个原生发起端的实际对象回读一致；568544 个节点、121880 条计划传输和全部计算成本保持，120980 项原生 STORE 的 139139 个实际请求按业务身份、核身份和字节范围完全相同，6144 组端点/通信核的实际请求顺序也保持一致。

原两条链的前序状态形成等待从 6188、4306 ns 降至 1161、1302 ns，但前序数据栅栏增加至 11233、11396 ns。同一贡献从专家结束到数据提交的链反而增加 474、215 ns。利用保存的回调和队列进一步核对：增量主要在全部数据请求形成之前，任务仍在队列且 worker 额度占满，形成后的尾段缩短；见[分段与占用证据](../../experiments/scaleup_issue_screening/results/e2/c1-organization-waits.json)。不把该事件分段写成纯交换机时延或可消除损失。

轮转下实际最晚消费者改为第 7 层 rank 10、第 8 层 rank 34，仍分别由 rank 70、65 的状态到达后归约 500 ns 决定，再执行 92 ns 后续计算；无额外资源调度间隔。原消费者 27、58 和新来源核的完整条件均保留在 [FIFO](../../experiments/scaleup_issue_screening/results/e2/c1-organization-fifo.json) / [轮转](../../experiments/scaleup_issue_screening/results/e2/c1-organization-rr.json)中。

三层最后模型计算从 110792 降至 109847 ns，仅提前 **945 ns（0.85%）**。网络排空另行核对，实际端口分布改变；没有把局部状态等待下降或相同随机种子解释成纯队列收益或相同路径。硬件方面仍缺选定 A3 的通信核（AIV）与搬运流水（MTE）请求队列共享组织、状态到后续准备的同步对应，不用更多参数扫描代替机制依据。

## 保存、资源与提交

消费者补查提交为 `3a2800e`；平台由内向外保存为 ns-3-UB `13cf3da`、主仓 `4e5fd4d`。二进制 SHA-256 为 `41a0d9b46aab6490dda25f1552839629534da976a428d443476f9c990dd59af5`。实验程序、结果及验收时的任务页由独立实验提交 `2c94c36` 保存，不混入平台提交；其余依赖保持锁定，收尾 13 项依赖检查通过。

2026-10-13 按用户授权先推送 ns-3-UB `13cf3da` 至 `baoy21/ns-3-ub:cosim`，再推送主仓上述三项提交至 `baoy21/moe-ub-cosim:main`，远端提交回读一致。发布前工作区干净、13 项依赖检查通过，推送时的子模块可获取性检查通过；未重跑构建或仿真，其他子模块及更深依赖未改变。发布命令与回读记录见 `runs/scaleup_issue_screening/c1-organization-20261013/publication-20261013T041406Z/`，大型运行产物保留在本地。

本轮记录位于 `runs/scaleup_issue_screening/c1-organization-20261013/`。`fifo.tar.gz`、`rr.tar.gz` 及相邻清单保存完整执行、日志和队列记录；`compact-slice/` 保留可直接重放的原图、输入及无损压缩的旧执行。摘要可直接查看，无须解压；用户命令见[组织对照操作](../../experiments/scaleup_issue_screening/usage.md#c1-固定总额度的队列组织对照)。

预算保持：后端 RSS 48 GiB、地址空间 56 GiB、7200 秒；其他受监控阶段 8 GiB、12 GiB、1800 秒；累计产物 48 GiB、最低空闲 20 GiB。累计采样峰值 **47.890 GiB**，收尾取样约 **47.253 GiB**；两次后端分别约 238.44、224.22 秒，RSS 峰值约 5.503、5.506 GiB。详见[资源及归档证据](../../experiments/scaleup_issue_screening/results/e2/c1-organization-resources.json)。

旧基线及上轮补采、额度 64 归档摘要核对不变。仅释放本轮经核验的恢复副本、新 case 和小测展开副本，以及隔离构建的可再生对象文件；二进制和库保留。启动前空间预检、平台早期输入错误、旧空文件硬链接导致的首次等价读取失败均保留原因；修正读取后只重做分析，两次目标后端均只运行一次，没有提高上限。

本次授权工作及上述提交的发布已完成。继续研究需先明确上述最小机制依据或新的研究范围；不自动进入 E3/E4。本次发布授权不延伸至未来的新实现。
