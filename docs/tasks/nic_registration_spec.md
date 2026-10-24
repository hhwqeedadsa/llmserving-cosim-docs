# 目标NIC权限与共享存储

平台基线`f785197`。为后续逐句柄退出建立实际基础：物理字节与访问权限分开，实际到达NIC参与远端查找。保留缺省旧MRT1功能行为；本阶段不实现动态撤销、未知provider回调、重注册或校准成本，不以静态映射宣称完整恢复。

v4/v5显式增加`registration_model={"mode":"nic_scoped_aliases/1"}`和`registrations`，二者必须同时出现。每条为`id/owner/nic/key/window_key/address/bytes/allow_atomic`，总数1—32，id唯一，owner/nic/key唯一。`window_key`定位同owner原物理窗口，注册范围必须非空且包含于其内，原子权限只能收紧。NIC必须出现在现有connection绑定中，不能任意宣称未声明设备。物理窗口依然不重叠、只分配一次；本地copy、源快照、local_wait均使用物理窗口key。rkey在不同NIC可以同值；同NIC不同rkey可覆盖同一存储。

源帧不增加字段。收到实际载体解码的MRT1后，按绑定目标owner/NIC及帧rkey查注册，再核范围/权限，映射物理窗口实际写入。没有正确NIC注册则失败，不落回owner/key。旧Apply接口在显式模式拒绝绕过NIC。重复应用必须保持context的owner/NIC绑定；无效权限不能改变内存或序号。当前功能模型访问是同步串行应用，不模拟查找后正在进行的DMA。

物理地址相交的不同别名仍要求远端副作用依赖，prepared不能排序跨连接写入。源读/local输出lease按owner+物理地址保护，别名不得绕过。源/本地存储和目标请求缓存分别核算；注册数量不是物理字节重复分配，未来MR对象成本仍需另计。

验收：严格内存检查包括两NIC同rkey、同NIC多rkey、不同子范围/原子权限、别名共享字节与lease、错误NIC及接口绕过拒绝；Python/native分别拒绝未知字段、缺失配对、重复身份/NIC-key、范围和越权、跨别名无序写。小型公开原生multi-rail任务让两个连接经不同绑定NIC先后修改同一存储，独立按捕获实际包/NIC/rkey重放字节；仅换初始数据改变后继真实源负载。旧默认与新代码完整结果比较，运行受影响回归。新源码根/空cwd完成公开流程和离线核验，保存来源/预算/失败/持久检查点，仅本地提交不push。

阶段`/dev/shm/moe-nic-registration-20261024/`；每命令60秒、RSS1GiB、地址2GiB、产物64MiB，累计600监控秒/256MiB含迁移/持久副本；每网络5ms/result16MiB，tmpfs8GiB/根盘64MiB预留。复用固定八库与未影响对象，只增量编译受头布局影响的三个消费者/两个入口和相关内存检查。失败保留不扩限或原样重跑，无安装下载/GPU/依赖变化或旧研究失败重跑。平台能力与论文创新分别验收。
