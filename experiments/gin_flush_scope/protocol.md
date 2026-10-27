# GIN完成等待：共享范围与大EP适用性筛选

2026-10-27；起点`1ac7af3`。新增13份路由已全部确认来自R1-AWQ/MMLU；接收记录无需重做。本轮针对上一阶段尚未检查的完成前缀采样范围，不重开任何关闭的计数、网络或来源条件。

问题：GDAKI flush是否会等待同context其他通道的请求；逐peer采样是否必然在目标Hybrid配置中形成“先等较早peer，再采样较晚peer”的串行链；该链能否直接推广到千卡。普通逐peer快照/flushAsync和完整动态资源DAG都是已有强基线，不称新方法。

只用本机缓存的固定DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3` 与NCCL `12df1a11afad322be5a204a2db890161cbf8131d`。核对7文件的来源清单、tree-entry、Git blob与SHA256：DeepEP comm/barrier、comm/handle、hybrid_combine、comm/context.cpp；NCCL gin__funcs、gin_gdaki及device_gin文档。保留原固定ref来源边界，不宣称新的远端根身份验证。

固定六个条件：EP64/128/256，每域8卡，声明GIN stride为1或8。分别计算provider peer数、每lane扫描次数、同lane的活动rail peer；不能把stride1当作正常Hybrid RAIL配置。context.cpp实际选RAIL或CUSTOM_STRIDE，后者的gin_min_stride来自运行环境，本机没有实际值。EP512/1024仅作8卡/域下编译约束拒绝的边界，不执行、不复制专家或token填卡。

固定context共享示例为2SM、每SM2个forward通道、同数scale-up warp，Q1/2/4；只核get_qp_mode分配，未测GPU启动形状或性能。对支持多轮同lane扫描的声明配置，枚举六种合法事件次序：首peer采样在先；首peer完成必须早于后一peer采样；另一生产者预约必须早于其完成。后一peer起始无请求。输出被采样的请求集合及当时是否未完成；不引入纳秒、拥塞、丢包或人为热点。

独立评价用偏序闭包枚举相同六种线性扩展，以“预约是否先于采样、完成是否后于采样”的关系式核对，不调用分析的状态机。对scope/扫描轮数/捕获/来源四类篡改应拒绝。正确动态资源DAG可在采样时绑定目标ticket；冻结入口快照只是一个不等价简化，不能充当唯一竞品基线。合作warp的前后sync、实际provider peer转换和同context共享均保留；不将每lane的源码顺序当作全warp同步快照或已实测GPU调度。

一次准备与分析，独立核验和Markdown/HTML；复制脚本及输入到新根/空cwd离线重算并逐字节核对。新根`runs/gin_flush_scope_20261027/`，累计300受监控秒/64MiB（含归档、恢复和2MiB/10秒管理预留），单命令60秒、RSS1GiB/地址2GiB、保留8GiB空闲。失败保留并停止原条件，不重跑成功条件、不扩限。不运行网络、GPU、原生编译、HTTP、平台测试或历史资源驱动；不修改平台/依赖、不安装、不push。

成败判据为上述源码适用性与事件关系是否成立，不设性能正收益目标。即便出现跨生产者等待，也不证明死锁、协议漏洞、应用加速、收益空间或两项主创新。若普通资源DAG已表达同一关系，则记录为强基线，后续必须另行证明决策或实际处理成本差距。

源码定位更正（原分析在任何六配置/事件计算前停止）：GDAKI的flush与fence都有相同peer循环，原定位器错误要求全文件唯一，因407/666两处匹配失败。原源码、状态和失败monitor保留在`failed-source-locator/`及`execute-monitor/`。只将定位限定为已指定的flushImplModeCore函数；通过独立`continue-unstarted`动作完成尚未开始的六配置/事件条件，不重复准备，不重跑任何已计算条件，不改输入、定义、门槛和总预算。此更正须在继续动作前保存；原失败不回填为成功。
