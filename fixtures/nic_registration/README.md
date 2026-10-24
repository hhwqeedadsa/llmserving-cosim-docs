# NIC注册与共享存储功能验收

平台基线`f785197`，要求见[规范](../../docs/tasks/nic_registration_spec.md)，普通运行见[示例](../../examples/scaleout_rdma_transactions/README.md#多nic的权限共享同一份存储)。本增量将物理字节与远端NIC权限分开，为后续逐句柄退出提供表示基础；本轮没有实现撤销或安全恢复策略。

8GPU、14交换机、两plane各100Gbps接入，GPU0→4的两个上下文分别绑定NIC1/2，另有4→0返回上下文。两个64B物理窗口由四项静态注册引用，两个NIC上使用相同rkey21，同NIC的rkey22另授原子权限。先写两段8B，再原子加一，将实际16B结果发回，最后本地复制100ns。

| 检查 | 结果 |
|---|---|
| 公开probe与在线session | 各完成4远端+1本地操作；48,362ns。session之外全部结果字段相同 |
| 实际网络 | 每例5源包/208B载体有效负载，包含1原子返回；不含ACK和包头 |
| 仅改变初始数据 | 两个初始put、反向put和原子返回4帧改变，加一请求不变；未改操作/网络/时间 |
| v5稀疏存储 | 两窗口各64KiB；实际线上负载和目标事件与稠密例相同 |
| 有界put子集 | 去掉原子，3远端+1本地；每owner物理存储上限64B通过，注册别名未重复计入；36,286ns |
| 默认回归 | 新程序完整result与旧默认健康原件逐字节一致 |
| 非法输入 | CLI/native各拒绝13类；native在Fabric构造前纯校验，不创建结果或CLI运行目录 |

这些时间只描述人工MRT1功能例，不是实际MoE、GIN/RoCE基线或优化收益。两plane不是任意把单NIC带宽翻倍，NIC绑定由真实载体检查。

`audit.py`只用标准库，按完整抓包的实际TX和端点RX解码MRT1、检查端口与rkey，将字节重放到独立物理存储，核对原子BE64请求/返回与LE64内存、后继源负载、真实ACK完成、本地复制及最终存储。适用于本组无丢包、单帧请求、末尾一次copy，不是通用重传检查器。拒绝端口、映射报告、目标字节、目标事件及原子返回篡改；put子集没有原子返回篡改项。

```bash
python3 -B fixtures/nic_registration/prepare_inputs.py \
  --work examples/scaleout_rdma_transactions/nic-registration.json \
  --output runs/nic-variants
```

将普通命令的config换成生成的changed/sparse/bounded.json可复现三个条件；每个使用新输出目录，再用`audit.py --result ... --work ... --output ...`离线核验。`reject_inputs.py`使用同样topology/protocol/work/backend参数和新`--output`目录检查13类非法输入；需已有`PYTHONPATH=python`，该脚本不发起合法网络。

52项不同Python单元通过，包含5项新注册单元；严格C++17内存检查分别为27项新权限断言、7,085项旧内存断言和6,483项稀疏断言。断言数量不是网络样本数。只增量编译三个事务消费者与受影响内存检查，两入口链接固定八库；未改依赖、下载、安装或执行GPU。

最初Python检查因20交换机超过既有16上限而失败，缩小示例为14后重查全部5项，未提高上限。独立checker首次把全局work id当连接内ticket，修正后仅重审已有结果；一次临时对比误要求加一请求也变化，已按实际操作语义更正，未重跑网络。原始输入、失败日志及旧二进制均保留于持久检查点。

迁移与预算收尾见`verification.json`、`recovery.json`、`closeout.json`；持久包入口为`runs/nic_registration_checkpoint_20261024/README.md`。本轮静态权限没有PD、注销/重新注册、逐句柄退出状态、并发DMA、GPU可见性或硬件校准；不能据此判定窗口安全复用、生产漏洞、现代DeepEP性能或论文创新成立。
