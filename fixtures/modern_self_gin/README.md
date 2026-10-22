# 同rank GIN操作分类更正

固定DeepEP direct源码中，“目的rank等于自己”不等价于“本地旁路”。此前modern plan `/1`把二者混淆；本增量修正为 `/2`，不改变操作/事件总数、专家路由、字节布局或动态slot依赖。

| 操作 | self-peer行为 | 源码分支 |
|---|---|---|
| Dispatch/Combine token | 本地指针/TMA旁路 | dispatch先尝试get_sym_ptr；combine判断nvlink_bypass |
| rank count | 本地store旁路 | NCCLGin.put_value先尝试get_sym_ptr |
| expert count block | GIN bulk put | 非NVLink分支无self判断，直接gin.put；put不做本地旁路 |
| 四个屏障的signal | GIN原子通知 | signal对全部rank调用GIN；GDAKI按peer取QP，host创建并连接self-loop peer QP |

`gin_self`表示需要provider工作与完成的自发操作，**不表示已经知道其物理回环路径或可用交换机网络模拟**。现在原生持久字节入口禁止src=dst，MRT1远端事务入口也未覆盖该provider路径。不能在未来适配器中默默省略它，也不能强行选择一条跨交换机路径或杜撰零延迟。

## 验收与证据

- [source_basis.json](source_basis.json)：固定源码完整hash/blob、原文位置与因果链；均复用已有缓存，无新网络下载。
- [verification.json](verification.json)：15条公开CLI的新目录计划流程，1076事件、224操作、16符号slot中40项为gin_self；self零专家块可见与self屏障本地完成的暂扣检查，及旧manifest/1明确拒绝。
- [runtime_regression.json](runtime_regression.json)：10条公开CLI，原事件执行、16个实际slot/Combine绑定及重复/迁移保持通过。没有执行真实GPU、provider或网络。
- [maintenance_checks.json](maintenance_checks.json)：15项单元检查，来源及实现hash；归档只保留当前增量的输入、命令与日志。

运行命令见[公开输入和版本迁移](../../examples/modern_deepep_partial_order/README.md)。原输入schema不变；必须重新编译旧输入，不能仅替换旧清单版本号。旧fixture及研究结果保留其固定版本，不改写为新模型的验收。

本修正纠正资源语义分类，尚未改变网络成本。后续真实路由分析应分别核算local、gin_self、rdma，记录GIN请求数和跨rank数据量，而不是将请求数写成WQE数、包数或优化收益。
