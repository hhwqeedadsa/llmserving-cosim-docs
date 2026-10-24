# 有界复制型事务缓冲验收

本增量按[预注册范围](../../docs/tasks/bounded_copied_buffers_spec.md)实现，默认结果保持，公开输入为[bounded-buffers.json](../../examples/scaleout_rdma_transactions/bounded-buffers.json)。它补齐失败副本与资源背压的功能语义，不是新恢复算法、现代GIN性能模型或论文创新证据。

原始阶段：`/dev/shm/moe-bounded-buffers-20261024/`。`verification.json`汇总实际结果、`independent-audit.json`重放数据和ACK、`recovery.json`记录新目录公开网络复现、`closeout.json`记录资源与身份。持久检查点保存在`runs/bounded_buffers_checkpoint_20261024/`，它是同机持久副本，不是异机备份。

9项新功能条件和1项旧对照涵盖默认完整结果、足量池、单帧复用、同源跨连接、不同源独立、失败保留与阻塞、实际尾包丢失和中段丢失/NACK。另保留2次故障规则未命中的诊断执行；它们不能作为实际丢包证据。独立检查覆盖全部12份结果，76次源发送、24次目标副作用，并拒绝提前释放、虚报峰值和篡改输出。10类绕过前端的原生拒绝用例及7项新单元检查通过；34项既有事务检查、4组相关原生流程和13项固定依赖检查通过。

首次NACK附加检查因故障规则误写发送端node0而未产生丢包，断言失败；原始tail条件同样未命中。保留原输入/结果/失败脚本与原因，只把接收端改为node4并执行两个修正条件，明确要求各发生一次drop，NACK条件还要求源端实际收到NACK。没有重跑不受影响网络或提高预算。

## 复核

先阅读脚本；在仓库根运行，输出使用新路径。已有原件不必重跑网络：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B tests/integration/audit_bounded_buffers.py \
  --functional /dev/shm/moe-bounded-buffers-20261024/functional \
  --additional-functional /dev/shm/moe-bounded-buffers-20261024/nack \
  --additional-functional /dev/shm/moe-bounded-buffers-20261024/corrected-tail \
  --additional-functional /dev/shm/moe-bounded-buffers-20261024/corrected-nack \
  --output /dev/shm/NEW_BOUNDED_AUDIT.json
```

`build.py`重编所有5个布局消费者并链接3入口，复用固定库，不安装依赖。具体命令及身份保存在检查点。`checkpoint.py`提供archive/recover/replay：严格校验每个成员、拒绝链接与越界路径，恢复后在新源码根与空cwd调用公开CLI；显式使用包内8份ns-3库，比较完整结果和独立审计。恢复仍需要宿主Linux/Python/动态加载器及兼容ABI，不是从零重编第三方依赖。

每命令60秒、采样RSS1GiB、地址2GiB、产物64MiB；阶段累计受监控600秒/新产物256MiB，含失败、构建、归档、恢复和持久副本，tmpfs预留8GiB，根分区预留64MiB。实际计量见closeout.json，功能测量的数十微秒不能解释为硬件速度或收益。
