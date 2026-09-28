# 当前任务

任务编号 `m3-1-service-loop`

状态 `in_progress`

范围：v0.2 第 4.5/4.6、5、7.1～7.5、7.7、13.5 节 M3.1。用户确认开始；M2.4 已在 116fb15 完成本地提交。开始时主仓库、LLMServingSim、ASTRA 与 UB 工作区干净，13 项依赖检查通过。完成验证后自行整理记录并本地提交，不推送，M3.2 仍等待用户确认。

## 目标与边界

新增联合服务入口，直接调用锁定 LLMServingSim 的 Scheduler、Request 和 Batch；在一个四 rank EP 组上运行预先到达、给定已就绪 KV 的短请求，连续执行多个解码步骤。每个 rank 每轮一个 Token，各请求的已有长度和解码进度独立记录。保持 M2 的 direct_rank_dedup_v1 单层通信模板、真实 UB 完成事件和现有原生入口。

实现 cosim/1 JSONL 的 DECIDE/COMMIT 交互：全部对象和零业务初始化图准备好后在 0 ns 决策；四 rank 全部完成后反馈绝对 now_ns；前端更新请求，再生成和提交下一轮图。所有轮次复用同一 Sys、UB 网络和事件队列。新图先整组校验、全部装载和登记，再按 rank 顺序激活。stdout 仅用于后端协议，业务日志单独保存。

本轮只接受预先到达且各 rank 有工作的一组请求，next_wakeup_ns 为 null。不实现 M3.2 的动态到达和第二个组，或 M3.4 的零 Token rank；不改变调度器内部记账，不生成占位请求，不增加 P/D、完整模型执行、真实数据或校准。计算仍是单层功能 profile，KV 给定就绪不是已执行 Prefill。

## 允许的动作与文件

允许修改 python/moe_ub_cosim/、backend/、tests/unit/、tests/integration/，新增 fixtures/m3_service/；更新 README.md、相关入口与 fixture 说明、docs/tasks/current.md、docs/environment.md。调用已有上游接口，本轮计划不修改依赖源码或 gitlink；如果证据表明需要最小依赖补丁，先明确原因及范围再实施，不升级依赖。

不初始化、不安装、不下载、不改全局配置。联合构建使用 build/m3-service-loop/，一次一个构建，最多八路并行。日志根 runs/m3-1-service-loop-20260928T155309Z/，保留命令、cwd、输入、退出状态和原始输出；已备份原生启动锁并记录 192 个旧产物哈希。

## 验收与停止

验证多轮实际调度、逐请求 Token 进度和 FINISHED 状态，后轮提交时间等于前轮全组完成时间；网络延迟改变后，服务进度跟随 UB 的实际完成时间变化。验证零业务初始化不推进时间、不发包、不进入业务操作记录；所有 rank 完成及网络任务收尾后方可正常退出。

检查消息编号、时间、路径及图身份，保留 EOF、错误消息和子进程失败的诊断。验证动态登记的观测来源不改变业务事件；执行受影响的 M2/M1 回归、单套 UB ns-3 链接检查及调度器相关原有测试。恢复启动锁并核对旧产物。通过后同步记录、本地提交，停止在 M3.1。
