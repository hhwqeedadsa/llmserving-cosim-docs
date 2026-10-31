# 两个完整旧库调用共用一次网络

`deepep-shared-run`在同一个128/256/512端点Clos网络中运行1或2个独立旧DeepEP v1.2.1 BF16组。每组保留自己的EP、专家、通知、缓冲、事件和完成条件；全部组共用一个Fabric、Simulator和持久可靠传输控制器。它用于观察不同任务的共享链路竞争，平台不会把独立运行时间相加作为共享结果。

这是功能通信模型，采用零成本可见性和功能专家拷贝；不是现代GIN/RoCE线协议、完整推理或GPU校准。本例使用128端点网络上的两个EP2组，每组4专家、H128、3个人工Top1 token，映射分别为`[0,64]`和`[4,68]`。其余124端点没有这两个组的工作；不能称128卡满载或真实R1多任务实验。更大的组需保留该模型的专家/EP约束并通过总容量准入。

## 准备后端

当前源码的[完整公开构建](../scaleout_rdma/README.md)所生成`backend.json.legacy_deepep`支持单组与共享输入。已有固定运行库时，还可以只重编译旧库驱动和持久流适配器，保持旧二进制与运行库不动：

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-shared-build \
  --runtime /path/to/prebuilt-rdma \
  --identity /path/to/prebuilt-rdma/backend.json \
  --output runs/my-shared-build
```

该命令检查锁定提交、旧Fabric相关源码、静态库及8个ns-3库hash；只允许本命令重编译的旧驱动/流适配器及其验证头不同，并记录当前全部后端源码。复制固定库到新输出，再使用已有C++20编译器以O3编译两个单元并核对实际加载路径。不会下载、安装或重编ns-3；不匹配则拒绝，不能替换库绕过。输出`backend.json.legacy_deepep`是本轮新程序的绝对路径。

## 准备和执行

先设置明确的运行预算。本例使用120秒、RSS2GiB、地址4GiB、结果256MiB，卷预留8GiB。共享入口允许在协议`limits`中显式提高到180秒、RSS3GiB、地址6GiB、结果512MiB；默认不会自动提高，原单组入口仍为120秒/2GiB/4GiB/256MiB。增加的是宿主执行与记录容量，不改变链路、包、重传或任务成本；审计入口最多读取512MiB结果。正式研究还需用`moe_ub_cosim.host_probe`限制整个实验根的累计产物和时间。

```bash
PYTHONPATH=python python3 -B examples/legacy_shared/prepare.py \
  --reference-events 200000 --output runs/my-shared-input
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-shared-run \
  --config runs/my-shared-input/shared.json \
  --topology runs/my-shared-input/topology \
  --protocol runs/my-shared-input/normal.json \
  --transport runs/my-shared-input/transport.json \
  --backend /path/to/new/legacy-deepep \
  --output runs/my-shared-normal
```

新增共享入口允许显式1..200,000条参考日志，未改变原单组/通用入口的100,000上限；不是所有场景都需要20万。容量是停止记录并报错的上界，不改变包、超时或算法。准备程序另外生成`transport-single.json`（至多100,000），用于原入口回归；`single.json`只包含第一组，可用于共享入口的单组对照。

`fault.json`仅在GPU0/NIC1丢弃第一组stream6/sequence0的一包，载体是零token count通知；可靠传输仍使用选择性重传。换用该协议并指定新输出目录即可进行故障功能检查，不能覆盖正常结果。配置文件存在不代表已经运行。

## 输入、输出与修改

CONFIG采用下列格式，路径相对CONFIG所在目录解析；绝对路径也可使用：

```json
{
  "schema_version": "moe-legacy-shared-input/1",
  "groups": [
    {"id": "group-0", "plan": "group-0/plan", "binding": "group-0/binding.json"},
    {"id": "group-1", "plan": "group-1/plan", "binding": "group-1/binding.json"}
  ]
}
```

每组使用[原完整索引计划及绑定](../legacy_native/README.md)，GPU必须组内及组间都不同。全部源计划逐行重生成核验后才准入；超限拒绝整体，不删除零通知、不扩大EP。按组顺序分配全局stream区间和wire port，所以对照应保持组顺序与部署一致；字符串id仅用于记录。组内rank、专家和事件索引保持原值，不把两个EP32变成一个EP64。

在运行命令加入`--validate-only`可核验源计划及Python容量，生成`validation.json`，不调用原生准入或网络。该模式仍要求后端路径用于身份记录；`passed`不证明原生执行成功。

输出包含逐组`group-N-plan-check.json`、完整`execution.json`、输入与拟执行/实际命令清单`inputs.json`、原生执行监控及`result.json`。实际结果中的`legacy_shared.groups`保存各组报告及`stream_offset`，`legacy_shared.transport`只保存一份共享传输。组kernel时刻来自共同原生时钟；顶层`finish_ns`是整个网络排空，不能当作某组独立排空或单独运行性能。

共享总限32,768条有向流、65,536请求/回复工作、65,536操作、280,000事件、2,000,000逐跳观测、64MiB源包捕获；各组仍遵守原32 ranks/256专家等维度及单组上限。两组EP32/E256可能通过计数准入，但本轮尚未验收其实际网络执行。日志、结果或内存超限仍会失败，不作成功截断。

## 只读审计

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-shared-audit \
  --run runs/my-shared-normal \
  --protocol runs/my-shared-input/normal.json \
  --output runs/my-shared-normal-audit.json
```

先核验全局stream归属、源TX/目标RX/包UID/实际负载及网络排空，再投影逐组记录，复用原完整DAG、内存、发布、通知及退休审计。投影只在读侧重标识stream，保留实际包UID、wire port、时刻、端点、字节和全局work ID。拒绝不完整结果、未知组或流、缺少捕获、已有输出和Python `-O`；真实路由采集来源及GPU校准仍须另证。

四个128Clos功能条件的实际结果与边界见[验收](../../fixtures/legacy_shared/README.md)。
