# 完整旧低延迟计划的原生网络入口

`scaleout deepep-run`把已经核验的旧DeepEP v1.2.1索引计划，连接到一次持久的原生ns-3网络。它保留逐专家数据、零token的count/flag及同rank操作；请求、目标内存、原子旧值回复和源确认分别推进，不将离线可达性当作通信完成。

当前状态：128端点Clos中，EP32/E256完整正常条件和一次精确通知丢包条件均通过独立逐包、DAG、内存及排空审计；另用EP32/E128连续两调用验证跨调用复用。均为人工输入与功能专家拷贝，未执行真实请求或GPU数值专家。结果与原失败记录见[验收报告](../../fixtures/legacy_native/README.md)。

## 准备与执行

在仓库根目录，用已有Python和固定依赖进行操作。先通过[公开构建](../scaleout_rdma/README.md)中的`scaleout rdma-build`构建当前代码，取`backend.json`中的`legacy_deepep`可执行文件。它不是`transaction_probe`、`transaction_session`或ASTRA服务程序。

```bash
PYTHONPATH=python python3 -B examples/legacy_native/prepare.py \
  --reference-events 100000 --output runs/my-legacy-inputs
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-run \
  --plan runs/my-legacy-inputs/plan \
  --binding runs/my-legacy-inputs/binding.json \
  --topology runs/my-legacy-inputs/topology \
  --protocol runs/my-legacy-inputs/normal.json \
  --transport runs/my-legacy-inputs/transport.json \
  --backend /absolute/path/from/backend.json/legacy_deepep \
  --output runs/my-legacy-normal
```

这里显式选择100,000条参考日志容量。本例至少产生32,464条`source_sent`日志，已验收正常条件实际共有48,336条参考日志；若显式选择10,000，入口应在构网前拒绝。`--reference-events`必须显式指定，范围1..100,000；重传、拥塞反馈及定时器还需要额外容量，最低数量检查通过不保证运行不会触限。失败后保留原配置和日志；经授权调整上限时记录新输入身份并使用新输出目录。

准备程序生成128端点Clos、20交换机、168链路，以及EP32/E256/H7168/BF16的一次Dispatch和Combine计划。rank映射为物理端点0、4、8……124；每rank8专家。仅rank0/8/16/24各有一个人工token，每token选择8专家；没有将这4个token写成32个真实请求或128卡满载。GPU类型为NONE，机内旁路关闭，远端统一使用NIC1/plane0。同rank操作是本地功能内存操作，不发包。

`normal.json`不注入额外丢包；`fault.json`仅在端点4/NIC1丢弃stream0、序号0的首个DATA包一次。它承载的是一个零token通知请求，区别于“全窗口持续丢所有数据”的局部故障。故障配置文件的存在不表示条件已经运行。

## 仅检查输入，不启动网络

在上述`deepep-run`命令增加`--validate-only`，并为`--output`指定新的目录，即可重生成并核对完整源计划、rank映射、协议和Python侧容量约束。仍须提供当前后端路径，以记录拟执行命令及二进制hash；该模式不调用后端，也不执行原生准入或Simulator。

通过后仅生成`plan-check.json`、`execution.json`、`inputs.json`和`validation.json`。后两份文件明确标注`validation_only=true`及`native_network_executed=false`；`inputs.json`保存`planned_native_command`，没有实际执行的`command`。`validation.json`另标明`native_admission_executed=false`，汇总每调用token数/数据活动rank数、EP/专家数量、操作/事件/载体规模及日志必要下界。不会生成`result.json`或`execution-monitor/`。

检查模式保留全部输入约束：10,000条容量仍会拒绝，损坏的计划不能通过。其`passed=true`只表示Python和源计划检查通过，不代表原生后端接受、故障恢复成功或任务完成。准备并检查一份新容量配置也不等于批准运行；失败后的预算变更仍按项目规则先确认，原失败输入和结果保留。

## 可修改项与支持范围

修改`plan-input.json`后，使用`deepep-plan --format indexed-jsonl`重新生成新目录；运行器逐行重生成并核对全部操作和事件。修改`binding.json`可调整逻辑rank到128/256/512端点Clos的映射及队列深度；修改protocol可调整合法链路/捕获/丢包配置；`transport.json`显式选择SR和参考DCQCN，不能省略后退到较弱协议。

本入口限BF16、2..32 ranks、至多256专家且均匀分配、H128..8192且为128的倍数、每rank至多16 token。队列深度取1..4096内的2的幂；单静态注册跨度中的一条远端put为一条WQE，按专家内token序号执行同步四条发布策略，通知强制发布。准入另按每个远端上下文的规范操作顺序检查：深度至少容纳下次发布前的完整跨度，否则工作尚未发出，不能靠ACK回收槽位。本例跨度2、配置32；两条数据put再通知至少需要3槽，按2的幂配置须选4槽。本地拷贝/存储绕过此队列，不生成WQE或网络ACK。完整计划另限32,768操作、140,000事件、65,536请求/回复工作和16,384有向流；超限拒绝整个计划，不删除零通知。

相同rank对的不同本地专家保留独立上下文。EP32/E256有8,192个上下文，其中7,936个跨rank上下文，对应15,872条请求/回复有向流。不能把这些功能流称为同等数量的真实GPU或生产RoCE硬件QP。

## 输出与核验

`inputs.json`记录实际命令、输入/二进制hash、端点与EP数量、最低日志需求及最小发布跨度；`plan-check.json`为完整源计划检查；`execution.json`为有界原生输入；`execution-monitor/`保存执行状态、宿主预算和日志；`result.json`仅在实际原生执行后生成。失败时检查`passed/execution_error`，不能引用未完成的时间为推理完成时间。本地操作的WQE、发布、请求ACK和回复时间为−1，`returned_value`为null；远端原子也只在实际回复到达后报告旧值，目标应用日志不替代源端回复。

成功执行后的独立审计入口是：

```bash
python3 -B tests/integration/audit_legacy_native.py \
  --run runs/my-legacy-normal --protocol runs/my-legacy-inputs/normal.json \
  --output runs/my-legacy-normal-audit.json
```

审计按包UID、端点/NIC、序号和负载长度将实际TX、目的RX及交付字节绑定，区分唯一载体数据和重复发送，再解码MRT1，核对通知、DAG、发布、目标内存、清理和退休。未额外注入故障不保证零重传；审计统计实际重复字节，不因正常条件出现重传而直接拒绝。最终计数器和数据记录须完整覆盖所有应有项，不能用缺失记录绕过检查。当前三份完整原生结果均已通过此审计；包核验子摘要的`complete_application_proven=false`指该子检查自身不证明业务完成，顶层审计还检查全部操作、事件、最终内存和排空。

补充跨调用用例使用同一128端点Clos和EP32，改为128专家、两次调用，每次4个人工Top8 token。相同选择在两次调用中复用地址，功能字节随call变化，验证清理后的真实写入和后续读取。准备与运行方式如下，其他参数沿用前述输入：

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-plan \
  --format indexed-jsonl --config examples/legacy_native/cross-call-input.json \
  --output runs/my-legacy-cross-plan
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-run \
  --plan runs/my-legacy-cross-plan \
  --binding runs/my-legacy-inputs/binding.json \
  --topology runs/my-legacy-inputs/topology \
  --protocol runs/my-legacy-inputs/normal.json \
  --transport runs/my-legacy-inputs/transport.json \
  --backend /absolute/path/from/backend.json/legacy_deepep \
  --output runs/my-legacy-cross
```

该用例保留全部零token通知，不替代原256专家正常/故障条件。256专家连续两调用的计划超过当前32,768操作上限，仍会整体拒绝；不把本例称为已验收该组合。

本地可见性与专家阶段显式为零成本功能拷贝：Dispatch前16B为标记元数据，后2H字节作为功能专家输入复制到Combine；没有执行数值专家、CUDA、真实请求调度或GPU性能模型。MRT1请求与反向可靠回复流不是RoCE报文/Atomic ACK，也不把旧库发布规则用于现代direct/GIN。资源预算和进一步验收要求见[规范](../../docs/tasks/legacy_native_spec.md)。
