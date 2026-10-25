# 用实际网络回调推进现代通信流程

这个入口把现代direct分支的Dispatch—专家—Combine控制图接到同一次原生ns-3事务会话。跨节点操作实际发包；发给自己的屏障和专家计数使用显式self服务；本地旁路、源准备及计数清零使用实际内存copy。全部必要操作保留，不能用离线事件完成代替网络回调。

原生会话仍最多256次控制交互。两rank两调用的示例正常和丢包均通过；可选的回调订阅减少无关交互。四份原始路由的一次四rank调用仍未验收通过：缺省模式曾触及256次交互；订阅模式最新执行触及60秒宿主时间上限，没有原生最终结果。输入检查允许2—4 rank，不保证在执行预算内完成。已记录的回调和命令保留；强制终止时不能保证生成最终内存、包记录或现代快照。见[当前实际验收](../../fixtures/native_wakeup/README.md)及[初始失败记录](../../fixtures/modern_native/README.md)。

## 运行

需要现有`transaction-session`原生程序或已核对库路径的配套启动器；不自动构建、下载或执行GPU。输出目录必须尚不存在。

```bash
export PYTHONPATH=python:.
export PYTHONDONTWRITEBYTECODE=1
MODERN_BACKEND=/absolute/path/to/run-transaction-session

python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/modern_deepep_native/topology.json --output runs/modern-topology
python3 -B -m moe_ub_cosim scaleout modern-deepep-native \
  --topology runs/modern-topology --protocol examples/modern_deepep_native/rdma.json \
  --config examples/modern_deepep_native/input.json --backend "$MODERN_BACKEND" --output runs/modern-native
```

使用`drop-first-rdma.json`及新输出目录可测试本示例第一个远端signal请求包丢失。该协议的`message_id=2`对应本示例的请求载体，不适用于任意修改后的连接集合；错误标识应明确拒绝，不能假定丢包已发生。

在以上现代入口命令后增加 `--callback-wakeups`，使用支持 `wake_on` 的新 session 后端，即可订阅下一现代事件需要的实际回调。所有回调仍按原顺序和时刻保留，首次、排空和控制闹钟强制唤醒；默认继续逐批交付。不启用推迟纯前端记账的策略。旧后端不支持此字段时会明确失败，不会静默降级。这个选项减少宿主交互，不代表网络时延下降。

### 可选的宿主开销观测

增加 `--host-timing` 并使用支持可选计时路径参数的session后端，保存 `worker_host.jsonl/native_host.jsonl`。这两份日志使用已有 `moe-host-phase-trace/1`；每条记录及时写出，超时后仍可检查合法前缀。缺省不生成日志，不读取观测用宿主时钟。旧后端收到额外参数会明确失败，不静默降级。

```bash
python3 -B -m moe_ub_cosim scaleout host-timing-check \
  --run runs/modern-native --output runs/modern-host-summary.json
```

Python阶段区分 `controller_advance/wake_conditions/python_candidate_copy/python_candidate_validate/python_command_write/python_wait_line`；原生阶段区分 `session_pump/operation_progress_snapshot/session_events/session_candidate_validate/session_frontend_wait`。同进程父阶段包含子阶段，跨进程等待重叠，不能直接相加。`simulator_run`的exclusive时间包含未细分的传输、内存与事件处理，不能全称为包处理。

原生 `session_progress_enter/exit` 快照记录当时的ns3时刻、处理事件数、决策/任务/回调数；不是终止瞬间或最终内存，也不是包计数。日志只供离线诊断，控制器不读取它决定工作。超时阶段没有结束记录时，分析器保留open_spans，不虚构耗时；`all_traces_complete`与网络成功分开。具体字段及计数限制见[宿主记录](../../docs/record_storage.md#原生rdma宿主阶段记录)。

记录器每进程最多65,536条；达到上限时明确失败，保留合法前缀，不静默截断或自动扩大额度。高频通知的大场景可能先耗尽观测额度；这类失败与网络失败、宿主限时分开解释。

## 怎样修改

输入格式`moe-modern-native-input/1`。`modern`沿用[现代计划](../modern_deepep_partial_order/README.md)的完整配置与专家选择。可改模型形状、token数量/专家选择、调用次数及context，但仍受原计划和事务的有界容量约束，不等于完整模型部署或服务请求调度。

`deployment`逐逻辑rank声明物理GPU节点和设备到NIC/plane的对应；设备编号须覆盖`modern.execution.local_devices`。相同communicator/source/context/peer身份跨阶段和调用复用连接。Combine槽位在实际运行时分配；只为所有可能绑定预留连接，不提前选择slot顺序。预留集合上限64，未使用连接也会占用原生对象，不可称作活跃GIN QP数量。

`provider.mode=functional_mrt1/1`及`target_visibility=native_apply_functional/1`必须显式填写。功能模型的accepted/visible来自目标实际applied，local_complete来自事务实际completed；前者不释放源保护。这个声明不证明真实GPU可见性、GIN CQ或RoCE报文等价。`self_service`给出开始后apply/complete的相对纳秒时长，`queue_depth`为2—32内2的幂；`transport_recovery`沿用现有明确的恢复输入。

`costs`必须声明`source_prepare_ns/expert_ns/reduction_ns/local_copy_ns`。本地带成本工作沿现有每GPU串行lane；控制依赖和slot分配为零时长逻辑步骤。此入口没有模拟CUDA各SM并发；成本是使用者条件，GPU名称不能自动生成实测成本。

数据内容为每call/源/token或Combine贡献生成的32B标记及显式零填充，保留现代布局的完整发送长度。主窗口跨phase/call实际复用；不可变输入、provider功能信号域与主窗口分开。这里不执行数值BF16专家、完整LLMServingSim服务调度或真实模型KV。

## 输出

`modern-plan.json`保存现代操作图，`initial.json`保存原生初始窗口/连接，`control.jsonl`记录实际DECIDE/COMMIT，`result.json`保存包、字节及目标应用。失败后仍保留这些已取得的文件。

`modern-execution.json`保存逐现代事件的`time_ns/origin`；实际回调事件另有`native_operation/native_event/native_time_ns`，并保存实际增量事务及绑定。顶层scope说明本功能接入；内部`runtime`仍是窗口影子核查器，不能把其`simulated_packets=false`误读为整个外部运行未发包，也不能把外部网络执行误称为GPU校准。

显式启用订阅时另存 `callback-delivery.json`，记录策略和生成器哈希；原生 `session.callback_wakeups` 记录过滤命令数、略去的无关交互批数及积累回调数。正常结束须无待交付回调；超时、缺少 `result.json` 或部分日志不能作为成功证据。

启用宿主观测时另存 `host-timing.json`，记录格式与worker/session/记录器源码hash；实际原生命令包含计时路径。原生程序及输入hash仍保存在 `native-command.json`。这些文件独立于 `result.json`，不改变网络结果字段。

本入口是功能接入，不是可用于发表性能排名的现代provider模型。四rank控制交互限制、真实provider成本、匹配校准、独立工作负载和完整千卡仍未解决。
