# 完整低延迟操作计划的表示验收

固定旧DeepEP v1.2.1应用语义，新增可选流式索引格式；原flat格式的数量、字节及默认行为保持。操作及全部零token通知仍在，不以删除通知或现代direct替代旧模式。[用户示例](../../examples/deepep_indexed/README.md)、[实施规范](../../docs/tasks/indexed_deepep_plan_spec.md)、[验证摘要](verification.json)。

验收结果：

- 15项单元方法通过；4种rank/调用/布局输入逐项还原与flat一致，独立后继传播核对728种单事件暂扣和1种组合暂扣；覆盖逻辑、输入、索引、累计输出、单行预算及重复JSON键、未知ID、重复/前向事件、重新计算hash后的篡改拒绝。
- 新目录公开EP32人工流程11条命令通过，独立枚举16,448操作、66,144事件及全部操作属性/事件引用。含64条数据操作、16,384条count/flag，其中各8,160条为零token；15,920条跨rank逻辑操作不等于网络包。
- 5个文件重复逐字节相同，迁移check相同；重新计算hash后的数据修改、未知暂扣ID及旧flat超限均拒绝。旧flat仍因event budget拒绝同一完整EP32输入，保留原stderr且不创建输出目录。
- 另运行旧入口8条CLI；读取改动前`96cd9e18e5`的原始源码，对4种输入核对旧flat编码逐字节不变。13项固定依赖一致，Python解析、公开帮助及新示例/规范链接检查通过。

最终人工EP32五文件合计6,532,645B，索引浅层核算峰值8,319,699B；这不是总进程内存。三阶段累计47.711秒，峰值采样进程组RSS139,268,096B，产物预算与实际峰值见摘要。预算600秒/128MiB/留8GiB，阶段120秒/RSS512MiB/地址1GiB。源码/命令/输出分别保留在`runs/indexed_deepep_plan_20261030/`；最终公开流程为`workflow-final/`。第一次功能流程通过后，收尾将存储字段改为“不保留完整数组”，明确生成器仍保存阶段状态，随后重验；早期输出保留，不以其清单替代最终结果。

复核命令（输出改为新路径）：

```bash
PYTHONPATH=python python3 -B -m unittest discover -s tests/unit -p 'deepep*test.py' -v
PYTHONPATH=python python3 -B tests/integration/check_deepep_indexed_workflow.py --output runs/my-indexed-check
PYTHONPATH=python python3 -B tests/integration/check_deepep_workflow.py --output runs/my-old-deepep-check
```

CPU计划验收没有执行32卡网络、128Clos或GPU。没有新网络、下载、安装、依赖修改、完整构建或研究收益；未实现操作到持久QP/WQE/CQ的绑定，尚未接入发送发布核心与网络时钟。成熟库语义和存储改进不能当作协议创新，整体研究目标仍active。
