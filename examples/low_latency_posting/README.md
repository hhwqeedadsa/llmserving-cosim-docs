# 旧DeepEP低延迟同步发布的功能示例

本例运行CPU上的[同步策略核心](../../backend/rdma/low_latency_posting.hh)，复用已有GinWorkQueue。它说明一条put可占多个WQE，但每四条逻辑put的同步发布判断仍使用调用方的message index；末尾count/flag通知触发发布。准备空洞挡住后项，迟到的旧发布取已有前缀与请求的最大值。

这不是网络仿真、现代GIN或异步postsend代理，不运行GPU，也不产生时延收益。调用方需显式声明注册区边界和每条put在当前expert/call中的message index；源数据集没有地址或WQE时刻。一个WQE条目不等于WQEBB、网络包或ACK。队列深度采用已有有界资源核心的正常availability检查，不声称复刻低延迟库跳过槽检查的实际实现。

## 独立运行

从仓库根使用已有C++17编译器，不依赖旧运行目录或仿真库：

```bash
mkdir -p runs/my-posting-demo
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -Ibackend \
  examples/low_latency_posting/demo.cc -o runs/my-posting-demo/demo
runs/my-posting-demo/demo 5 3
```

第一个参数是put数量（0—63），第二个参数选择1/2/3个WQE的说明性注册区布局。固定数据put为14352B，来自7168 hidden的旧低延迟BF16格式；32KiB注册边界只是显式算例，不是GPU或NVSHMEM的默认参数。程序分别输出通知前、通知后的发布位置，以及调用方提供功能完成前的完成位置。

`5 3`中，五条put占15个WQE；第四条put将前12个发布，第五条的3个等待通知。通知增加一个声明原子WQE并发布至16。之后程序分别消费和提供人工本地完成，明确标记`completion_supplied_by_demo=true`；不是收到真实ACK或CQE。

带预算和独立oracle的完整CPU检查：

```bash
python3 tests/integration/check_low_latency_posting.py --output runs/my-posting-check
```

输出必须是新目录；整个命令120秒以内，子进程地址2GiB/CPU80秒、验收RSS1GiB、产物16MiB、卷留8GiB。编译失败保留日志，不安装依赖、不自动扩限。覆盖21个公开示例组合和非法输入；细节见[功能验收](../../fixtures/low_latency_posting/README.md)。

## 接入限制

源码依据是固定DeepEP v1.2.1 `9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee`的`ibgda_device.cuh`及`internode_ll.cu`，来源与摘录见[发送核查](../../experiments/library_posting_audit/results/20261030/sources.json)。单put支持1—32个显式WQE、≤1MiB、每次reservation不超过声明深度。同步post判断按message index，不按WQE数或对象生命周期总put数；跨call的索引由调用方显式重置。

`CompleteThrough`只接收外部提供的合法累计完成，发布/消费不会代替它。关闭还要求每个生产者完成一次Post处理；较后的通知发布了前面的描述符，不代表前面生产者已返回。当前没有把本策略接入PersistentStreams或RemoteTransactions，也未改变它们的默认行为。现有staged_prefix已经实现的准备/发布功能继续保留；后续网络接入按[规范](../../docs/tasks/posting_boundary_spec.md)检查库模式、操作表示和128/256/512端点Clos预算。
