# 固定传输声明复用的功能验收

公开入口`modern-deepep-native`和`rdma-transaction-session`均支持默认关闭的`--reuse-static-validation`，类接口为`NativeTransactionSession(..., reuse_static_validation=True)`。用法见[现代小例](../../examples/modern_deepep_native/README.md#可选的固定校验复用)，范围和预算见[事前规范](../../docs/tasks/static_stream_validation_spec.md)。

本阶段只验证少做固定检查时结果是否保持；没有测量提速，也没有新增网络恢复算法。平台保留完整动态任务、历史依赖、内存权限和写入顺序检查，C++准入不变；键包含完整拓扑、协议、派生streams和准确类型，未知类型回退原检查，失败不能污染成功键。缓存每会话最多一项，增加的内存和构键成本尚需单独计量。

## 实际结果

| 功能输入 | 静态检查请求 | 实际完整静态检查 | 复用次数 | 实际回调 | 已提交节点 |
|---|---:|---:|---:|---:|---:|
| 两rank、两调用的现代完整功能图 | 64 | 1 | 63 | 437 | 97 |
| 原子加法后再写入的在线事务 | 9 | 1 | 8 | 11 | 2 |

六次预定网络均完成：上述两种入口默认/开启各一次，再用复制Python源码、空cwd和复制O3运行时各执行一次开启条件。同根比较不规范化任何result字段；迁移仅对应已核hash的八个ns-3库根路径与枚举多重集合，系统库路径/相对次序仍保持。全部包、队列、回调、内存、控制消息和现代快照保持。

现代输入为公开`input-expert-costs.json`，两rank两调用、合成BF16布局与声明成本，已有选择性重传，不注入新故障；不是完整模型或真实GPU性能。事务使用原样`session-program.json`，实际旧值7、原子后10、后继写107，独立解码3个MRT1帧和核对最终内存；后继数据在本公开程序中预先声明，不能称为本次动态返回值生成。拓扑从公开配置重新生成；事务拓扑中的A100仅为标签，没有运行GPU或使用A100 profile。

11项新单元、70项相关回归、13固定依赖检查通过。开启的两个原件各拒绝4类证据篡改：实际回调、源包负载、事务完成时刻、缓存计数；迁移条件也通过相同拒绝检查。`verify.py`先独立核对控制交互、回调时间及事务帧/内存，现代还使用既有独立应用审计器，缓存计数不代替网络行为证据。

原件`runs/static_stream_validation_20261027/`保留实际命令、全部结果、终态监控、八库hash、原始失败和三版验收源码。首次核验误把系统库计入八个ns-3库，第二次误读目标副作用的身份字段；两者均为核验器错误。默认网络均成功，按原预算先离线纠正，再只执行尚未开始条件；没有成功网络重跑、失败网络重试或第七次网络。原始失败检查没有改写为通过。

摘要见[完整核验](verification.json)、[检查点](checkpoint.json)、[离线恢复](recovery.json)和[资源收尾](closeout.json)。归档1560成员/51,199,960B，SHA256为`768c9f474a5a625167d03816102f0c63519575d6c3845eecd08fd76398ea9019`。这是同主机ABI对应的运行时迁移，不是从零构建或任意机器二进制可移植性。

## 离线复核

已有Python环境即可，不下载、构建或启动新网络。输出目录必须不存在，完整成员hash及复制源码先核对，再在空cwd重新生成核验JSON，与归档中的原报告逐字节比较：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir runs/my-static-validation-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 536870912 --artifact-root runs/my-static-validation-restored \
  --wall-seconds 60 --min-free-bytes 8589934592 -- \
  python3 -B -m fixtures.static_stream_validation.accept restore \
  --archive runs/static_stream_validation_20261027/checkpoint.tar.gz \
  --digest 768c9f474a5a625167d03816102f0c63519575d6c3845eecd08fd76398ea9019 \
  --output runs/my-static-validation-restored
```

若只核对某个已保存开启结果，可在同等host_probe预算内使用：

```bash
PYTHONPATH=python:. python3 -B -m fixtures.static_stream_validation.verify \
  --run runs/static_stream_validation_20261027/modern-cached \
  --cached --corruptions --output runs/my-static-validation-check.json
```

本阶段预算已关闭，不继续追加网络或顺便开展速度比较。后续研究须单独登记完整流程的配对测量和较大真实负载，比较CPU、墙钟和RSS；即使有正收益，普通校验复用也只是强基线工程工作，两项主创新仍待验证。
