# 发送发布与反馈边界核查结果

本轮核对旧低延迟和现代GIN两条路径的发送语义，定位“准备数据”和“发布给网卡”的具体差别。没有新增网络或修改平台默认，也未取得新的性能收益。

- 在同一15真实请求、四EP32组、两层两个Decode步的选择上，旧低延迟RDMA-only投影每方向463次数据put、126,976次原子通知，其中126,513次零token。其同步发布条件下，每个非空source/expert上下文只有一次put；数据本身不触发四条批次发布，后面的通知触发postsend。真实async模式未取得，这不是硬件发包时间测量。
- 注册key边界可将同一14352B说明性put拆成1、2、3条WQE。地址、key布局和实际保留顺序不在专家选择中；CQ update请求不能拿来数ACK。
- 当前direct抽象的778条逻辑消息/11,228,096B另列；其rank-reduced Combine与旧低延迟不同。现代DeepEP调用GIN，不能照搬旧库的四条发布策略。固定NCCL/DOCA的QP超时设置只证明有配置入口，实际值和匹配组合构建仍未知。

接下来先按[实现要求](../../docs/tasks/posting_boundary_spec.md)交付每QP保留、写就绪和发布前缀，再选择库模式接入网络。低延迟EP32/E256完整计划的既有限额问题仍在，网络接入前需解决有界表示；不能删零token通知、缩小拓扑或在失败后自动提限。批处理本身已有先例，任务进度调度的创新收益尚未证明。

[网页](results/20261030/report.html)、[详细报告](results/20261030/README.md)、[真实选择投影](results/20261030/projections.json)、[逐窗口核算](results/20261030/analysis.json)、[源码身份](results/20261030/sources.json)、[独立验证](results/20261030/verification.json)、[执行收据](results/20261030/receipt.json)。源码6份逐一核验，262,144个source/expert项独立枚举，四类篡改拒绝；266份原始文件hash保持。

方法提交`17ea4ed80e`，实际命令和退出状态见收据及`runs/library_posting_audit_20261030/monitors/*/probe.json`。按README依次prepare→analyze→verify→publish；独立新目录复现，拒绝覆盖。最后运行：

```bash
.venv/frontend/bin/python -B experiments/library_posting_audit/closeout.py \
  --stage runs/library_posting_audit_20261030 \
  --delivery experiments/library_posting_audit/results/20261030
```

收尾程序为结果取得后增加，身份在收据单列，不冒充原冻结分析方法。4个监控阶段合计1.524秒，峰值采样组RSS154,406,912B、产物827,392B；预检与收尾不在该监控总数内。零下载、安装、构建、GPU或新网络；网页仅静态检查，模型仍未硬件校准，整体目标继续。
