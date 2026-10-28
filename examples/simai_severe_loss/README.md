# 并发MoE任务的周期性严重丢包

本例沿用[SimAI联合小例](../simai_service/README.md)的64端点/8服务器、EP64、每rank每层4专家、两个人工源请求。不是实际GPU或生产路由。平台已验证正常、窗口50%丢包、100%短时黑洞，以及持续100%丢包导致有限重试失败四种条件。

先准备固定SimAI runtime及按当前源码编译的原生service程序。在仓库根使用已安装`simai`依赖的Python环境：

```bash
python -m moe_ub_cosim scaleout topology --config examples/simai_service/topology.json --output runs/severe-example/topology
python -m moe_ub_cosim scaleout simai-run --config examples/simai_severe_loss/service.json --topology runs/severe-example/topology --protocol examples/simai_severe_loss/half-burst.json --runtime "$SIMAI_RUNTIME" --backend "$NATIVE_SERVICE" --output runs/severe-example/half
python -m moe_ub_cosim scaleout simai-summary --run runs/severe-example/half
python -m moe_ub_cosim scaleout data-loss-check --run runs/severe-example/half
python -m moe_ub_cosim scaleout rdma-arrivals --run runs/severe-example/half --graph g0.w0 --layer 0 --phase dispatch --output runs/severe-example/half-arrivals
```

每次`simai-run`自动生成`scenario.json/scenario.md`，含模拟GPU标签、物理卡/服务器/Pod数、EP各组、每rank专家、源请求、真实batch与上限、分层带宽、恢复设置与结果；原生执行失败也保存结果摘要。单独`simai-summary`只读历史运行，不执行网络。

协议可选`data_loss`如下（与旧`faults/drop_once`互斥）：

```json
{"kind":"periodic_destination_data/1","numerator":1,"denominator":2,"offset":0,"start_ns":0,"end_ns":100000,"nodes":[]}
```

这会在[0,100µs)内，对实际到达目标GPU的数据包按全局到达顺序，每两个丢一个。`3/4`代表每四个丢前三个；`1/1`代表窗口内全丢。`offset`调整周期起点，`nodes=[]`选择全部GPU，也可列出目标GPU子集。重传包同样参与计数，ACK/NACK/PFC不丢；这不是随机独立丢包、BER或拥塞自然溢出模型。窗口外正常交付。仅根据本次实际到达顺序判断，不读取未来或专家身份。

`normal.json`不注入；`half-burst.json`为50%/100µs；`blackout.json`为100%/100µs；`persistent.json`为持续黑洞，预期原生命令非零退出、`failure.reason=recovery_retry_exhausted`。失败后仍可运行`data-loss-check`和`rdma-arrivals`，查看未完成的有效到达曲线，不能用失败时间当成功时延。

结果`data_loss`给出实际目标数据包到达尝试总数、窗口内尝试数和丢包数；`data-loss-check`用完整抓包独立重放周期，分别输出窗口丢包率和全观察期比例。窗口50%不代表整个服务50%；必须带分母报告。完整包记录支持显式最多1,000,000事件，缺省额度不变；该示例预设400,000，分析上限128MiB。捕获截断时拒绝累计到达核验，不自动扩大额度或删包。

后端机制见[RDMA拥塞与恢复说明](../../docs/rdma_congestion_loss.md)，功能验收见[固定摘要](../../fixtures/simai_severe_loss/README.md)。
