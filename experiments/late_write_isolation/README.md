# 失败后晚到写入：已停止的边界实验

本轮没有证明晚到覆盖，也没有完成安全策略性能对照。平台固定`2cfbaf8`，代码未修改。

- 原[五条件方案](design.md)首个fast-shared在100μs提交新写时，因为未声明同址跨连接副作用顺序而被Python拒绝。旧写6036ns应用、12060ns完成；新写没有提交到原生层。原生EOF是控制器异常退出的后果。其余4条件未执行。
- [范围修订](amendment.md)保留失败，选择两个此前未执行的合法输入。safe-late-isolated又在建网时被拒绝：250μs接入延迟要求交换机8保留9,503,884B，而缓冲只有8,388,608B。没有回调、数据包或result.json，第二个修订条件未执行。

这是实验设计遗漏两条既存约束的负面结果，不能写成新漏洞、恢复机制收益或失败后包仍到达的实测证据。不能使用`run.py`或`safe_run.py`继续既有目录；原矩阵和修订矩阵均已终止。`audit.py`是未验收的原五条件审计器，本轮结果使用`admission_review.py`和`stop_review.py`，不以原审计器成功为验收。

独立核验读取原始TX字节、目标RX/内存、实际回调与未提交命令。三类篡改（虚构已提交新写、修改目标内存、提前回调）均拒绝。第二项失败按固定原生源码的整数公式，逐端口核对保留量；没有提高缓冲或改延迟重跑。

本机完整证据目录为`/dev/shm/moe-late-write-20261024/`；持久离线包与核验收据为`runs/late_write_isolation_checkpoint_20261024/`。包不含运行时程序和8个库，只支持离线复算；实际网络重放须使用上一个平台检查点匹配的程序。平台/程序/库hash保留在manifest。

恢复到一个不存在的新目录，再从空工作目录执行：

```bash
python3 -B /absolute/repo/experiments/late_write_isolation/checkpoint.py restore \
  --archive /absolute/checkpoint.tar.gz --destination /dev/shm/my-late-write-restored
python3 -B /dev/shm/my-late-write-restored/source/experiments/late_write_isolation/stop_review.py \
  --stage /dev/shm/my-late-write-restored/evidence --output /dev/shm/my-late-write-restored/review.json
```

以上每条命令均须用公开`moe_ub_cosim.host_probe`监测，60秒/RSS1GiB/地址2GiB/产物16MiB，tmpfs至少8GiB预留；原轮累计300秒/64MiB含原失败、归档恢复及持久副本。离线结果与包内`evidence/stop-review.json`逐字节比较，不运行网络。

下一项平台改进是将拓扑、带宽、延迟与交换机缓冲的联合可行性检查提前到前端；它属于输入鲁棒性修复，单独提交，不算论文主贡献。后续须重新设计合法的有限窗口恢复问题，不能通过放宽顺序保护来制造覆盖反例。
