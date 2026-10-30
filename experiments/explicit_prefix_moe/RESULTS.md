# 真实MoE显式绕行：结果与复核

固定平台行为`43a5dc08b8`；事前方法提交`bb4cf2e1c6`，准备启动路径修正`a9105384f5`，然后才冻结输入及运行网络。

- [网页](results/20261030/report.html)、[完整说明](results/20261030/README.md)、[逐组完成与重传](results/20261030/comparison.png)、[累计有效接收与确认](results/20261030/arrival_curves.png)。
- [独立逐包检查](results/20261030/verification.json)、[曲线独立重算和四类篡改拒绝](results/20261030/delivery_verification.json)、[最终资源与身份](results/20261030/receipt.json)。
- [事后机制解释](results/20261030/diagnosis_explanation.md)、[原始包路径与计时器核算](results/20261030/path_diagnosis.json)、[事后分析源码身份](results/20261030/postrun_sources.json)。

128端点Clos、四组EP32、15份不同R1真实专家选择、两层两个Decode步，保持17µs选择性重传及原集合门限。只有四个新网络，各一次；另外五个旧条件只读复用。正常和local-w60原始输出与旧同条件完整一致，仅加载库路径不同。不是完整671B模型、128卡满载、现代DeepEP实现或GPU硬件性能测量。

| 中断持续时间 | local最晚任务 | prefix最晚任务 | 变化 | 相对local |
|---|---:|---:|---:|---:|
| 20µs | 147.706µs | 149.028µs | 晚1.322µs | 慢0.90% |
| 60µs | 244.806µs | 157.628µs | 早87.178µs | 快35.61% |

长故障收回此前额外等待的79.96%；正常为135.773µs，prefix-w60仍多21.855µs。对零延迟全局改表参考仍多8.645µs。这些差距不是已证明可消除的收益，绕行和选择性重传均为成熟方法，不是本轮创新。

关键发现：短故障中，绕行保住部分ACK，当前QP计时器却由此把剩余缺口的重试从18.030µs推迟到29.124µs。最终组1差4.878µs由同消息ready晚4.560µs、末DATA多驻留0.318µs解释。长故障相对instant的组1最终差10.935µs，由所选最终消息ready晚9.443µs、末DATA驻留多2.081µs、减去旧门限余量0.589µs解释；与不同最终组的全局差不混用。已有数据足以否定“少重传必然更快”，尚不能证明新的调度策略收益。

下一步先核对目标网卡的ACK/缺口计时行为及成熟按段丢失检测，再评价利用MoE集合未完成贡献安排有限恢复机会是否有额外价值。不能将仿真计时器行为直接推广到所有RoCE硬件，也不能把缩短RTO直接称为创新。

## 复现与边界

原始运行目录`runs/explicit_prefix_moe_20261030`不提交。输入准备、预算、四个网络及核查命令见[冻结方法](README.md)。事后诊断在四个网络完成后新增，未改变输入、平台或已有结果。按顺序执行：

```bash
.venv/frontend/bin/python -B experiments/explicit_prefix_moe/monitor.py --root /new-root --stage paths -- \
  .venv/frontend/bin/python -B experiments/explicit_prefix_moe/postrun/diagnose_paths.py --stage /new-root
.venv/frontend/bin/python -B experiments/explicit_prefix_moe/monitor.py --root /new-root --stage publish -- \
  env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLCONFIGDIR=/tmp/moe-prefix-moe-matplotlib \
  .venv/frontend/bin/python -B experiments/explicit_prefix_moe/publish.py --stage /new-root --output /new-root/delivery
.venv/frontend/bin/python -B experiments/explicit_prefix_moe/monitor.py --root /new-root --stage explain -- \
  .venv/frontend/bin/python -B experiments/explicit_prefix_moe/postrun/explain.py --stage /new-root --delivery /new-root/delivery
.venv/frontend/bin/python -B experiments/explicit_prefix_moe/monitor.py --root /new-root --stage delivery-check -- \
  .venv/frontend/bin/python -B experiments/explicit_prefix_moe/postrun/verify_delivery.py \
  --stage /new-root --delivery /new-root/delivery
bash scripts/check_deps.sh > /new-root/dependency-check-final.txt
.venv/frontend/bin/python -B experiments/explicit_prefix_moe/postrun/finalize.py \
  --stage /new-root --delivery /new-root/delivery
```

事后诊断中的固定消息ID和时刻只适用于本轮冻结输入，不是通用任务约束。原始来源属于用户本机已取得的15份路由，不从fixture构造真实路由。公开准备/执行入口被独立调用，没有导入旧实验驱动或测试。

受监控阶段通过；启动前曾因调用环境漏设模块路径失败，未运行网络，修正监控器环境并保存失败记录。最终收尾还发现首版交付收据把自身空文件加入hash清单，原closeout因此拒绝；保留[旧收据](results/20261030/delivery_verification-v1.json)，改为目录外生成同一核验程序的输出后再复制，见[修订记录](results/20261030/verification_revision.json)。没有改断言或放宽检查，没有重跑网络。资源总计见receipt.json；峰值进程组RSS509,054,976B，低于事前预算。无构建、安装、下载、依赖修改或GPU。139,892次逐物理跳和7,002消息是九个比较条件合计，不能说成四个新网络的数量。290个有效RX增量用独立bytearray重算；四类篡改分别破坏RX时刻、源ACK时刻、封装字节和源payload，并重新计算结果hash，检查仍拒绝，避免只验证文件身份。

PNG已查看，HTML完成静态资源检查，未声称浏览器渲染通过。封装是项目内IPv4协议250，不是标准RoCE/MPLS/TI-LFA；没有故障检测、硬件处理/路径分发和MTU碎片校准。整体研究目标保持active。
