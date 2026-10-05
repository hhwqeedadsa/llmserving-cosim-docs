# 完整首轮小例

此目录是无需真实数据账户的用户输入，操作步骤见[用户指南](../../docs/user_guide.md)。

- `run.json`：一次完整服务运行及宿主预算。
- `service.json`：三条同时到达的请求、完整小模型、容量及参考计算耗时；每请求只运行一次解码迭代。
- `layers.json`：三 rank 的路由、专家放置和 CANN 模板参数。
- `system.json`、`network/`：ASTRA 执行和真实 UB 网络配置。
- `observation.json`、`sample-10us.json`：原 1 µs 和重采样 10 µs 观测；不是两次业务执行。
- `record-policy.json`、`export-policy.json`：有限原始记录与研究导出预算。
- `sample.json`、`replay.json`：直接复用切片的实验配置。

配置内路径相对配置文件所在目录，后端默认位于仓库的 `build/default/`；其他构建路径通过 `--backend` 指定。输入的合成路由和参考耗时用于功能验证，不代表目标硬件测量。
