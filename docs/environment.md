# 环境与运行记录

## 当前状态

目标开发环境为原生 Linux。2026-09-27 按用户单独提出的 Codex 沙箱排查请求读取了本机环境，并完成独立沙箱对照测试。本机是 LXC 容器中的 Linux。项目依赖初始化、检查脚本、编译和仿真尚未执行；本次没有安装系统软件或修改全局配置。

| 项目 | 当前记录 |
|---|---|
| Linux 发行版及版本 | Ubuntu 24.04.4 LTS；内核 6.11.0-1027-oem；LXC 容器 |
| CPU 架构 | x86_64 |
| Git / Bash / Python 版本 | Git 2.43.0 / Bash 5.2.21 / Python 3.12.3 |
| Codex / bubblewrap | 已安装 Codex 0.157.1 / bubblewrap 0.9.0；另在临时目录测试 Codex 0.159.0-alpha.9 |
| C/C++ 编译器与 CMake | 未确认，M0 检查 |
| Protobuf 与其他构建库 | 未确认，按锁定源码检查 |
| GPU 或 NPU | 未确认，不作为仓库准备前提 |
| 递归子模块初始化 | 未执行；`git submodule status --recursive` 显示两个直接子模块均未初始化 |
| `check_deps.sh` 本地结果 | 未执行 |
| UB 原生样例 | 未执行 |
| LLMServingSim 原生样例 | 未执行 |
| 联合编译与仿真 | 未实现、未执行 |

v0.2 中 Python 3.12 等内容是原 UB 文档的环境参考，不表示本机已经采用或验证该版本。不要将编写文档所用环境填入此表。

## 本地初始化时记录

按照 `docs/tasks/current.md` 操作。只记录实际获取的系统与工具版本、子模块初始化结果、依赖检查的命令和退出状态。某个远端或提交不可获取时，保留该路径、来源和错误摘要，不自动换源。

日志可以保存在主仓库忽略的 `runs/` 下。需要提交的记录写明日志相对路径和必要校验值，不把凭证、用户名敏感路径或完整私有网络配置写入文档。

## 运行记录格式

每次实际执行后追加一个记录，包含任务编号、日期、仓库及依赖提交、工作目录、输入、命令、退出状态、日志位置和结论。没有执行的命令保留为待执行，不填写成功标记。

本文件目前没有项目依赖检查、构建或仿真通过的记录。

## 2026-09-27：Codex 沙箱启动错误诊断

本次诊断由用户单独授权，主仓库基准提交为 `62fc755406cea250b5c990ceb13e8eaef9263efd`，开始时工作区干净。未执行 `repo-local-check` 的依赖初始化，未改变任何依赖提交。日志目录为 `runs/codex-sandbox-diagnosis-20260927/`，属于 Git 忽略的本机产物。

### 直接原因

Codex 0.157.1 在生成 bubblewrap 参数时检查 `/tmp/codex-daemon-0` 的挂载身份。本机该目录位于 Btrfs 子卷：打开目录后的 `fstat().st_dev` 为 `0:146`，对应挂载 ID `5994` 的 `/proc/self/mountinfo` 设备号为 `0:58`。挂载 ID 唯一，目录的 `fstatfs` 类型与挂载表类型均确认为 Btrfs。

该版本 `codex-rs/linux-sandbox/src/daemon_mounts.rs` 第 82 至 83 行要求两个设备号相等，因而返回 `cannot establish app-server socket mount isolation`。这是本次错误的直接触发条件；改变工作区权限或 `TMPDIR` 不会修正这项检查。源码中的 socket 根目录固定由系统 `/tmp` 解析，明确不依赖 `TMPDIR`、`HOME` 或 `CODEX_HOME`。

上游已在提交 `a708fc683934d7d7ea7560354968fedd2be28581`（[修复 #47968](https://github.com/openai/codex/pull/47968)）处理 Btrfs 设备号差异：仅在目录与唯一匹配挂载都确认为 Btrfs 时接受差异，并保留目录别名与不安全挂载检查。查询时最新正式版仍为 0.157.1，尚未包含该修复；已确认 0.159.0-alpha.9 源码含修复。

### 实际命令与结果

除边界检查外，命令工作目录均为 `/root/moe-ub-cosim`。表中日志均位于上述日志目录，每份 JSON 保存完整参数、工作目录、UTC 时间、实际子进程退出码、标准输出和标准错误。`probe.py` 是记录工具；判断测试结果使用 JSON 中的 `exit_code`，不能用记录工具本身的退出码代替。

| 检查与输入 | 实际结果 | 日志 |
|---|---|---|
| 已安装 CLI：`codex sandbox -c 'sandbox_mode="workspace-write"' -- /usr/bin/true` | 退出 1，复现原 socket 挂载隔离错误 | `codex-original.json` |
| 同一 CLI、独立空 `CODEX_HOME`、相同 `true` 输入 | 退出 1，仍为同一错误，排除用户配置作为必要触发条件 | `stable-clean-home.json` |
| `python3 mount_identity.py` 读取目录描述符与挂载身份 | 退出 0，确认上述 Btrfs 设备号差异 | `mount-identity.json` |
| `bwrap --ro-bind / / --proc /proc --dev /dev --unshare-user --unshare-pid --unshare-net --die-with-parent /usr/bin/true` | 退出 1，`Can't mount proc on /newroot/proc: Operation not permitted` | `bwrap-with-proc.json` |
| 上一命令去掉 `--proc /proc` | 退出 0 | `bwrap-without-proc.json` |
| 临时 0.159.0-alpha.9、独立 `CODEX_HOME`、`workspace-write`、`true` 输入 | 退出 0；`CODEX_HOME` 在 `/tmp` 时有辅助 PATH 别名警告，后续边界检查改用日志目录内的测试 home | `alpha-true.json` |
| 临时版本的边界检查首次使用 `sandbox -C` | 退出 2，CLI 要求同时指定 `--permission-profile`；命令尚未运行 | `alpha-boundaries.json` |
| 在日志目录的 `workspace/` 内启动临时版本，使用相同测试 home、`workspace-write`、空额外可写目录、关闭网络，运行 `workspace/check_boundaries.py` | 退出 0：工作目录写入成功；兄弟测试目录写入返回 EROFS；daemon socket 目录内容不可见；创建 AF_INET socket 返回 EPERM | `alpha-boundaries-cwd.json` |

`/proc` 挂载限制确实存在，但 Codex 0.157.1 已有 `run_bwrap_with_proc_fallback`：探测到无法挂载新 procfs 时，改用其兼容路径。本次预览版在相同环境中的成功运行说明，无需先解除这一系统限制来处理原报错。进程的 AppArmor 标签实测为 `unconfined`；本次没有调整 AppArmor、sysctl 或容器配置。

临时二进制来自官方 `rust-v0.159.0-alpha.9` release，压缩包 SHA-256 已与官方资产元数据核对：`abc7e9a872ff8ba697bbd41ef2100362f7ccf2288a79acebc7396d4035fb27fe`。下载记录在 `download-record.json`；源码快照及校验值在本机日志目录中。临时二进制保存于 `/tmp/codex-sandbox-diagnosis-20260927/alpha-0.159.0-alpha.9/codex-x86_64-unknown-linux-musl`，没有替换已安装的 CLI 或运行中的 app-server。

### 修复范围与停止位置

已验证的修复方向是采用包含 #47968 的 Codex 版本；0.159.0-alpha.9 的独立沙箱执行与上述四项边界检查通过。该版本是预览版，不能据此声称整个交互客户端或后台服务已验证。正式替换时还需重启客户端及实际执行命令的 app-server，并确认其版本都包含修复。

本轮停止在诊断、临时版本验证和记录阶段，没有进行全局升级、重启现有服务、调整容器挂载或初始化项目依赖。用户当前选择的 Full access 会话继续使用原安装；也可以等待包含该修复的后续正式版再恢复沙箱。
