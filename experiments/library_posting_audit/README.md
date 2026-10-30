# 通信库发送发布与反馈边界核查

从`9f9465d8c9`的ACK语义缺项继续：重点核对数据准备、WQE（发送工作条目）、门铃发布、CQ（本地完成队列）与网络ACK是否被混为同一事件。只读取本地固定来源与既有128端点Clos结果；不执行网络或GPU，不修改协议，不由静态操作数计算时延。

预检已发现低延迟v1.2.1的put默认每四条逻辑消息触发一次同步postsend，末尾原子通知强制触发；实际async模式、注册地址/key边界和网卡ACK请求策略未知。现代direct调用GIN，不能把旧低延迟的发布规则套到当前direct任务。本轮不是盲测，也不按结果修改故障、路由、超时或输入。

## 方法

1. 核验DeepEP v1.2.1、固定现代DeepEP、NCCL v2.32.3-1若干相关文件的提交、Git blob、SHA256及原文行段；这些组件并未联合编译或证明版本匹配。引用QP、注册key拆分、默认模板参数、四条发布、原子通知、CQ标志及QP超时配置的具体位置。
2. 固定上一轮normal的8份wave、输入、结果和15真实文件来源。按原始token专家选择枚举每个source/expert贡献，分别核算旧低延迟RDMA数据put、零token通知和条件同步发布；同步、关闭P2P及库模式均为声明投影。当前rank-reduced任务另外统计，不把旧库投影当已有网络执行。
3. 用显式假设的注册区边界展示一次put可以拆1/2/3个WQE；不同边界不改变逻辑数据字节，但改变WQE/CQ请求数量。源与目的地址、key布局不在路由数据中，这不是实测拆分。
4. 独立验证器直接枚举每个token/expert及全部source/expert组合，核对组、层、步、通知和QP key；不导入分析器、历史实验或测试fixture。少量计数/来源篡改必须拒绝。CPU逻辑只检查发布前缀与边界计算，不当作RDMA执行。
5. 生成中文网页、机器可读证据和下一项平台实现规范。先判断应改哪个入口，保留不确定项；不把成熟批处理、CQ信号或ACK变化称为MoE创新。

## 执行

工作目录为仓库根；Python仅用标准库。先读源码，再通过已有`moe_ub_cosim.host_probe`分阶段执行，累计预算根`runs/library_posting_audit_20261030`，总300秒/64MiB/留8GiB，阶段60秒/RSS512MiB/地址1GiB。源码准备、分析和验证分别保存输入hash、实际命令、退出状态和资源；失败保留，不自动扩预算。公开来源不联网更新。

```bash
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim.host_probe \
  --output-dir /new-root/monitors/prepare --wall-seconds 60 \
  --rss-bytes 536870912 --address-bytes 1073741824 \
  --artifact-bytes 67108864 --artifact-root /new-root --min-free-bytes 8589934592 -- \
  .venv/frontend/bin/python -B experiments/library_posting_audit/audit.py prepare \
  --run runs/explicit_prefix_moe_20261030/study/normal --output /new-root/input
```

随后分别执行`audit.py analyze --input /new-root/input --output /new-root/analysis.json`、`verify.py --input /new-root/input --analysis /new-root/analysis.json --output /new-root/verification.json`及`publish.py --stage /new-root --output /new-delivery`，均纳入同一根预算。各程序`--help`说明参数；输出拒绝覆盖。原始网络不随Git发布，但报告保存所用公开wave中的选择投影和身份，不提供硬件校准承诺。
