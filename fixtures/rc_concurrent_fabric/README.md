# 多连接共享网络与目标内存的独立核验

本目录验证平台功能，不是正式研究实验驱动。范围见[事前规范](../../docs/tasks/rc_concurrent_fabric_spec.md)，普通操作与输入修改见[公开示例](../../examples/rc_concurrent/README.md)。

`verify.py` 独立解码实际包，按收包顺序重放各连接的公开 Python RC 状态参考；各连接序号和原子重放独立，共享同一目标字节数组。核对源完成、目标效应、旧值、实际路径、物理收发守恒、端口字节及队列占用/面积。七类篡改分别修改内存、原子返回、连接、QPN、物理路径、完成时刻、重复目标效应，均须拒绝。

本轮三个网络各执行一次：正常排空 24.395 μs；丢连接0中间写入包排空 124.373 μs；丢连接1原子回复排空 124.325 μs。后两组仅指定连接各一次超时，其余连接先行完成。共享计数器均从41到47，反向计数器从7到11；各连接取得的旧值随实际到达顺序变化。额外约100 μs主要由声明RTO决定，不是新恢复方案收益。

端点请求/回复分别为14/8、17/10、15/9包；逐跳TX/RX/丢弃分别132/132/0、162/161/1、144/143/1。三组均有四条连接重叠在途，两个源QP共用一个物理端口。结果见[中文网页](report.html)、[核验摘要](verification.json)、[身份记录](identity.json)、[恢复](recovery.json)及[资源收尾](closeout.json)。

只编译一次独立CPU驱动，复用固定原生库，不重建ns-3，不修改依赖。每QP仅一项在途WQE、固定速率、ICRC占位，不是完整RoCE或现代MoE；本轮不使用LLMServingSim或真实路由输入，不证明论文创新与性能收益。

离线核验已有标准目录：

```bash
PYTHONPATH=python python3 -B fixtures/rc_concurrent_fabric/verify.py \
  --stage /path/to/run --output /tmp/new-verification.json
```

标准目录含`inputs/`、`healthy/`、`drop_write_middle/`、`drop_atomic_ack/`及`build/backend.json`和`build/runtime/lib/`。原始包输出不回填或改写；库位置按当时记录核验，哈希读取本次阶段目录的副本，恢复时不读取旧根文件。

`checkpoint.py create` 保存源码、原始记录、预编译驱动及八库，只选已结束的monitor。`restore` 逐文件校验哈希，将源码和runtime搬至新根，在空工作目录重生成全部输入、重算核验JSON和HTML、执行相关单元并用`ldd`核对实际搬迁的库。不执行网络或编译，不声称完整构建的离线复现。归档及哈希见[归档收据](archive.json)。

原件位于`runs/rc_concurrent_fabric_20261027/`；累计上限1200受监控秒/512MiB，保留8GiB空闲。本轮不重开任何已关闭研究条件；两项主创新的决策价值及宿主净收益仍待验证。
