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

## 本次结果

[交互网页](results/20261028/report.html)、[文字报告](results/20261028/report.md)、[Dispatch图](results/20261028/dispatch-arrival.png)、[Combine图](results/20261028/combine-arrival.png)和[独立核验](results/20261028/verification.json)已生成。实际运行目录为`runs/collective_arrivals_20261028-frontend/`，含输入快照、原始逐包记录、命令、原生计数和CSV；两个条件都完成，捕获51,904/53,862事件且无截断。76消息、2,609,152有效字节相同；服务333.298→417.998µs，首个Dispatch有效收齐53.917→134.950µs。

离线重建核验和报告使用新输出路径，不启动网络：

```bash
python3 experiments/collective_arrivals/verify.py \
  --stage runs/collective_arrivals_20261028-frontend --output /path/to/new-verification.json
python3 experiments/collective_arrivals/render.py \
  --stage runs/collective_arrivals_20261028-frontend --output /path/to/new-report
```

绘图需要现有Matplotlib，不自动安装。`verify.py`独立核对原件专家选择、两条件图一致、原始TX/RX、曲线增量及故障命中；平台的协议接收重放还逐消息核对原生前缀和data-ready回调。网页脚本语法检查和PNG可视检查通过，本机没有浏览器运行时，未执行浏览器交互自动化。两个实际网络及前端依赖、任务图、通信完成的联合路径已执行。

首次启动误用系统Python，缺`google.protobuf`，在启动原生网络前失败，旧日志保留于`runs/collective_arrivals_20261028/`。修正为已有`.venv/frontend`并在准备前检查依赖；新目录执行同一预定两条件，未重跑任何已执行的网络。额度仍累计600秒/256MiB、不安装依赖。
