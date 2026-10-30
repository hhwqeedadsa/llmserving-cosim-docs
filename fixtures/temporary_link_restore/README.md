# 临时交换机断链的功能验收

公开操作见[恢复示例](../../examples/scaleout_link_restore/README.md)，要求见[平台规范](../../docs/tasks/temporary_link_restore_spec.md)，可读结果见[网页](report.html)，原始核验摘要见[verification.json](verification.json)，源码/二进制/资源身份见[receipt.json](receipt.json)。本轮为平台功能，不是新恢复协议或研究收益实验。

## 验证了什么

所有实际网络均128端点Clos/20交换机；GPU类型NONE、4条人工字节连接，各先64,000B后16,000B，追加工作等待声明100µs本地步骤。使用SR选择性重传及参考DCQCN；不需要真实路由账户、模型或GPU。一次公开完整构建，只链接一套固定原生ns-3，没有修改子模块。

| 条件 | 实际核验 |
|---|---|
| 旧/新正常对照 | 两个128Clos运行的完整原始JSON除loaded_libraries外一致；各320个源数据包，无物理丢包 |
| 旧断链通知晚到 | 4µs关闭、24µs恢复及获知恢复，300µs旧断链通知被忽略；route_epoch保持1。工作在110.791µs已全部ACK，仍等到300µs处理未来事件才关闭 |
| 先获知故障、后恢复 | 16µs获知关闭、24µs物理上线、36µs获知恢复。启用本地剔除，核对两端关闭/恢复时的四条本地记录，原边实际重用 |
| 物理恢复早于旧通知 | 10µs物理上线、20µs合法获知旧关闭、30µs获知恢复；不通过物理真值跳过20–30µs控制面排除，实际入队/原生哈希独立核对 |
| 发送未完成时恢复 | ToR128端口17在1,021ns开始发送1,036B，100Gb/s发送持续83ns；1,050–1,051ns短断链跨过BUSY状态。保留同一原发送，该包随后真实接收；全部端口TX检查不重叠 |

各条件仅执行一次。共18,556次物理发送逐跳与对端RX/物理丢弃配对；9,785条DATA/ACK/NACK入队独立按已到通知、BFS和线上端口哈希核验。单ns状态边界的跨记录顺序不补造。源字节/ACK/SACK重放、每QP一次创建、空闲追加序号连续、队列积分、选定MMU终态、全网物理排空均通过。关闭时真实回收端口95,312B排队数据，未恢复丢弃的队列。

34项单位检查通过（恢复输入11项，RDMA/持久/RC相关23项）。16类非法日程同时被Python和直接原生入口拒绝，包括合法前项+非法后项、越界/布尔/浮点恢复时刻、版本字段错配、重复及非交换机边；原生验证位于建网前。6类原记录篡改被拒绝：旧通知覆盖新版本、错误代次、用物理真值替代控制已知状态、错误上线时间、虚假早ACK及残余MMU。

## 复现与原件

本机原件在`runs/temporary_link_restore_20261030`，含新完整构建、公开命令及监控、独立准备输入、六个network原件、native-rejects和逐级verification。固定原生依赖及源码60文件的身份保存于receipt；13项依赖检查通过。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B -m fixtures.temporary_link_restore.audit \
  --stage /path/to/retained-run-root --output /path/to/new-verification.json
.venv/frontend/bin/python -B -m fixtures.temporary_link_restore.tamper \
  --stage /path/to/retained-run-root --output /path/to/new-tamper-verification.json
```

独立新运行用公开prepare与rdma-stream-probe；完整六条件由`run.py --stage ... --old-build ... --build ...`调用公开CLI，stage先通过公开prepare生成input，两个build分别提供已验证的旧/新backend.json。目录必须不存在，不覆盖原件。审核与展示程序不参与在线协议。

预算为1800受监控秒/新增2GiB/卷留8GiB；一次公开构建compile600秒/并行2，外层900秒/RSS2GiB/地址4GiB；其余阶段120秒/RSS1GiB/地址2GiB，最多六新网络。实际约437.011受监控秒，峰值组RSS1,802,297,344B、产物峰值110,198,784B，均在预算内。全部受监控阶段成功；无失败网络、重跑、安装、下载、GPU或依赖修改。构建保留固定来源的编译警告，未关闭检查来绕过失败。

## 限定

这是到达瞬间链路状态的功能模型。没有模拟发送中途比特/CRC损坏、光模块训练、分布式路由协议和实际控制报文，也没有校准检测/编程时延。1ns窗口不是现实丢包率实验；没有新增生产MoE、完整LLMServingSim恢复性能、PFC阈值故障恢复或256/512端点实际执行证据。重传、链路上线和版本检查是已有系统方法，不作为论文主创新；总体目标仍active。
