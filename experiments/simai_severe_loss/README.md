# 真实路由并发任务的严重丢包实验

先读[预先登记方法与资源限额](protocol.md)。公开平台功能、研究方法和结果分开维护。本目录不导入tests、平台内部控制器或旧研究输出，执行只调用公开CLI。

在仓库根使用已安装SimAI依赖的Python，设置PYTHONPATH=python：

```bash
python experiments/simai_severe_loss/run.py prepare --root runs/my-severe-study
python experiments/simai_severe_loss/run.py run --root runs/my-severe-study --runtime "$SIMAI_RUNTIME" --backend "$NATIVE_SERVICE" --protoc "$PROTOC"
python experiments/simai_severe_loss/run.py audit --root runs/my-severe-study
```

准备阶段读取`data/moe-trace-small/`的15份合格原件并核对固定intake哈希。真实原件不进Git；缺失时明确失败，不以人工fixture替代。运行输出目录拒绝覆盖，支持`--case`分条件执行，每个条件只执行一次。持续黑洞的非零退出是预期结果，但必须保存完整抓包、失败原因和未完成曲线。
