# 用实际网络回调推进现代通信流程

这个入口把现代direct分支的Dispatch—专家—Combine控制图接到同一次原生ns-3事务会话。跨节点操作实际发包；发给自己的屏障和专家计数使用显式self服务；本地旁路、源准备及计数清零使用实际内存copy。全部必要操作保留，不能用离线事件完成代替网络回调。

当前功能范围有一个已知限制：原生会话最多256次控制交互。两rank两调用的示例正常和丢包均通过；四份原始路由的一次四rank调用在256次交互处失败，Combine未完成。输入检查允许2—4 rank，但不保证满足这个运行上限；失败结果和部分事件仍保存。不要调高限额或删除控制操作来绕过它，见[验收与限制](../../fixtures/modern_native/README.md)。

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

## 怎样修改

输入格式`moe-modern-native-input/1`。`modern`沿用[现代计划](../modern_deepep_partial_order/README.md)的完整配置与专家选择。可改模型形状、token数量/专家选择、调用次数及context，但仍受原计划和事务的有界容量约束，不等于完整模型部署或服务请求调度。

`deployment`逐逻辑rank声明物理GPU节点和设备到NIC/plane的对应；设备编号须覆盖`modern.execution.local_devices`。相同communicator/source/context/peer身份跨阶段和调用复用连接。Combine槽位在实际运行时分配；只为所有可能绑定预留连接，不提前选择slot顺序。预留集合上限64，未使用连接也会占用原生对象，不可称作活跃GIN QP数量。

`provider.mode=functional_mrt1/1`及`target_visibility=native_apply_functional/1`必须显式填写。功能模型的accepted/visible来自目标实际applied，local_complete来自事务实际completed；前者不释放源保护。这个声明不证明真实GPU可见性、GIN CQ或RoCE报文等价。`self_service`给出开始后apply/complete的相对纳秒时长，`queue_depth`为2—32内2的幂；`transport_recovery`沿用现有明确的恢复输入。

`costs`必须声明`source_prepare_ns/expert_ns/reduction_ns/local_copy_ns`。本地带成本工作沿现有每GPU串行lane；控制依赖和slot分配为零时长逻辑步骤。此入口没有模拟CUDA各SM并发；成本是使用者条件，GPU名称不能自动生成实测成本。

数据内容为每call/源/token或Combine贡献生成的32B标记及显式零填充，保留现代布局的完整发送长度。主窗口跨phase/call实际复用；不可变输入、provider功能信号域与主窗口分开。这里不执行数值BF16专家、完整LLMServingSim服务调度或真实模型KV。

## 输出

`modern-plan.json`保存现代操作图，`initial.json`保存原生初始窗口/连接，`control.jsonl`记录实际DECIDE/COMMIT，`result.json`保存包、字节及目标应用。失败后仍保留这些已取得的文件。

`modern-execution.json`保存逐现代事件的`time_ns/origin`；实际回调事件另有`native_operation/native_event/native_time_ns`，并保存实际增量事务及绑定。顶层scope说明本功能接入；内部`runtime`仍是窗口影子核查器，不能把其`simulated_packets=false`误读为整个外部运行未发包，也不能把外部网络执行误称为GPU校准。

本入口是功能接入，不是可用于发表性能排名的现代provider模型。四rank控制交互限制、真实provider成本、匹配校准、独立工作负载和完整千卡仍未解决。
