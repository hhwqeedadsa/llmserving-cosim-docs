# 参数化拓扑的静态验收

本目录通过公开 `scaleout topology` 命令，验证四套千卡网络和一个八卡小图的输入生成及确定性，不执行网络。实际输入在[用户示例](../../examples/scaleout_topology/README.md)，实现依据见[拓扑说明](../../docs/reviews/scaleout_topology_20261015.md)。平台代码不导入本目录或测试。

## 复现

仓库根目录，仅需Python 3.10+标准库，无需模型/原始路由或原生构建：

```bash
PYTHONPATH=python python3 experiments/scaleout_topology/validate.py \
  --output runs/scaleout-topology-validation-new
```

输出须是 `runs/` 内的新专用目录。脚本先执行全部 `scaleout*_test.py`，再对5个独立配置分别通过公开CLI生成两遍，比较全部文件SHA-256；测试覆盖独立BFS路径、GPU不能中转、分层带宽、Core接线、NIC预算、两种文本格式及非法输入。资源监控通过已有公共 `host_probe` 模块运行；子任务共享300s总时间预算，每步RSS1GiB、每进程地址空间2GiB，本次全部日志/产物累计128MiB，不联网、不编译、不改变原生探针8GiB磁盘预留。失败保留日志并退出，不扩限重跑。

`validation.json` 保留平台HEAD、工作树状态、脚本/配置/输出摘要、完整命令、各图计数、连通性和原生装载未运行原因；每步 `probe.json`、stdout、stderr在对应子目录，实际生成的图/文本位于 `*-generated/`。所有大产物留在忽略的 `runs/`，不提交。

版本化小摘要为 [results.json](results.json)，来源为 [sources.json](sources.json)。实际日志入口：`runs/scaleout_topology_validation_20261015_final/`；该路径不是复现依赖，按上面的新目录命令可重建。该轮运行前平台功能已本地提交，研究脚本尚未提交，工作区状态有据可查。

## 结果如何解释

- 23项相关功能/回归检查通过；5套配置各生成两次，所有文件逐字节一致。
- 三套可全局互通的千卡图：两层leaf-spine、分组三层Clos、共享Agg/Core的双plane HPN参考结构。
- 独立8 rail示例有8个128 GPU网络域，458752个无序GPU对无直接分组路径；这是预期物理约束，不是通过强行跨rail连线掩盖的错误。
- 标准三层Clos与HPN参考配置端口总量、层数、超售率不同，本轮没有比较其性能。
- `network_executed=false`。原生装载因磁盘低于8GiB预留未运行；Python格式检查不能代替原生设备装载或RDMA业务验证，既有回调问题也没有在本轮修复。

HPN图只作为结构参考，未取得论文全文；双plane分流、故障切换、NVLink/PCIe和训练热点均不由本结果证明。下一步小图原生装载、跨Pod路径和All-to-All完成语义检查，应单独验收后再扩容。
