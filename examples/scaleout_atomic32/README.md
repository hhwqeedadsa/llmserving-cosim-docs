# 相邻32位通知计数器与实际回复

本例检查4字节计数器在实际网络请求、回复、本地消费和后继发送之间的关系。128端点Clos中只有0、1、16、17四个rank活动，三个持久连接；不是EP128、真实专家路由、完整MoE或128卡满载。GPU类型声明为NONE，不执行物理GPU。

准备程序调用公开拓扑命令，生成`work.json`、正常和恢复协议，以及拓扑。工作包括两次16B写入、六次32位原子、一次64位原子和两个本地复制节点。相邻计数器分别覆盖溢出、减1、两连接竞争同一位置、按位宽等待/清零及消费后再次累加；旁边的哨兵字节应保持不变。本地7ns处理只用于验证因果顺序，不是GPU测量。

## 运行与修改

使用已有工具和锁定依赖；构建方法见[原生持久传输](../scaleout_rdma_streams/README.md)。`BUILD/backend.json`提供本次实际二进制路径。所有输出目录必须是新目录，卷保留至少8GiB；示例每个原生运行限制60宿主秒、5ms模拟时间、RSS1GiB、地址2GiB和20万条包/队列记录。正式执行也应在外层使用已有`moe_ub_cosim.host_probe`限制累计产物。

```bash
PYTHONPATH=python python3 -B examples/scaleout_atomic32/prepare.py --output runs/my-atomic32-inputs
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology runs/my-atomic32-inputs/topology \
  --protocol runs/my-atomic32-inputs/normal.json \
  --config runs/my-atomic32-inputs/work.json \
  --backend BUILD/output/moe-rdma/ns3.42-moe-rdma-transaction-probe \
  --output runs/my-atomic32-normal
```

上述二进制名称须以构建清单为准。将协议换成`recovery.json`可注入两次各丢一个DATA包的故障：首次32位请求丢失、首次原子回复丢失；两者均由选择性重传恢复。初始重传期限17µs、上限640µs、最多3次重试，保留独立参考DCQCN。故障是人工精确注入，不表示生产丢包分布。在线前端用`rdma-transaction-session`，后端换成同清单的`transaction_session`，其他输入相同。

可修改`work.json`中的`value/address/local_wait.bits`，必须保持窗口权限、范围、对齐及源/目标保护；32位增量上限4294967295，缺省`fetch_add`仍为64位。`normal.json`和`recovery.json`用于相同工作量的功能对照，不以时延改善为通过条件。

## 逐包复核

版本化驱动`tests/integration/check_atomic32_network.py`以`--case normal/recovery/session-recovery`分别调用公开CLI，不依赖旧运行或fixture；`--inputs`指向本例准备目录，`--backend`指向新构建的`backend.json`，`--output`分别使用一个新目录。独立检查：

```bash
python3 -B tests/integration/audit_atomic32_network.py \
  --inputs runs/my-atomic32-inputs --cases runs/my-atomic32-cases \
  --output runs/my-atomic32-audit
```

`--cases`下须含上述三条件各自的`run/result.json`。核验器从实际发送包及目的端`payload_delivered`字节独立解码MRT1，检查请求/回复宽度、请求ACK与回复门限、内存事件顺序、最终窗口及相邻哨兵。观察字段不供在线算法读取。MRT1反向可靠字节流不是RoCE Atomic ACK；这里没有GIN硬件开销、GPU一致性、完整旧低延迟计划绑定或性能校准。
