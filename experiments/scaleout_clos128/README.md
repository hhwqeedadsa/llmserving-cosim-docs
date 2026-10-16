# 完整 Qwen3 结构与百卡三层 Clos 验收

目标是验证 48 层主流规模 MoE 的完整依赖图、机内/跨机网络和真实下一轮调度，不用固定时刻流量回放替代服务。配置和字段见[平台用户示例](../../examples/scaleout_clos128/README.md)，[方法 PDF](../../docs/review_artifacts/ScaleOut实验方法_审阅稿_v0.3_20261016.pdf)为实验实施前后的审阅快照，其状态不随之后运行改写。

## 已有结果

| GPU / EP | 逻辑消息 | 已核验节点 | 两轮结束 / ns | 机内 payload / B | 跨机 payload / B |
|---|---:|---:|---:|---:|---:|
| 16 / 16 | 18876 | 50168 | 1846967 | 43868160 | 50200576 |
| 32 / 32 | 43912 | 112656 | 1966816 | 43229184 | 151838720 |
| 128 / 128 | 195082 | 489492 | 2121369 | 44810240 | 754245632 |

每例为完整 48 层加首尾、两轮 ready-KV Decode，B=1、context=2048、模型约305.3亿参数。EP、全局 token 数、拓扑及权重复制同时改变，本表不是强扩展效率或 H100 实机预测。128 卡另独立核验全部256个 rank-wave 的48层和首尾阶段，以及98520条跨Pod消息的实际六链路路径。

原128卡运行正常退出但校验失败，原因是默认滚动日志覆盖早期节点。修复后的完整节点日志使逐节点因果核验通过；后续元数据和终态表示优化保持128卡 `result.json` 字节相同。相应执行版本不同，这证明表示优化的本例结果保持，不冒充同版本独立重跑。最终证据在[平台验收摘要](../../fixtures/scaleout_scaled/README.md)，原始失败、修复及优化运行归档于本地 `runs/scaleout_clos128_20261016/evidence.tar.gz`。

## 复现

使用已有非UB后端和前端依赖环境，从仓库根目录执行：

```bash
PYTHONPATH=python /path/to/frontend/python experiments/scaleout_clos128/run.py \
  --backend-json /path/to/rdma-build/backend.json \
  --protoc /path/to/protoc --output /dev/shm/clos-full-new --sizes 16 32 128
```

脚本首先执行跨机身份和机内单流/incast微基准，再依次通过公共CLI生成拓扑、运行服务和独立 `rdma-check`。`--sizes` 可以选择子集，已有输出拒绝覆盖。脚本保存命令、每例验证及资源、运行开始的源码/后端哈希，并拒绝执行期间源码变化。推荐外层使用 `moe_ub_cosim.host_probe`：宿主1100秒、RSS6 GiB/地址空间12 GiB、累计3 GiB、卷预留8 GiB；各实际服务仍为协议配置中的300秒和100ms仿真上限。

模型维度、batch、上下文、各层带宽及机内参数修改后生成新输入和新输出，先核算消息、显存及资源。不把 GPU 类型标签当成本表，不把 summary 队列当完整时序，不自动扩限。多副本向千卡扩展的对照和复现见[规模实验](../scaleout_scaling/README.md)。
