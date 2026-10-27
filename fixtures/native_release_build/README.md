# 原生Release构建验收

本增量修复公共`scaleout rdma-build`被固定依赖告警分支追加`-O0`的问题。保留`-Wall -Wpedantic`，在配置后、编译前核对全部单元的有效`-O3`及关键网络/会话源码覆盖；不修改依赖源码或gitlink。功能范围和事前资源预算见[独立规范](../../docs/tasks/native_release_build_spec.md)，普通用户操作见[构建示例](../../examples/scaleout_rdma/README.md)。

`workflow.py`记录本阶段的实际公共构建及小例流程；新参数`--compile-wall-seconds`默认300秒，只有事前显式设置才使用其他60—1800秒上限，资源失败不自动重试。构建选项格式见[输出字段](../../docs/record_storage.md#原生scale-out构建选项记录)。

验收使用一次全新构建、一个普通probe及一个已固定归档的共享Agg/双NIC/两rank两调用modern功能小例。普通probe独立核对消息字节、物理包及端口；modern独立核对包路径、队列和应用数据/事件，并比较旧完整result、control和应用快照。只有实际加载库的位置在逐hash检查后按库名对应，其他内容必须相同。这里不启用宿主计时、不比较运行速度；编译优化不是论文算法贡献。

实际偏差单独保留：事前只允许库路径差异的严格比较未通过，优化后`dl_iterate_phdr`列举库的顺序发生变化。后续解释核对库名多重集合及实际库hash，并逐项确认其余完整result、control和应用快照一致；`strict_original_result_equivalence=false`保留在收据中，不把这项严格条件改写成通过。这是构建元数据的实际差异，不能由该功能小例证明所有输入都不受加载顺序影响。首版检查另误把CMake缓存条目的类型限定为BOOL，而本版本为UNINITIALIZED=ON，已只读更正并保留失败；没有因此重新编译或重跑网络。

原件目录`runs/native_release_build_20261027/`，公开命令、退出、日志与预算分别保存在`commands.json`及各`monitor/`、`build/logs/`。版本化收据：[独立验收](verification.json)、[离线恢复](recovery.json)、[收尾与资源](closeout.json)。

只读复核已经保存的原件，不执行网络：

```bash
PYTHONPATH=python:. python3 -B -m fixtures.native_release_build.verify \
  --stage runs/native_release_build_20261027 \
  --output runs/native_release_build_20261027/new-verification.json
```

从逐成员hash归档恢复到一个不存在的目录：

```bash
PYTHONPATH=python:. python3 -B -m fixtures.native_release_build.checkpoint restore \
  --archive runs/native_release_build_20261027/checkpoint.tar.gz \
  --output runs/native_release_build_recovery_new
```

恢复从包中的复制源码和空工作目录重算验收，核对字节一致；不依赖历史动态库路径、不执行native程序、GPU、构建或网络。归档包含五程序、八库和编译数据库，证明保存结果可复核，不声称从该证据包脱离固定依赖重新编译或完成了硬件校准。优化后的宿主成本须另行预注册研究。
