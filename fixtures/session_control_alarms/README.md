# 在线控制timer平台验收

目标是把业务控制定时唤醒与GPU本地FIFO分开，仍使用同一实际ns-3网络时钟。接口见[公开示例](../../examples/scaleout_rdma_transactions/README.md#不占用gpu队列的控制定时器)，边界见[实施规范](../../docs/tasks/session_control_alarms_spec.md)。此阶段不执行研究收益矩阵，不将人工等待修正称为创新算法或GPU加速。

## 验收入口

先通过公开`scaleout rdma-build`取得新session程序；本机已存在匹配的固定构建时，`build.py`仅编译受影响入口，并核验所有复用源码、对象、旧程序和原生库。它是有显式输入的本机增量构建工具，不是离线构建第三方依赖的声明。

以下检查器须各自通过公开`moe_ub_cosim.host_probe`整体监控，单命令60秒/RSS1GiB/地址2GiB/64MiB，tmpfs预留8GiB。配置和新输出路径均显式指定，不覆盖既有结果：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B tests/integration/check_session_control_alarms.py \
  --backend /ABS/NEW_SESSION --old-backend /ABS/OLD_SESSION \
  --topology /ABS/TOPOLOGY/topology.json --output /dev/shm/NEW_ALARM_FUNCTIONAL
python3 -B tests/integration/audit_session_control_alarms.py \
  --functional /dev/shm/NEW_ALARM_FUNCTIONAL --output /dev/shm/NEW_ALARM_AUDIT.json
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s tests/unit -p transaction_session_alarms_test.py
```

本轮有11个新功能条件及1个旧程序对照：不用timer的新旧完整result字节相同（新路径故意增加20ms宿主等待）；控制timer未阻塞实际接收后的复制；GPU忙时准时触发；空闲同QP继续；零时延多timer与operation同ID；取消、无数据会话和取消后结束；timer实际解锁条件等待；源失败后健康工作继续且旧8B源租约保留，仍为5ms partial退出。人工小例中旧GPU计时使复制完成100μs，控制timer方式为6.036μs；端口服务量不变，不能解释为网络或算法提速。

22种绕过Python的非法原生命令均在整批准入前拒绝，无操作注册或源包；9项前端单元检查覆盖类型/期限/ID生命周期、整批无发送、显式取消后finish、伪造和重复回调。旧在线回归3条公开命令、前缀发布32条命令及相关17项事务输入检查通过。

独立标准库审计从真实COMMIT和原生回调重建timer状态，重放14次源TX、11次实际目标应用与窗口/复制字节，核对控制对照端口服务完全不变；触发时间、输出内存和重复回调三类篡改均被拒绝。首版审计器在纯timer无包条件误以为必须存在空`source_payload_packets`字段；按实际报告的省略规则修正后通过，错误与原脚本保留，没有重跑网络。

## 证据和恢复

小摘要见`verification.json`、`independent-audit.json`和`recovery.json`。实际运行目录为`/dev/shm/moe-control-alarms-20261024/`。本阶段累计受监控450秒/新产物192MiB，包含失败、构建、归档、恢复与持久副本；整体研究目标仍进行中。

`checkpoint.py archive/recover/replay`保存及校验源码、原始结果、受影响程序和8个固定原生库。843成员/53,496,298B展开、10,814,968B压缩全部核hash。新源码根/空cwd中实际执行一次公开网络，完整result只替换ns-3库目录后逐字节一致，实际八库全部从包内加载；独立离线审计输出逐字节一致，归档成员运行后未变。源码快照216份，13固定依赖及受保护旧程序/库身份保持。运行仍依赖宿主Linux/Python/加载器/glibc/libstdc++，不称完整离线构建；离线复算与新增功能网络重放分别记录。

持久入口见`runs/session_control_alarms_checkpoint_20261024/README.md`，归档SHA256见[archive.json](archive.json)；同机根分区副本已fsync、核hash，不是远程备份。大件原始证据仍在tmpfs，重启会丢失。30份原始监控（含嵌套、排除恢复包内重复日志）保守合69.01秒，不是完整阶段wall时间；峰值采样603.5MiB，原件/恢复/归档/持久副本分配块合约89.15MiB，无遗留受监控进程。前缀回归3个预期partial退出与首审计器失败均保留，不把它们写成全部网络成功。

例如，从持久包恢复到一个不存在的新目录，须保留64MiB单命令产物预算：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/NEW_ALARM_RESTORE_MONITOR \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 67108864 --wall-seconds 60 \
  --artifact-root /dev/shm/NEW_ALARM_RESTORE --min-free-bytes 8589934592 \
  -- python3 -B fixtures/session_control_alarms/checkpoint.py recover \
  --archive runs/session_control_alarms_checkpoint_20261024/checkpoint.tar.gz \
  --receipt runs/session_control_alarms_checkpoint_20261024/archive.json \
  --destination /dev/shm/NEW_ALARM_RESTORE
```

再将`python3 -B /dev/shm/NEW_ALARM_RESTORE/source/fixtures/session_control_alarms/checkpoint.py replay --restored /dev/shm/NEW_ALARM_RESTORE --output /dev/shm/NEW_ALARM_REPLAY`放入同样限额的新host_probe，产物根改为新replay输出目录。replay自己切换源码根和空cwd，并显式设置包内LD_LIBRARY_PATH，不依赖归档程序原有RPATH。
