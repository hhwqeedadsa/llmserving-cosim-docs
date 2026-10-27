# CLEM接口与规模边界核查

本研究从固定公开源码重新确定MoE网络仿真的强基线，没有运行竞品、网络、GPU或构建。阅读[中文报告](results/20261027/report.md)或[自包含网页](results/20261027/report.html)，范围、限额与停止规则见[事前协议](protocol.md)。

结果：CLEM已有实际通信库→host verbs→持久QP→网络ACK→CQ/控制反馈；旧版READ按NCCL flush处理，不能直接推定通用READ/原子及现代GPU发布已适配。固定buffer和设备地址改写有具体代码依据，但规模倍数不是网络事件或宿主加速倍数。我们尚无完整Hybrid、硬件校准或新的强基线收益证据，两项创新候选保持进行中。

核查覆盖CLEM六份材料及网络十份材料，包括双方许可原文；下载及本地README fallback都与固定tree/blob匹配。独立检查重建394个Git tree对象、核对29处源码引用，并拒绝七类证据篡改。原raw获取有两次超出10秒，失败收据保留；修正为总截止后只取尚未尝试文件，没有重试旧URL。离线分析首次因命令锚点同时匹配注释而停止，原源码/收据保留；只收窄锚点，未改上游文件。

## 复核

原件在 `runs/clem_boundary_audit_20261027/`，它是本地证据目录，不随Git提交。运行记录包含命令、工作目录、限额、退出状态及资源采样；[小摘要](results/20261027/verification.json)与源码身份记录随Git保存。需要完整缓存时使用本阶段归档及[SHA256收据](results/20261027/checkpoint.json)，保留原始失败；不能用本仓库摘要凭空重建缺失上游内容。

以下命令只读已封存缓存，不联网、不导入平台执行器；`--run`为解包后的`evidence`目录或原件目录：

```bash
python3 -I -B /path/to/copied/source/study.py \
  --run /path/to/evidence --output /path/to/new-analysis
python3 -I -B /path/to/copied/source/verify.py \
  --run /path/to/evidence --output /path/to/new-verification.json
```

用复制脚本和空工作目录恢复的完整命令如下，输出目录必须不存在；实际归档标识见收据：

```bash
python3 -I -B /path/to/copied/source/checkpoint.py restore \
  --archive /path/to/checkpoint.tar.gz --sha256 SHA256_FROM_RECEIPT \
  --output /path/to/new-recovery
```

恢复逐成员校验后，从复制的研究源码在空cwd重算身份、补丁引用及Markdown/HTML，并比较全部字节。恢复不执行HTTP、网络或上游脚本。`fetch.py`是带额度/不重试的获取工具，已关闭的本轮获取不能再次启动；本地README备用件的原始来源和固定blob单独登记，未覆盖失败的空响应。
