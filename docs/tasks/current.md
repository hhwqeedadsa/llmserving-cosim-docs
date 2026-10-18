# 当前任务与交接

状态：`completed`。用户下载的4份R1/MMLU原件均可用，已更新实际文件清单、完成热点统计及4卡两层两轮非UB网络接入。保留原始文件，不下载更多原始路由，不push。

## 结果与证据

- 文件：1472、1808、2675、6447.json，总计8,193,641 B。用户确认来源为R1/MMLU；公开镜像固定修订目录的Git blob OID及大小逐一吻合，原件完整形状均通过，处理前后SHA-256未变化。
- 每份61层（3个Dense、58个MoE）、256专家、Top-8、128个Decode入口；prompt分别105、28、10、6。旧推荐清单在本地备份，实际清单位于忽略的`data/moe-trace-small/manifest.json`及下方版本化研究目录。
- 离线统计覆盖149个Prefill token和512个Decode token，分阶段逐层统计。例如原始层24的专家72出现179次（34.96%），以独立原件计数复核；仅描述这4请求，不作总体推断。
- 非UB实际执行原层3—4、入口1—2、EP4、每卡batch1，4GPU/2服务器/2Pod。自动及独立检查通过：76条跨rank消息、216个图节点、16条真实来源Decode层记录；跨机1,691,648 B、机内917,504 B。下一轮等待实际完成，观察结束不推断EOS。
- [完整报告、输入清单、统计摘要及复现](../../experiments/moe_trace_hotspots/results/four_requests_20261018/report.md)。完整统计和目录元数据保留于`runs/moe_trace_four_requests_20261018/`，网络原始输出保留于`/dev/shm/moe-real-four-20261018/`，Git不包含原始路由。

## 范围与验证

来源固定版本已核验；采集软件、权重精确修订、原始GPU与激活dtype仍未知。网络使用声明的BF16向量、通用片段计算/KV及参考放置，不是完整R1 Dense/MLA或硬件校准。没有训练结论、优化收益或千卡新运行。

分析按60秒、RSS1 GiB、地址空间2 GiB、产物128 MiB执行，状态completed；网络维持原小例60秒、RSS1 GiB、地址空间2 GiB、产物512 MiB和5 ms仿真限额，无扩限。平台和依赖源码未修改，不触发大规模回归。

原[HTML报告](../review_artifacts/scaleout_runtime_20261016/report.html)仅把数据访问说明改成历史时态，8条测量、6次完成和2次千卡超时保持原值；Node交互检查通过，重复生成字节一致，未做浏览器截图。来源回放平台验收仍见[前期4卡人工样例](../../fixtures/scaleout_route_replay/README.md)。
