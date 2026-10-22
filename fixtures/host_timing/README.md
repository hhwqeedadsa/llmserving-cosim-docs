# 可选宿主计时的独立验收

本增量用于区分前端生成、初始化、同步等待、事件执行及报告开销，不证明千卡完成或论文贡献。公开接口与口径见[RDMA示例](../../examples/scaleout_rdma/README.md#可选宿主阶段计时)、[输出数据](../../docs/record_storage.md#原生rdma宿主阶段记录)。

`tests/integration/check_host_timing.py` 在新工作目录只调用公开CLI：生成8GPU拓扑，以保存的`727bfcd`旧service、新service不计时、新service计时执行同一人工两层MoE输入。三份完整`result.json`及ASTRA节点日志逐字节相同；前端仅归一化输出目录后相同，stdout控制事件相同，普通日志宿主时间戳不参与比较。均628消息/4轮/1,512节点，完成193,252ns。

独立从原始JSONL重建嵌套及全部exclusive时间，核对同进程exclusive之和等于根inclusive；跨进程每次control_exchange包住对应frontend_decide。最终SEND/RECV提交与回调各628，未完成差额均0，并与实际结果回查。关闭计时无旁路文件。使用真实已落盘记录截成不完整前缀后，通过公开检查命令验证缺失worker、未完成Run和最后半行都不会变成成功或完整Run耗时；该前缀是复制构造，不是一次真实网络超时。

6项单元检查覆盖重复ID、交叉结束、错误父节点、倒退时钟、含多线程CPU的嵌套扣除、失败footer、尾部半行、完整畸形行和默认关闭不取时钟。未闭合阶段不补造结束。

实际命令、资源和结果见[verification.json](verification.json)。增量构建目标service已通过，采样RSS816,398,336B，13.73秒；三组完整公开验收约7.54秒，外层采样RSS521,895,936B。每子命令≤60秒/RSS1GiB/地址2GiB/32MiB，构建目标目录64MiB，新任务累计128MiB、卷预留8GiB、流程累计300秒；无安装、下载或依赖修改。宿主时间是这一小例的单次观测，不作加速比或硬件性能结论。完整本机检查点见[archive.json](archive.json)。

复现时提供新旧配套可执行文件与既有protoc，设置绝对`PYTHONPATH`和含前端依赖的Python：

```bash
python tests/integration/check_host_timing.py \
  --reference /absolute/path/to/old-service --backend /absolute/path/to/new-service \
  --protoc /absolute/path/to/protoc --output /absolute/path/to/new-check-directory
```
