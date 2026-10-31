# 显式Scale-Out事务容量验收

本平台增量同步扩展通用事务的Python/C++准入、原生窗口/注册/源读与本地输出保护、会话容量和宿主预算。仅transactions/5显式`execution_limits.profile="scaleout_ep32/1"`启用；没有profile的输入和旧输出字段保持。

[操作示例](../../examples/scaleout_rdma_transactions/README.md#显式扩展到scale-out事务容量)、[规范](../../docs/tasks/transaction_scaleout_profile_spec.md)、[核验摘要](verification.json)、[网页](report.html)。独立核验程序[audit.py](audit.py)只读输入、实际包、最终窗口和控制记录，不导入平台。人工输入生成器在[tests/integration](../../tests/integration/make_transaction_scaleout_input.py)，不用于研究工作量。

本轮只选128端点Clos：16台服务器、8 ToR、8 Agg、4 Core、168链路，400Gbps端口/1000ns链路延迟，机内旁路关闭。人工容量例只有32个活动端点（32—63），65连接含一条self，96窗口/64NIC注册，66远端或self操作、3本地操作。self采用10/20ns显式服务，没有跨交换机包；本地10ns只是功能输入，不是GPU测量。32个等待条件、67个前驱及回调后追加完整保留。这不是MoE路由、满载128卡或现代GIN。

七个纯C++程序共184,962项断言，Python覆盖82个不同方法（初次回归79，后续仅重查改动后的9项，新增3项）；包括旧边界、扩展边界、越界/权限/源保护、非法候选不改输入、依赖128/129、会话255/256及1023/1024边界。C++完整候选在不构造Fabric的验证函数中检查。固定八个原生库和Fabric归档hash一致，四个受影响单元独立重编为probe/session；ldd确认加载复制的同一套库。13依赖检查一致，无安装/下载/升级/GPU。

三个新网络各运行一次，未重跑成功网络：

| 场景 | 实际丢弃 | 逻辑任务完成 | 网络排空 | 源payload包捕获 |
|---|---:|---:|---:|---:|
| 显式扩展、正常 | 0 | 8.036µs | 8.036µs | 66 |
| 同例、connection0首请求丢弃一次 | 1 | 8.047µs | 10.019µs | 67 |
| 无profile的旧选择重传事务链 | 0 | 10.173µs | 12.175µs | 11 |

独立重放全部窗口，逐字节相同；正常/故障96窗口均49,152B且两者最终相同。检查全部源捕获、唯一接收区间、MRT1请求/原子回复及目标操作，每个扩展例66个网络帧＋一个self请求，网络唯一有效载荷2,632B。正常/故障各339回调、3项依赖回调后追加核对；兼容例三窗口196,608B、7网络帧、5目标操作，旧输出没有execution_limits字段。

故障中第二包在2,006ns先到并缓存，4,010ns反馈触发快重传，6,014ns缺口补齐，不能称超时重传收益。受损原子事务完成4,013→8,017ns，但另一并行分支原来约8,016ns才完成，因此最后本地工作仅晚11ns；10,019ns是反向原子回复的ACK到齐。任务完成与网络排空分别报告，不把1,983ns排空差包装为推理损失，更不是新算法收益。

预算根`runs/transaction_scaleout_profile_20261031`：累计2400受监控秒/新增3GiB/卷留8GiB；构建阶段≤900秒、其他阶段≤180秒，进程组RSS3GiB/单进程地址6GiB，单结果512MiB。实际阶段统计、命令、失败和源码hash见verification.json；统计只累计顶层监控，内层编译/运行不重复计时。保留首版构建元数据路径类型错误、输入/descriptor验证问题及内存测试大小端预期错误；没有因此扩大预算或重跑成功网络。HTML只做静态链接检查，未验浏览器渲染。

从空输出目录复现：按示例创建拓扑，用已构建session分别运行normal/loss，用probe运行selective-chain兼容例。随后：

```bash
python3 -B fixtures/transaction_scaleout_profile/audit.py \
  --run runs/transaction_scaleout_profile_20261031 \
  --output runs/transaction-scaleout-independent-audit.json
```

此程序期待run目录有normal/loss/compat三目录的原始result，前两项另有control.jsonl。它不执行新网络。实际命令和构建来源可从本地运行目录核查；新检出没有原生预建库时先用标准rdma-build，不依赖本机旧产物才能生成输入。

本增量没有接通现代原生包装的完整EP32连续调用：旧现代容量报告仍有效，须在下一独立平台增量显式接入并再做真实工作量验收。整体研究目标保持active。
