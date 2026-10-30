# 128端点Clos的完整片段副本

两个互斥EP64完整组执行同一片段，先完成整个尝试者提供结果。每rank每层4专家；128模拟GPU端点、Clos单plane/rail、各层400Gbps、每链路1us。两组门限、原消息与ACK均保留，较晚图继续真实清理。

仓库根目录执行，替换绝对路径、使用新输出目录：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_whole_call/prepare.py \
  --output /path/to/new-input
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new-input/service-whole.json \
  --protocol /path/to/new-input/rdma.json --topology /path/to/new-input/topology \
  --backend /path/to/native-service --protoc /path/to/protoc \
  --output /path/to/new-normal
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-check \
  --run /path/to/new-normal
```

故障条件将protocol换成`rdma-whole-local.json`、输出换成新目录后同样检查。前20us只丢弃发往第一层primary组数据量最多GPU的数据包，ACK等控制保留。目标在准备阶段从固定路由确定，不向在线策略提供故障真值。

默认用两份不同人工原件，显式派生为2个逻辑请求、4次执行。已有R1-AWQ清单时另建输入：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_whole_call/prepare.py \
  --source-manifest /path/to/local-manifest.json --max-source-files 4 \
  --output /path/to/new-real-input
```

清单沿用route replay格式，原始路径相对仓库根解析或用绝对路径。脚本不下载、不改来源身份。真实模式限定R1原层3/4、第一个Decode入口、H7168/E256/Top8；4份原件是4个逻辑请求和8次执行，不是8份独立数据、完整671B推理或满载128卡。计算仍为声明成本，完整权重/等价ready-KV为显式初态假设。

查看`whole-preparation.json`、`route_evidence.json`、`whole_call_events.jsonl`及`whole_call_report.json`，区分源文件数、逻辑请求、物理尝试、结果、全部尝试退出和网络排空。准备不执行网络；新机器继续用公开`rdma-build`。本功能没有新原生选项，不要求重编已有支持执行事件的后端。

接口和成本边界见[完整说明](../../docs/whole_call.md)。完整副本是成熟基线；没有原地SR和延迟备用对照时，不将两次尝试的时间差当成净恢复收益。

## 在相同部署中准备无冗余控制

给上面的准备命令增加`--include-primary-control`，会另生成`service-primary.json`。它保留相同128端点、两组EP64、全部连接绑定、专家部署、原始请求和原尝试图，只取消副本执行；显式使用`sparse_active_groups/1`允许第二组没有请求。仍用相同的`rdma-run/rdma-check`，将`--config`换为`service-primary.json`，输出使用新目录。

此控制不删除网络节点或改变EP大小，也不为备用组虚构请求/KV。normal仍用`rdma.json`，local仍用准备时已固定的`rdma-whole-local.json`。这是无副本控制，不是延迟备用；副本初态建立、延迟启动和其他请求负载须另研究。
