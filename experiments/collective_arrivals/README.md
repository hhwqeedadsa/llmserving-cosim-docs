# 真实专家路由的两端统计与collective到达曲线

使用原四份DeepSeek-R1真实专家选择，执行EP4、源层3—4、两步Decode的有限片段，比较正常与数据/最终ACK丢失。网络为跨两Pod的100G接入/25G上层Clos，全部阶段共享同一网络状态。方法、目标选择规则和预算见[protocol.md](protocol.md)。

从仓库根目录执行，输出目录必须不存在；后端为既有`rdma-build`的service，不重新编译：

```bash
PYTHONPATH=python .venv/frontend/bin/python experiments/collective_arrivals/run.py \
  --repository /home/lry/moe-ub-cosim \
  --backend /path/to/ns3.42-moe-rdma-service --protoc /path/to/protoc \
  --output /path/to/new-study
```

准备会逐hash核对`data/moe-trace-small/manifest.json`原四份文件，不下载、不复制请求。`run.py`只通过公开CLI执行两个服务和四个离线`rdma-arrivals`。失败即停止，不自动重跑。原始结果和快照保存在运行目录；`*-audit/`提供全任务统计及所选collective的CSV。计算为声明值，不执行GPU数值计算；原模型含MLA与Dense，本例仅代表其两个MoE层的网络片段。

通用命令、接受字节的定义和百B形状声明见[使用说明](../../examples/scaleout_arrivals/README.md)。

首次启动误用系统Python，缺`google.protobuf`，在启动原生网络前失败，旧日志保留于`runs/collective_arrivals_20261028/`。修正为已有`.venv/frontend`并在准备前检查依赖；新目录执行同一预定两条件，未重跑任何已执行的网络。额度仍累计600秒/256MiB、不安装依赖。
