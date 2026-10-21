# 系统视角的仿真器创新方案与修改建议

2026年10月21日，北京时间。**报告已供审阅，后续实现待用户审阅意见。** 本轮没有修改平台执行逻辑。

- [离线HTML报告](report.html)：含目录、对比表、具体修改位置与审阅项，支持浏览器打印，无外部脚本、字体或统计依赖。
- [Markdown原稿](../../reviews/系统视角的仿真器创新方案与修改建议_20261021.md)：教师点评、当前代码、相关工作、主创新候选、验证与论文/系统产出计划。
- [源码证据](code_evidence.json)：14段当前代码摘录，绑定`699312b`、文件SHA-256和行号；覆盖Scale-Up与Scale-Out的已有能力及边界。
- [公开来源](sources.json)、[关键段落](prior_art_passages.json)：15份成功取得的论文/项目资料，保留1次错误Aalo URL的404；Aalo全文随后从作者网站取得。论文固定版本，GitHub资料保存blob身份与原始内容摘要。
- [报告核验](verification.json)：核对来源、源码、链接、HTML全文与重复生成；它不证明创新有效、平台回归通过或实机预测准确。

推荐主线是“资源释放与服务关键性共同决定通信准入”。先固定工作量、消息粒度和资源，在Scale-Up、Scale-Out各自后端中研究哪些等待真正影响Decode，再验证使用有限可见信息的控制方法。安全跨轮推进和决策导向的精度选择是条件分支，不同时全面开发。

相对于前一份宏微结合报告，本版补查LLMServingSim 2.0、SimAI-CLEM、Lina、Comet、2026年9月的解析资源管理、NanoFlow、Coz与Aalo，明确已有工作的覆盖范围，不把接入仿真器、热点统计、分层通信或SM调参直接当作新贡献。

## 复核

从仓库根目录运行，使用已有Python标准库与Poppler，不安装依赖：

```bash
python3 docs/review_artifacts/system_innovation_20261021/render_report.py
python3 docs/review_artifacts/system_innovation_20261021/verify_report.py
```

验证来源全文哈希时，本机应有`data/research/system_innovation_20261021/`中的已记录快照。它们不提交Git；换机可根据sources中的固定arXiv版本、Git blob API或作者URL取得相同内容后检查摘要。网络与网页更新可能导致内容变化或访问失败，不能覆盖旧hash后当作同一来源。

`collect_sources.py`是本次有界公开资料获取工具，单文档上限1.5 MiB，并发3、每次请求超时20秒，保存状态和内容摘要；默认重新抓取完整清单，`--ids`可指定来源。它会更新本次来源清单，因此不作为常规离线检查的一部分。脚本未运行下载文档中的安装或构建指令。完整原始快照及文本合计约4.1 MiB。

本次只保存本地研究文档提交，不push。用户应优先审阅报告第11节，再决定平台修改方向；本轮没有取得这些未来改动的性能结果。
