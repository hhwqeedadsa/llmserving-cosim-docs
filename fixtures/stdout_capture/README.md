# 原生service控制日志按行写出

worker原先在读取后端stdout后使用Python默认块缓冲写入`native.stdout.log`。超时终止父进程时，已经处理并回复的控制事件可能仍在Python内存中；失败日志中少几条通知不能证明这些图没有完成。现改为`buffering=1`，完整行在调用前端决策前提交给操作系统。不修改网络、前端决策或时钟，不保证fsync、断电持久性或ASTRA异步日志的完整尾部。

实际检查见[verification.json](verification.json)：

- 通过公开topology/rdma-run执行8GPU人工MoE层段；固定已有原生二进制，修改前后628条通信、4轮、1,512节点核验通过，完成193,252ns。不是完整模型或硬件测量。
- 两份`result.json`和完整ASTRA节点日志逐字节相同；前端仅归一化输出目录后相同。stdout控制事件相同；普通诊断中的宿主时间戳不要求相同。
- 两个真实子进程写入同一49B完整控制行后暂停，再由检查者SIGKILL。块缓冲在kill前后文件均为0B；行缓冲在kill前后均为49B。检查的是操作系统和Python缓冲机制，不是人为触发千卡超时。

各次公开service命令受60秒/RSS1GiB/地址2GiB/32MiB限制，总产物64MiB、卷预留8GiB；不安装、不改依赖、不新构建。使用已有LLMServingSim Python环境和protoc，完整命令在摘要的两个monitor中。原始目录`/dev/shm/moe-stdout-capture-20261022/`易失；本机持久检查点及逐文件hash见[archive.json](archive.json)。

普通用户操作没有变化，仍使用[原生RDMA示例](../../examples/scaleout_rdma/README.md)。现有失败结果不补写、不覆盖；这个修复不会恢复历史丢失行，也不证明旧千卡任务最终完成。
