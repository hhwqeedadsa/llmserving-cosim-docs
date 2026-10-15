# 小规模非 UB Scale-Out 全流程方法

本目录保存小规模网络机制实验的方法、执行与分析脚本。链路为LLMServingSim→依赖图→ASTRA→单一非UB ns-3→完成回调→下一轮服务，以8个GPU逻辑端点研究网络机制。方法审阅和性能结果分别交付。

先读 [8页PDF审阅稿 v0.2](../../docs/review_artifacts/ScaleOut实验方法_审阅稿_v0.2_20261015.pdf)，可编辑原文是 [method_review_v0.2.md](documents/method_review_v0.2.md)。其中列出输入/消息定义、5种小规模拓扑、10个主实验条件、网络与业务指标、可视化及G0—G3验收；明确千卡静态生成、小图逐包执行、声明GPU成本和故障恢复的边界。非UB运行入口见[用户示例](../../examples/scaleout_rdma/README.md)。原 [plan.md](plan.md) 与 v0.1 PDF 保留为实施前的历史方案，其“尚未实现”不表示当前状态。

仅重新导出文档，在仓库根目录运行，输出选择新路径：

```bash
python3 experiments/scaleout_e2e/documents/render_method.py \
  --source experiments/scaleout_e2e/documents/method_review_v0.2.md \
  --revision v0.2 --footer '实验方法与验证边界；性能结果另报' \
  --output runs/scaleout-method-review-new.pdf
```

需要已有的Matplotlib、Pillow和中文TTF/TTC字体；程序不安装或下载。默认使用本机文泉驿微米黑，可用 `--font` 指向其他已存在字体。PDF为A4、嵌入中文字体、文本可搜索，旁边的manifest记录源文/排版程序/字体及PDF的SHA-256。文中的拓扑数量由配置规则推导，不能当作原生装载成功的证据。

v0.2 PDF生成与检查记录在 `runs/scaleout_method_pdf_v02_20261015/`：8页A4、中文字体嵌入、文本可搜索，并逐页预览。本次文档修订未新增构建或网络执行。当前任务统一见 [任务记录](../../docs/tasks/current.md)。
