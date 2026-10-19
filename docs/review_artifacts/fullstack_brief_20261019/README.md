# 全栈仿真两页材料与配套讲稿

日期：2026-10-19（北京时间）。用途：用两页实质内容说明当前全栈仿真思路，以及拟增加的网络仿真细节。仅制作汇报材料，没有新增仿真或硬件测量。

## 交付

- `全栈仿真思路与拟增细节_两页.pptx`：两页可编辑PowerPoint，沿用用户指定模板的内容页布局、紫色标题/横线、母版、主题与媒体；不占用一页单做封面。
- `全栈仿真思路与拟增细节_两页.pdf`：实际由LibreOffice渲染的两页阅读版。
- `讲稿.md`、`配套讲稿.docx`、`配套讲稿.pdf`：逐页讲稿，正文同时进入PPT备注；讲稿PDF和DOCX按两页组织，不等同“两页材料”的额外幻灯片。
- `preview/`：实际PDF栅格预览；`manifest.json`与`verification.json`：模板身份、来源、生成/渲染与检查证据。

## 内容与边界

第一页呈现请求/专家选择→任务与字节→ASTRA依赖执行→原生RDMA ns-3→实际完成反馈。全栈限定为这些层次的因果连接，并非GPU周期级执行或任意模型/训练阶段。第二页将现有基础与计划分别说明，聚焦路由及分层通信、物理连接、协议等待归因、校准和执行规模。

4卡真实R1片段、128卡单EP和256/512卡多副本分开表述，不包装成同工作量扩卡对比；千卡两次超时、计算成本未校准均保留。拟增加的细节不是已完成的实现。

## 来源

1. [当前架构](../../architecture.md)：调度/任务图/网络完成的关系及两个独立后端。
2. [真实四请求核验](../../../experiments/moe_trace_hotspots/results/four_requests_20261018/report.md)：4卡、两层两步的实际范围。
3. [小规模非UB网络机制](../../../experiments/scaleout_e2e/conclusions.md)：热点、双plane及反馈的实际结果。
4. [规模与超时证据](../../../experiments/scaleout_scaling/results/runtime_20261016/report.md)：128/256/512完成与1024超时；不可混用宿主时间和仿真时间。
5. [拓扑配置能力](../../reviews/scaleout_topology_20261015.md)、[研究范围](../../reviews/scaleout_configuration_and_research_scope.md)：已有输入与尚待建模的资源。
6. [综合报告](../../reviews/研究工作总报告_目标约束与ScaleOut网络路线_20261018.md)：研究目标与分步路线。

## 生成

使用已有Python标准库、Pillow、LibreOffice和Poppler，不安装依赖。原始模板不修改也不提交，模板路径和SHA-256见manifest。生成器接受`--template`指定另一台机器上的同一原件；模板hash不匹配时拒绝。具体命令与实际渲染记录见manifest及verification。仅重做材料，不调用仿真程序。

```bash
python3 docs/review_artifacts/fullstack_brief_20261019/build_materials.py --template /path/to/original.pptx
python3 docs/review_artifacts/fullstack_brief_20261019/render_manuscript.py
libreoffice --headless --convert-to pdf --outdir docs/review_artifacts/fullstack_brief_20261019 docs/review_artifacts/fullstack_brief_20261019/全栈仿真思路与拟增细节_两页.pptx
```

PPT阅读版由现有LibreOffice Impress转换为PDF。当前机器没有Writer模块，讲稿PDF由同一份Markdown逐页排版生成并实际检查，DOCX检查其OOXML结构和全文一致性，不宣称已通过Word/Writer视觉验收。讲稿与两页材料的内容均可继续编辑。

已有预览可直接查看；需要重新核验时，运行`python3 docs/review_artifacts/fullstack_brief_20261019/verify_materials.py`，模板应位于manifest记录的位置。核验覆盖两个内容页、原模板设计部件一致、各页PDF文字完整、备注与讲稿/DOCX全文一致、来源摘要，以及PPTX/DOCX重复生成一致性；不能代替新的仿真实验或硬件校准。
