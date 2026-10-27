# 共享上联与多物理网卡：网络配置选择结果

真实MMLU路由，四活动rank、单层单次Decode调用；一个原生ns-3时钟。功能协议MRT1、声明计算/provider成本，未执行GPU或生产硬件校准。

| batch | 配置 | 应用阶段 μs | 源释放 μs | 排空 μs | 完整图等价 |
|---|---|---:|---:|---:|---|
| 4 | baseline | 81.777 | 87.877 | 93.925 | True |
| 4 | access | 81.316 | 87.406 | 93.446 | True |
| 4 | core | 66.121 | 72.181 | 78.213 | True |
| 8 | baseline | 111.394 | 117.494 | 133.377 | True |
| 8 | access | 110.856 | 116.946 | 131.123 | True |
| 8 | core | 81.007 | 87.067 | 116.902 | True |

| batch | 预测方法 | 选择 | 实际最优 | 选择后悔值 | 最大绝对相对时延误差 |
|---|---|---|---|---:|---:|
| 4 | volume | access | core | 22.98% | 97.87% |
| 4 | endpoint_phase | access | core | 22.98% | 44.92% |
| 4 | nic_phase | access | core | 22.98% | 42.72% |
| 4 | link_phase | core | core | 0.00% | 13.09% |
| 8 | volume | access | core | 36.85% | 96.88% |
| 8 | endpoint_phase | access | core | 36.85% | 58.03% |
| 8 | nic_phase | access | core | 36.85% | 56.94% |
| 8 | link_phase | core | core | 0.00% | 11.92% |

网络尝试12次；阶段状态`completed`。未执行条件与失败均保留。

baseline为接入100G/核心25G，access为接入200G/核心25G，core为接入100G/核心50G；传播均1000ns。不同升级改变的资源不同，只能说明参数收益，不能称同成本优化或商业ROI。

endpoint_phase只计端点总NIC容量；nic_phase按实际设备拆分；link_phase进一步保留共享上联负载。后三者都是普通七阶段帧服务模型，忽略细粒度队列与反馈，正确选择不代表时延准确。

Combine负载由slot双射的多重集合事前确定，没有读取未来token–slot绑定。完整通用图仅从每batch原网络提取；失配须区分冻结选择与表达能力。普通逐链路基线若足以选对，不能将收益包装成新增MoE语义创新。
