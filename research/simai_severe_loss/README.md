# 320端点真实路由严重丢包研究交付

网页与完整证据位于`source/_static/severe-loss-20261028/report.html`，Sphinx专题为`source/simai-severe-loss-20261028.rst`。本目录保存实验脚本、预登记方法、资源/命令摘要与平台增量补丁；`publication.json`记录复制来源提交和逐文件哈希。原始路由文件不随文档站分发。

代码来源为研究仓库`baoy21/moe-ub-cosim`：基础`0f53b15`，平台扩展`0d1939e`，研究结果`88b6d03`。这里是文档与代码增量的交付包，不能单独当作安装完毕的仿真器。平台代码需原研究仓库及其固定依赖；不在此处复制完整依赖历史。

## 在研究仓库应用本轮改动

先取得基础提交和锁定依赖、准备原生ns-3可执行程序及固定SimAI runtime。为可复核，先在独立工作分支核补丁，不覆盖现有平台改动：

```bash
git switch -c codex/severe-loss-reproduce 0f53b15
git apply --check /path/to/llmserving-cosim-docs/research/simai_severe_loss/platform.patch
git apply /path/to/llmserving-cosim-docs/research/simai_severe_loss/platform.patch
mkdir -p experiments/simai_severe_loss
cp /path/to/llmserving-cosim-docs/research/simai_severe_loss/{run.py,verify.py,render.py,audit_report.py,protocol.md} experiments/simai_severe_loss/
```

已经含有平台提交`0d1939e`时，不重复应用补丁。按平台用户指南完成原生service构建；旧二进制不支持新`data_loss`字段。补丁包含周期丢包、场景摘要、小例与功能检查，并保留恢复/拥塞机制来源说明。

## 重现实验

在研究仓库根、使用已安装前端依赖的Python环境：

```bash
export PYTHONPATH=python
python experiments/simai_severe_loss/run.py prepare --root runs/my-severe-study
python experiments/simai_severe_loss/run.py run --root runs/my-severe-study --runtime "$SIMAI_RUNTIME" --backend "$NATIVE_SERVICE" --protoc "$PROTOC"
python experiments/simai_severe_loss/run.py audit --root runs/my-severe-study
python experiments/simai_severe_loss/verify.py --root runs/my-severe-study --output runs/my-severe-study/independent-verification.json
OPENBLAS_NUM_THREADS=1 python experiments/simai_severe_loss/render.py --root runs/my-severe-study --output runs/my-severe-study/web
python experiments/simai_severe_loss/audit_report.py --root runs/my-severe-study --report runs/my-severe-study/web --output runs/my-severe-study/web-check.json
```

先按网页`inputs/sources.json`提供对应15份原始JSON到研究仓库`data/moe-trace-small/`，准备阶段核对原有intake哈希。缺失或不匹配会失败，不循环复制请求或用人工fixture替代。高比例丢包为周期性人工注入；持续黑洞的非零退出是预期失败，不是成功完成。

预算、六条件、控制变量及竞争解释见`protocol.md`；原生RDMA和项目适配的区分见`rdma_congestion_loss.md`。绘图需matplotlib，网页脚本检查需node；不自动安装依赖或提高网络运行额度。

## 只审阅结果

直接打开网页，无外部网络请求；同目录含PDF/SVG/PNG和压缩逐包CSV。网页的`artifacts.json`核文件字节，`verification.json`核原件→图→包→曲线。`publication.json`核复制后的交付包；它证明复制一致，不替代仿真执行和硬件校准。
