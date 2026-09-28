# 单份写任务功能输入

`single_write.json` 用于 M1.2；网络初始化复用锁定 UB 的 `scratch/2nodes_single-tp` 的五个网络文件，运行目录不含流量 CSV。

源 rank 0 向目标 rank 1 发送 409,600 字节，起点 10 ns，业务优先级 7。内部 transfer_id 故意大于 32 位，UB task_id 独立为 7，用于检查没有把内部标识静默截断为业务任务号。Chakra SEND 节点为 1，接收等待身份为 2，标签为 17。

`receive_first` 只改变 SEND/RECV 接口登记顺序，`reclaim_tps` 只控制原 UB 的空闲 TP 回收策略。默认值保留 TP；执行器始终释放完成任务的 RTP Jetty 和相关绑定。功能测试另检查 1 字节及非法输入，配置不代表真实模型或校准结果。
