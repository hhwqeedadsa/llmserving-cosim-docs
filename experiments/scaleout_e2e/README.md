# 小规模非 UB Scale-Out 全流程方法

本目录目前只有**待审阅实验方法**与PDF排版程序，尚未开始新的网络仿真实验。目标是打通LLMServingSim→依赖图→ASTRA→单一非UB ns-3→完成回调→下一轮服务，并以8个GPU逻辑端点研究网络机制。

先读 [8页PDF审阅稿](../../docs/review_artifacts/ScaleOut实验方法_审阅稿_v0.1_20261015.pdf)，可编辑原文是 [plan.md](plan.md)。其中列出输入/消息定义、5种小规模拓扑、10个主实验条件、网络与业务指标、可视化及G0—G3验收。所有运行安排都是拟实施方案，非UB在线闭环仍待实现。

仅重新导出文档，在仓库根目录运行，输出选择新路径：

```bash
python3 experiments/scaleout_e2e/documents/render_method.py \
  --output runs/scaleout-method-review-new.pdf
```

需要已有的Matplotlib、Pillow和中文TTF/TTC字体；程序不安装或下载。默认使用本机文泉驿微米黑，可用 `--font` 指向其他已存在字体。PDF为A4、嵌入中文字体、文本可搜索，旁边的manifest记录源文/排版程序/字体及PDF的SHA-256。文中的拓扑数量由配置规则推导，不能当作原生装载成功的证据。

本次PDF生成与检查记录在 `runs/scaleout_method_pdf_20261015_final/`；只生成文档，没有构建、修改后端或运行网络。当前实验状态统一见 [任务记录](../../docs/tasks/current.md)。
