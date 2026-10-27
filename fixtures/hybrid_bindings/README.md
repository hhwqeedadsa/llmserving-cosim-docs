# Hybrid Dispatch 资源绑定验收

规范：[Hybrid持久资源绑定](../../docs/tasks/hybrid_binding_spec.md)，平台起点 `cdd52b3`、执行前规范 `811b2da`。本轮新增 `hybrid-deepep-bind`，未改变旧逻辑/direct功能；操作见[独立小例](../../examples/hybrid_bindings/README.md)。

## 已验证的对象

人工2域×2GPU、E16/Top4/H256/BF16、2SM×2warp、两次调用，共111绑定：32计数块、15份域去重数据、64次finish。三context、两connection的向上分配为4，活跃持久资源16；数据、通知、不同通道及两调用的共享依据实际身份字段，不能把这些计数当报文或加速。

新旧Hybrid单元15项，以及direct plan相关回归通过。独立展开原始专家选择核对数据目的域、源/目标及peer转换，另外核对只有一个context时跨角色/通道/调用共享、多个context的SM分配/设备轮转，以及0/10/12/14/16 token的周期进度与最后finish差值。计数GIN self、本地copy和本地release atomic分开检查。

公开命令在空cwd从小输入生成logical和bindings：重复输出、复制源码后同一流程的四文件逐字节相同；独立搬迁bundle通过公开加载接口，不读取原logical目录。完整命令和退出状态保存在 `runs/hybrid_binding_20261027/workflow/commands.json`。

11类拒绝包括handler、stride、bool类型、重复设备、资源预算、host SM形状、原logical完整性、文件hash、重算hash的错误绑定、重算hash的不同provider和错误scope；另外输出目录已存在时拒绝且原件不变。host SM1只在新绑定入口拒绝，旧logical仍支持抽象输入。详见[核验摘要](verification.json)。

固定DeepEP/NCCL的11份原文通过本地tree/blob/SHA及大小核验，新增核对了host至少2SM、连接stride与requested rail stride的区别；见[来源](source-identities.json)。依赖检查按锁核对13项通过，不是新构建或上游库执行。

## 边界与资源

仅编译Dispatch资源与逐peer本地API顺序。没有WQE预约/门铃交错、完整通知屏障、Combine执行、最终slot、真实内存序或成本校准；输入SM/warp没有代替真实host共享内存形状选择。不是完整可执行网络程序，没有新网络/GPU/构建/下载/依赖修改或push，没有时延/千卡/论文创新收益。

各命令受60秒/RSS1GiB/地址2GiB限制，整个阶段保持600受监控秒/128MiB并保留128MiB空闲；[收尾](closeout.json)汇总监控退出状态和代码身份。原始运行目录保留，未重开旧研究条件。
