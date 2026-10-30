# 备用贡献与原调用完成约束

本实验回答：备用结果提前返回后，为什么同token、原图和推理服务可能仍不能提前完成？读取已有128端点Clos的正常、局部故障、相同故障备用三组公开导出记录，追踪全部调用与实际必要依赖；不执行网络，不生成删除门限后的假想时延。

分析输入为正常通过`scaleout rdma-check`的运行目录，三个子目录名分别为`normal/local/helper`。每组须有原始图、执行事件、贡献绑定和共享计算报告；当前实例是四份独立人工来源、两个EP64、两层两步，不代表完整R1或生产性能。可通过[共享资源备用示例](../../examples/scaleout_inflight_helpers/README.md)独立准备新的输入和执行，旧大原件不提交Git。

程序在`analysis/analyze.py`。参数均为显式目录；不覆盖输出：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B \
  experiments/recovery_completion_limits/analysis/analyze.py \
  --runs runs/inflight_helpers_20261030 \
  --deep-ep-sources data/research/deepep_modern_20261022 \
  --interface-sources data/research/invocation_semantics_20261023 \
  --output /path/to/new-analysis
```

以上两份来源目录仅为本机示例路径。源码原件不提交；新机器可按[固定来源清单](results/20261030/sources.json)准备五个公共文件，置于`来源根/sources/<path>`，清单复制为`来源根/sources.json`，两个source参数均可指向此根。程序接受该JSON数组，或先前抓取的`sources`对象清单；读取时同时验证SHA-256、Git blob SHA-1和固定提交，不联网、不安装或静默更新。固定版本为DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`。

输出`analysis.json`包含每个调用的输入到达、门限、实际计算开始、原/获胜结果及原图退休；每个门限的最后到达成员和实际限制前驱；以及布局载荷之外的观测边界。`sources.json`保存五份源码身份、公开URL及45行短摘录；`report.html`方便阅读。程序核对原Combine接收节点仍是实际merge前驱，且实际图没有helper消息消费节点，不能仅凭贡献表的赢家推断图已替代原依赖。

[本次分析](results/20261030/report.html)覆盖每条件16调用/32贡献包。只有一个贡献获得62.002us提前，全部调用结果到齐时刻均无变化，应用收益0%；下一步需要真实的可替代执行语义，而非删除现有必要屏障。报告也列出完整调用副本重执行与分块/贡献消费两条路线的必要条件，均尚未完成或验证收益。

这个结果可由现有依赖定义直接推出，本轮执行核对用于排除错误解释，不作为新机制、DeepEP缺陷或论文创新。分析自身的检查在`tests/check.py`，包含原件重建相等及漏原接收前驱、提前门限、helper混入原图、源码SHA变化四类拒绝；支持用新输出目录重做，不执行网络。

固定源码的异步返回不证明输出可提前消费；direct结束屏障不推广到全部版本、全部路径或其他库。源ACK是当前传输投影，不能直接等同商用CQ或GPU可见性。输入到门限的间隔有重叠，不能累加成业务收益。当前`compute_begin_ns`对原尝试表示ASTRA issue，实际GPU开始以共享队列start字段为准；本组三条件原专家队列等待均0，不代表繁忙场景。
