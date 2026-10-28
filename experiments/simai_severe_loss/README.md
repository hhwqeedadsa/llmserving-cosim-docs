# 真实路由并发任务的严重丢包实验

先读[预先登记方法与资源限额](protocol.md)。公开平台功能、研究方法和结果分开维护。本目录不导入tests、平台内部控制器或旧研究输出，执行只调用公开CLI。

在仓库根使用已安装SimAI依赖的Python，设置PYTHONPATH=python：

```bash
python experiments/simai_severe_loss/run.py prepare --root runs/my-severe-study
python experiments/simai_severe_loss/run.py run --root runs/my-severe-study --runtime "$SIMAI_RUNTIME" --backend "$NATIVE_SERVICE" --protoc "$PROTOC"
python experiments/simai_severe_loss/run.py audit --root runs/my-severe-study
python experiments/simai_severe_loss/verify.py --root runs/my-severe-study --output runs/my-severe-study/independent-verification.json
OPENBLAS_NUM_THREADS=1 python experiments/simai_severe_loss/render.py --root runs/my-severe-study --output runs/my-severe-study/web
python experiments/simai_severe_loss/audit_report.py --root runs/my-severe-study --report runs/my-severe-study/web --output runs/my-severe-study/web-check.json
```

准备阶段读取`data/moe-trace-small/`的15份合格原件并核对固定intake哈希。真实原件不进Git；缺失时明确失败，不以人工fixture替代。运行输出目录拒绝覆盖，支持`--case`分条件执行，每个条件只执行一次。持续黑洞的非零退出是预期结果，但必须保存完整抓包、失败原因和未完成曲线。

`verify.py`是可读取原始来源真值的独立评价程序；绘图只使用它的审核汇总和公开arrival导出。绘图需要已有matplotlib环境，网页检查使用node V8核脚本与DOM替身，不冒称完成浏览器布局自动化。CPU调度预检在本轮原件中保存为cpu-scheduler-check.json；网页将其作为补充证据，正常复现时若未执行该独立诊断，可不提供此文件。

本轮交付：[离线结果网页](results/20261028/report.html)、[文字结果](results/20261028/report.md)。严重故障与带宽瓶颈代价已经测出，未将成熟恢复或平台接入称为新算法。25G无人工丢包的126次超时中，86次已有进度ACK在途中，为下一轮核对RTO与排队的匹配提供依据。
