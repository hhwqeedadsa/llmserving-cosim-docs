# 用户提供的四份R1路由：核验、热点统计和网络接入

这四个文件全部可用，无需重新下载。用户确认来自DeepSeek-R1-AWQ/MMLU；逐文件完整形状检查通过，本机Git blob OID及字节数均与固定修订`27febb7b2d24169560a9f6f83d38eb53f94916cf`的公开镜像目录一致。只查询目录元数据，没有下载更多原始路由。原文件保留于`data/moe-trace-small/`，不提交Git。

| 文件 | 学科 | Prefill token | Decode入口 |
|---|---|---:|---:|
| 1472.json | computer_security（计算机安全） | 105 | 128 |
| 1808.json | econometrics（计量经济学） | 28 | 128 |
| 2675.json | high_school_biology（生物） | 10 | 128 |
| 6447.json | international_law（国际法） | 6 | 128 |

合计8,193,641 B（约7.81 MiB），每入口61层：0—2为Dense，3—60为58个MoE层，每层256专家、Top-8。完整原件均未发现缺层、错误Top-K、越界或重复专家。文件编号与原先建议不同不影响使用，已按实际文件生成[manifest](manifest.json)，旧推荐清单在本地备份，不修改用户原件。

## 这份小样本中的热点

Prefill共149个token，Decode共512个token，分阶段、逐层统计；不把不同层的专家编号混在一起。每层Decode有4096次专家选择，均匀参考为每专家16次、每token选中概率3.125%。

| 原始层号 | 最热门专家 | 512个Decode token中的选中次数 | 被选概率 | 相对均匀负载 | Top-8合计选择份额 |
|---|---:|---:|---:|---:|---:|
| 3 | 247 | 60 | 11.72% | 3.75倍 | 9.96% |
| 4 | 183 | 84 | 16.41% | 5.25倍 | 10.74% |
| 24 | 72 | 179 | 34.96% | 11.19倍 | 15.89% |

“被选概率”分母为512个token，“选择份额”分母为4096次选择，不能混用。58层的Decode峰/均比范围为3.5—12.94倍、中位数6.375倍；Prefill与Decode的分布不同，应分别保留。所有数字只描述这4个用户选择的请求，不能推断整个MMLU、所有任务或训练分布。热点本身也不能证明网络拥塞。

## 实际非UB网络验证

使用公开4卡示例的两台服务器、两个Pod、100 Gb/s外部三层网络与机内织构；EP4，每卡batch1。保留真实R1的E256/K8/H7168，执行原始MoE层3—4、各请求Decode入口1—2。BF16激活字节与计算耗时是声明条件，GPU标签不是硬件校准；KV沿用通用片段声明，不是完整R1的MLA容量。

- 两轮实际执行通过自动及独立`rdma-check`，共76条跨rank消息、216个图节点、16条来源Decode层记录。
- 跨rank payload 2,609,152 B，其中机内917,504 B、跨机1,691,648 B；这里不把机内字节称为跨机字节。
- 下一轮开始由上一轮真实完成事件触发；四请求原始context分别为105、28、10、6，观察结束均为`OBSERVATION_ENDED`，没有从文件末尾推断EOS。
- 本例只证明真实选择已进入任务图和单一原生ns-3，不证明完整R1时延、硬件性能、网络优化收益或千卡规模。

完整来源核验见[identity.json](identity.json)，统计和原始运行位置见[summary.json](summary.json)，网络独立检查见[network-verification.json](network-verification.json)。离线分析按60秒、RSS1 GiB、地址空间2 GiB、产物128 MiB执行并成功；网络维持原小例60秒、RSS1 GiB、地址空间2 GiB、产物512 MiB及5 ms仿真限额，没有扩限。

## 在已有原件上复现

从仓库根目录使用已有环境，输出必须尚不存在；无需再次下载或安装依赖：

```bash
PYTHONPATH=python python3 experiments/moe_trace_hotspots/analyze.py \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --window 16 --top-m 8 --max-files 4 --max-total-bytes 16777216 \
  --output runs/four-requests-hotspots-new.json
```

网络运行采用本目录[service.json](service.json)，其清单引用为本机已校验的`data/moe-trace-small/manifest.json`；拓扑、协议与命令顺序沿用[来源回放示例](../../../../examples/scaleout_route_replay/README.md)。本次实际完整命令记录在summary中，后端从既有`backend.json`取得，没有重新构建。
