# 有界原生回调批次验收

新增公开 `scaleout modern-deepep-native --callback-batches`。保持原256次同步上限、完整现代依赖和包参数；使用原收集批次/时刻推进，缓存有硬界。平台与研究收益分开，规范见[阶段要求](../../docs/tasks/native_callback_batches_spec.md)，结论见[中文报告](../../docs/reviews/原生批次交付与四卡完整调用_20261025.md)。

证据入口：`verification.json`（全部四条件）、`recovery.json`（新源码根/空cwd独立第五次网络）、`closeout.json`（预算/失败/进程）、`identity.json`（构建输入）和`final-management.json`（最后管理收据）。原件和可复核包保存在 `runs/native_callback_batches_checkpoint_20261025/`；阶段目录 `/dev/shm/moe-native-callback-batches-20261025/`。包为同主机Linux ABI-bound，不是干净原生构建。

默认健康完整结果/现代快照/控制文本保持；批次健康63同步/213逻辑步，故障66/222，全部网络及现代时序保持。原四rank65同步/360逻辑步，118操作/566事件完成，70610ns；故障前缀保存，缓存最大58批/8234B紧凑JSON。原四rank捕获20000条并遗漏103168条，只能验证物理前缀，完整应用/源字节/事务内存与物理覆盖分开。

`build.py`仅复用固定对象/八库、按原O0增量构建；`stage.py`只管理本阶段600秒/512MiB及5次网络，不调用旧关闭阶段runner。`verify.py`不导入控制器/条件生成器，独立核对批次、组合条件、交付、注册和原结果。`application.py`保留旧完整应用oracle断言，`transport.py`明确只对物理捕获前缀作检查；不静默删除旧完整捕获要求。`checks.py`拒绝证据篡改。恢复与资源脚本保留命令和失败，不因离线checker错误重跑已成功网络。

本轮准备脚本的重复sources参数、四卡checker的重复scope参数及旧观测测试替身缺少新显式参数曾失败；原始日志保留。前两项仅修准备/离线核验，第三项仅让替身接受且断言缺省模式；没有为这些问题重跑成功网络。研究主创新、真实provider/GPU时延、匹配校准、独立留出和完整千卡仍未完成。
