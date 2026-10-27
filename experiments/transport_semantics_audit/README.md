# 事务载体与网络评价边界

先看[结果网页](results/20261027/report.html)或[中文报告](results/20261027/report.md)。本轮只读取已关闭实验的原件，未运行新网络、GPU或构建；[事前协议](protocol.md)固定输入、解释和360秒/128MiB预算。

两条件各140个实际MRT1帧、280个源数据包。每包都保持work边界；4480B功能头只占帧字节约1.493%，按原2000B载荷MTU去头后包数仍是280，不能据此新增去头加速功能。更实质的边界是48次远端fetch-add各有独立Reply及其ACK：原子相关源数据包与ACK共192个，占本次全部560个源数据包与ACK的34.286%，不含CNP或逐跳复制。RXE的具体成功分支则用Atomic ACK携带旧值；本轮不将其当NVIDIA网卡校准或固定生产包数。

single/quad的全部事务分别在64612/81969ns完成，而最后Reply确认到70636/88017ns才到，差6024/6048ns。应用阶段更早结束；三个口径分别报告。它们是功能载体状态，不能作为删除这些事件后仍可获得的推理收益。结论是先补可信的操作级RC参考，再用同一协议比较两项仿真候选；本轮没有建立新算法、仿真加速或现代RoCE性能贡献。

独立核验按逐字节流重组帧，检查目标body、原子旧值、各work/源包边界、实际ACK反向地址和完成时刻；五类篡改拒绝通过。[汇总](results/20261027/summary.json)及来源/历史函数检查随Git保存，详细逐帧analysis.json、源码与原始包留本地归档，不复制上游源码入Git。

原件位于 `runs/transport_semantics_audit_20261027/`。复制源码/空cwd的离线恢复对分析、Markdown/HTML和核验逐字节比较，不访问旧运行目录、不重新仿真或下载。归档标识见[收据](results/20261027/checkpoint.json)，复核方式如下：

```bash
python3 -I -B /path/to/copied/source/study.py analyze \
  --run /path/to/evidence --output /path/to/new-analysis
python3 -I -B /path/to/copied/source/verify.py \
  --run /path/to/evidence --output /path/to/new-verification.json
python3 -I -B /path/to/copied/source/checkpoint.py restore \
  --archive /path/to/checkpoint.tar.gz --sha256 SHA256_FROM_RECEIPT \
  --output /path/to/new-recovery
```

结果保留旧四核心严格回调排列比较失败，不重判或重开其网络。Linux来源仅有原保存的v6.12 tag及SHA256，未独立锁定Git tree；源码参考、功能验收、真实数据和硬件校准的证据等级保持区分。两个论文创新及总体目标仍进行中。
