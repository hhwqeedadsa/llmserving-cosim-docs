# 百卡 Clos 实验方法审阅稿

[PDF v0.3](../../../docs/review_artifacts/ScaleOut实验方法_审阅稿_v0.3_20261016.pdf) 共 9 页，面向用户审阅实验方法；[可编辑原文](method_review_v0.3.md)与[生成程序](render_method.py)一并保留。内容包括模型和负载定义、机内与三层 Clos、配对对照、热点来源、千卡 EP 部署、因果验收、指标和复现边界。

本次只整理文档，没有新建构建、仿真或参数扫描。16/32 卡已有通过证据；128 卡原生子进程完成，但后续服务校验缺少 ASTRA issue 事件，不能称为百卡验收通过。[证据快照](evidence_snapshot.json)保留本稿引用的字段及原记录 SHA-256；它不替代原始完整运行记录。当前千卡结构为设计候选，不是执行结果。

以上为 PDF 生成时的历史状态，原稿不随后续执行改写。后续已经定位并修复滚动日志覆盖问题，128 卡通过了完整节点与消息校验；最新执行状态统一见[任务记录](../../../docs/tasks/current.md)，规模扩展方法见[多 EP 组实验](../../scaleout_scaling/README.md)。

使用已有 Matplotlib、Pillow 和中文字体生成；不安装依赖。生成器拒绝覆盖已有 PDF，修订时换版本号和输出路径：

```bash
python3 experiments/scaleout_clos128/documents/render_method.py \
  --source experiments/scaleout_clos128/documents/method_review_v0.3.md \
  --output /tmp/ScaleOut实验方法_v0.3_review.pdf \
  --revision v0.3 --date 2026-10-16 \
  --footer '百卡 Clos / 机内互连 / 千卡路线；方法审阅'
```

旁侧 manifest 保存源文、排版程序、字体和 PDF 的 SHA-256。已检查 A4 9 页、中文字体嵌入与文本检索，并逐页预览；本地检查产物位于 `runs/scaleout_method_pdf_v03_20261016/`。
