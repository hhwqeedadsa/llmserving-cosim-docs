# 实验方法审阅稿 v0.4

[十页 PDF](../../../docs/review_artifacts/ScaleOut实验方法_审阅稿_v0.4_20261016.pdf)和[可编辑原文](method_review_v0.4.md)面向方法审阅，保留原 [v0.3](method_review_v0.3.md) 的历史状态，不将旧稿中的验收进度延续为当前事实。本次仅生成文档，不执行新的构建、仿真或参数矩阵。

本稿集中说明网络研究问题、前端逻辑 trace 与后端事件反馈、模型/batch/GPU 声明、All-to-All-v 和 EP、副本、可配置分层拓扑、对照与热点分析、宿主运行成本、正确性和阶段产出。拓扑生成、单 rail 业务执行与尚待验收的双平面分流分别标明；训练 DP、真实路由、硬件校准不写作已完成能力。

采用已有 Matplotlib、Pillow 和文泉驿字体。复现时选择尚不存在的输出路径：

```bash
python3 experiments/scaleout_clos128/documents/render_method.py \
  --source experiments/scaleout_clos128/documents/method_review_v0.4.md \
  --output /tmp/ScaleOut实验方法_v0.4_review.pdf \
  --revision v0.4 --date 2026-10-16 \
  --title 'MoE Scale-Out 网络联合仿真实验方法' \
  --footer '参数化拓扑 / 专家通信 / 规模成本；方法审阅'
```

旁侧 manifest 记录原文、生成器、字体与 PDF 的 SHA-256。检查范围为 A4 十页、字体嵌入、中文文本提取和逐页排版；预览保存在本地忽略目录 `runs/scaleout_method_pdf_v04_20261016/`。这些文档检查不代表新仿真通过。
