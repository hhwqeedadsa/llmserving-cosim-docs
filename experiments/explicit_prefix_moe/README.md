# 真实MoE任务在显式绕路强基线上的剩余等待

问题：旧ECMP候选全失效时，显式段能否解释此前ACK黑洞带来的长等待？在较强成熟绕路与17µs选择性重传之上，还剩多少任务时延和共享网络干扰可供研究？本轮比较成熟方法，不将封装、改路或重传称为论文创新。

## 运行前固定

- 固定平台`43a5dc08b8`的显式修复实现，复用已验收构建，逐源码/程序/库hash核对；不构建、下载、改依赖或修改平台。128端点Clos、20交换机、4个交错EP32组。15份不同R1-AWQ/MMLU原件各一个请求，组内4/4/4/3请求，层3/4、两步单token Decode，每rank8个专家；不是EP128、128卡满载或完整671B执行。
- 独立调用公开准备入口重建输入，保留同一真实专家选择、声明计算、功能标记字节、任务图、原ACK/集合门限、源/目标消息缓冲释放。禁用备用。SR初始17µs、上限640µs、3次重试与DCQCN参考保持。
- 沿用已选128–132共享上行，在4µs双向关闭，24/64µs恢复；关闭/恢复通知均延迟60µs。本地显式段在空候选时启用，已有活候选仍保留原ECMP。中间节点只能读实际线上段，逐两跳真实增加38B，CE传回内层。没有额外硬件检测、处理/路径分发或MTU碎片模型。
- 四个新128Clos网络各一次：`normal`、`local-w60`（原live_ecmp/1）、`prefix-w20`、`prefix-w60`。前两项是新构建适用性核查，完整输出除加载库路径外必须与旧同条件一致。旧normal及global/local/instant的20/60µs条件只读保留；不重跑成功网络，不按新结果改窗口、位置、参数或输入。旧结果已知，不是盲测。旧instant为零延迟全局改表参考，不是数学下界或可部署性能。
- 方法及执行/分析代码先提交；在任何新网络前冻结15份来源、输入、旧对照、源码、程序、库及图hash。首次normal与local核对失败即停，不用旧基线支撑新收益。

## 竞争解释与判别

1. ACK返回黑洞主导：若成立，prefix-w60应使同一消息3983（1→65）无需等到120.754µs重试便获得真实源确认，首Dispatch及相应组应同步提前。对照同一消息/门限并保留其他并列末前驱，不将不同组的全局差直接归给该消息。
2. 原始已丢DATA/ACK与重传退避仍决定剩余等待：追踪实际末前驱、最后DATA/ACK、源post/重试和包路径；数据到齐不代替安全确认。若没有重试或数据损失支撑，就不能把残余normal差称为重传可优化空间。
3. 绕路增加竞争：同时报告4组完成、逐组相对normal/同窗口local的差、源重传、逐跳封装/模型字节、队列峰值和排空。原边被多组共享，不预设其他组“健康”。累计队列驻留、某条路径的额外时间与可消除业务损失分别说明。

收益口径：同窗口local→prefix的最晚任务减少量、占local完成时间比例、收回local相对normal额外等待的比例。prefix相对normal/instant差只作参考，不能当可消除上限。首个调用、全任务和资源排空分别报告；负收益或成熟基线解释全部旧空间均正常收尾。

## 执行、复算及预算

根`runs/explicit_prefix_moe_20261030`：累计1800外层监控秒、新增2GiB、卷留8GiB；每阶段120秒/RSS1GiB/地址2GiB。native60秒/模拟5ms/包与队列200000行、每次512MiB产物/256MiB结果。最多四新网络；失败保留，不抬限、不重跑成功网络、不改平台，不读取真实路由后的统计来改变选路。原始路由不提交Git。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B experiments/explicit_prefix_moe/monitor.py --root /new-root --stage prepare -- \
  .venv/frontend/bin/python -B experiments/explicit_prefix_moe/study.py prepare \
  --source-manifest /path/to/R1-manifest.json --source-base /path/to/manifest-base \
  --build /path/to/verified-explicit-build --reference /path/to/retained-transient-study --output /new-root/study
.venv/frontend/bin/python -B experiments/explicit_prefix_moe/monitor.py --root /new-root --stage normal -- \
  .venv/frontend/bin/python -B experiments/explicit_prefix_moe/study.py run \
  --study /new-root/study --case normal --protoc /path/to/protoc
```

依次运行其余三条件；normal/local-w60各运行后先执行`study.py compatible`。之后`study.py analyze`生成摘要，`verify.py`独立核对物理TX/RX、真实源ACK、原图/路由/字节/最终缓冲及实际显式段/开销，`mechanism.py`复核瓶颈与累计有效数据曲线，`publish.py`生成网页和图。分析不导入旧实验驱动或fixture，方法与平台功能分开提交；整体研究目标active。
