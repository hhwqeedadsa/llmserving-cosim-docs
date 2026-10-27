# Hugging Face终端连通性核查

2026-10-27。用户说明浏览器能访问、此前没有测试终端，随后表示当前运行代码的机器可能不能访问。本次只诊断，不下载路由、不配置代理、不修改系统网络。

## 已确认的事实

| 检查 | 当前结果 |
|---|---|
| 运行环境 | LXC容器，存在默认网关 |
| 当前进程代理 | 没有HTTP_PROXY/HTTPS_PROXY/ALL_PROXY或对应小写环境变量；urllib也未发现代理 |
| 系统域名解析 | Hugging Face、GitHub均能返回地址；不等于已验证地址正确 |
| Hugging Face直连IPv4/443 | curl退出28，约4秒建立TCP连接超时；HTTP状态000，TCP/TLS完成计时均为0 |
| GitHub直连IPv4/443 | 同样约4秒建立连接超时 |
| 两个公共DNS的HTTPS入口 | 指定1.1.1.1及8.8.8.8连接，均在约4秒TCP连接超时；未取得独立DNS回验 |
| Python公开元数据探测 | 外层15秒超时退出124，没有写出结果；保留失败，没有重跑 |

因此，本次失败发生在获得HTTP响应之前。它不能说明Hugging Face账号或数据集授权有问题，也还无法判断具体是出口策略、路由、网关还是缺少代理。浏览器可以使用自己的代理/VPN/DNS或运行于另一台机器；浏览器成功不能直接证明当前容器的终端直连可用。代理环境未配置也不能证明代理一定是唯一缺项。

系统对huggingface.co返回IPv4 `31.13.86.21`、IPv6 `2001::b93c:d832`。本轮独立DNS回验没有连通，故不把“解析有返回”写成“解析正确”，也不据此确定DNS污染。后续若要修复，可与能访问的机器的解析结果、出口和实际代理配置对照。

上次获取程序只保存外层 `URLError`，没有保存底层reason/errno；因此不能事后精确断定上次每次失败原因。这里记录的是本次直接测得的TCP连接超时。

## 检查方法与证据

工作目录 `/home/lry/moe-ub-cosim`。命令、输出和小型JSON保存在本机 `runs/hf_connectivity_20261027/`。域名解析使用有6秒子进程限额的 `socket.getaddrinfo`；Python HTTPS探测外层15秒限制，内部请求10秒。四条独立curl探测的共同选项为：

```bash
curl -q -4 --connect-timeout 4 --max-time 10 --silent --show-error \
  --output /dev/null \
  --write-out 'http_code=%{http_code} remote_ip=%{remote_ip} dns_seconds=%{time_namelookup} tcp_seconds=%{time_connect} tls_seconds=%{time_appconnect} total_seconds=%{time_total}\n' \
  https://huggingface.co/
```

`-q`不读取用户curl配置；本次未加凭证、cookie或禁用证书校验。GitHub对照替换为 `https://github.com/`；公共DNS仅作诊断，以 `--resolve` 指定HTTPS服务IP，未修改系统DNS。

本次诊断另设180秒/1MiB日志预算，每个探测不超过15秒，无重试。没有仿真、模型执行或路由下载，未向已关闭的数据获取阶段追加请求。64份扩充清单保持原状；按用户要求先整理清单。后续可在能访问的机器下载并传到本机，或在取得可用代理配置后另行验证终端访问，不为本次诊断修改全局设置。
