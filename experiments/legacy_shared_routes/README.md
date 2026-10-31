# 真实专家选择下的两个完整旧库任务

本轮判断：成熟选择性重传和显式绕路启用后，局部短时故障是否仍拖累另一个不直接经过坏边的MoE通信组。先判别收益空间，再决定是否值得实现MoE进度感知的恢复资源分配；没有独立增益时正常报告，不加重故障或削弱基线。

## 固定输入及比较

- 一个128端点Clos、20交换机；全部接入及上层链路400Gb/s、每跳1µs，关闭机内旁路，不执行物理GPU。
- 两个独立EP32组，每组256专家、每rank8专家、H7168。调用保留全部旧DeepEP v1.2.1 BF16 Dispatch/Combine通知、缓冲及退休，不把两组变成EP64。
- 来源为仓库中已记录的15份不同R1-AWQ/MMLU原件，按可迁移来源清单顺序划分前8、后7份。每份只取layer3、entry1、token row0的一个Decode token，分别放到本组rank0开始；不复制请求。其余rank仍参与完整通知。AWQ采集精度不证明激活精度，BF16仅功能载体。
- 第一组端点为0、4、…、124。第二组从实际access连线推导：排除第一组和ToR128的端点后，每pod取编号最小的16个。它不以正常结果或故障表现选择部署。两组共享部分Agg/Core及其他ToR，不共享端点。端点不是按连续32个一组接ToR。
- 故障128–132边于4µs关闭、24µs恢复；两次全局路由通知均延迟60µs。对照normal、local（仅剔除原坏候选）、prefix（已有显式绕路，零检测/编程成本，保留封装成本）。所有条件请求、组顺序、资源、可靠传输和完成语义一致，每条件一次。
- 选择性重传初始RTO17µs、最大640µs、至多3次重试，窗口4096B、接收8192B、每上下文队列32。不得事后调整参数制造收益。

## 观测与竞争解释

以公共共享审计核对全部实际字节、源TX/目标RX、操作、发布、最终内存与退休。逐组kernel完成来自唯一原生时钟；物理排空只属于全网，不当作单组时间。真实选择另外与原件hash和Dispatch操作一一核对。

捕获64个任务端点和交换机128、132、133的整个执行窗口，最多100万条。包含实际TX、RX、交换机入队/出队，ACK也按同一stream归组。覆盖坏边和一个公共修复邻居；不是全网所有路径抓包。额外启用全交换机队列积分，以识别可能的共享端口；未捕获端口不能声称逐包归因。

竞争解释及判别：

1. **直接受损误分类**：第二组若在坏边出现DATA、ACK、回复或丢弃，则不能称纯粹旁观任务。须按实际包与端口核对，不只看端点。
2. **共享排队**：比较第二组完成、源重传、共同端口实际发送与捕获端口驻留。没有变慢或仅有相位变化时，不声称故障恢复导致新的瓶颈。
3. **关键前驱切换**：保留每rank完成与最终kernel的实际依赖，沿最晚前驱追到接收屏障/操作完成；不同rank最大值不相加，时刻差不直接等于可消除收益。
4. **旧库或载体成本主导**：完整全对全零token通知、ACK-each-packet、MRT1功能事务和零成本专家拷贝均可能主导稀疏任务。它们不是现代RoCE/GIN、完整R1服务或生产硬件证据。

prefix与normal的差只作观测残差，不称可优化上限。local与prefix的差属于成熟方法收益；本轮不实现或宣称新算法。单次确定性对照没有分布或置信区间，不推广为线上P99。

## 预算与复现

根`runs/legacy_shared_routes_20261031`，累计2400受监控秒、3GiB新增产物、卷留8GiB；每阶段180秒/RSS3GiB/地址6GiB。每次网络产物768MiB/结果512MiB；SR记录50万、参考记录20万、逐包100万、原生逐跳200万。失败保留，不自动扩限或重跑成功网络。复用已验证共享后端，不构建、安装、下载、改依赖或执行GPU。

```bash
export PYTHONPATH=python
python3 -B experiments/legacy_shared_routes/monitor.py --root runs/legacy_shared_routes_20261031 --stage prepare -- \
  python3 -B experiments/legacy_shared_routes/study.py prepare \
  --source-manifest experiments/legacy_real_routes/results/20261031/source-manifest.json \
  --build runs/legacy_shared_20261031/build-v2/backend.json --output runs/legacy_shared_routes_20261031/input
# 各执行一次，CASE依次normal、local、prefix；不覆盖输出。
python3 -B experiments/legacy_shared_routes/monitor.py --root runs/legacy_shared_routes_20261031 --stage run-CASE -- \
  python3 -B experiments/legacy_shared_routes/study.py run --study runs/legacy_shared_routes_20261031/input --case CASE
python3 -B experiments/legacy_shared_routes/monitor.py --root runs/legacy_shared_routes_20261031 --stage audit-CASE -- \
  python3 -B experiments/legacy_shared_routes/study.py audit --study runs/legacy_shared_routes_20261031/input --case CASE
```

准备先核对二进制、全部后端源码和8个固定库hash；公开CLI生成两组计划与拓扑，并以`--validate-only`核对完整计数后冻结。实验只用公共CLI及来源读取接口，不导入旧实验、测试或fixture。原始大文件留在runs，版本化结果另附入口。
