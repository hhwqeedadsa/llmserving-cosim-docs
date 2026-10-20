# MoE推理全栈联合仿真：三页汇报材料

日期：2026-10-20（北京时间）。本版在原两页正文前增加“李润宇”封面，并把正文和讲稿调整为同时关注Scale-Up与Scale-Out。两页正文保留原有图表结构；封面沿用用户提供的PPT模板第一页版式。

## 交付

- [可编辑PPTX](MoE推理全栈联合仿真_三页_李润宇.pptx)与[三页PDF](MoE推理全栈联合仿真_三页_李润宇.pdf)：封面、当前全栈链路、拟增加的机内与跨机网络细节。
- [逐页讲稿](讲稿.md)、[可编辑DOCX](配套讲稿.docx)、[三页讲稿PDF](配套讲稿.pdf)：正文也保存在PPT备注中。
- `preview/`：六张由实际PDF生成的逐页预览；`manifest.json`、`render_record.json`和`verification.json`：模板身份、生成与检查记录。

## 内容与证据边界

Scale-Up使用独立的ns-3-UB后端，已有128计算端点、1024条真实来源请求的完整首轮及出口服务对照；Scale-Out使用独立的原生RDMA ns-3后端，已有4卡真实R1片段及128、256、512卡不同任务的完成记录。两条后端不合并运行时间，规模不同的任务也不用于计算扩卡收益。Scale-Out的1024卡两次在600秒预算处超时，目标硬件计算成本仍待校准。幻灯片第三页列出的新增网络细节和优化对照属于下一步研究，不是既有实现或已证实收益。

数据与限制分别见[Scale-Up完整首轮](../../../experiments/scaleup_observability/results-representative.md)、[Scale-Out真实路由](../../../experiments/moe_trace_hotspots/results/four_requests_20261018/report.md)、[Scale-Out规模记录](../../../experiments/scaleout_scaling/results/runtime_20261016/report.md)、[架构说明](../../architecture.md)及[宏微结合研究](../../reviews/宏微结合与硬件联合仿真研究_20261020.md)。

## 复现和验证

使用本机已有的Python、Pillow、Matplotlib、LibreOffice Impress与Poppler；本次没有安装依赖、运行仿真或硬件测量。原始模板不修改、不提交；路径及SHA-256见`manifest.json`。在仓库根目录运行：

```bash
python3 docs/review_artifacts/fullstack_brief_20261020/build_materials.py --template '/path/to/original.pptx'
python3 docs/review_artifacts/fullstack_brief_20261020/render_manuscript.py
libreoffice --headless --convert-to pdf --outdir docs/review_artifacts/fullstack_brief_20261020 docs/review_artifacts/fullstack_brief_20261020/MoE推理全栈联合仿真_三页_李润宇.pptx
python3 docs/review_artifacts/fullstack_brief_20261020/verify_materials.py
```

验证脚本检查三页及封面/正文原模板布局、26个设计部件逐字节一致、PDF各页可见文字、备注与讲稿/DOCX全文一致、来源摘要和PPTX/DOCX重复生成一致。讲稿DOCX通过OOXML结构与全文检查；本机LibreOffice缺少Writer，因此不宣称它经过Word/Writer视觉渲染。最终PPT与讲稿PDF预览已经逐页目视检查。
