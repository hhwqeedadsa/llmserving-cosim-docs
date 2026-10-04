# 环境与运行记录

## 当前状态

截至 2026-09-29，M0、M1、M2 和 M3.1 的分步检查已完成，当前开发能力与限制见 README 和下文各阶段记录。`publish-maintenance-repos` 已将主仓库 `main` 及 ASTRA、UB、LLMServingSim 的 `cosim` 推送到 `baoy21` 的四个私有仓库，维护地址与固定指针已保存；独立目录已从远端取回全部 13 个固定依赖，并通过依赖检查与来源核验。此次发布不改变执行代码，也不代表新机器构建、全模型性能或校准已验证。M3.2 尚未开始。

## M1.1 完成时的环境概览（历史）

目标开发环境为原生 Linux，本机是 LXC 容器中的 Linux。2026-09-27 完成独立沙箱诊断、`repo-local-check` 和 M0；2026-09-28 完成 M1.1 的单节点计算与时间检查。13 个依赖仓库静态检查通过；M0 两项样例分别构建并重复运行通过，8 项时钟回归和 112 个请求内部一致性通过，同名 MoE 示例 CSV 的工作负载错配及原比较失败已记录。M1.1 复用现有依赖，由 ns-3 实际执行 ASTRA COMP 的完成事件。尚未实现 UB 业务收发和服务调度。

| 项目 | 当前记录 |
|---|---|
| Linux 发行版及版本 | Ubuntu 24.04.4 LTS；内核 6.11.0-1027-oem；LXC 容器 |
| CPU 架构 | x86_64 |
| Git / Bash / Python 版本 | Git 2.43.0 / Bash 5.2.21 / Python 3.12.3 |
| Codex / bubblewrap | 已安装 Codex 0.157.1 / bubblewrap 0.9.0；另在临时目录测试 Codex 0.159.0-alpha.9 |
| C/C++ 编译器与 CMake | GCC/G++ 13.3.0、CMake 3.28.3、Ninja 1.11.1、Make 4.3；两项原生样例已构建 |
| Protobuf 与其他构建库 | protoc/C++ Protobuf 3.21.12；Python Protobuf 7.36.2、Chakra 0.0.4（锁定源码）；ASTRA analytical 样例已构建 |
| GPU 或 NPU | 未确认，不作为仓库准备前提 |
| 递归子模块初始化 | 已完成，退出 0；两个直接子模块及递归依赖共 13 个仓库均按父仓库 gitlink 检出 |
| `check_deps.sh` 本地结果 | 已执行，退出 0；11 项显式依赖及 2 项更深层依赖通过检查 |
| UB 原生样例 | `scratch/2nodes_single-tp` 构建及两次相同输入运行通过，四个任务均完成，后处理独立复核通过 |
| LLMServingSim 原生样例 | `single` 两次均完成 10 个请求；时钟 1663996645 ns、逐请求 CSV 均匹配上游，图的来源路径差异已核验 |
| M0.2 上游回归 | 所选 8 项时钟全部匹配；请求核验通过，1 份不适用的上游 CSV 已定位来源，未运行全套回归 |
| M0.3 联合编译与链接 | 445 步构建通过；ASTRA 52 个核心对象全部链接，运行时 10 个 ns-3 模块全部来自本次 UB 构建 |
| M1.1 计算与时间 | 1,000 ns COMP 经 ASTRA 原路径在 ns-3 时间上完成；非零起点、1 ns、零耗时及边界检查通过 |
| 后续联合仿真 | UB 业务收发、服务调度尚未实现；M1 整体未完成 |

本机 Python 3.12.3 已确认。UB 任务中的 `ns3` 启动器拒绝 root 会话，实际通过的是锁定版本文档提供的直接 CMake 入口及生成的样例程序；启动器原命令的失败记录保留在下文。

## 本地初始化时记录

按照 `docs/tasks/current.md` 操作。只记录实际获取的系统与工具版本、子模块初始化结果、依赖检查的命令和退出状态。某个远端或提交不可获取时，保留该路径、来源和错误摘要，不自动换源。

日志可以保存在主仓库忽略的 `runs/` 下。需要提交的记录写明日志相对路径和必要校验值，不把凭证、用户名敏感路径或完整私有网络配置写入文档。

## 运行记录格式

每次实际执行后追加一个记录，包含任务编号、日期、仓库及依赖提交、工作目录、输入、命令、退出状态、日志位置和结论。没有执行的命令保留为待执行，不填写成功标记。

以下按时间保留沙箱诊断、依赖初始化与静态检查、UB 和 LLMServingSim 原生样例构建及运行记录；早期记录中的“未执行”表示当时的状态。

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

## 2026-09-27：repo-local-check

本次递归初始化与静态依赖检查通过。工作目录为 `/root/moe-ub-cosim`，主仓库 HEAD 为 `62fc755406cea250b5c990ceb13e8eaef9263efd`，保留 `main` 分支。开始时 `docs/environment.md` 和 `docs/tasks/current.md` 已有未提交修改，两个直接子模块未初始化；没有合并冲突。本次保留既有诊断与范围核对记录，仅更新这两份文档，并生成忽略目录内的本机日志。

执行前已审阅 `scripts/check_deps.sh` 和 `scripts/check_deps.py` 全文，确认脚本仅使用 Python 标准库与只读 Git 命令，不初始化、不切换版本、不编译、不删除文件。初始化后读取两个直接子模块根目录的 `AGENTS.md`；其中构建和仿真实验流程不属于本任务，没有执行。

日志目录为 `runs/repo-local-check-20260927T084441Z/`。其中 `record.py` 记录每次命令的完整参数、工作目录、UTC 起止时间、实际退出码及标准输出、标准错误文件名；命令仅额外设置 `GIT_TERMINAL_PROMPT=0`，不保存完整环境变量。表中的日志名均相对于该目录。

| 实际命令或检查及输入 | 退出状态与结果 | 记录文件 |
|---|---|---|
| `git status --short --branch`、`git submodule status --recursive`、`git ls-tree HEAD third_party/LLMServingSim third_party/ns-3-ub`，初始化前的工作区与父仓库 gitlink | 均为 0；两个直接子模块未初始化，仅两份记录文档有改动 | `initial-status.json`、`initial-submodules.json`、`initial-gitlinks.json` |
| Python 标准库读取 `/etc/os-release`、内核和架构，并执行 `git --version`、`bash --version`、`python3 --version`；完整 `python3 -c` 参数见记录 | 0；Ubuntu 24.04.4 LTS、内核 6.11.0-1027-oem、x86_64、Git 2.43.0、Bash 5.2.21、Python 3.12.3 | `environment.json` |
| `bash -n scripts/check_deps.sh` | 0；Shell 语法通过 | `bash-syntax.json` |
| `python3 -c 'import ast; from pathlib import Path; ast.parse(Path("scripts/check_deps.py").read_text(), filename="scripts/check_deps.py"); print("Python syntax OK")'` | 0；Python 语法通过，无字节码文件生成 | `python-syntax.json` |
| `git submodule update --init --recursive`，输入为各级父仓库固定 gitlink 和 `.gitmodules` | 0；08:45:30 至 08:47:29 UTC 完成 13 个依赖仓库初始化，未换源或升级 | `initialize.json` |
| `git submodule status --recursive`，初始化后的依赖树 | 0；全部检出与父仓库指针一致，无未初始化项 | `initialized-submodules.json` |
| `rg --files --hidden --no-ignore -g AGENTS.md third_party` | 0；发现两个直接子模块根指令及 LLMServingSim 的 docs 专用指令；本次不操作其 docs 路径 | `agent-inventory.json` |
| `bash scripts/check_deps.sh`，输入为原锁文件、规范校验和、各级 HEAD、索引、`.gitmodules` 和工作区 | 0；输出 `PASS: checked 13 dependency checkouts; versions and inspected states agree.`，唯一警告是主仓库两份文档已有修改 | `dependency-check.json` |
| `git status --short --branch`，初始化后、更新记录前 | 0；仍仅两份记录文档有改动，主仓库无 gitlink 变更 | `post-initialization-status.json` |
| 更新记录后的 `git diff --check`、`git diff --cached --exit-code`、`git status --short --branch`，以及按初始 SHA-256 清单核对文件改动和原历史记录保留情况 | 均为 0；无空白错误、无暂存改动，本次仅修改两份允许的文档，既有历史记录原文保留 | `final-diff-check.json`、`final-index-check.json`、`final-status.json`、`final-scope-check.json` |

锁文件显式登记的 11 项依赖全部通过。四个核心组件实际检出分别为 LLMServingSim `a4053bc1161872420e1e0607cb3409ef659b828e`、ASTRA-sim `d3469945c50410cac2e727c9ab627670c965db2a`、Chakra `30221ab8bfd9212aa9c063cd0b14a7d0a2ea6dd7` 和 ns-3-UB `581bf28a90740def0e79eafe5423af838c6f687d`。GitCode 上的 `ns-3-ub-tools` 已实际获取并检出到 `2afc7202f88c132b63c38e8c7dcfc08505b13bb2`。

另外两项递归依赖位于 ASTRA 的 `extern/network_backend/analytical/extern/`：`googletest` 为 `1204d634444b0ba6da53201a8b6caf2a502d883c`，`yaml-cpp` 为 `3d2888cc8a45da2f420454ad728cdfad01a3d54f`。它们按固定父提交的 gitlink 检查，没有扩写锁文件。

所有依赖仓库的已检查源码工作区均干净，HEAD、索引 gitlink 和来源记录一致。主仓库锁文件、`.gitmodules`、技术规范及检查脚本保持原内容；原文档内容和初始文件校验值分别保存在 `initial-environment.md`、`initial-current.md` 和 `initial-tracked-sha256.json`。锁文件中的 `not_run` 等准备期描述按本任务范围保留，当前实际运行结论以本节记录为准。

本次没有发现初始化或静态依赖检查的阻塞项。停止在本地依赖检查完成处：未安装软件、修改上游源码、编译、运行模拟器、创建提交或推送。编译器、构建库、原生样例及联合程序仍未验证，检查通过不代表编译或仿真通过。

## 2026-09-27：m0-1-ub-native-baseline

UB 原生 `scratch/2nodes_single-tp` 样例构建及两次相同输入运行通过。任务 0 至 3 均有完整任务事件和包时间戳，独立后处理复核通过，两次原始跟踪及统计文件逐字节一致。本结论仅覆盖该固定版本的原生样例，不表示联合仿真、真实数据来源或目标硬件校准已验证。

主仓库起点为 `12f31d2fd446a25c4914eb5d5a77515e7f75dc34`，开始时 `main` 工作区干净。ns-3-UB 保持 `581bf28a90740def0e79eafe5423af838c6f687d`，配套工具保持 `2afc7202f88c132b63c38e8c7dcfc08505b13bb2`。执行前后 `bash scripts/check_deps.sh` 均退出 0，13 个依赖仓库的固定提交和已检查源码状态保持一致。

日志目录为 `runs/m0-1-ub-native-baseline-20260927T092308Z/`，下文日志名均相对于该目录。`record.py` 保存完整参数、绝对工作目录、UTC 起止时间、实际退出码和独立 stdout/stderr 文件；只额外设置 `GIT_TERMINAL_PROMPT=0`。日志、原始结果归档和本次核验脚本均为本机忽略产物，不提交 Git。

### 环境与构建入口

本机进程为 UID 0，`USER=root`。可用 CPU 亲和性为 32 核，容器内存上限为 64 GiB；本轮只启动一个构建，使用 8 路并行。GCC/G++ 为 Ubuntu `13.3.0-6ubuntu2~24.04.1`，CMake 3.28.3、Ninja 1.11.1、Make 4.3、Python 3.12.3 已存在；`pkg-config` 和 `ccache` 不存在，本样例实际构建未依赖它们。

Python 包元数据检查显示 pandas 3.0.0、numpy 2.4.1、matplotlib 3.10.8、seaborn 0.13.2、networkx 3.6.1 与工具 requirements 一致；tqdm 实装 4.67.3，requirements 要求 4.68.4。本轮使用的三个跟踪处理脚本仅导入 Python 标准库，因此没有安装或升级这些包；不据此声称整套工具依赖已满足。完整事实见 `preflight.stdout.log`。

执行前已审阅启动器、构建入口、场景加载代码、输入及后处理脚本。初始没有 `build/`、`cmake-cache/` 或样例旧输出。以下命令均以 `third_party/ns-3-ub/` 为工作目录。

上游启动器原命令实际退出 1（`launcher-configure.json`）：

```bash
python3.12 ./ns3 configure --enable-modules=unified-bus --disable-examples --disable-tests --disable-mpi --disable-mtp --disable-werror -d release -G Ninja
```

`ns3:1728` 的 `refuse_run_as_root()` 在参数解析前拒绝 `USER=root`。未修改启动器、伪造用户变量或使用 sudo。锁定版本 `doc/manual/source/working-with-cmake.rst` 的原生 CMake 配置及目标构建说明提供了另一入口；采用以下命令后，配置与构建分别退出 0（`cmake-configure.json`、`build.json`）：

```bash
cmake -S . -B cmake-cache -DCMAKE_BUILD_TYPE=release \
  -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_ENABLED_MODULES=unified-bus \
  -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF \
  -DNS3_CCACHE=OFF -G Ninja
cmake --build cmake-cache --target scratch_ub-quick-example --parallel 8
```

断言和普通 ns-3 日志选项采用启动器 release 配置的原有取值，UB 样例输入中的跟踪开关保持原值。未构建单元测试、其他样例或联合程序。CMake 解析出的模块为 applications、bridge、config-store、core、internet、network、point-to-point、stats、traffic-control、unified-bus；MPI/MTP 关闭。未安装 CMake 报告的可选库。

构建完成 382 个步骤，生成 `build/scratch/ns3.44-ub-quick-example`，SHA-256 为 `f25fb3e86f898971e0c9ec464e5ddd8e417ff66499ea8f9fe7f9d5332d09f02e`。构建与仿真的成功结论来自上述实际执行，不来自启动器检查或静态依赖检查。

### 输入、运行与归档

样例包含 2 个设备和 5 个交换机，四个任务各 409600 字节，总业务数据量 1638400 字节。输入保持原内容，包括三个 `URMA_WRITE` 和一个 `URMA_READ`。以下 SHA-256 在首次运行前记录，归档时及结束验收时再次核对，全部一致。

| 样例内文件 | SHA-256 |
|---|---|
| `network_attribute.txt` | `3d4ec2706b6487df78386cec007863f68aa75812d83178fa2481efea6896b3e3` |
| `network_topology.png` | `883caf71d9a3e3b6b4d61c9112b54ecdeba836133108d81d6a2e362a5435dd04` |
| `node.csv` | `cd06746eacfe49c5727adeaf3110c60931df1063211d397dcc1f06367b559f60` |
| `routing_table.csv` | `d0b8280fece127140e7396393e6d61f1b357a07500d23104be4f78b704cae515` |
| `topology.csv` | `3733e8323d0d63f7cc86ccbc05e52282ff8e103a1cae828f0046668a2332defb` |
| `traffic.csv` | `05e0863be4864eaff83ed14fa89e3975c66e5531ad62e8768414d35af662b015` |
| `transport_channel.csv` | `285c048b315a0f38833ffe1622f457f2b3b36584779c91e926a9116c9ddaef46` |

两次均在 `third_party/ns-3-ub/` 执行以下完全相同的命令，均退出 0，记录为 `run-1.json`、`run-2.json`：

```bash
./build/scratch/ns3.44-ub-quick-example --case-path=scratch/2nodes_single-tp
```

程序采用原生单进程运行；未额外设置截断时间或随机参数。锁定入口的默认 `rngRun=10` 实际传给 `RngSeedManager::SetSeed`。两次日志均记录调度四个本地任务、仿真结束及统计输出；标准错误为空。

样例原生入口会在运行前重建 `runlog/`，因此第一次运行后先将完整样例目录复制到本日志目录的 `run-1/` 并校验，再执行第二次并复制到 `run-2/`。归档命令为主仓库根目录下的 `python3 runs/m0-1-ub-native-baseline-20260927T092308Z/archive_run.py run-1` 和相同脚本的 `run-2` 参数；均退出 0（`archive-1.json`、`archive-2.json`），每份保留 44 个文件、3604523 字节，包含 7 个输入、35 个跟踪文件及 2 个自动统计 CSV。逐文件大小和校验值见 `run-1-files.json`、`run-2-files.json`。没有覆盖未知旧结果。

### 后处理与验收

自动路径 `UbUtils::ParseTrace`（`src/unified-bus/model/ub-utils.cc:795`）仅处理 `system()` 返回 -1，工具 `trace_analysis/parse_trace.py` 又未传播两个子脚本的退出码。因此样例程序返回 0 不能独立证明自动后处理成功，本轮不能提供其子脚本当时各自的实际退出码。

为核对实际后处理，在主仓库根目录对两份归档分别执行以下四条命令；`true` 将复核结果写入归档的 `test/`，保留原自动 `output/`。四次均退出 0，标准错误为空，记录分别为 `postprocess-1-tasks.json`、`postprocess-1-ports.json`、`postprocess-2-tasks.json`、`postprocess-2-ports.json`。

```bash
python3 third_party/ns-3-ub/scratch/ns-3-ub-tools/trace_analysis/task_statistics.py runs/m0-1-ub-native-baseline-20260927T092308Z/run-1 true
python3 third_party/ns-3-ub/scratch/ns-3-ub-tools/trace_analysis/cal_throughput.py runs/m0-1-ub-native-baseline-20260927T092308Z/run-1 true
python3 third_party/ns-3-ub/scratch/ns-3-ub-tools/trace_analysis/task_statistics.py runs/m0-1-ub-native-baseline-20260927T092308Z/run-2 true
python3 third_party/ns-3-ub/scratch/ns-3-ub-tools/trace_analysis/cal_throughput.py runs/m0-1-ub-native-baseline-20260927T092308Z/run-2 true
```

主仓库根目录执行 `python3 runs/m0-1-ub-native-baseline-20260927T092308Z/verify_results.py`，退出 0（`verify-results.json`）。该核验逐项检查原始输入校验值、四个任务的全部输入列、原始跟踪中的任务开始与完成事件、包时间戳、非空端口统计、后处理日志及结果一致性；完整结论见 `verification-summary.json`。四个任务各有且仅有一条 `WQE Starts` 与一条 `WQE Completes`，均位于相应发起节点，统计如下；两次结果相同。

| 任务 | 源→目的 | 操作 | 字节数 | 任务开始（μs） | 任务完成（μs） | 首包发送（μs） | 最后 ACK（μs） |
|---|---|---|---|---|---|---|---|
| 0 | 0→1 | URMA_WRITE | 409600 | 0.010000 | 32.731634 | 0.011000 | 33.653800 |
| 1 | 0→1 | URMA_WRITE | 409600 | 0.010000 | 33.065554 | 0.436405 | 33.654800 |
| 2 | 0→1 | URMA_WRITE | 409600 | 0.010000 | 33.399474 | 0.881637 | 33.655800 |
| 3 | 1→0 | URMA_READ | 409600 | 0.010000 | 33.644974 | 0.011000 | 33.732280 |

首包与最后 ACK 按上游统计脚本定义，分别取该任务所有包事件的最小发送时间和最大 ACK 时间；不把它们等同于任务完成事件。任务统计按业务字节数计算，端口 `TotalBytes` 按原始包大小统计且为时间区间扣除边界包：Rx 扣首包，Tx 扣末包，因此不等于业务数据量或完整线路字节总量。每份端口 CSV 有 16 条方向统计，本轮只验证有效输出和可重复性，不据此作线速或硬件性能结论。

两次归档中的 44 个原始文件逐字节一致；两份归档独立复跑的 `test/` CSV 也分别与原 `output/` 一致。任务统计 CSV 的 SHA-256 为 `1d6502b33250f5dd15391b4e64aea98d5eaf82b7e4f701570120f4f19ee809a3`，端口统计 CSV 为 `e15e543e1b511ceaaa8f8ea1903850d23483db48bf26909a90cfd7a2ff4bfbfe`。命令 stdout 中的墙钟时间不同属于实际执行耗时，不纳入仿真结果相等判断。

### 已解释的现象与停止位置

原始 `TaskTrace` 中部分 `WQE Segment` 行、`PacketTrace` 中部分包事件成对重复。源码显示预加载通道经 `ub-utils.cc:597` 调用 `UbController::CreateTp`，后者在 `ub-controller.cc:98` 注册 `SingleTpTraceConnect`；随后 `ub-case-runner.cc:445–446` 又调用 `TopoTraceConnect`，在 `ub-utils.cc:2635–2643` 对同一通道再次注册对应回调。这解释了重复跟踪行，不应直接按这些行数计算业务传输次数。任务级开始/完成事件不重复，两次原始重复行也保持一致。本轮保留全部原始记录，未去重或修补源码；建议在获准的跟踪修复任务中处理回调重复注册，并在独立后处理修复任务中传播子进程失败。

启动日志中的旧属性别名改写是 `CreateConfigStoreInput` 写入临时配置后加载的兼容行为，原 `network_attribute.txt` 未改变；`node.csv` 旧四列的 `forwardDelay` 按锁定加载器映射到 `AllocationTime`，本轮未重新定义该语义。未发现未解释的缺失任务或运行错误。

任务前后的依赖检查日志为 `dependency-check.json`、`dependency-final.json`，均退出 0。`git diff --check` 和 `git submodule status --recursive` 均退出 0，记录为 `final-diff-check.json`、`final-submodules.json`。全部依赖源码工作区干净，固定版本、锁文件、技术规范和 gitlink 保持原值。仅更新 `README.md`、`docs/environment.md` 和 `docs/tasks/current.md`，完成记录按持续授权本地提交、不推送。

`docs/development.md` 开头仍有准备期“构建和仿真尚未验证”的状态句，与本次新证据不再一致；该句可能使后续读者误判进度，当前事实以本节及已同步的 README/当前任务为准。该文件不在本任务允许修改范围内，建议下次获准维护管理说明时改为仅引用环境记录，避免复制易过期状态。

本任务标为 `completed` 并停止。LLMServingSim 原生样例尚未执行，联合构建与仿真尚未实现，M0.1 和 M0 整体仍未完成；下一项任务由用户确认，不自动改写为新任务。

## 2026-09-27：m0-1-llmservingsim-native-baseline

用户确认开始 LLMServingSim 原生样例。任务说明已切换并本地提交为 `46659d2`，同时将开发管理说明开头的过期状态句改为引用环境记录，未改变开发规则。起点为 `88bed68`，主仓库及相关子模块开始时干净；固定源码版本不变。

采用上游 `serving/run.sh` 默认样例与 `serving/validate.sh` 的 `single` 场景：`configs/cluster/single_node_single_instance.json`、`workloads/example_trace.jsonl`、`--dtype bfloat16 --block-size 16 --num-reqs 10`。本地模型配置为 Llama-3.1-8B，使用随仓库提交的 RTXPRO6000 bf16/tp1 性能表；10 条请求共 120 个输入 token 和 591 个输出 token。上游记录时钟为 1663996645 ns，后续两次实际运行均匹配，完成结果见下文。没有下载模型权重或新增工作负载。

日志目录为 `runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/`。记录工具沿用已审阅的 `record.py`，每份 JSON 保存工作目录、完整参数、时间、真实退出码及 stdout/stderr 文件名。

| 实际检查（工作目录均为主仓库根目录） | 结果 | 日志 |
|---|---|---|
| `python3 runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/preflight.py` | 退出 0；保存 12 个样例/参照文件校验值，发现下述缺项，无已有 analytical 构建或 Chakra 生成文件 | `preflight.json`、`preflight.stdout.log` |
| `apt-get --simulate --no-install-recommends --no-upgrade install protobuf-compiler libprotobuf-dev` | 退出 0；预演新增 5 个包，无升级、删除，尚未实际安装 | `install-plan.json` |
| `bash scripts/check_deps.sh` | 退出 0；13 个依赖版本与源码状态一致 | `dependency-check.json` |

预检时系统没有 `protoc`、Protobuf C++ 开发头文件；Python 没有 protobuf、Chakra、pyinstrument、setuptools-grpc 或 grpcio-tools。原有 numpy 2.4.1、pandas 3.0.0、PyYAML 6.0.1、rich 13.7.1；它们不同于上游 Docker 示例中的部分旧版本，兼容性由后续实际运行核对，不由版本号预先推断。

预检提出的系统安装方案为现有软件源中的 `protobuf-compiler`、`libprotobuf-dev` 及依赖 `libprotobuf-lite32t64`、`libprotobuf32t64`、`libprotoc32t64`，候选版本均为 `3.21.12-8.2ubuntu0.3`。`python3.12-venv` 已存在，无需新增 `python3-venv` 元包。Python 使用项目专用 `.venv/llmservingsim-native/`，安装固定 Chakra 源码、本样例运行依赖和打包依赖，避免 `scripts/compile.sh` 中的全局 `pip3 install .`。预检曾停在等待安装授权处，以下为用户回复“允许，继续吧”后的实际执行记录。

### 获准安装与实际版本

LLMServingSim 保持 `a4053bc1161872420e1e0607cb3409ef659b828e`，ASTRA-sim 保持 `d3469945c50410cac2e727c9ab627670c965db2a`，Chakra 保持 `30221ab8bfd9212aa9c063cd0b14a7d0a2ea6dd7`。下列安装命令均在主仓库根目录执行并退出 0，记录依次为 `install-system.json`、`create-venv.json`、`install-python.json`、`install-chakra.json`。

```bash
env DEBIAN_FRONTEND=noninteractive apt-get --no-install-recommends --no-upgrade -y install protobuf-compiler=3.21.12-8.2ubuntu0.3 libprotobuf-dev=3.21.12-8.2ubuntu0.3
python3 -m venv --system-site-packages .venv/llmservingsim-native
.venv/llmservingsim-native/bin/python -m pip install --disable-pip-version-check --report runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/python-install-report.json 'protobuf>=7.35.1,<8' pyinstrument==5.1.3 setuptools-grpc==1.0.0 wheel
.venv/llmservingsim-native/bin/python -m pip install --disable-pip-version-check --no-deps --no-build-isolation --report runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/chakra-install-report.json ./third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra
```

系统实际新增上述 5 个包，均为 `3.21.12-8.2ubuntu0.3`，无升级或删除；`protoc --version` 返回 `libprotoc 3.21.12`。虚拟环境使用 Python 3.12.3，复用原有 numpy、pandas、PyYAML、rich 和 wheel 0.42.0；新增 protobuf 7.36.2、pyinstrument 5.1.3、setuptools-grpc 1.0.0、grpcio-tools/grpcio 1.84.0、setuptools 84.0.0、typing-extensions 4.16.0 和 Chakra 0.0.4。系统 Python 包未卸载或替换。安装来源、版本与下载摘要保存在两个 `*-install-report.json` 中，虚拟环境自身的软件清单见 `python-freeze.stdout.log`。

Chakra 采用 `--no-deps`，因为本次调用的 `LLMConverter` 及 `protolib` 只依赖标准库与 protobuf；没有安装其他转换、可视化工具所需的 HolisticTraceAnalysis、graphviz、pydot 等扩展依赖，不声称完整 Chakra 工具集可用。`verify-runtime.json` 记录前端及转换器实际导入检查退出 0，导入位置确认为专用虚拟环境安装的 Chakra；已安装 `llm_converter.py` 与固定源码逐字节相同，SHA-256 为 `d375023f49eea65f3678f5150d4a51a281d73fb64422991e7879e3f7fe839975`。

### 原生 analytical 构建

采用已审阅的上游构建步骤，拆开 protobuf 生成、CMake 配置及目标构建，以限制本次目标和并行度。以下命令工作目录均为 `third_party/LLMServingSim/astra-sim/`，分别退出 0（`generate-cpp-protobuf.json`、`cmake-configure.json`、`build.json`）：

```bash
protoc et_def.proto --proto_path=extern/graph_frontend/chakra/schema/protobuf --cpp_out=extern/graph_frontend/chakra/schema/protobuf
cmake -S build/astra_analytical -B build/astra_analytical/build -DBUILDTARGET=congestion_unaware -G Ninja
cmake --build build/astra_analytical/build --target AstraSim_Analytical_Congestion_Unaware --parallel 8
```

实际使用 C++17、GCC 13.3.0、CMake 3.28.3、Ninja 1.11.1 和上游默认 `RelWithDebInfo`。构建 118 步完成；保留 31 条来自 Chakra 头文件的 `std::binary_function` 弃用警告，没有修改代码或警告策略。生成的程序为 `build/astra_analytical/build/bin/AstraSim_Analytical_Congestion_Unaware`，SHA-256 为 `a5ea8e84ad0f6c354218a009adebd534d0ade1caefb965b4788ef651463a7b70`。

主仓库根目录执行 `python3 runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/prepare_run.py`，退出 0（`prepare-run.json`）：按上游 `build.sh` 的同一路径创建 `AnalyticalAstra/bin/AnalyticalAstra` 到新程序的兼容符号链接，核对并归档输入。程序动态依赖包含 `libprotobuf.so.32`、C++/C 标准库、libm 和 libz，详情见 `prepare-run.stdout.log`。本轮没有构建 ASTRA 自带 ns-3 后端，也没有链接或修改 UB。

### 两次样例运行

工作目录为 `third_party/LLMServingSim/`。首次实际命令如下，退出 0（`run-1.json`）：

```bash
timeout --kill-after=10s 180s /root/moe-ub-cosim/.venv/llmservingsim-native/bin/python -m serving \
  --cluster-config configs/cluster/single_node_single_instance.json \
  --dtype bfloat16 --block-size 16 --dataset workloads/example_trace.jsonl --num-reqs 10 \
  --network-backend analytical --run-id m0_1_llm_native_run1 \
  --inputs-root /root/moe-ub-cosim/runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/run-1/inputs \
  --output /root/moe-ub-cosim/runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/run-1/requests.csv \
  --keep-inputs
```

第二次只将 run ID 改为 `m0_1_llm_native_run2`、输入产物目录和输出 CSV 改到 `run-2/`，其他参数和业务输入相同，退出 0（完整参数见 `run-2.json`）。超时保护未触发。`--keep-inputs` 仅保留生成物，没有改变调度或网络模型；没有运行整个 `validate.sh`，也未覆盖原生 `outputs/example_single_run.csv`。

| 检查项 | 第一次 | 第二次 |
|---|---|---|
| 运行退出码 / stderr | 0 / 空 | 0 / 空 |
| ASTRA 完成标记 | `All Request Has Been Exited` | 相同 |
| 完成请求数 | 10 | 10 |
| 输入 / 输出 token 数 | 120 / 591 | 120 / 591 |
| 总仿真时钟 | 1663996645 ns | 1663996645 ns |
| 逐请求 CSV 与上游参照 | 逐字节一致 | 逐字节一致 |
| 保留的配置与结果 | 150 个文件，1625186 字节 | 相同文件数和字节数 |

每次保留 146 份 Chakra `.et` 图、network/system/memory 三份配置和 `requests.csv`。生成的网络为一个模拟加速器的 FullyConnected analytical 拓扑，计算耗时来自随仓库提交的性能表；这不是 GPU 上的真实推理运行，也未校准目标硬件。

输入与上游参照归档在 `original-inputs/`，12 个 SHA-256 见 `preflight.stdout.log`，验收时确认原文件未变。关键校验值：集群配置 `9ec70560412a8925df762c4633bdab23b84123e1cf27bb23b1168dd9a06b4ffb`，请求文件 `8e55b35d62c0530fd6fefcf67d422b1dcb8b80e1c71243a21900f8ac829b5429`，模型配置 `54acfad3cffe057640904ca8a1e83525e6551c70c7a04c641f5a9eda0bbf64bd`。两份新请求 CSV 与上游参照的 SHA-256 均为 `58f349a355a8c7d728bbc0768edca2babf51a7320b23ff380f8d9e4cb83159e6`。

### 比较失败、差异解释与验收

主仓库根目录执行 `python3 runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/verify_results.py`，在通过输入校验、10 个请求的编号/token/到达时间/完成时间/延迟/ITL 校验及上游 CSV 比较后，因两份归档不完全字节相等而退出 1（`verify-results.json`）。原断言、错误日志和两份原始结果保持原样，没有修改预期时钟或覆盖上游结果。逐文件摘要保存在 `run-1-files.json`、`run-2-files.json`。

差异定位为全部 146 个 `.et` 文件的来源路径。锁定转换器 `src/converter/llm_converter.py:113` 的 `get_global_metadata()` 将 `input_filename` 写入 `GlobalMetadata.input_file`，而两次 `--inputs-root` 本来不同；锁定读取器 `src/feeder/et_feeder.cpp:87` 只读取并丢弃该元数据，不将来源路径作为执行节点。

随后执行以下独立解码核验，退出 0（`audit-graph-metadata.json`）：

```bash
.venv/llmservingsim-native/bin/python runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/audit_graph_metadata.py
```

核验逐个读取原始图，确认元数据只有相同 schema 和对应本次目录的 `input_file`；两次路径去掉各自已知运行目录前缀后的后缀完全相同，除此以外元数据一致。元数据之后的全部执行节点字节完全相同，共 42631 个节点；节点 ID 唯一，数据与控制依赖都引用各自图内节点。请求 CSV 和三份生成配置共 4 个文件本身逐字节一致。该脚本只在内存中比较来源字段，没有重写任何 `.et`。完整逐图证据见 `graph-metadata-audit.json`。

因此本任务要求的关键仿真结果可重复，且与上游原生参照一致；原始归档的全文件字节一致检查仍保留为失败，并有明确、已验证的来源路径解释。两次 stdout 的运行目录、run ID 和壁钟耗时也不同，不属于模拟时钟或请求行为差异。

结束前 `bash scripts/check_deps.sh` 退出 0（`dependency-final.json`），13 个依赖的固定提交、gitlink 和已检查源码状态一致；所有子模块源码工作区干净。`git diff --check` 与 `git submodule status --recursive` 均退出 0（`final-diff-check.json`、`final-submodules.json`）。原输入、性能表、上游参照、技术规范和锁文件均未修改。构建、protobuf 生成文件、虚拟环境及运行归档保留在各自忽略路径中。

本次只提交任务、环境和 README 的实际完成记录，不推送。M0.1 的 UB 和 LLMServingSim 原生起步样例均已完成；原生数值作为后续回归参照，不能代替 M0.2 回归、M0.3 联合构建或模型校准。停止在本任务 `completed`，下一项由用户确认。

## 2026-09-27：m0-2-upstream-regressions

用户确认开始 M0.2。主仓库起点为 `7da6b6763553fcffb5fc6c6027403035790c43bd`，任务说明单独本地提交为 `4b96d06`；开始时主仓库及 13 个依赖工作区干净。LLMServingSim、ASTRA-sim、Chakra 分别仍为 `a4053bc1161872420e1e0607cb3409ef659b828e`、`d3469945c50410cac2e727c9ab627670c965db2a`、`30221ab8bfd9212aa9c063cd0b14a7d0a2ea6dd7`，UB 与其他 gitlink 未变。

本次只执行锁定 `serving/validate.sh` 的 8 个相关场景。复用 `.venv/llmservingsim-native/` 和 M0.1 的 analytical 程序，没有新增软件、构建或下载。Python/Chakra 导入及转换器源码校验通过，版本与前一记录相同；后端 SHA-256 仍为 `a5ea8e84ad0f6c354218a009adebd534d0ade1caefb965b4788ef651463a7b70`。

日志目录为 `runs/m0-2-upstream-regressions-20260927T103119Z/`，以下路径以此为基准。记录工具 `record.py` 保存真实退出码、完整参数、工作目录、UTC 起止时间和 stdout/stderr；`scenarios/<场景>/command.json` 另保存实际 Python 调用与各场景退出码。

### 输入与实际命令

预检读取原脚本中的参数，归档 38 个文件、10206627 字节，含 7 份集群配置、两份模型描述、对应架构 YAML、RTXPRO6000 bf16/tp1 与所需 tp2 性能数据、3 份请求文件、脚本及参照；逐文件 SHA-256 和 CSV 表头/行数见 `preflight-manifest.json`，原文件在 `original-inputs/`。表中集群名均省略 `configs/cluster/` 前缀和 `.json` 后缀。

| 场景 | 集群配置 | 请求文件 / 取前几条 |
|---|---|---|
| `moe` | `single_node_moe_single_instance` | `example_trace.jsonl` / 10 |
| `moe_dp_ep` | `single_node_moe_dp_ep_instance` | `example_trace.jsonl` / 10 |
| `pd` | `single_node_pd_instance` | `example_trace.jsonl` / 10 |
| `moe_pd` | `single_node_moe_pd_instance` | `example_trace.jsonl` / 10 |
| `dp_pp_uneven` | `single_node_dp_pp_instance` | `sharegpt-llama-3.1-8b-300-sps10.jsonl` / 50 |
| `moe_dp_pp_uneven` | `single_node_moe_dp_pp_instance` | `sharegpt-qwen3-30b-a3b-300-sps10.jsonl` / 2 |
| `moe_dp_tp_pp_uneven` | `single_node_moe_dp_tp_pp_instance` | 同上 / 10 |
| `moe_dp_ep_uneven` | `single_node_moe_dp_ep_instance` | 同上 / 10 |

请求文件均在锁定仓库的 `workloads/` 中。沿用上游 `--dtype bfloat16 --block-size 16 --log-level WARNING`，analytical 为原默认后端。保留原生 `LOAD` 请求分配、`BALANCED` 专家路由、block-copy、组批及缓存设置；这些原生近似不作为本项目真实专家路由或目标硬件校准依据。带 TP/PP 的场景用于检查已有代码，不扩大本项目首版功能范围。

实际回归命令工作目录为 `third_party/LLMServingSim/`，退出 0：

```bash
env PYTHON=/root/moe-ub-cosim/runs/m0-2-upstream-regressions-20260927T103119Z/python-capture \
  LOG_DIR=/root/moe-ub-cosim/runs/m0-2-upstream-regressions-20260927T103119Z/upstream \
  TIMEOUT=180 bash serving/validate.sh \
  moe moe_dp_ep pd moe_pd dp_pp_uneven moe_dp_pp_uneven moe_dp_tp_pp_uneven moe_dp_ep_uneven
```

`python-capture` 先核对收到的参数与预检中的原脚本参数完全一致，再调用项目虚拟环境 Python，只追加以下记录选项：`--inputs-root <本次日志目录>/scenarios/<场景>/inputs --output <本次日志目录>/scenarios/<场景>/requests.csv --keep-inputs`。run ID 保留原 `validate_<场景>`。脚本按其内部顺序逐项执行，每项 `timeout 180`；没有传 `--update`，显式场景选择跳过 bench 精度阶段。上游源码、示例输出和固定参照均未覆盖。

### 实际结果与产物

回归于 UTC 10:34:02 至 10:36:50 执行，原脚本报告 `Behaviour: 8/8 scenarios match their baselines.`。8 个子进程均退出 0，全部出现 `All Request Has Been Exited`，无超时或日志中的错误、警告消息。下表时钟均与 `serving/validate-baselines.txt` 完全相同。

| 场景 | 完成请求数 | 输入 / 输出 token | 仿真时钟（ns） | 保留执行图数 |
|---|---:|---:|---:|---:|
| `moe` | 10 | 120 / 591 | 1945309759 | 310 |
| `moe_dp_ep` | 10 | 120 / 591 | 1479062704 | 346 |
| `pd` | 10 | 120 / 591 | 1661784496 | 167 |
| `moe_pd` | 10 | 120 / 591 | 2081236210 | 226 |
| `dp_pp_uneven` | 50 | 46957 / 33805 | 14403280459 | 7900 |
| `moe_dp_pp_uneven` | 2 | 3489 / 1304 | 6225124056 | 4272 |
| `moe_dp_tp_pp_uneven` | 10 | 10469 / 6705 | 9595091395 | 12992 |
| `moe_dp_ep_uneven` | 10 | 10469 / 6705 | 9991090353 | 1670 |

总计 112 个请求，71864 个输入 token、50883 个输出 token。核验了请求编号无遗漏或重复，token 数、模型与到达时间符合原输入，结束时间不晚于总时钟，延迟等于结束减到达，排队时间不超过 TTFT，ITL 数量等于输出 token 数减一，ITL 总和加 TTFT 等于延迟，TPOT 的整除关系正确。两项 P/D 的 CSV 各有 10 条最终记录，均在 decode 实例 1；prefill 实例不另计为完成请求，这与 `scheduler.py:353` 的交接及 `save_output()` 的保存逻辑一致。

| 忙闲不均场景 | 实例 0：请求数 / 最后请求完成（ns） | 实例 1：请求数 / 最后请求完成（ns） |
|---|---:|---:|
| `dp_pp_uneven` | 25 / 13916025386 | 25 / 14402280459 |
| `moe_dp_pp_uneven` | 1 / 2107878605 | 1 / 6224124056 |
| `moe_dp_tp_pp_uneven` | 5 / 9510805607 | 5 / 9594091395 |
| `moe_dp_ep_uneven` | 5 / 9906680159 | 5 / 9990090353 |

这四项实际结果证明成员请求完成时间不一致时，所选原生样例仍正常退出。上游 `serving/__main__.py:733` 附近的 dummy 分支及 `:1116` 附近的组退出条件解释其同步方式；本次 WARNING 级日志没有逐次记录 dummy 创建，不能据此宣称已独立验证每次 dummy 的生命周期。

每个场景保存一份请求 CSV、network/system/memory 三份生成配置及执行图，共 27915 个文件、357635606 字节，其中图 27883 份。每场景的逐文件大小与 SHA-256 在 `<场景>-files.json`，统计在 `verification-summary.json`。没有修改或清理原始结果。

### 同名上游 CSV 的来源不一致

`verify_results.py` 在全部时钟、请求字段与输入校验通过后，报告唯一错误：`moe_dp_ep: per-request CSV differs from upstream example`，退出 1。完整检查条件、失败结果与日志保留，不将该记录改为通过。

原因是初次核验按同名文件选择了不适用的比较对象。`serving/run.sh:65-68` 和 `serving/validate.sh:126` 使用 `example_trace.jsonl` 的 10 条独立请求，但锁定 `outputs/example_moe_dp_ep_run.csv` 实际有 20 条记录。后续 `audit_reference.py` 逐项确认它们对应 `swe-bench-qwen3-30b-a3b-50-sps0.2.jsonl` 第一会话 `astropy__astropy-12907-run0`：全部 20 次调用的输入/输出 token 相同，首到达为 4059740 ns，后续到达均等于上次完成时间加对应工具等待。该 CSV 最后请求结束为 36550191391 ns；锁定回归另有 `agentic_moe_dp_ep 36551191391` 的独立时钟参照。

来源核验退出 0，详见 `reference-audit.json` 与 `reference-history.txt`；只读取既有会话数据，没有运行 agentic 场景。该请求文件另归档并记录 SHA-256 `2f7cb044b4e45ef05b7d05337a6a947094fb3e44bc0ef0753d3566ffbab8591b`。原 CSV 的 SHA-256 保持 `85fb5c213d093239ca0e95e8fbd93462ccc319d31241e50326bed8254157eb88`；本次 10 请求 CSV 为 `e8bfc749afe9c1e9b490ee2f96bd02c6d36c90b5c76834f92b14811ccab374c3`。

因此，`moe_dp_ep` 的时钟与输入/输出完整性已通过，但没有匹配本次输入的上游逐请求 CSV 可供对照。建议后续单独修正上游示例输出与生成命令的对应关系，本次保留上游文件。`moe` 和 `pd` 两份 CSV 与适用的上游文件逐字节一致，SHA-256 分别为 `05bf06d18e1e746e6a451b488fe212d327fa6278d2e9c3213547f05953bde149`、`321e2c281447e00aeee699cec32d60abf0145e7e7b9397775aea7849580d0e44`。其余五个场景没有同名上游 CSV，记录时钟回归和请求内部一致性，不宣称逐请求数值已与独立参照一致。

### 检查记录与停止位置

除上述回归命令外，下表命令均在主仓库根目录执行，脚本路径前缀为本次日志目录。

| 实际命令 | 退出码与结果 | 命令记录 |
|---|---|---|
| `bash scripts/check_deps.sh` | 0；开始时 13 个依赖一致 | `dependency-before.json` |
| `.venv/llmservingsim-native/bin/python <日志目录>/verify_runtime.py` | 0；环境与固定转换器一致 | `runtime-check.json` |
| `python3 <日志目录>/preflight.py` | 0；归档输入，核对后端与参数 | `preflight.json` |
| 上述原 `serving/validate.sh` 选择命令 | 0；8/8 时钟回归，各场景也均退出 0 | `upstream-regressions.json`、`scenarios/*/command.json` |
| `python3 <日志目录>/verify_results.py` | 1；请求核验通过，唯一失败是上述不适用的 CSV 比较 | `verify-results.json` |
| `python3 <日志目录>/audit_reference.py` | 0；核对 20 次调用来源及到达链，复核输入与程序未变 | `audit-reference.json` |
| `bash scripts/check_deps.sh` | 0；结束前 13 个依赖源码、提交与 gitlink 一致 | `dependency-final.json` |
| `git diff --check` | 0；文档差异格式检查通过 | `final-diff-check.json` |

本任务完成了规定的相关上游回归与失败原因记录，没有更改任何仿真行为或上游参照。技术规范、依赖锁文件和所有子模块保持原样。完成记录仅本地提交，不推送；停止在 M0.2，M0.3 联合构建及目标硬件校准未执行，下一任务仍由用户确认。

## 2026-09-27：m0-3-joint-build-boundary

用户确认开始 M0.3，主仓库起点为 `cc2f03246252ef117b243bae69486f303b94b7db`，任务说明单独提交为 `132e7f2`。开始时主仓库与 13 个依赖工作区干净。LLMServingSim、ASTRA、Chakra、UB 分别保持 `a4053bc1161872420e1e0607cb3409ef659b828e`、`d3469945c50410cac2e727c9ab627670c965db2a`、`30221ab8bfd9212aa9c063cd0b14a7d0a2ea6dd7`、`581bf28a90740def0e79eafe5423af838c6f687d`；其他依赖仍按原 gitlink 检出。

本次新增 [联合链接探针](../scripts/m0_joint_build/README.md)，验证 ASTRA 核心、Chakra 读取组件和 UB 的构建及链接边界。复用现有 GCC/G++ 13.3.0、CMake 3.28.3、Ninja 1.11.1、C++ Protobuf/protoc 3.21.12，不安装软件、不下载依赖、不修改上游源码。CMake 的 whole-archive 特性要求至少 3.24，本次只验证上述实际版本组合。

日志目录为 `runs/m0-3-joint-build-boundary-20260927T104500Z/`，下文日志名均相对于此目录。`record.py` 保存完整参数、工作目录、UTC 起止时间、实际退出码及 stdout/stderr。构建目录为 `build/m0-joint-build/`。这些目录均被 Git 忽略，提交中仅保存代码与必要结果记录。

### 构建方式与实际命令

ns-3-UB 是 CMake 主项目。`CMAKE_PROJECT_NS3_INCLUDE` 加载本次 `inject.cmake`，先加入 ASTRA 根库，再延迟到 UB 模块建立后创建探针。ASTRA 的 `AstraSim` 静态目标包含 52 个对象：45 个 ASTRA 内部源文件、6 个 Chakra/protobuf 源文件及 1 个解析内存模型源文件。没有加载 `build/astra_analytical`、`network_frontend/` 或 `extern/network_backend/` 下的构建入口。

Chakra 的 6 个源文件为 `schema/protobuf/et_def.pb.cc`、`src/feeder/et_feeder.cpp`、`et_feeder_node.cpp`、`json_node.cpp`、`wrapper_node.cpp` 和 `src/third_party/utils/protoio.cc`。复用 M0.1 用固定 schema 生成的 C++ 文件；每个只编译一次。解析内存模型 `AnalyticalMemory.cc` 属于核心依赖，不是解析网络后端。

以下命令工作目录均为主仓库根目录。第一次实际配置使用完整参数，退出 1；修正本次 CMake 后，保持缓存参数的第三次配置退出 0，原因见后文。

```bash
cmake -S third_party/ns-3-ub -B build/m0-joint-build -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/scripts/m0_joint_build/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m0-joint-build/output \
  -DNS3_ENABLED_MODULES=unified-bus \
  -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF \
  -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF \
  -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF \
  -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake -S third_party/ns-3-ub -B build/m0-joint-build
cmake --build build/m0-joint-build --target m0-joint-link-probe --parallel 8
```

构建退出 0，UTC 10:50:59 至 10:52:32 完成 445 个步骤，其中 431 条编译命令。只启动一个构建，最多 8 路并行；未构建样例、测试或其他后端。编译日志有 31 条 Chakra `std::binary_function` 弃用警告，未修补上游或新增抑制选项。CMake 提醒自定义输出目录位于 UB 源码树外，可能影响部分上游测试；本任务明确使用独立输出，只验证探针，不宣称上游测试已运行。

| 对象 | 实际 C++ 选项与编译单元数 |
|---|---|
| UB 与探针 | `-std=c++20`，370 个 |
| ASTRA（含 Chakra/内存模型）与 fmt | `-std=gnu++17`，52 + 2 个 |
| spdlog | `-std=c++17`，7 个 |

全部使用同一 GCC 13.3.0，release 选项为 `-O3 -DNDEBUG`，未覆盖 `_GLIBCXX_USE_CXX11_ABI`。UB 沿用原 release 的断言及日志关闭设置；探针使用显式条件和异常核验，不依赖 release 下失效的 C++ `assert`。ASTRA 依赖固定 fmt 11.0.2、spdlog 1.14.1 和系统 Protobuf；`SPDLOG_FMT_EXTERNAL=OFF` 保留上游 spdlog 的内置 fmt 选择，同时 ASTRA 仍链接单独的 fmt 库。本次构建、链接和运行证明该组合可用，不推广到其他编译器或标准库。

探针和最终审计实际命令如下，分别退出 0，超时保护未触发，探针 stderr 为空：

```bash
timeout --kill-after=5s 30s build/m0-joint-build/output/m0/ns3.44-m0-joint-link-probe \
  runs/m0-3-joint-build-boundary-20260927T104500Z/probe.et
python3 scripts/m0_joint_build/audit.py \
  --build-dir build/m0-joint-build \
  --run-log runs/m0-3-joint-build-boundary-20260927T104500Z/probe.stdout.log \
  --output-dir runs/m0-3-joint-build-boundary-20260927T104500Z/audit-final
```

输入是探针构造的一个 `METADATA_NODE`（id 7，名称 `m0-metadata-only`），带 `GlobalMetadata.version=m0-link-probe`；它不包含计算时长或通信业务。程序写入新文件后用原 ETFeeder 读取并移除该节点，核对无剩余节点；构造一个 NPU 的 HardwareResource，核对初始在途计数均为 0；核对 `ns3::UbApp` 类型及初始时钟 0 ns。源码没有调度事件或调用 `Simulator::Run`，输出 `simulation_events_executed=0` 与 `PASS: link and reader probe only`。

### 链接与实际装载的证据

审计读取 `ninja -t commands m0-joint-link-probe`、静态库成员、link map、`nm` 符号、ELF `NEEDED` 及探针用 `dl_iterate_phdr` 报告的实际装载路径。没有仅凭目录名或单次 `ldd` 判断。UB 设置编译命令导出的时间晚于 ASTRA 目标建立，因此 `compile_commands.json` 不能单独代表全部源文件；完整 Ninja 目标命令已另行保存。

链接命令以 `--push-state,--whole-archive` / `--pop-state` 包围 `libAstraSim.a`。全部 52 个成员在 link map 中均标记由 whole-archive 引入；Sys、Workload、Chakra 读取器关键符号实际存在于程序，ASTRA 静态库中没有 ns-3 符号定义。全部编译来源中没有 ASTRA 网络前端、其他网络后端或其内置 ns-3。

实际装载的 10 个 ns-3 模块为 applications、bridge、config-store、core、internet、network、point-to-point、stats、traffic-control、unified-bus。其真实路径全部位于 `build/m0-joint-build/output/lib/`，文件名均为 `libns3.44-<模块>.so`。没有装载 UB 原生 `build/` 或 ASTRA 的另一套 ns-3。其余文件库为系统的 protobuf、libstdc++、libm、libgcc_s、libc、libz 和动态加载器；ASTRA、fmt 与 spdlog 静态链接。

`audit-final/summary.json` 保存实际库路径、SHA-256、源文件及工具版本；`compiled-sources.json` 保存每个编译单元的完整命令、标准和源码校验值；`commands.json` 及各 stdout/stderr 保留审计所执行的命令与退出状态。`build-evidence/` 归档 CMake 缓存、52 项源文件清单、目标事实和 link map，`build-evidence.json` 保存其校验值。

| 产物 | SHA-256 |
|---|---|
| `output/m0/ns3.44-m0-joint-link-probe` | `375b21aae80cd98abc7c263a4f4f37142a68011e68657b814be39fd59cd4cc97` |
| `cosim-m0/lib/libAstraSim.a` | `023054d8f7b46eae21fcc2276008ee62f62ae5a5da338479f7a952cb695ed6c7` |
| `cosim-m0/link.map` | `cf7865e9252faaa2dd5c1b585708a69b7d7b4364a6d6dcb996cfda0032dc3adb` |

### 初次失败、修正与独立缓存核对

第一次配置把 `add_subdirectory` 放入 CMake 延迟调用，被 CMake 拒绝（`configure.json`，退出 1）；改为早期加入 ASTRA、延迟阶段只包含探针定义后，第二次配置因本次代码误判 UB 目标为 `libunified-bus` 而退出 1（`configure-retry.json`）。锁定 UB 的目标实际为 `unified-bus`，别名为 `ns3::unified-bus`；修正检查后 `configure-final.json` 退出 0。前两版文件分别保留于 `attempt-1/`、`attempt-2/`。

初次审计把 GNU whole-archive 的结束形式限定为 `--no-whole-archive`，但 CMake 实际使用 `--pop-state` 恢复先前状态，因而退出 1（`audit.json`）。原脚本保留为 `audit-initial.py`；修正为检查实际成对参数，并逐项核对全部归档成员均由 whole-archive 引入。link map 中的相对路径同时按构建目录解析。最终 `audit-final.json` 退出 0；没有删除构建边界检查或修改任何仿真预期。

为排除旧缓存依赖，用最终代码重新执行上述完整配置命令，仅将 `-B` 改为 `build/m0-joint-build/fresh-config`、输出目录改为该目录下的 `output`，退出 0（`configure-clean.json`）。未再次编译。两份 `ninja -t commands` 各有 445 条命令；首次替换各自绝对构建前缀后的逐字比较退出 1（`verify-clean-config.json`，差异保留于 `clean-recipe.diff`）。

逐参数核验确认 431 条编译命令完全相同，唯一差异是 10 个 UB 库链接命令的 RPATH 尾部冒号从 31 个变成 18 个：新构建目录多了 13 个字符，原始 RPATH 参数总长度不变，库目录、源文件、编译选项及其余链接参数一致。独立核验 `audit_clean_recipe.py` 退出 0，逐项差异见 `clean-recipe-audit.json`。原逐字比较仍保留为失败，不将两份命令描述为字节一致。

### 原生基线保护与结束检查

配置前备份 UB 忽略的 `.lock-ns3_linux_build`，并记录两项原生程序、10 个原生 UB 库、两个已生成 schema 文件及启动锁共 15 个文件的 SHA-256（`baseline-artifacts.json`、`native-ns3-lock.backup`）。UB 每次配置都会重写该启动锁；先核对新锁对应本次已知输出目录，再保存新锁并恢复原文件。主构建后与全新缓存配置后分别执行恢复，两次均确认 15 个文件校验值不变。未清理原生产物或改动原生缓存。

| 实际命令或步骤（工作目录为主仓库根目录） | 退出状态 | 记录 |
|---|---|---|
| 开始与结束 `bash scripts/check_deps.sh` | 均为 0；13 个依赖版本与已检查源码状态一致 | `dependency-before.json`、`dependency-final.json` |
| 保留参数的最终 CMake 配置 / 探针目标构建 / 探针运行 | 均为 0 | `configure-final.json`、`build.json`、`probe.json` |
| 上述最终链接审计 | 0 | `audit-final.json` |
| 最终代码全新缓存配置 | 0 | `configure-clean.json` |
| `python3 <日志目录>/verify_clean_config.py` | 1；上述 RPATH 填充差异 | `verify-clean-config.json` |
| `python3 <日志目录>/audit_clean_recipe.py` | 0；逐参数确认唯一差异 | `audit-clean-recipe.json` |
| `python3 <日志目录>/restore_native.py` 与 `restore_after_clean.py` | 均为 0；恢复锁并核对 15 个文件 | `restore-native.json`、`restore-after-clean.json` |

本任务确认了本机固定依赖组合的构建、链接、元数据读写与初始状态。没有运行联合计算图、COMP 或 UB 业务传输，没有实现时间转换、服务调度或 CCL 模型，也没有完成目标硬件校准。技术规范、锁文件、子模块指针和源码均未修改。整理完成记录后仅本地提交，不推送；停止在 M0.3，下一项 M1.1 的 1,000 ns COMP 等待用户确认。

## 2026-09-28：m1-1-compute-time

用户确认执行 M1.1，主仓库起点为 `925f54a261067369638b07535f4f1fe4ad0d4cd1`，任务说明单独提交为 `6005ce2`。开始时主仓库和 13 个依赖工作区干净，依赖仍为上一节的固定提交。复用本机 GCC 13.3.0、CMake 3.28.3、Ninja 1.11.1、Protobuf 3.21.12，没有安装、升级、下载或改动依赖源码。

本次新增 [单节点计算程序](../backend/README.md)、时间适配器、日志桥接、两份小型输入及单元/集成检查。程序保留 ASTRA Sys、Workload、HardwareResource 的原计算路径，由 UB 所用 ns-3 事件队列实际触发完成。M1.1 已通过，M1 整体尚未完成；没有创建业务网络拓扑或发起传输。

本机日志目录为 `runs/m1-1-compute-time-20260928T083610Z/`，以下日志名相对此目录。`record.py` 记录完整参数、工作目录、UTC 起止时间、真实退出码和 stdout/stderr；集成检查的每次程序调用另记于 `integration/commands.json`。构建产物在 `build/m1-compute/`。日志、缓存和产物均不提交 Git。

### 时间接口与锁定源码事实

Chakra `schema/protobuf/et_def.proto:144` 将字段命名为 `duration_micros`，但本分支 `src/feeder/et_feeder_node.cpp:10` 直接把它作为 runtime；ASTRA `Workload.cc:178-190` 明确保留“changed chakra to return nanoseconds”注释并取消乘 1,000。`Sys.cc:611-640` 以 NS 单位交给 `sim_schedule`，`boostedTick():566-579` 读取 `sim_get_time`，`CLOCK_PERIOD=1` 表示 1 ns。本程序的 `duration_ns` 在写入该字段时保持原值，不重复进行单位换算。

上游字段名与实际语义不一致，若按名称再乘 1,000，主例就会被误计为 1,000,000 ns。建议在所有本项目输入边界明确使用纳秒并注明固定分支语义；本轮不修改 schema。另查到 LLMServingSim `serving/core/trace_generator.py:265-266` 使用 `round()` 和至少 1 ns 的下限，与规范第 4.2 节正耗时向上取整、零保持零的要求不同。本轮不调用该 profile 读取路径，显式整数输入不受影响；以后接入 profile 时须在本项目输入规范化处处理，并保留原生基线，不能把上游转换直接当作已满足本项目规则。

时间适配器只接受锁定 Sys 的 NS 单位，检查非负且有限，向上取整后再检查 `now + duration <= INT64_MAX`，最后调用 `Simulator::Schedule`。返回的当前时间直接来自 `Simulator::Now()`，使用 long double 保留纳秒整数精度；编译时要求其有效二进制位至少为 63。没有 analytical 网络时间队列。完成通知回传当前时间，不调用 `Simulator::Stop`。SEND/RECV 当前明确抛出未实现错误，单元检查已确认不会伪造通信完成。

### 配置、构建与实际运行

工作目录均为主仓库根目录。ns-3-UB 仍为 CMake 主项目，新的 `backend/inject.cmake` 包含未修改的 M0 构建入口，再加入计算目标。首次完整配置退出 0：

```bash
cmake -S third_party/ns-3-ub -B build/m1-compute -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m1-compute/output \
  -DNS3_ENABLED_MODULES=unified-bus \
  -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF \
  -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF \
  -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF \
  -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m1-compute \
  --target cosim-compute cosim-time-test m0-joint-link-probe --parallel 8
```

构建命令前三次退出 1，均为本次日志头接入问题，具体原因见后文。修正后同一构建命令于 UTC 08:48:32 至 08:49:58 退出 0，完成剩余 395 个步骤；最终三个目标共对应 453 条 Ninja 构建命令。单元测试进程划分修正后，仅重建 `cosim-time-test`，2 个步骤退出 0。没有同时运行多个构建，最多 8 路并行。CMake 的源码树外输出警告及上游弃用警告保留，未关闭额外检查或修改上游头文件。

计算程序实际包含 433 个编译单元：371 个 C++20（UB 与本次入口/时间适配器）、54 个 GNU C++17（ASTRA 的 52 个对象和 fmt 的 2 个对象）、8 个 C++17（spdlog 的 7 个对象和本次日志桥接）。沿用同一 GCC/libstdc++、`-O3 -DNDEBUG`，没有覆盖 ABI 宏。C++ 检查使用显式条件，单元测试使用抛异常的检查函数，不依赖 release 下禁用的断言。

实际检查命令如下，修正后的全部检查退出 0：

```bash
timeout --kill-after=5s 30s build/m1-compute/output/cosim/ns3.44-cosim-time-test
timeout --kill-after=5s 30s build/m1-compute/output/cosim/ns3.44-cosim-time-test --wrong-resolution
python3 tests/integration/check_compute.py \
  --build-dir build/m1-compute \
  --output-dir runs/m1-1-compute-time-20260928T083610Z/integration
timeout --kill-after=5s 30s build/m1-compute/output/m0/ns3.44-m0-joint-link-probe \
  runs/m1-1-compute-time-20260928T083610Z/m0-probe.et
python3 scripts/m0_joint_build/audit.py \
  --build-dir build/m1-compute \
  --run-log runs/m1-1-compute-time-20260928T083610Z/m0-probe.stdout.log \
  --output-dir runs/m1-1-compute-time-20260928T083610Z/m0-audit
```

集成检查逐项执行 `timeout --kill-after=5s 30s <计算程序> <输入.json> fixtures/m1_compute/system.json <新运行目录>`，完整绝对路径在 `integration/commands.json`。基准输入为 `fixtures/m1_compute/comp_1000ns.json`；变体仅改变起点或持续时间，各自输入原文保留。配置显式关闭 roofline、全图 replay-only 和共享内存总线。程序每次生成一个 rank 0、id 1 的 COMP，`is_cpu_op=false`、`num_ops=0`，不含通信或内存节点。使用空内存 API 列表，不调用解析内存模型计时。

### 实际结果

| 用例 | 起点 ns | 输入 runtime ns | 完成 ns | ASTRA 调度/执行完成回调数 |
|---|---:|---:|---:|---:|
| `comp-1000ns` | 0 | 1000 | 1000 | 1 / 1 |
| `offset-1000ns` | 37 | 1000 | 1037 | 1 / 1 |
| `comp-1ns` | 0 | 1 | 1 | 1 / 1 |
| `zero-comp` | 37 | 0 | 37 | 0 / 0 |

四例均正常退出且 stderr 为空，完成通知均只有一次。正耗时用例的计算资源在发起后为 1，完成后 CPU、计算和通信三个在途计数均为 0；计算耗时累计等于输入值，节点、ASTRA 待处理事件及事件桶均清空。零耗时按锁定 `Workload::skip_invalid` 直接移除，不占资源、不增加 1 ns。独立脚本同时核对 result、Chakra reader 读回值和原 ASTRA 日志；主例的真实记录为 `issue ... tick=0 ... node->id=1` 与 `callback ... tick=1000 ... node->id=1`。

集成检查还确认负数、非整数纳秒输入、未知 schema、单值溢出、起点加持续时间溢出和覆盖已有运行目录共六项调用被拒绝，进程均退出 1，错误消息匹配；已有运行文件校验值不变。它们是预期的拒绝测试，不是失败的功能用例。

时间单元检查实际回调时刻为 `[0, 1, 2, 15]`，对应 0、0.125、1.125 ns，以及在 10 ns 发起的 5 ns 相对延时；检查 0 ns 回调不在调用栈内同步执行。完成通知在 10 ns 上报后，15 ns 事件仍继续执行。负数、NaN、无穷、非 NS 单位、空回调及溢出被拒绝。独立一次事件精确到达 `9223372036854775807 ns`，再加 1 ns 被拒绝；另一进程以 PS 精度启动，适配器明确拒绝。

计算程序链接审计确认全部 52 个 ASTRA 对象均由 whole-archive 引入，源文件和 link map 不含其他网络后端。四例实际装载的 10 个 ns-3 模块均在本次 `build/m1-compute/output/lib/`；M0 原探针和原审计规则也通过。`integration/compiled-sources.json`、`summary.json` 与 `build-evidence/` 保存实际编译参数、源文件/库校验值、link map、缓存和完整目标命令。

| 产物 | SHA-256 |
|---|---|
| 计算程序 | `729b9f128a194f3f82f7e7070f0abb08e5032f7446e63a79a918ec765ab7333a` |
| 最终时间单元程序 | `ad8e8e12047b1b00b007730f465cf4340fcac5e41406f2393173fe0fd92b9c72` |
| 基准 1,000 ns Chakra 图 | `0355b8c5667bbadc7a431b6c63595f36fcbfd1c12522ef8e99962dd1604fb670` |
| ASTRA 功能配置 | `5e323e19d327ba40afa7073cca6d9df5fbbfd28aafae94ba250c4289ed9360c3` |

### 失败记录与修正依据

第一次编译找不到 `Logging.hh:6` 所包含的 `spdlog_setup/conf.h`，其目录在 ASTRA CMake 中仅为 PRIVATE。补充本目标头文件目录后，第二次编译暴露两个问题：日志配置头引入 `syslog.h`，其中 `LOG_INFO` 与 ns-3 `src/core/model/log.h:92` 的枚举名冲突；日志配置代码的无命名空间 `format()` 在 C++20 下与 `std::format` 发生重载歧义。第三次构建已隔离头文件，仍因 C++20 歧义退出 1。最终将独立日志桥接按 ASTRA 的 C++17 编译，仅通过两项普通函数供 C++20 入口调用，构建通过。三个版本分别保留在 `attempt-1/` 至 `attempt-3/`，没有修改或抑制上游定义。

初次时间单元程序收到 SIGSEGV，原命令记录返回 `-11`。用 `timeout --kill-after=5s 30s stdbuf -oL <原单元程序>` 复现，确认前面的时间和整数边界检查均已通过，崩溃位于同进程第二次 `Time::SetResolution`。锁定 ns-3 `src/core/model/time.cc:365-391` 的 `ConvertTimes` 首次使用后将登记表清空并置空；其接口只允许初始化时设置一次，测试在 `Simulator::Run` 之后重设精度违反该约束。本次只修正测试组织：PS 精度拒绝检查用 `--wrong-resolution` 独立进程执行，保留相同拒绝条件。原源文件和二进制在 `time-test-attempt-1/`；两个最终进程均退出 0。计算程序本身始终只在初始化时设置一次精度。

| 实际步骤或命令 | 退出状态 | 记录 |
|---|---|---|
| 完整 CMake 配置 | 0 | `configure.json` |
| 同一目标构建命令的三次初始尝试 | 1、1、1 | `build.json`、`build-retry.json`、`build-final.json` |
| 使用 C++17 日志桥接后的构建 | 0 | `build-logging-bridge.json` |
| 初次时间单元检查及无缓冲复现 | -11、-11，SIGSEGV | `time-unit.json`、`time-unit-unbuffered.json` |
| `cmake --build build/m1-compute --target cosim-time-test --parallel 8` | 0 | `rebuild-time-test.json` |
| 最终时间单元 / 错误精度独立进程 | 均为 0 | `time-unit-final.json`、`wrong-resolution.json` |
| 四项计算、六项拒绝及实际计算程序链接核验 | 0 | `compute-integration.json`、`integration/commands.json` |
| 原 M0 探针 / 原链接审计 | 均为 0 | `m0-probe.json`、`m0-audit.json` |
| `python3 <日志目录>/preflight.py` / `restore_native.py` | 均为 0 | `preflight.json`、`restore-native.json` |
| 开始及结束 `bash scripts/check_deps.sh` | 均为 0，13 个依赖一致 | `dependency-before.json`、`dependency-final.json` |

配置前记录并核对 27 个原生及 M0 关键产物，备份 UB 启动锁；结束时先确认新锁对应本轮输出目录，再保存本轮锁并恢复原锁，27 项 SHA-256 全部未变。没有清理历史产物、改变原生工作区或更新 gitlink。

本轮结果仅证明合成 COMP 的执行与时间接口正确。`compute_source=synthetic_fixed_duration`、`calibration_status=unvalidated_reference` 明确标记其来源与校准状态。尚未实现 UB 写任务、接收等待及两侧完成事件，也未验证服务调度或真实计算数据。完成记录仅本地提交，不推送；停止在 M1.1，下一项 M1.2 仍待用户确认。

## 2026-09-28：m1-2-write-transfer

M1.2 完成一份实际 ASTRA SEND 经 UB URMA_WRITE/RTP 执行、源端完成和资源释放，以及配对接收适配器的等待登记。主仓库起点 `1cc2f0f`，任务说明提交 `47e14e4`；开始时主仓库及 13 个依赖工作区干净、固定提交一致。没有安装、下载、升级依赖或修改技术规范。

UB 从锁定 `581bf28a90740def0e79eafe5423af838c6f687d` 建立 `task/m1-2-transfer-executor`，本地补丁提交为 `2d5509b3db6450ada7682f9bcb0b7ca442d90371`，`cosim` 同指该提交。父仓库更新 UB gitlink、checkout_commit 与 project_patch_commits，baseline_commit 和来源 URL 不变；其余依赖不变。子模块首次提交因没有 Git 身份而被拒绝，随后通过命令级 `-c user.name=baoy21 -c user.email=baoy21@mails.tsinghua.edu.cn` 使用用户已提供的身份，没有修改全局配置。所有提交仅保存在本地，没有推送。

本节日志均位于 `runs/m1-2-write-transfer-20260928T090505Z/`。`record.py` 记录实际 argv、cwd、UTC 起止时间、退出码及 stdout/stderr；集成脚本另在各输出目录的 `commands.json` 保存子命令和退出码，输入副本、图文件、原始跟踪与 summary 均保留。构建目录为 `build/m1-transfer/`，不覆盖原生、M0 或 M1.1 构建。

### 实现与执行边界

`UbTransferExecutor` 按活动 taskId 保存任务值、完成接收者和清理事件，不调用 CSV 调度器查询任务或 phase。它复用 UbApp 原提交代码，CSV 模式结束后通知 UbTrafficGen；联合模式结束后通知适配器。WQE 内部回调仍执行原完成路径，同一时刻另排事件清理 RTP Jetty、双向 TP 绑定及事务服务/顺序元数据，避免在 Jetty 自身回调栈中销毁它。TP 是否回收保持原 RemoveUselessTp 属性，CTP 缓存 Jetty 保留。

联合入口 `cosim-write` 用原 UbUtils 加载原样例的五个网络文件，不加载 traffic.csv，也不为未来图节点预生成 CSV 任务。源 rank 0 执行实际 ASTRA SEND 图，rank 1 适配器仅登记等待。本步不运行目标端 ASTRA RECV 图、不触发 data_ready，不能据此宣称双端图全部完成。

接口按显式参数检查 UINT8、字节数、端点及标签，保存独立的 64 位 transfer_id 和可表示的 UB taskId，不保留可能来自栈的 sim_request 指针。锁定 `Workload::issue_comm` 仅初始化 SEND 请求的部分字段，RECV 请求未初始化字段；本适配器所需信息全部来自明确接口参数及固定描述，不复制未初始化字段。发送完成来源固定为 transaction_done，经 ScheduleNow 调用原 ASTRA 发送回调，不替代接收回调。

### 配置与构建

实际配置命令（主仓库根目录，退出 0，`configure.json`）：

```bash
cmake -S third_party/ns-3-ub -B build/m1-transfer -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m1-transfer/output \
  -DNS3_ENABLED_MODULES=unified-bus \
  -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF \
  -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF \
  -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF \
  -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m1-transfer --target cosim-write cosim-write-test \
  cosim-compute cosim-time-test m0-joint-link-probe scratch_ub-quick-example --parallel 8
```

首次构建 `build.json` 退出 0，完成 463 步；加入接口单元目标及修正后，最终上述目标命令退出 0，见 `build-final.json`。过程中始终只运行一个构建，最多 8 路并行。配置仍提示输出目录在 UB 源码树外，采用的是已验证的独立输出方式。没有编译 MPI/MTP 或全部上游测试套件。

### 实际检查

以下命令均在主仓库根目录执行。为便于重放，`L` 只指本次日志目录；各 Python 检查要求新输出目录，重跑应换目录名，不覆盖本次证据。

```bash
L=runs/m1-2-write-transfer-20260928T090505Z
build/m1-transfer/output/cosim/ns3.44-cosim-write-test
build/m1-transfer/output/cosim/ns3.44-cosim-time-test
build/m1-transfer/output/cosim/ns3.44-cosim-time-test --wrong-resolution
python3 tests/integration/check_write.py --build-dir build/m1-transfer --output-dir "$L/write-verified"
python3 tests/integration/check_compute.py --build-dir build/m1-transfer --output-dir "$L/compute-verified"
python3 tests/integration/check_csv_regression.py \
  --binary build/m1-transfer/output/scratch/ns3.44-ub-quick-example \
  --reference-binary third_party/ns-3-ub/build/scratch/ns3.44-ub-quick-example \
  --output-dir "$L/csv-final"
python3 tests/integration/check_csv_regression.py \
  --binary build/m1-transfer/output/scratch/ns3.44-ub-quick-example \
  --reference-binary third_party/ns-3-ub/build/scratch/ns3.44-ub-quick-example \
  --case-dir "$L/phase-case" --dependency-visibility-delay 1ns \
  --output-dir "$L/csv-phase-configured"
python3 tests/integration/check_csv_regression.py \
  --binary build/m1-transfer/output/scratch/ns3.44-ub-quick-example \
  --reference-binary third_party/ns-3-ub/build/scratch/ns3.44-ub-quick-example \
  --case-dir third_party/ns-3-ub/scratch/rtp-ctp-micro-ctp-write \
  --output-dir "$L/csv-ctp"
build/m1-transfer/output/m0/ns3.44-m0-joint-link-probe "$L/m0-probe.et"
python3 scripts/m0_joint_build/audit.py --build-dir build/m1-transfer \
  --run-log "$L/m0-probe-with-graph.stdout.log" --output-dir "$L/m0-audit"
python3 "$L/restore_baselines.py"
```

CSV 检查器先复制输入到新的 reference/patched 目录，再分别从 UB 根目录调用两个二进制；不会改原样例。随后显式运行原 `task_statistics.py`、`cal_throughput.py`，检查实际退出码，并与入口自动统计比较，避免依赖原入口未传播的子进程状态。phase-case 保留原四个任务，仅将 task 2 移到 phase 1 并依赖 phase 0；两份二进制传入相同的 1 ns 依赖可见延迟。

| 检查 | 结果 | 实际记录 |
|---|---|---|
| 写接口单元 | 0；类型、描述匹配、重复调用、32 位边界拒绝、异步提交与提交前取消 | `write-unit-verified.json` |
| 时间单元与错误精度独立进程 | 均 0；零延迟异步、ceil、绝对时间、完成不停止其他事件、64 位边界 | `time-unit-final.json`、`time-resolution-final.json` |
| 写事务四例及七项非法输入/覆盖拒绝 | 检查器 0；有效调用均 0，非法调用均按预期返回 1 | `write-verified.json`、`write-verified/commands.json`、`summary.json` |
| 计算四例及六项拒绝 | 检查器 0；完成时刻 1000、1037、1、37 ns，资源清空 | `compute-verified.json`、`compute-verified/summary.json` |
| 原 CSV 四任务 | 0；1,638,400 字节，39 个跟踪和统计文件与原版逐字节一致 | `csv-final.json`、`csv-final/comparison.json` |
| CSV phase 依赖 | 0；同四任务，39 个文件仍与原版一致 | `csv-phase-configured.json`、同名目录内比较记录 |
| 原 CTP 小例 | 0；一个 64 字节写任务，18 个文件与原版一致 | `csv-ctp.json`、`csv-ctp/comparison.json` |
| M0 探针与审计 | 均 0；52 个 ASTRA 对象和单套 10 个 UB ns-3 库 | `m0-probe-with-graph.json`、`m0-audit.json` |
| 恢复原生锁及旧产物核对 | 0；39 个原生、M0、M1.1 产物的大小和 SHA-256 不变 | `restore-baselines.json`、`baseline-verification.json` |

### 写事务证据

主例使用 `fixtures/m1_transfer/single_write.json` 和原 `fixtures/m1_compute/system.json`。三组 409,600 字节测试在 10 ns 发起，WQE 完成、transaction_done、send_done 和源图完成均为 8,622 ns；一字节例在 188 ns 完成。应用 WQE TraceSource、原 TaskTrace、原 ASTRA issue/callback 日志和报告相互核对，不使用打印的固定完成值。

所有例只有一份实际 WQE 和一次发送完成，接收回调为 0、等待仍在；CSV 完成计数为 0。发送槽从 0 到 1 再回到 0，节点移除，ASTRA 待处理事件及执行器任务数为 0，源端 UbFunction 不再持有 Jetty，事务层服务/顺序记录和两向绑定不存在。默认两端 TP 数量保持 2；开启原空闲回收后，两端各为 0。WQE 完成后仍执行 start_ns 后 500,000 ns 的测试事件，确认源 rank 完成未调用 Stop。

联合程序使用 NS 精度，原 CSV 入口原样保留 PS 精度。原交换机每 10 ms 永久调度死锁检查，联合功能入口因此显式在 start_ns 后 1 ms 停止；成功取决于真实事务和资源检查，不能把测试截止时刻当作通信完成。当前是单份写任务测试入口，不是可持续运行的服务控制器。

计算与写程序均 whole-archive 引入全部 52 个 ASTRA 核心对象。编译来源和 link map 排除了 ASTRA 其他 network_frontend/network_backend；实际加载的 10 个 ns-3 模块都来自本次 UB 输出目录。ASTRA 核心库 SHA-256 仍为 `023054d8f7b46eae21fcc2276008ee62f62ae5a5da338479f7a952cb695ed6c7`。写程序 SHA-256 为 `314a6a0dbf68d71ea15366c74708d425d0af124110229cc51338adfa0ca32021`，完整来源与库校验值见 `write-verified/compiled-sources.json` 和 summary。

### 保留的失败与限制

首次 `write-integration.json` 返回 1，内部 receive-first 命令在外部 30 秒超时后返回 124；原因是没有测试停止上限，永久周期检查让 Run 不结束。`UbSwitchAllocator::Init/CheckDeadlock` 的周期调度和原入口的 CheckExampleProcess/Stop 是定位依据。没有停止单 rank 的完成回调或修改 UB 协议；后续有限运行检查通过。

`build-tests.json` 返回 1，单元中的 `Callback` 函数名与上游导出的 `ns3::Callback` 歧义；改名为 RecordCompletion 后构建通过（`build-tests-fixed.json`）。`write-integration-bounded.json` 的前三例通过，最后一例因测试初版预计还留一个未使用 TP 而失败。锁定原版 `UbTransaction::GetUselessTpns` 遍历所有本地 TP，并回收两端均不在用的通道，所以实际应为 0。保留失败脚本 `check_write.before-tp-oracle-correction.py`、原输出和 `baseline-idle-tp-policy.txt`；只纠正这个没有源码依据的预期，未改变 TP 策略或减少资源检查。

`csv-phase.json` 在运行保留原版时收到 SIGABRT（子进程 -6，检查器 1），原入口明确要求为含 phase 依赖的 CSV 提供 dependency visibility delay。为原版与补丁版同时显式设置 1 ns 后，完整比较通过。`m0-probe.json` 因首次遗漏必要的新图输出路径而返回 1；补齐参数的 `m0-probe-with-graph.json` 与后续审计均返回 0。这些失败记录不删除、不计为通过。

本轮校准状态仍为 `unvalidated_reference`。没有完成目标端 data_ready、ASTRA RECV 图、晚到接收回调、正式观测导出、M2 发送调度或真实模型校准；没有运行全部上游测试、MPI/MTP 或全部内存语义场景。CSV 与 CTP 回归只证明所列输入保持结果，不代表整个 UB 功能面已穷尽验证。停止在 M1.2，M1.3 仍待用户确认。

## 2026-09-28：m1-3-receive-ready

M1.3 已完成实际目标端数据就绪、独立收发回调与双端 ASTRA 图执行。主仓库起点 `d4f272e`，任务说明提交 `7405f9e`。开始时 13 个依赖检出与工作区检查通过。UB 从锁定 `2d5509b` 建立 task/m1-3-receive-ready，仅修改事务层两个文件，补丁 `2713a38cd42376c655ceb26b80507d65677c0653` 已本地提交，cosim 快进到该提交。父仓库更新 UB gitlink、checkout_commit 与补丁清单；原始 baseline_commit 和其他依赖保持不变。没有推送、下载、安装或修改全局配置。

证据根目录为 `runs/m1-3-receive-ready-20260928T121101Z/`（以下记为 L）。每项命令由审阅过的 record.py 保存 cwd、argv、UTC 起止、退出码与 stdout/stderr；集成目录另有 commands.json、输入、Chakra 图、原始跟踪、summary 和校验值。新构建目录为 build/m1-receive/；原生、M0、M1.1、M1.2 构建均保留。

### 接收事件依据和实现边界

已沿 `src/unified-bus/model/protocol/ub-transport.cc` 的 RecvDataPacket 核对：原 NotifyLastPacketReceived 在去重之前，不能用于整份业务数据就绪。实际有效数据经过 HandleRepeatedDataPacket、UpdateReceiveWindowAndCollectCompletedTa，再由 TrackInboundTaPacket 按连续 PSN 重组完整 TA 单元。CompleteInboundTaUnits 将完整单元送入 UbTransaction::HandleInboundTaUnit；WRITE 执行 ExecuteRemoteWriteAndBuildAck。

补丁在 `ub-transaction.cc` 更新 m_memoryStore 后、构造原事务 ACK 前增加 RemoteWriteCommitted TraceSource。通知使用 Ptr<const UbWqeSegment>，不修改包、时延、优先级或路径。通知大小代表一个重组分段；目标 GetWqeSize 也是该分段大小，不能当作原始整份 WQE 大小。审阅片段保存在 L/receive-event-source.txt，最终集成来源哈希在 write-check/compiled-sources.json。

UbWriteTransfer 筛选固定 taskId 与端点，WriteCompletion 对原 Jetty/TA 序号去重、累计有效字节，达到描述中的业务量后记录 data_ready。RECV 在 ScheduleNow 完成，SEND 独立由原执行器 transaction_done 完成。未登记 RECV 时保留已到达状态，源事务结束不能删除它；晚到登记使用当前时间。两侧回调返回后删除匹配上下文与分段表、清空借用回调参数、断开目标观察并释放应用引用，保留不可变身份、字节计数和完成时刻。

当前写输入为 `moe-ub-write/2`：用 receive_start_ns 代替 receive_first，并明确接收后的 receive_compute_ns。源图为 SEND，目标图为 RECV→COMP；两个原 ASTRA Sys 独立激活，未修改 ASTRA 源码。旧版输入明确拒绝，M1.2 历史输入保存在旧日志与 Git 历史。计算入口的原 schema 不变。

### 配置、构建和实际命令

以下命令均从主仓库根目录执行；配置和有效构建退出 0，记录为 configure.json、build-targets.json。仍只运行一个构建，最多 8 路并行。没有启用全部上游测试或 MPI/MTP。

```bash
cmake -S third_party/ns-3-ub -B build/m1-receive -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m1-receive/output \
  -DNS3_ENABLED_MODULES=unified-bus \
  -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF \
  -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF \
  -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF \
  -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m1-receive --parallel 8 --target cosim-write cosim-write-test \
  cosim-time-test cosim-compute m0-joint-link-probe scratch_ub-quick-example
L=runs/m1-3-receive-ready-20260928T121101Z
build/m1-receive/output/cosim/ns3.44-cosim-write-test
build/m1-receive/output/cosim/ns3.44-cosim-time-test
build/m1-receive/output/cosim/ns3.44-cosim-time-test --wrong-resolution
python3 tests/integration/check_write.py --build-dir build/m1-receive --output-dir "$L/write-check"
python3 tests/integration/check_compute.py --build-dir build/m1-receive --output-dir "$L/compute-check"
python3 tests/integration/check_csv_regression.py \
  --binary build/m1-receive/output/scratch/ns3.44-ub-quick-example \
  --reference-binary third_party/ns-3-ub/build/scratch/ns3.44-ub-quick-example \
  --output-dir "$L/csv-native"
python3 tests/integration/check_csv_regression.py \
  --binary build/m1-receive/output/scratch/ns3.44-ub-quick-example \
  --reference-binary third_party/ns-3-ub/build/scratch/ns3.44-ub-quick-example \
  --case-dir runs/m1-2-write-transfer-20260928T090505Z/phase-case \
  --dependency-visibility-delay 1ns --output-dir "$L/csv-phase"
python3 tests/integration/check_csv_regression.py \
  --binary build/m1-receive/output/scratch/ns3.44-ub-quick-example \
  --reference-binary third_party/ns-3-ub/build/scratch/ns3.44-ub-quick-example \
  --case-dir third_party/ns-3-ub/scratch/rtp-ctp-micro-ctp-write --output-dir "$L/csv-ctp"
build/m1-receive/output/m0/ns3.44-m0-joint-link-probe "$L/m0-probe.et"
python3 scripts/m0_joint_build/audit.py --build-dir build/m1-receive \
  --run-log "$L/m0-probe.stdout.log" --output-dir "$L/m0-audit"
python3 "$L/restore_baselines.py"
```

命令均实际执行并退出 0；重跑检查应改用新输出目录。CSV 检查器从 UB 根目录执行保留原版和新二进制，输入复制到各自运行目录，显式重验两个原统计脚本的退出码。phase 使用 M1.2 已保存的四任务输入，两份二进制均显式传入 1 ns 依赖可见延迟。

### 实际结果

| 检查 | 结果 | 日志记录 |
|---|---|---|
| 完成与接口单元 | 四组独立事件时序、部分/乱序分段、重复累计拒绝、晚到接收、过期身份、回调取消及原接口边界通过 | write-unit.json |
| 真实双端图六例 | 各一份 WQE、两侧各一次回调、RECV 后计算时刻正确、上下文释放 | write-integration.json、write-check/summary.json |
| 输入检查 | 十项非法输入及一次覆盖目录均按预期返回 1，检查器返回 0 | write-check/commands.json |
| 时间与计算回归 | 时间边界和精度拒绝通过；四项计算仍在 1000、1037、1、37 ns 完成 | time-unit.json、time-resolution.json、compute-check.json |
| 原 CSV RTP 四任务 | 1,638,400 字节，39 个跟踪和统计文件逐字节一致 | csv-native.json、csv-native/comparison.json |
| CSV phase 依赖 | 同四任务，39 个文件逐字节一致 | csv-phase.json、csv-phase/comparison.json |
| 原 CTP 小例 | 一个 64 字节写任务，18 个文件逐字节一致 | csv-ctp.json、csv-ctp/comparison.json |
| M0/计算/写程序链接 | 全部 52 个 ASTRA 核心对象；仅本轮构建的 10 个 UB ns-3 库，排除其他网络后端 | m0-audit.json、两个集成目录的编译来源与 summary |
| 原生锁及旧产物 | 恢复启动锁，54 个旧产物大小及 SHA-256 不变 | restore-baselines.json、baseline-verification.json |

409,600 字节主例从 10 ns 开始，目标提交六个 65,536 字节分段和一个 16,384 字节分段，时间依次为 1,561、2,889、4,217、5,546、6,874、8,202、8,534 ns。data_ready 和 RECV 为 8,534 ns，原 ASTRA 日志中的后继 COMP 也在该时刻 issue，至 9,534 ns 完成。源 WQE、transaction_done、SEND 和源图完成仍为 8,622 ns，与 M1.2 一致。没有用源端完成或测试上限反推接收时间。

先接收在 0 ns 登记，先发送例在 30 ns 登记接收，两者实际数据与源完成时刻相同。晚到接收及晚到接收加 TP 回收在 20,000 ns 登记，当时源事务已完成，等待状态仍保留；回调在当前 20,000 ns 执行，计算至 21,000 ns 完成。1 字节例 data_ready/RECV 为 100 ns、源完成为 188 ns，计算至 1,100 ns 完成。所有例两端图完成、收发槽及执行器上下文清空，通知断开；默认 TP 两端各保留 2 个，原回收属性开启时各剩 0 个。CSV 完成计数均为 0。

单元使用人为事件时间检验数据/事务独立性：数据在 20 ns 收齐，事务分别在 30 ns 或 5 ns 完成，晚到登记为 50 ns；发送和接收各自等待对应事件，不内联重入。重复观察不重复累计，冲突或超量分段拒绝。退役后旧事件报原 graph/transfer 身份，不影响复用局部节点号的新测试状态。这些是完成逻辑单元证据，不当作真实网络重传集成证据。

### 失败和未验证范围

首次构建 build.json 返回 1：命令误用短目标名 ub-quick-example，Ninja 在编译前报告 unknown target。核对生成目标后使用 scratch_ub-quick-example，build-targets.json 的 471 步构建退出 0。没有改断言或预期。配置保留已知的源码树外输出目录提示；构建保留上游警告，没有本轮编译错误。

联合功能入口仍使用 start_ns 后 1 ms 的测试上限，以处理 UB 永久周期死锁检查；实际完成、后续事件执行和清理必须另外通过，不把上限当作通信时间。m_memoryStore 的原模型仅记录模拟地址与大小，未模拟张量内容和完整硬件内存一致性。当前只有一份不可变写任务，没有做丢包恢复集成、跨图 taskId 复用、全套上游测试、正式端口观测、完整服务终止或真实校准。校准状态仍为 unvalidated_reference。

本轮停止在 M1.3 completed，M1.4 等待用户确认；提交后主仓库与 13 个依赖的最终检查记录位于 L/dependency-final.json，Git 状态另存 L/final-status.json 和 L/final-ub-status.json。

## 2026-09-28：m1-4-port-provenance

用户确认继续 M1.4，并将当前会话切换为 Full access。沿用已授权的本地提交流程，不推送。任务说明提交 ec1053c；开始时父仓库 cdca5ff、UB 2713a38 和其他依赖工作区干净，dependency-before.json 检查 13 个依赖通过。UB 在 task/m1-4-port-provenance 上增加四文件、22 行被动来源补丁，提交为 1ba722217ef94716a07a56a2388be2c7dddd8e0b，cosim 已快进到该提交。baseline_commit 和其他依赖、远端不变。

本节日志根为 `runs/m1-4-port-provenance-20260928T130407Z/`，下文简记 L。record.py 经审阅后复用，每个命令保存 cwd、完整 argv、UTC 起止、退出状态和 stdout/stderr；集成检查的 commands.json 继续保存每个子进程。生成输入、图、配置、原始日志和核验结果保留在对应目录，不提交 Git。没有安装软件或下载数据。

### 实现与计数依据

当前写入口保留 moe-ub-write/2，追加可选 OBSERVATION.json，不配置时保留既有行为。端口计数挂在现有 TraComEventNotify；已核对 UbPort 普通和 detached 发送路径，此通知发生在通道 TransmitStart 成功之后、发送完成之前，通知参数为持续时间。原 PortTxNotify 在通道尝试之前发出，因此没有用它作为成功发送计数的实现。计入 GetSize 整包字节，并与原 GetTxBytes 增量独立核对。

Jetty 在更新 WQE 已发送进度前保存真实任务偏移，TP 构造 WRITE request 包后被动报告任务/包内载荷范围。侧表使用已有 Packet UID，读取包副本和已有 FlowTag，不新增包字段、Tag、传输或定时事件。观察器不持有 TP 对象，原 TP 回收仍按原协议执行；已移出控制器的 TP 即使延迟销毁，绑定侧表也不依赖观察器对象地址。控制包完整计入观测但无业务来源行，未知/冲突来源明确报错。这里只覆盖无额外分片合并的单任务 RTP 写路径。

observed 中保存半开本地窗口、零窗口以及逐 rank 的真实 ASTRA SEND/RECV 开始和完成；开始来自适配器实际调用，完成来自各自回调，与原 ASTRA issue/callback 日志交叉核对。operation_id、transfer_id、UB task_id、图节点和包内范围放在 truth，时钟偏移及漂移的零值也仅在 truth 中保存。observation-audit.json 和原 result.json 均是内部功能证据，不属于关联输入。本轮仅有测试所需输出，未宣称完成 M5 的正式导出格式。

### 配置、构建与实际命令

以下均从 /root/moe-ub-cosim 执行。新建 build/m1-observation，一次构建最多 8 路；保护并保留既有原生、M0 和 M1.1 至 M1.3 构建。

```bash
M14_LOG=runs/m1-4-port-provenance-20260928T130407Z
cmake -S third_party/ns-3-ub -B build/m1-observation -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m1-observation/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m1-observation --parallel 8 --target \
  cosim-write cosim-write-test cosim-observation-test cosim-time-test \
  cosim-compute m0-joint-link-probe scratch_ub-quick-example
build/m1-observation/output/cosim/ns3.44-cosim-observation-test
python3 tests/integration/check_observation.py --build-dir build/m1-observation \
  --output-dir "$M14_LOG/observation-check-final"
python3 tests/integration/check_write.py --build-dir build/m1-observation \
  --output-dir "$M14_LOG/write-check-final"
python3 tests/integration/check_compute.py --build-dir build/m1-observation \
  --output-dir "$M14_LOG/compute-check"
build/m1-observation/output/cosim/ns3.44-cosim-write-test
build/m1-observation/output/cosim/ns3.44-cosim-time-test
build/m1-observation/output/cosim/ns3.44-cosim-time-test --wrong-resolution
build/m1-observation/output/m0/ns3.44-m0-joint-link-probe "$M14_LOG/m0-probe.et"
python3 scripts/m0_joint_build/audit.py --build-dir build/m1-observation \
  --run-log "$M14_LOG/m0-probe.stdout.log" --output-dir "$M14_LOG/m0-audit"
```

原 CSV 使用 check_csv_regression.py，--binary 为 build/m1-observation/output/scratch/ns3.44-ub-quick-example，--reference-binary 为 third_party/ns-3-ub/build/scratch/ns3.44-ub-quick-example。默认 2nodes_single-tp 输出至 L/csv-regression；phase 例加 --case-dir runs/m1-2-write-transfer-20260928T090505Z/phase-case 和 --dependency-visibility-delay 1ns，输出至 L/csv-phase；CTP 例加 --case-dir third_party/ns-3-ub/scratch/rtp-ctp-micro-ctp-write，输出至 L/csv-ctp。完整 argv 分别见 csv-regression.json、csv-phase.json、csv-ctp.json。

### 实际结果

| 检查 | 实际结果 | L 内证据 |
|---|---|---|
| 观测单元 | 半开边界、末尾短窗口、空窗口、TP/TA ACK 排除、包副本不改字节、尾部范围、未知/冲突拒绝、重复经过不去重，退出 0 | observation-unit-final.json |
| 观测集成 | 7 组 × 来源开/关/不挂观测 = 21 次成功运行；10 种非法配置按预期退出 1，检查脚本退出 0 | observation-check-final/summary.json、commands.json |
| 写回归 | 六项真实双端写例通过；10 项非法输入及一次目录覆盖拒绝；最终二进制链接边界通过 | write-check-final.json、write-check-final/summary.json |
| 时间/计算 | 时间接口及错误精度拒绝通过；四项计算和五项非法计算输入通过 | time-unit.json、time-wrong-resolution.json、compute-check.json |
| 原 CSV RTP | 四任务、1,638,400 字节，39 个结果文件逐字节一致 | csv-regression/comparison.json |
| CSV phase 依赖 | 四任务、39 个结果文件逐字节一致 | csv-phase/comparison.json |
| 原 CTP | 一个 64 字节写，18 个结果文件逐字节一致 | csv-ctp/comparison.json |
| 联合链接 | M0、计算及最终写程序均有全部 52 个 ASTRA 对象，仅装载本轮 10 个 UB ns-3 库 | m0-audit/、compute-check/、write-check-final/ |
| 原生保护 | 启动锁按备份恢复，71 个旧产物大小和 SHA-256 不变，退出 0 | restore-baselines.json、baseline-verification.json |

标准例是 409,600 字节、10 ns 发起。交换机 3 的端口 0 共计 107 个成功发送包、417,834 字节，其中 409,600 为业务有效数据、7,800 为数据包协议开销、434 为控制包；分项逐窗相加等于观测整包量。100 个数据包的来源范围无缝覆盖 `[0,409600)`，唯一目标写入仍为 409,600。观测区间 `[0,30000)` ns 的 30 个窗口全部输出，含 21 个零窗口。实际 data_ready/RECV 为 8,534 ns、SEND 为 8,622 ns，与此前一致。

反向交换机 2 端口 0 的 109 个包、6,833 字节均计作控制包，业务字节和来源数据行均为零。1 字节例得到 141 整包字节、100/188 ns 的 data_ready/SEND；65,537 字节跨 TA 尾块例范围覆盖精确，数据就绪/发送完成为 1,562/1,652 ns。另将原生 TaSegmentBytes 配为 5,000，得到 14 个真实目标分段、27 个业务包，范围仍覆盖 65,537 字节，证明偏移并非固定 65,536 字节推算。晚到 RECV 加 TP 回收仍在 20,000 ns 完成接收回调，两端 TP 数各归零。

每组来源开、关和不挂观测的 27 个原生跟踪文件 SHA-256、Chakra 图、完整结果状态及去除墙钟前缀的 ASTRA 执行日志一致；来源开关还保持 observed 三文件逐字节相同，以及实际序列化包/发送时刻的 FNV-1a 指纹相同。改选反向端口或裁剪统计区间也不改变同输入的执行结果。指纹仅用于测试比对，不用作安全校验或操作身份。

### 保留的失败与限制

首轮 build.json 退出 1：新单元测试的辅助函数名 Packet 与上游头暴露的 ns3::Packet 名称冲突。仅将辅助函数改名为 MakeTestPacket，build-fixed.json 退出 0；原失败日志保留。审阅输出边界时又将公开配置中的 identity 时钟说明移至 truth，明确本地窗口字段；build-observation-final.json、observation-unit-final.json、observation-check-final.json 和 write-check-final.json 验证最终版本均退出 0。没有删除断言、降低预期或修改技术定义。

配置仍有已记录的源码树外输出目录提示，构建有上游弃用等警告。没有执行完整上游测试、实际丢包/重传集成、非 RTP 观测、多任务/多图服务或非零时钟实验。单元中的重复包计数不替代真实重传集成。原生 CTP 回归只确认补丁不改变它的行为，不表示联合观测支持 CTP。

本步仍是 unvalidated_reference 的合成功能验证，无应用元数据，无真实激活张量/硬件校准依据。联合入口继续由原有 1 ms 测试上限结束，通信时刻取实际事件。来源开关只关闭来源侧表和 truth，本来已有的内部 result 与原生调试日志仍保留，不能误当作公开算法输入。

M1.4 完成，停止在本任务；M2.1 等待用户确认。父仓库按持续授权本地提交；提交后执行 bash scripts/check_deps.sh，记录保存在 L/dependency-final.json，最终主仓库和 UB 状态保存在 L/final-status.json、L/final-ub-status.json。

## 2026-09-28：m2-1-single-layer

用户确认执行 M2.1。开始时父仓库 dd034a2、UB 1ba7222、LLMServingSim 及递归依赖工作区干净，dependency-before.json 检查 13 项依赖通过。M1.4 已提交后才切换任务，任务说明提交为 30f9b70。全程沿用 Full access 和本地提交授权，没有推送、安装、下载或修改全局配置。

日志根为 `runs/m2-1-single-layer-20260928T133604Z/`，下文记为 L。经审阅复用的 record.py 保存实际 cwd、argv、UTC 起止、退出状态和 stdout/stderr；集成目录内 commands.json 保存各子进程。旧任务记录、原生启动锁及 89 个旧构建关键产物的大小和 SHA-256 已在开始时备份。运行结果和生成的 protobuf 模块只留在忽略目录中。

### 实现与输入来源

新增 python/moe_ub_cosim/，从固定四 rank Token/专家输入构造 ExecutionPlan，校验后导出四份 Chakra 图。当前采用 direct_rank_dedup_v1、单块、单槽、零应用元数据。Dispatch 按 Token 的远端目的 rank 汇总，Combine 按返回结果独立生成；专家计算输入单独累计，保留 rank 2 上 C 的本地专家 4 输入。来源保存原输入数组的 source_record_index、请求、规范化 Token/层号和专家集合。

固定输入导出两个操作 D.w0.L7.dispatch/combine、十四条各 8192 字节的传输、一对一的 SEND/RECV 及二十个计算节点，共 48 个节点。每 rank 的计算为 5000 ns 准备、每份专家输入 4000 ns、1000 ns 专家结果处理、1000 ns Combine 解包合并和 1000 ns 后继检查计算。专家输入份数为 2、2、3、1，因此专家计算分别为 8000、8000、12000、4000 ns。耗时均为显式合成假设。

系统 python3 未提供 google.protobuf；使用已有 .venv/llmservingsim-native/ 中的 protobuf 7.36.2。生成器调用已安装的 protoc 3.21.12，直接从锁定 Chakra et_def.proto 在每次新输出目录生成编码模块，并保存命令/返回状态。实际输出已由后端的 protobuf 校验和锁定 ASTRA Chakra reader 两次读取，未复用依赖目录中旧的未跟踪 et_def_pb2.py。

新增 cosim-layer 读取并核对计划、四份二进制图、收发配对、身份与局部依赖；所有图验证通过后才创建运行目录和网络。随后登记全部传输、构造四个 Sys，再按 rank 顺序在 ready_ns=0 激活。UbNetworkAdapter 增加按 (src_rank,dst_rank,comm_tag) 查找多传输描述，仍复用 M1 的独立数据就绪/事务完成/异步回调与资源释放。M1 的单传输入口保留。

已核对锁定 HardwareResource.cc：SEND 使用单个 GPU 通信槽，RECV 的 occupy/release 不占该槽，发送忙碌时仍可登记接收。本步不需要 ASTRA 或其他依赖补丁；全部依赖源码、版本、gitlink、锁文件和远端保持不变。网络样例使用四端点经单交换机连接，链路 400 Gbps、20 ns；为事先挂接来源通知，固定预建六对 RTP 连接并保留，每 rank 三个 TP。

最小端口观察器扩展为按 UB task_id 查找多个来源范围，通知位置和计数语义沿用 M1.4。端侧逻辑阶段使用刷新后的原 ASTRA issue/callback 记录导出，缺失或重复记录明确失败；本步要求 trace-enabled=1 和正计算耗时。只使用端侧原始执行事件，没有用网络首末包推算调用时刻。观测中仍只保留 rank 内记录号；全局操作、传输、图和时钟参数保留在内部输出。

### 构建与实际命令

所有下列命令的 cwd 均为 /root/moe-ub-cosim。构建目录为 build/m2-single-layer/，一次一个构建、最多 8 路并行；未使用或重配旧构建目录。

```bash
M21_LOG=runs/m2-1-single-layer-20260928T133604Z
cmake -S third_party/ns-3-ub -B build/m2-single-layer -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m2-single-layer/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m2-single-layer --parallel 8 --target \
  cosim-layer cosim-write cosim-write-test cosim-observation-test \
  cosim-time-test cosim-compute m0-joint-link-probe
env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.generate \
  --input fixtures/m2_single_layer/layer.json --output-dir "$M21_LOG/generated"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/unit/layer_plan_test.py
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_layer.py \
  --build-dir build/m2-single-layer --output-dir "$M21_LOG/layer-check-final"
python3 tests/integration/check_write.py --build-dir build/m2-single-layer \
  --output-dir "$M21_LOG/write-check"
python3 tests/integration/check_observation.py --build-dir build/m2-single-layer \
  --output-dir "$M21_LOG/observation-check"
python3 tests/integration/check_compute.py --build-dir build/m2-single-layer \
  --output-dir "$M21_LOG/compute-check"
build/m2-single-layer/output/cosim/ns3.44-cosim-write-test
build/m2-single-layer/output/cosim/ns3.44-cosim-observation-test
build/m2-single-layer/output/cosim/ns3.44-cosim-time-test
build/m2-single-layer/output/cosim/ns3.44-cosim-time-test --wrong-resolution
build/m2-single-layer/output/m0/ns3.44-m0-joint-link-probe "$M21_LOG/m0-probe.et"
python3 scripts/m0_joint_build/audit.py --build-dir build/m2-single-layer \
  --run-log "$M21_LOG/m0-probe.stdout.log" --output-dir "$M21_LOG/m0-audit"
```

单次首跑的完整命令在 L/layer-smoke.json：cosim-layer 参数为 L/generated、fixtures/m1_compute/system.json、fixtures/m2_single_layer/network、fixtures/m2_single_layer/observation.json、L/layer-smoke，外层 timeout 为 30s、kill-after=5s。新集成检查保留所有实际输入副本和三次执行的命令。

### 实际结果

| 检查 | 结果 | L 内证据 |
|---|---|---|
| 首次全目标构建 | 479 步，退出 0；输入边界和诊断增补后的增量构建也退出 0 | configure.json、build.json、build-final.json、build-diagnostics.json |
| 生成器单元 | 五项测试通过，覆盖固定矩阵、八份专家输入、局部依赖、确定性、19 种非法配置及 11 种坏计划 | plan-unit-final.json |
| M2.1 真实执行 | 来源开/关/不挂观测三次通过，十四份 UB 写共交付 114688 字节，四图完成 | layer-check-final/summary.json、on/result.json |
| ASTRA 执行核对 | 48 个节点各有一次 issue/callback，共 96 条；固定计算耗时和本地前驱均正确 | on/log/log.log、on/result.json 的 node_execution |
| 发送资源 | 原 ASTRA 执行日志及原 WQE 事件两处核对，每 rank 最大在途 SEND/WQE 均为 1；发送占槽时 RECV 仍能登记 | layer-check-final/ 及 write-unit-final.json |
| 开关无扰动 | 三次运行的 33 个原生跟踪文件、图、完整执行/资源结果和去墙钟前缀的 ASTRA 日志一致；来源开关时 observed 和包/时刻指纹一致 | layer-check-final/commands.json、summary.json |
| 整组拒绝 | 12 种错误计划/图及一次覆盖既有目录按预期返回 1，检查脚本返回 0；坏计划/图均未创建运行目录 | layer-check-final/commands.json |
| M1 写回归 | 六例真实收发、十种非法输入及覆盖拒绝、最终资源释放通过 | write-check.json、write-check/summary.json |
| M1 观测回归 | 七组开/关/不挂观测共 21 次运行、十种非法配置及来源范围检查通过 | observation-check.json、observation-check/summary.json |
| M1 时间/计算 | 时间接口和错误精度拒绝、四项实际计算及五项非法计算输入通过 | time-unit.json、time-wrong-resolution.json、compute-check.json |
| 链接边界 | M0、计算、写、单层入口均包含全部 52 个 ASTRA 核心对象，只装载本轮 10 个 UB ns-3 库 | m0-audit/、各集成检查的 compiled-sources.json 与 summary.json |
| 旧产物保护 | 原生启动锁恢复，89 个旧产物大小及 SHA-256 不变，退出 0 | restore-baselines.json、baseline-verification.json |

四个 rank 的实际时间如下，单位 ns。这些值仅来自本次事件执行，不用于生成通信耗时。

| rank | Dispatch 开始/完成 | 专家计算开始/完成 | Combine 开始/完成 | 图完成 |
|---|---|---|---|---:|
| 0 | 5000 / 5923 | 5923 / 13923 | 14923 / 20168 | 21168 |
| 1 | 5000 / 5840 | 5840 / 13840 | 14840 / 20506 | 21506 |
| 2 | 5000 / 5877 | 5877 / 17877 | 18877 / 20552 | 21552 |
| 3 | 5000 / 5842 | 5842 / 9842 | 10842 / 16675 | 17675 |

端侧输出四个 rank 各有 Dispatch/Combine 两条记录。Dispatch 完成记录取专家计算实际开始，在本固定图中核对为所有必要本地条件的最晚完成；Combine 完成还包括最后的 1000 ns 解包合并。后继 COMP 再运行 1000 ns。所有四个图已预先装载，激活顺序 0、1、2、3，激活时刻均为 0。

十四条传输两侧各回调一次，每条唯一交付 8192 字节，源 Jetty/事务状态、适配器传输上下文和各 rank 槽/事件最终清空，CSV 完成计数为 0。保留的 TP 每 rank 为 3，不将它们误记为未完成业务。

选定交换机 4 端口 2 共计 18 个成功发送包、25839 字节，其中业务 24576、数据包协议开销 468、完整控制包 795。六个业务包的范围分别覆盖 Dispatch 0→2、1→2 和 Combine 3→2 的 [0,8192)。100 个本地半开窗口全部输出，各窗分项相加等于整包计数。该端口只经过三条入向业务，其业务总量对应这三条任务；十四条任务的全局唯一交付量另按目标提交核对。

### 未验证范围与停止位置

本轮正式构建和验证没有未解决失败。非法输入用例的返回 1 是预期拒绝，原始 stderr 保留。配置继续有源码树外输出目录提示，构建有上游 std::binary_function 等弃用警告；没有通过关闭本轮断言、改变预期或修改技术规范绕过失败。

依赖源码未改动，本轮没有重复构建原 CSV 入口或运行其原生全集；原有 M1.4 CSV RTP/phase/CTP 对照记录和二进制保留。没有进行 M2.2 的给定完成时刻替身和指定延迟传播实验，也没有运行 M2.3 去重/分块/多槽变体、M2.4 连续图、动态服务、真实丢包重传或硬件校准。M2.1 当前明确限制为正计算、单块单槽、零应用元数据和单张四 rank 图。

与 M1 一样，功能入口仍在 ready_ns 后 1 ms 的测试上限停止，rank 完成不会直接终止仿真；实际完成和资源释放在上限前单独验证。模型状态仍为 unvalidated_reference，不把合成图运行成功当作真实模型或硬件校准。

本轮停止在 M2.1 completed，M2.2 等待用户确认。按持续授权整理记录并本地提交；提交后依赖检查记录在 L/dependency-final.json，最终主仓库及 UB 状态在 L/final-status.json、L/final-ub-status.json。

## 2026-09-28：m2-2-delay-feedback

用户确认执行 M2.2。开始时主仓库 0a3bfc2、UB 1ba7222、LLMServingSim 及递归依赖工作区干净，dependency-before.json 的 13 项依赖检查通过；旧任务已提交后才切换，任务说明提交 38f8095。依赖源码、版本、gitlink、锁文件和远端均未修改，没有安装、下载、修改全局配置或推送。

日志根为 `runs/m2-2-delay-feedback-20260928T142324Z/`，下文记为 L。复用并审阅 record.py，保存每项命令的 cwd、完整 argv、UTC 起止、退出状态、stdout/stderr；集成目录的 commands.json 保存各子进程。旧任务记录和原生启动锁已备份，107 个旧产物保存大小与 SHA-256。运行目录、生成图和原始日志不提交 Git。

### 实现与核验依据

仅新增专用测试目标 cosim-layer-delay-test、tests/integration/layer_delay_driver.cc、check_delay.py 和 fixtures/m2_delay_feedback/ 输入说明。backend/CMakeLists.txt 增加测试目标；生产图生成器、cosim-layer、UbNetworkAdapter、UbWriteTransfer、WriteCompletion 和观察器源码均未改动。

给定事件替身使用原 ASTRA 的四个 Sys 和实际 Chakra 图，组合复用 UbNetworkAdapter 的 ns-3 时间/事件调度接口，使用原 WriteCompletion 分别交付数据和事务完成。只有 SEND 登记后才安排给定事件，RECV 只登记等待；不创建 UB 网络对象，输出明确标记 event_source=test_double、network_nodes=0。它不向 ExecutionPlan 写预测时间，也不能作为 cosim-layer 的运行选项。

五组替身检查复用完全相同的四份图，每组只改变一个给定事件。图中准备计算 5000 ns，rank 2 专家计算 12000 ns、局部结果处理 1000 ns、最终 Combine 解包合并和后继 COMP 各 1000 ns。检查脚本从每条传输的独立事件构造必要本地条件，再与原 ASTRA 执行记录核对，不仅依据图的 deps 自证。四 rank 的 48 个节点均各有一次 issue/callback，计算耗时、局部前驱、单 SEND 槽和占槽时 RECV 仍可登记均通过。

真实执行复用 M2.1 生产入口和网络，第二例仅将 rank 1 与交换机 4 之间物理链路传播延迟由 20 ns 改为 2020 ns。该变化作用于经过该链路的所有包；没有按操作身份修改包、优先级、路径或时延。程序从实际 RemoteWriteCommitted 和 WQE 完成分别核对接收/发送回调。所有给定事件与真实运行的图哈希一致，计划按 JSON 结构比较一致。

### 构建与实际命令

下列命令 cwd 均为 /root/moe-ub-cosim。一次一个构建，最多 8 路并行，使用新目录 build/m2-delay-feedback/；此前的构建目录未重配或覆盖。

```bash
M22_LOG=runs/m2-2-delay-feedback-20260928T142324Z
cmake -S third_party/ns-3-ub -B build/m2-delay-feedback -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m2-delay-feedback/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m2-delay-feedback --parallel 8 --target cosim-layer cosim-layer-delay-test
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_delay.py \
  --build-dir build/m2-delay-feedback --output-dir "$M22_LOG/delay-verified"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_layer.py \
  --build-dir build/m2-delay-feedback --output-dir "$M22_LOG/layer-regression"
python3 "$M22_LOG/restore_baselines.py"
```

实际生成命令由 check_delay.py 记录：已有 .venv/llmservingsim-native/bin/python 执行 -m moe_ub_cosim.generate，输入为本次 input.json，输出 generated/。生成器从锁定 proto 用已有 protoc 编码，schema/compiler.json 保存其命令与结果。每次替身调用为 cosim-layer-delay-test generated SYSTEM.json given-*.json NEW_RUN_DIR；真实调用为 cosim-layer generated SYSTEM.json UB_CASE - NEW_RUN_DIR。仿真子进程均使用 timeout --kill-after=5s 30s，完整绝对路径与输入副本保存在 commands.json 和各目录。

### 实际结果

| 检查 | 结果 | L 内记录 |
|---|---|---|
| 首次配置/构建 | 459 步，退出 0；随后测试错误诊断及注释修正的增量构建退出 0 | configure.json、build.json、build-diagnostic.json、build-final.json |
| 延迟反馈最终检查 | 五组给定事件、两组真实 UB、十项错误/覆盖拒绝全部通过，退出 0 | delay-verified.json、delay-verified/summary.json |
| M2.1 回归 | 来源开/关/不挂观测三次执行及十三项错误图/覆盖拒绝通过，退出 0 | layer-regression.json、layer-regression/summary.json |
| 链接范围 | 专用替身和真实入口各包含全部 52 个 ASTRA 核心对象，实际装载单套 10 个本次 UB ns-3 库 | 两项集成的 compiled-sources.json、summary.json 与构建 link.map |
| 旧产物保护 | 原生启动锁恢复；最终增量构建后 107 个旧产物大小、SHA-256 仍一致 | restore-baselines.json、baseline-verification.json、baseline-final.json |

rank 2 的专家开始/结束和局部结果处理结束如下，单位 ns。最后一列是本次全组 Dispatch 最晚完成，用于核对没有额外屏障；没有把该统计值插入图依赖。

| 用例 | 专家开始 | 专家结束 | 局部结果处理结束/首个 Combine SEND | 全组 Dispatch 最晚完成 |
|---|---:|---:|---:|---:|
| 给定事件基准 | 26000 | 38000 | 39000 | 56000 |
| 1→2 数据就绪从 26000 改为 30000 | 30000 | 42000 | 43000 | 56000 |
| 2→3 事务完成从 18000 改为 34000 | 34000 | 46000 | 47000 | 56000 |
| 0→2 数据就绪从 20000 改为 24000 | 26000 | 38000 | 39000 | 56000 |
| 3→1 数据就绪从 50000 改为 54000 | 26000 | 38000 | 39000 | 56000 |
| 真实 UB 基准 | 5877 | 17877 | 18877 | 5923 |
| 真实 UB 链路延迟对照 | 11629 | 23629 | 24629 | 13675 |

最后一份必要输入推迟 4000 ns 时，专家计算和首次 Combine SEND 同样推迟 4000 ns；但替身中 rank 2 的 Combine 仍等待 70000 ns 的 3→2 接收，最终合并仍为 71000 ns，后继计算仍为 72000 ns。该例明确区分局部依赖响应与整图完成时刻。无关输入对照使 rank 1 专家开始从 50000 移至 54000 ns，而 rank 2 不变；本地 SEND 对照则使 rank 2 必须等到 34000 ns。

真实 UB 基准中 rank 2 的必要条件分别为：本地输入 5000、0→2 接收 5877、1→2 接收 5794、2→3 发送完成 5337 ns。链路对照变为 5000、9629、11629、5337 ns；因此专家开始/结束及局部结果处理均推迟 5752 ns，实际最后必要输入为 1→2。此变化包含网络协议、单发送槽和前序事务的影响，不等同于单链路增加的 2000 ns。rank 2 图完成从 21552 移到 31387 ns，也不等于专家计算的位移。

两次真实 UB 均为十四次业务写、114688 唯一交付字节，每条源/目标各一次回调，目标提交与实际 WQE 完成分别匹配，CSV 完成计数为 0。每 rank 最大在途 SEND/WQE 为 1，最终图、借用回调、传输上下文、计算/发送槽、事件和执行器任务清空。五组替身同样完成四图及全部收发，但其模拟提交字节仅证明状态机行为，不计为真实网络证据。

M2.1 回归保留原图完成时刻 21168、21506、21552、17675 ns；来源开关/不挂观测的 33 个原生跟踪文件、图和执行/资源结果相同，选定端口仍为 18 个包、25839 字节，FNV-1a 包/时刻指纹仍为 15435793776749377513。生产路径未修改，因此不重复 M1 全部用例或上游原生全集；其此前日志与产物均保留。

### 保留的失败与未验证范围

首次 delay-check.json 返回 1：脚本错误地比较 Python 与 C++ 重写后的 plan.json 原始哈希。两者仅对象键顺序不同；改为比较完整解析结构，四份二进制图仍逐字节比较 SHA-256，没有改变图、字节、依赖或时序预期。原输出保留在 L/delay-check/。

第二次 delay-check-final.json 返回 1：五组替身、两组真实执行和静态拒绝已经通过，但过期事件的具体错误只出现在原 ASTRA 日志，外层 stderr 仅报 incomplete test transfer。锁定 Sys.cc 的 call_events 捕获回调异常后只记录、不向外重抛；专用测试适配器现在保留首次原始错误，运行结束后将其明确报告为失败，不修改上游。后续 delay-validation.json 和最终 delay-verified.json 均退出 0；两次失败的 stderr、命令和输入保留，没有删除断言或降低验收。

最终十项预期拒绝包括八种静态事件表错误、一次事件早于 SEND 的运行时错误及一次覆盖已有目录。静态错误在创建运行目录前拒绝；运行时错误保留输入和 ASTRA 日志，不产生成功 result.json；覆盖拒绝保持既有输出不变。预期返回 1 的子进程与检查脚本返回 0 分开记录。

没有未解决失败。配置仍有源码树外输出提示，编译仍有上游 std::binary_function 弃用警告。当前仍是未校准的固定四 rank、单块单槽参考功能验证；没有 M2.3 去重/分块/多槽变体、M2.4 连续图、M3 动态服务、真实丢包重传或硬件校准。

本轮停止在 M2.2 completed；M2.3 等待用户确认。按持续授权本地提交，不推送；提交后的 13 项依赖检查记录为 L/dependency-final.json，主仓库和 UB 最终状态为 L/final-status.json、L/final-ub-status.json。

## 2026-09-28：m2-3-chunks-slots

用户确认执行 M2.3。开始时主仓库 5023358、ASTRA d346994、LLMServingSim a4053bc、UB 1ba7222 工作区干净，13 项依赖检查通过；旧任务已提交后才切换，任务说明提交 b11e0fe。日志根为 `runs/m2-3-chunks-slots-20260928T144612Z/`，下文记为 L。复用经审阅的 record.py 保存命令、cwd、UTC 起止、退出码及 stdout/stderr；集成目录 commands.json 保存逐次生成/仿真命令和原始输出。

### 修改与依赖保存

生成器按 chunk_bytes 拆分每个有向向量，保留真实尾块，分配独立 transfer_id、UB task_id、comm_tag 和 SEND/RECV。来源仍是原 Token/专家及逻辑操作；task_byte_range 为块内范围，新增 pair_byte_range/pair_payload_bytes 表示原向量位置和总量。前后端均检查连续编号、完整范围、真实尾块及一致来源；后端在启动前核对图和计划。专家计算输入份数不乘以块数。输入在生成前复制，后续修改配置不会改变已有计划。

参考生成逻辑已有同目的 rank 去重，本步新增实际变体核验。向量总长可以超过单个 UB 任务上界，但每块仍须在 uint32 范围；单元测试生成 2^32 字节向量的 2^32−1/1 字节两块，不实际执行该大流量。为限制功能图规模，最多 4096 条传输；本步只接受一个或两个 SEND 槽。零应用元数据、每 rank 正计算、四 rank 身份映射等已有边界保留。

ASTRA 原 HardwareResource 在 occupy/release/is_available 中固定一个通信槽，无法从已有参数增至两槽。已从锁定提交建立本地 task/m2-3-send-slots 分支，增加 configure_p2p_send_slots：联合入口激活前显式配置，点对点 SEND 按节点身份登记、释放，支持乱序完成。RECV 不占槽，原通信总计数继续参与完成判断；默认不开启新分支，集合通信继续独占原资源。没有修改 Workload、网络后端或 UB 协议。

通过全部检查后，ASTRA 源码和 tests/cosim/send_slots_test.cc 先提交为 3f72d0af113f468ec8031a1037685464b47e9535；LLMServingSim 的 ASTRA gitlink 再提交为 47b3a219da6151207f6c21256a6dc63a9e6c4830。父仓库更新对应 gitlink、checkout_commit 与 project_patch_commits，保留原 baseline_commit。两个本地仓库的 Git 身份按用户已提供的 baoy21 / baoy21@mails.tsinghua.edu.cn 设置，没有修改全局配置。

本轮仅本地提交：ASTRA/LLMServingSim 的 origin 仍为原来源，没有创建 fork、修改远端或推送；本地补丁尚未发布到可取回的维护远端，不能声称从现有远端已经能复现新增提交。发布步骤仍需另行处理。UB 及其工具、Chakra 等其他依赖源码和版本未变。没有安装软件、初始化依赖或下载模型/数据。

### 构建与实际命令

所有下列命令 cwd 为 /root/moe-ub-cosim。联合构建为 build/m2-chunks-slots/；为核对默认资源规则，另用 build/m2-chunks-slots-native/ 构建原 analytical 入口。两次构建先后执行，最多八路并行。analytical 仅作独立原生回归，没有链接进联合程序。

```bash
M23_LOG=runs/m2-3-chunks-slots-20260928T144612Z
cmake -S third_party/ns-3-ub -B build/m2-chunks-slots -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m2-chunks-slots/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m2-chunks-slots --parallel 8 --target \
  cosim-layer cosim-layer-delay-test cosim-send-slots-test cosim-write cosim-write-test \
  cosim-observation-test cosim-compute cosim-time-test m0-joint-link-probe
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/unit/layer_plan_test.py
build/m2-chunks-slots/output/cosim/ns3.44-cosim-send-slots-test
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_variants.py \
  --build-dir build/m2-chunks-slots --output-dir "$M23_LOG/variants"
cmake -S third_party/LLMServingSim/astra-sim/build/astra_analytical \
  -B build/m2-chunks-slots-native -DBUILDTARGET=congestion_unaware -G Ninja
cmake --build build/m2-chunks-slots-native --target AstraSim_Analytical_Congestion_Unaware --parallel 8
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_native_collective.py \
  --patched-binary build/m2-chunks-slots-native/bin/AstraSim_Analytical_Congestion_Unaware \
  --output-dir "$M23_LOG/native-collective-final"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_layer.py \
  --build-dir build/m2-chunks-slots --output-dir "$M23_LOG/layer-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_delay.py \
  --build-dir build/m2-chunks-slots --output-dir "$M23_LOG/delay-regression"
python3 tests/integration/check_write.py --build-dir build/m2-chunks-slots --output-dir "$M23_LOG/write-regression"
python3 tests/integration/check_observation.py --build-dir build/m2-chunks-slots --output-dir "$M23_LOG/observation-regression"
python3 tests/integration/check_compute.py --build-dir build/m2-chunks-slots --output-dir "$M23_LOG/compute-regression"
build/m2-chunks-slots/output/cosim/ns3.44-cosim-write-test
build/m2-chunks-slots/output/cosim/ns3.44-cosim-observation-test
build/m2-chunks-slots/output/cosim/ns3.44-cosim-time-test
build/m2-chunks-slots/output/cosim/ns3.44-cosim-time-test --wrong-resolution
build/m2-chunks-slots/output/m0/ns3.44-m0-joint-link-probe "$M23_LOG/m0-probe.et"
python3 scripts/m0_joint_build/audit.py --build-dir build/m2-chunks-slots \
  --run-log "$M23_LOG/m0-probe.stdout.log" --output-dir "$M23_LOG/m0-audit"
python3 "$M23_LOG/restore_baselines.py"
```

每个变体的生成器调用、输入和三次真实执行在 L/variants/commands.json。继续使用已有虚拟环境和 protoc，从锁定 proto 生成本次编码模块。原生集合通信检查在自己的运行目录分别执行旧二进制与本次新二进制，显式传入八个 end-npu-ids 和七行 done/一行 exit；cwd、stdin 及完整 argv 均保存在 native-collective-final/commands.json。没有运行带清理命令的原 rt_template/run.sh，也未使用其中已过时的 remote-memory 参数。

### 实际结果

| 检查 | 结果 | L 内记录 |
|---|---|---|
| 联合配置/构建 | 487 步，退出 0 | configure.json、build.json |
| 原生 analytical 配置/构建 | 118 步，退出 0 | native-configure.json、native-build.json |
| 生成器单元 | 八项测试通过，含去重、分块/尾块、输入快照、大小边界及错误配置/计划 | plan-unit-verified.json |
| 发送资源单元 | 默认/集合资源、一槽/两槽、RECV、忙时改配置拒绝、重复/错误释放及乱序完成通过 | send-slots-unit.json |
| M2.3 变体 | 三种输入各来源开/关/不挂观测，共九次真实执行；十一种坏计划在启动前拒绝 | variants.json、variants/summary.json |
| 默认原生集合通信 | 补丁前/后八 rank、十六个集合节点及十六个计算节点；64 条事件、完整执行日志和八份完成记录相同 | native-collective-final.json、对应 summary.json |
| M2.1 回归 | 三次来源模式及十三项错误/覆盖拒绝通过，原完成时刻保持 | layer-regression.json |
| M2.2 回归 | 五组给定事件、两组真实 UB 和十项错误/覆盖拒绝通过 | delay-regression.json |
| M1 写与观测 | 六个真实写用例、21 次观测及相应非法输入拒绝通过 | write-regression.json、observation-regression.json |
| M1 时间/计算 | 四个计算用例、时间单位/溢出/异步回调、错误精度与相关单元通过 | compute-regression.json、write-unit.json、observation-unit.json、time-unit.json、time-wrong-resolution.json |
| 联合链接范围 | 全部 52 个 ASTRA 对象，实际装载单套 10 个本次 UB ns-3 库 | m0-audit/ 及各集成审计 |
| 旧产物保护 | 原生启动锁恢复，120 个旧产物大小和 SHA-256 不变 | restore-baselines.json、baseline-verification.json |

按验收顺序，实际业务量和图完成时间如下，时间单位 ns。分块与发送槽变化允许改变执行曲线，不将这些结果写入通信模型作为固定时间。

| 输入 | UB 写数 / 业务字节 | 专家输入份数 | 每 rank SEND/WQE 峰值 | rank 0/1/2/3 图完成 |
|---|---|---|---|---|
| dedup | 12 / 98304 | 2、1、4、1 | 1、1、1、1 | 25003 / 25341 / 25387 / 17090 |
| chunks | 42 / 114688 | 2、2、3、1 | 1、1、1、1 | 21805 / 22409 / 22455 / 18477 |
| two_slots | 42 / 114688 | 2、2、3、1 | 2、2、2、2 | 21196 / 21473 / 21519 / 17621 |

去重保留 A 的专家 4 和 5 两份计算，rank 2 的专家耗时为 16000 ns。分块后三个块依次覆盖 [0,3072)、[3072,6144)、[6144,8192)，各块 UB 任务内范围从零开始，专家计算仍为 8000/8000/12000/4000 ns。单槽与两槽的传输描述和四份图逐字节相同，只有槽参数改变。ASTRA 的 208 条 issue/callback 验证分块图 104 个节点，SEND 顺序为目的 rank、分块索引；实际 WQE 另核对并发和每条事务完成。

每种输入的三次运行结果、图、去墙钟前缀 ASTRA 日志及 33 个原生跟踪文件相同；开关来源还保持 observed 文件与实际序列化包/时刻指纹相同。每条业务恰好一次 UB 提交和目标写入，两侧各回调一次；最终图、发送/计算槽、借用回调、传输上下文、执行器任务和源 Jetty 资源清空，CSV 完成计数为零。

所选交换机 4 端口 2，去重例为 18 个包、25839 字节，分项为业务 24576、数据协议开销 468、控制 795。两种分块例均为 36 个包、27105 字节，分项为业务 24576、数据协议开销 702、控制 1827。各窗守恒，实际包范围完整覆盖经过该端口的各块；来源指向正确操作及原向量位置，没有把块内偏移误当向量偏移。

独立原生对照使用 rt_template 的八 rank Ring 网络及集合算法参数，生成两个依赖同一准备 COMP 的 1 MiB AllReduce，再运行后继 COMP。旧、新程序每个 rank 都在 237560 ns 完成，集合之间保持原单槽串行行为，64 条执行事件及完整去墙钟前缀日志相同。没有把这一原生 analytical 执行当作 UB 网络或硬件真实性证据。

### 保留的失败与未验证范围

第一次 native-collective.json 返回 1：原始 stdout 中异步 spdlog 与直接 cout 的输出交错不同。检查时确认全部 64 条 issue/callback、八份完成时刻和完整 ASTRA 日志已经一致；问题只在 stdout 文本顺序。脚本现比较完整执行日志和解析后的完成记录，仍验证原生退出握手、零 stderr、无残留图，原始 stdout/stderr 不改写。native-collective-final.json 返回 0；首次失败保留在 L/native-collective/，未更改时刻或资源预期。

代码检查还发现旧生成器保存输入对象引用，修改配置可能影响此前计划。已改为复制输入，并在单元中验证修改槽配置后旧计划仍有效；这不改变任务语义或计算/通信时间。

没有未解决失败。继续保留源码树外 UB 输出目录提示和上游 std::binary_function 弃用警告。未执行全套上游 serving/CSV 测试；本步未修改 serving 或 UB，已执行直接受影响的默认资源、原生集合及联合回归，旧产物全部保留。未运行大向量真实传输、零计算/零 Token 成员、丢包重传、连续图、动态服务或硬件校准。校准状态仍为 unvalidated_reference。

本轮停止在 M2.3 completed；M2.4 等待用户确认。ASTRA、LLMServingSim 和父仓库按由内向外顺序本地提交，不推送；父仓库提交后的依赖检查记录为 L/dependency-final.json，主仓库、LLMServingSim、ASTRA 与 UB 状态另存 final-*.json。

## 2026-09-28：m2-4-graph-lifecycle

用户确认执行 M2.4。旧任务已经在 f8d7be3 提交，新任务说明提交 ed85f98；开始时主仓库、ASTRA 3f72d0a、LLMServingSim 47b3a21、UB 1ba7222 工作区干净，13 项依赖检查通过。本轮日志根 `runs/m2-4-graph-lifecycle-20260928T151643Z/`，下文记为 L。record.py 保存每条命令的 cwd、argv、起止 UTC、退出码和原始 stdout/stderr，集成目录 commands.json 保存逐次生成、实际执行和错误输入命令。

### 行为、身份与依赖补丁

新增固定两步生成器 moe_ub_cosim.sequence 和真实入口 cosim-sequence。两步都显式给出 A～D 路由，Token 索引从 9 增至 10，局部 node_id 复用。RunIdentities 检查图和操作身份不重复，并统一分配本次运行内的传输编号；第一步 UB task_id/comm_tag 为 100～113，第二步为 114～127，transfer_id 在对应数值上加 2^32。局部计算图的结构与耗时不变，每步十四次写、114688 业务字节、2/2/3/1 份专家输入。

第一步使用构造函数装载初始业务图。全部四 rank 装载后才登记该轮传输并按 rank 顺序激活；没有交互式前端或首次调度消息。第一步四个完成回调全部到达后，经 ScheduleNow 在同一时刻检查资源、校验第二步全部图、准备全部 feeder，再装载、登记和激活。四个 Sys、UbApp、控制器与 UB 网络对象保持不变，传输任务仍只在 ASTRA SEND 派发时提交。第二步输入 ready_ns=0 表示固定步骤尚未提交，运行目录 1/plan.json 写入实际绝对提交时刻。

原 ASTRA add_workload() 将逐 rank 装载与 fire 混在一起，回调也只保存局部节点号。新增 opt-in bind_graph/load_graph/activate_graph，在途或历史图身份不能重新装载，未激活的图不能 fire，活动图不能重复激活。WorkloadLayerHandlerData 保存图身份与通信 tag；Workload 在查找局部节点或释放资源前拒绝旧图、非活动图或已结束节点的回调，并报告旧图、当前图、rank、node、tag。通信 tag 与 UbWriteTransfer 的不可变描述共同定位传输。默认未启用该接口时保留原生行为和日志格式。

原 ASTRA 日志在显式图模式下附带 graph_id，并记录实际 load/activate。导出器按图身份读取原 issue/callback，避免同一局部节点号串到下一图。端侧每 rank 的记录号为 1～4，内部 operation_registry 指明两步对应，observed 不新增全局图、传输、请求或 Token 身份。单层入口和 M1 入口仍可运行。

从锁定提交创建 task/m2-4-graph-lifecycle 本地分支。全部检查后，ASTRA 的 Workload.hh/.cc、WorkloadLayerHandlerData.hh 提交 e2a14f55d9a45db8f3831bda7035af3e191e4bb7；LLMServingSim 的 gitlink 随后提交 0038df80f1a46cd7b1cd73616f739e374c11c3e4。父仓库更新 gitlink、checkout_commit 和 project_patch_commits，保留原 baseline_commit、来源及之前补丁。UB、Chakra 和其他依赖源码没有修改。

没有推送、创建 fork、改变远端或仓库可见性；ASTRA/LLMServingSim 的项目补丁仍仅本地保存，当前记录的远端不能视为已可取回这些提交。没有初始化、升级、安装软件、下载模型或修改全局配置。

### 构建与实际命令

以下命令 cwd 均为 /root/moe-ub-cosim，使用已有虚拟环境和 protoc。联合构建目录 build/m2-graph-lifecycle/；默认原生入口在独立 build/m2-graph-lifecycle-native/ 构建。一次一个构建，最多八路并行。

```bash
M24_LOG=runs/m2-4-graph-lifecycle-20260928T151643Z
cmake -S third_party/ns-3-ub -B build/m2-graph-lifecycle -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m2-graph-lifecycle/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m2-graph-lifecycle --parallel 8 --target \
  cosim-sequence cosim-lifecycle-test cosim-layer cosim-layer-delay-test cosim-send-slots-test \
  cosim-write cosim-write-test cosim-observation-test cosim-compute cosim-time-test m0-joint-link-probe
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/unit/sequence_plan_test.py
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/unit/layer_plan_test.py
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_sequence.py \
  --build-dir build/m2-graph-lifecycle --output-dir "$M24_LOG/sequence-final"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_layer.py \
  --build-dir build/m2-graph-lifecycle --output-dir "$M24_LOG/layer-final"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_delay.py \
  --build-dir build/m2-graph-lifecycle --output-dir "$M24_LOG/delay-final"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_variants.py \
  --build-dir build/m2-graph-lifecycle --output-dir "$M24_LOG/variants-final"
python3 tests/integration/check_write.py --build-dir build/m2-graph-lifecycle --output-dir "$M24_LOG/write-regression"
python3 tests/integration/check_observation.py --build-dir build/m2-graph-lifecycle --output-dir "$M24_LOG/observation-regression"
python3 tests/integration/check_compute.py --build-dir build/m2-graph-lifecycle --output-dir "$M24_LOG/compute-regression"
build/m2-graph-lifecycle/output/cosim/ns3.44-cosim-send-slots-test
build/m2-graph-lifecycle/output/cosim/ns3.44-cosim-write-test
build/m2-graph-lifecycle/output/cosim/ns3.44-cosim-observation-test
build/m2-graph-lifecycle/output/cosim/ns3.44-cosim-time-test
build/m2-graph-lifecycle/output/cosim/ns3.44-cosim-time-test --wrong-resolution
cmake -S third_party/LLMServingSim/astra-sim/build/astra_analytical \
  -B build/m2-graph-lifecycle-native -DBUILDTARGET=congestion_unaware -G Ninja
cmake --build build/m2-graph-lifecycle-native --target AstraSim_Analytical_Congestion_Unaware --parallel 8
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_native_collective.py \
  --patched-binary build/m2-graph-lifecycle-native/bin/AstraSim_Analytical_Congestion_Unaware \
  --output-dir "$M24_LOG/native-collective"
build/m2-graph-lifecycle/output/m0/ns3.44-m0-joint-link-probe "$M24_LOG/m0-probe.et"
python3 scripts/m0_joint_build/audit.py --build-dir build/m2-graph-lifecycle \
  --run-log "$M24_LOG/m0-probe.stdout.log" --output-dir "$M24_LOG/m0-audit"
python3 "$M24_LOG/restore_baselines.py"
```

联合首次构建 504 步通过。加入实际 load/activate 日志与故障检查后，build-lifecycle.json 的 46 步增量构建通过；最后审查将首轮描述登记移到四 rank 全部装载之后，build-final.json 的六步构建通过，随后重新运行 sequence-final、layer-final、delay-final、variants-final。最终代码哈希与测试记录一致。原 native 构建为 118 步；未将 analytical 库链接进联合程序。

sequence-final/commands.json 记录生成器对子目录 0/、1/ 的编码、五次真实执行，以及十二种坏输入和输出覆盖拒绝。每份导出保留 generation.json、schema/compiler.json、原计划和四份图。原 native 对照采用 rt_template 的网络/system 参数，生成八 rank 的两个独立 AllReduce 与前后 COMP，保留 stdin 的七行 done/一行 exit 和每个程序的实际命令；未执行带清理动作的原 run.sh。

### 验收结果

| 检查 | 实际结果 | L 内记录 |
|---|---|---|
| 编号与两步输入单元 | 四项通过，身份不复用、局部号复用、下一 Token 步、边界耗尽且状态不变 | sequence-unit.json |
| 原计划单元与发送资源 | 八项原计划单元及默认/集合、一槽/两槽、RECV、乱序释放检查通过 | plan-unit.json、send-slots-unit.json |
| 连续两步 | 来源开/关/不挂观测、重复运行、旧回调注入，共五次执行；每次 28 次真实写、229376 字节 | sequence-final.json、对应 summary.json |
| 图生命周期负向检查 | 未激活执行、重复激活、忙时替换、已用图身份、运行中损坏第二轮末 rank 全部拒绝；无部分装载 | sequence-final/stale/result.json、原 ASTRA 日志 |
| 整组启动前拒绝 | 十二种坏输入及旧输出覆盖；跨图重复身份用各自合法且配套的图验证 | sequence-final/commands.json |
| M2.1/M2.2/M2.3 回归 | 三种单层来源模式、五组给定事件、两组真实延迟、九次去重/分块/双槽变体及相应负向检查通过 | layer-final、delay-final、variants-final |
| M1 回归 | 六个实际写、21 次观测、四个计算用例，时间/写/观测单元与错误输入拒绝通过 | write/observation/compute-regression 及相应 unit.json |
| 默认原生集合通信 | 八 rank、十六个集合节点、64 条事件和完整执行日志与旧程序一致，每 rank 237560 ns 完成 | native-collective.json、对应 summary.json |
| 联合链接范围 | cosim-sequence 和 cosim-lifecycle-test 均包含全部 52 个 ASTRA 对象，实际只装载本次 UB 的十个 ns-3 库 | sequence-final 的命令、link.map 核验及 m0-audit/ |
| 旧产物保护 | 原生启动锁已恢复，155 个旧产物大小与 SHA-256 不变 | restore-baselines.json、baseline-verification.json |

两轮的实际时间如下，均为 ns-3 绝对纳秒。第一轮部分 rank 提前完成时没有替换图；第二轮在全组完成时刻提交，不加入固定步进。

| 图 | 提交/激活 | rank 0 完成 | rank 1 完成 | rank 2 完成 | rank 3 完成 |
|---|---:|---:|---:|---:|---:|
| g.m24.0 | 0 | 21168 | 21506 | 21552 | 17675 |
| g.m24.1 | 21552 | 42761 | 43099 | 43145 | 39268 |

两轮共 96 个节点，原日志 192 条 issue/callback；每轮专家计算仍为 8000/8000/12000/4000 ns，全部局部等待由对应真实 UB 事件独立核对。两轮的实际历时分别为 21552 与 21593 ns，没有将独立运行耗时累加来计算第二轮。测试边界仍为 1000000 ns，500000 ns 的较晚事件执行，证明图完成没有直接终止仿真；这些测试时刻不是服务完成时刻或新的通信等待。

五个旧回调来自第一轮 rank 0 的一个 COMP、两个 SEND、两个 RECV；测试在第二轮的计算和通信期间通过 Workload 或 Sys::handleEvent 正常入口送入新分配的旧身份上下文，没有访问已释放指针，也没有伪造 UB 数据完成。日志记录五条 stale_callback，当前图保持 g.m24.1。除预期的错误诊断和计数外，全部业务节点、收发时刻、WQE、目标提交、33 份原生网络跟踪与正常执行一致。四 rank 最后均 finished，图/槽/执行器任务/借用回调清空，源 Jetty 已释放，TP 数仍为每 rank 三条，CSV 完成计数为零。

所选交换机 4 端口 2 的连续观测共 36 个包、51678 整包字节，分项为业务 49152、数据协议开销 936、控制 1590、应用元数据及未归属字节均零，包/时刻指纹为 6220457999441248563。100 个连续窗口逐项守恒，每个经过该端口的传输范围从 0 覆盖至 8192。来源开关不改变 observed 或序列化包/时刻；不挂观测的业务和原生跟踪也相同。端侧十六条记录的局部编号唯一，四个内部操作分别关联两步。

### 限制与停止位置

本轮列出的构建、正向执行和检查命令均返回 0；负向输入按预期返回 1，没有未解决测试失败。继续保留源码树外 UB 输出目录提示及上游 std::binary_function 弃用警告。原生集合对照仅证明本次默认路径回归，未执行全套 serving 或 CSV 场景；相应原生旧产物保留。

comm_tag 继续限制为 1～499999999，以避开锁定 NATIVE 路径的标签重映射；达到已验证上界时明确报错，不截断，不宣称完整正 31 位空间可用。固定两步、四 rank、正计算和零应用元数据限制保持；本步不覆盖 M3 的零业务初始化决策、动态请求、空闲组唤醒、多组共享瓶颈、零 Token 成员或服务退出协议，也没有真实路由或模型校准。

停止在 M2.4 completed。ASTRA、LLMServingSim 和父仓库由内向外本地提交，不推送。父仓库提交后的依赖检查保存为 L/dependency-final.json，主仓库和三个相关子模块状态保存为 final-*.json。M2 固定图功能步骤已经完成，M3.1 等待用户确认。

## 2026-09-28：m3-1-service-loop

用户确认执行 M3.1。M2.4 已在 116fb15 保存，新任务说明提交 1784b1d；开始时主仓库、LLMServingSim 0038df8、ASTRA e2a14f5、UB 1ba7222 工作区干净，13 项依赖检查通过。本轮日志根为 `runs/m3-1-service-loop-20260928T155309Z/`，下文记为 L。record.py 保存 cwd、argv、起止 UTC、退出码与原始 stdout/stderr；集成目录的 commands.json 记录实际子进程命令与输入。没有修改依赖源码、gitlink 或锁文件，没有安装、下载、初始化、改变远端或推送。

### 服务和执行行为

新增 moe_ub_cosim.service 与 cosim-service。前端导入锁定 LLMServingSim 的真实 Scheduler/Request/Batch，使用 add_decode 登记给定就绪的 KV，以 schedule 选择每轮请求，通过 add_done 更新完成状态。没有复制调度器或改变它的计数规则：num_computed_tokens 在调度时推进，num_tokens_reached 在完成时推进。四个请求初始已有的生成 Token 数分别为 1、2、3、4，本次各执行三个解码步；逐请求状态保存在 service_result.json。

通信和固定计算仍由已验证的 M2 单层生成器产生，Top-2、专家放置、向量和分块、单 SEND 槽不变。每轮根据实际 Batch 和请求 Token 下标导出新图；运行内 graph/wave/operation、transfer/UB task/tag 不复用。后端先创建全部对象，以四份仅含 Chakra 元数据的图完成零业务初始化，再在 0 ns 发首次 DECIDE。图装载、传输登记与按 rank 激活沿用 M2.4 接口，全部 rank 完成后才通知 WAVE_DONE。前端使用绝对 now_ns，不累加独立运行结果。

后端 stdout 的原文件描述符仅用于 cosim/1 JSONL，上游 cout/printf 日志重定向至 stderr。前端保存双方协议消息，核对 run/decision/time 与完成图身份，并报告错误或子进程退出。读取使用有壁钟时限的分段管道读取，不完整行也不会无限阻塞。next_wakeup_ns 必须为 null、input_exhausted 必须为 true；动态到达尚未执行。

来源侧表可按轮原子追加，历史包范围不丢失。正常服务完成后仍检查所有图和传输上下文、实际链路包开始/到达计数、端口状态与队列；收尾探测只在前端完成后安排，业务运行中的监听只计数。网络空闲才停止，随后刷新统计、解除观察、销毁事件与对象。原生分配器的周期巡检不再使已完成服务继续运行。

### 实际命令与构建

除原生前端命令外，cwd 均为 /root/moe-ub-cosim。使用既有虚拟环境和 protoc，所有联合产物写入本轮独立目录，最多八路编译。

```bash
M31_LOG=runs/m3-1-service-loop-20260928T155309Z
cmake -S third_party/ns-3-ub -B build/m3-service-loop -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m3-service-loop/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m3-service-loop --target \
  cosim-service cosim-layer cosim-layer-delay-test cosim-sequence cosim-lifecycle-test \
  cosim-observation-test cosim-send-slots-test cosim-compute cosim-time-test \
  cosim-write cosim-write-test m0-joint-link-probe --parallel 8
cmake --build build/m3-service-loop --target cosim-service cosim-observation-test --parallel 8
env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*_test.py'
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_service.py \
  --build-dir build/m3-service-loop --output-dir "$M31_LOG/service-drain-audit"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_sequence.py \
  --build-dir build/m3-service-loop --output-dir "$M31_LOG/sequence-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_layer.py \
  --build-dir build/m3-service-loop --output-dir "$M31_LOG/layer-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_variants.py \
  --build-dir build/m3-service-loop --output-dir "$M31_LOG/variants-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_delay.py \
  --build-dir build/m3-service-loop --output-dir "$M31_LOG/delay-regression"
python3 tests/integration/check_compute.py --build-dir build/m3-service-loop --output-dir "$M31_LOG/compute-regression"
python3 tests/integration/check_write.py --build-dir build/m3-service-loop --output-dir "$M31_LOG/write-regression"
python3 tests/integration/check_observation.py --build-dir build/m3-service-loop --output-dir "$M31_LOG/observation-regression"
build/m3-service-loop/output/cosim/ns3.44-cosim-observation-test
python3 "$M31_LOG/restore_baselines.py"
```

配置和首次 512 步构建通过。退出逻辑修复后 build-drain.json 两步增量构建通过；补充侧表检查与观测追加单元后 build-final.json 四步构建通过。最终审查进一步核对每条 TP 的待发送队列与未确认段，build-drain-audit.json 两步构建和 service-drain-audit 复验通过，业务及收尾时刻均未改变；所有列出的回归也通过。继续保留源码树外 UB 输出目录提示和上游 std::binary_function 弃用警告。

原生前端未重新编译、未改动源文件。为验证仍能运行原流程，执行 serving/validate.sh 中 single 与 moe_dp_ep 两项的原参数，增加本轮独立输入和 CSV 输出目录；直接运行命令，保留输入，未执行全套脚本或更改基线。两条命令 cwd 为 /root/moe-ub-cosim/third_party/LLMServingSim：

```bash
env PYTHONPATH=. /root/moe-ub-cosim/.venv/llmservingsim-native/bin/python -m serving \
  --dtype bfloat16 --block-size 16 --log-level WARNING \
  --cluster-config configs/cluster/single_node_single_instance.json \
  --dataset workloads/example_trace.jsonl --num-reqs 10 --run-id m3-1-native-single \
  --inputs-root /root/moe-ub-cosim/runs/m3-1-service-loop-20260928T155309Z/native-single-inputs --keep-inputs \
  --output /root/moe-ub-cosim/runs/m3-1-service-loop-20260928T155309Z/native-single.csv
env PYTHONPATH=. /root/moe-ub-cosim/.venv/llmservingsim-native/bin/python -m serving \
  --dtype bfloat16 --block-size 16 --log-level WARNING \
  --cluster-config configs/cluster/single_node_moe_dp_ep_instance.json \
  --dataset workloads/example_trace.jsonl --num-reqs 10 --run-id m3-1-native-moe-ep \
  --inputs-root /root/moe-ub-cosim/runs/m3-1-service-loop-20260928T155309Z/native-moe-ep-inputs --keep-inputs \
  --output /root/moe-ub-cosim/runs/m3-1-service-loop-20260928T155309Z/native-moe-ep.csv
```

### 检查结果

| 检查 | 实际结果 | L 内记录 |
|---|---|---|
| Python 单元 | 15 项通过，含既有 12 项、真实调度器计数与完成、八种超范围/坏服务输入及不完整协议行的限时退出 | python-unit-final.json |
| 三轮服务 | 四个请求各推进三次并 FINISHED，四次 DECIDE/COMMIT、12 次 schedule，共 42 次真实写、344064 字节 | service-drain-audit.json、service-drain-audit/on/service_result.json |
| 原执行层证据 | 144 个节点、288 条原始 issue/callback，全部依赖、局部计算耗时、收发事件与全组完成核对通过 | service-drain-audit/on/execution/ 与各轮 graphs/ |
| 时延反馈 | 仅把 rank 1 链路传播从 20 ns 改为 2020 ns，三轮完成时刻随实际 UB 事件变化 | service-drain-audit/slow、slow-network |
| 被动观测 | 来源开/关/不挂观测与重复运行的业务事件、33 份原生网络跟踪一致；三个观测运行的 observed 与包/时刻指纹一致 | service-drain-audit/summary.json |
| 壁钟暂停 | 每次 COMMIT 前暂停 30 ms，四次后端消息及全部业务结果与基线一致 | service-drain-audit/paused、paused.protocol.json |
| 网络收尾 | 缩短最终两个 COMP 至各 1 ns，并设置真实端口间隔 500 ns；服务在 83685 ns 结束，504 次链路发送/到达均收尾后在 84207 ns 退出 | service-drain-audit/tail |
| 错误诊断 | EOF、非法 JSON、决策号/时刻/run 不符、未来唤醒、没有提交工作、错误路径、末 rank 坏图均返回 1；不部分装载业务图。前端捕获 /bin/false 子进程退出 | service-drain-audit/rejected-*、child-failure |
| 来源追加 | 新图登记保留旧包范围；批量中有重复身份时不部分追加 | observation-unit.json |
| M2 回归 | 单层、给定事件及真实延迟、去重/分块/双槽、固定两轮和旧回调检查通过 | layer/delay/variants/sequence-regression |
| M1 回归 | 四个计算用例、六次真实写、21 次观测及对应错误输入检查通过 | compute/write/observation-regression |
| 原生服务 | single=1663996645 ns，moe_dp_ep=1479062704 ns，均与 serving/validate-baselines.txt 精确一致，各完成十个请求 | native-single.json、native-moe-ep.json、native-clock-comparison.json |
| 链接与旧产物 | 联合服务含全部 52 个 ASTRA 核心对象，实际仅装载本次 UB 的十个 ns-3 库；原生启动锁恢复，192 份旧产物大小和 SHA-256 不变；25 份执行源码与最终测试哈希一致 | service-drain-audit/summary.json、restore-baselines.json、final-audit.json |

基线三轮提交时刻为 0、21552、43145 ns，完成时刻为 21552、43145、64738 ns；每轮仅在四个 rank 全部完成后推进服务。增加链路延迟后三轮分别在 33470、66940、100410 ns 完成，输入 Token 和路由不变。完成时间不是导出器预设或独立运行时长相加。

所选交换机 4 端口 2 三轮累计 54 个包、77517 整包字节，其中业务 73728、数据协议开销 1404、控制 2385，应用元数据与未归属字节均零。24 条端侧局部记录、六个内部操作覆盖三个图；observed 不包含 graph/operation/transfer/request/Token 身份。逐窗口整包计数与原 UB PortTrace 完全相符。

### 保留的失败、边界和停止位置

首次 service-check.json 返回 1，原三轮已经完成，但后端收到最终 COMMIT 后试图让 ns-3 自然耗尽事件，等待 60 秒超时。源码定位至 ub-switch-allocator.cc 的 Init()/CheckDeadlock()：即使网络空闲也每 10 ms 重复登记巡检。修复为服务完成后核对真实链路发送/到达、端口与队列，再停止并通过对象/Simulator 销毁剩余巡检事件，没有修改协议或按固定时刻冒充完成。原失败的协议、日志与 manifest 保留于 service-check/on/。service-check-2.json 及包含额外收尾测试的 service-drain-audit.json 均返回 0，没有未解决失败。

本步仍是 fixture/unvalidated_reference：单 EP 组、四 rank、给定就绪 KV、预先到达的短请求、每 rank 每轮一个 Token，参考单层计算和通信模板。请求剩余步数须相同，支持 2～32 步；本机功能输入为三步。未验证完整模型性能、真实路由、硬件校准、动态到达、多组竞争、零 Token 成员、P/D 或丢包重传。未运行全套 serving/validate.sh 和 CSV 场景；本轮没有修改依赖源码，两个相关原生场景和联合回归已通过。comm_tag 保持已验证的 1～499999999 范围。

停止在 M3.1 completed，下一项 M3.2 等待用户确认。代码、说明和任务记录只在主仓库本地提交，不推送。提交后的依赖检查保存在 L/dependency-final.json，主仓库及相关子模块状态保存在 final-*.json。


## 2026-09-28～29：维护仓库发布与远端源码获取

任务 `publish-maintenance-repos`，用户明确确认创建必要维护仓库并按由内向外的顺序推送。开始时主仓库为 `4fdc41695d8686637492416a40cfd648880771e0`，工作区及所有子模块干净，13 项依赖检查通过。M3.1 验收保留在上节；本次不运行 M3.2。

### 维护位置与修改范围

| 仓库 | 发布分支 | 固定提交 | 可见性 |
|---|---|---|---|
| `baoy21/astra-sim` | `cosim` | `e2a14f55d9a45db8f3831bda7035af3e191e4bb7` | 新建 private 维护副本 |
| `baoy21/ns-3-ub` | `cosim` | `1ba722217ef94716a07a56a2388be2c7dddd8e0b` | 既有 private，未改变 |
| `baoy21/LLMServingSim` | `cosim` | `9aed949c8e66fd1424d48365af64bfc0fe0b2ed1` | 新建 private 维护副本 |
| `baoy21/moe-ub-cosim` | `main` | `e3a2f2eea18f5515d434ea9cc7f2ffaba785095e`（地址与指针提交；其后仅整理本任务文档） | 既有 private，未改变 |

ASTRA 与 LLMServingSim 建立私有维护副本，完整保留被推送分支的已有提交历史和许可证，不是 GitHub fork 网络中的公开 fork。两者本机 `upstream` 保留 `casys-kaist` 原来源，`origin` 指向 `baoy21` 维护仓库；`cosim` 跟踪 `origin/cosim`。已有 UB 使用原来的维护仓库，`main` 仍为原始基线 `581bf28`；未推送或移动其默认分支。新副本首个发布分支为 `cosim`，GitHub 默认分支也为 `cosim`。

LLMServingSim 的新提交只将 `.gitmodules` 中 ASTRA URL 改为 `https://github.com/baoy21/astra-sim.git`，ASTRA gitlink 不变。主仓库修改 `.gitmodules` 的 LLMServingSim URL、该子模块 gitlink 与锁文件两项 `clone_url`、LLMServingSim `checkout_commit`/补丁列表；所有 `baseline_commit`、`upstream_url`、其他依赖提交和执行源码不变。锁文件准备期的环境与验证说明按开发管理约定保留，当前远端验证事实记录于本节。

### 命令、日志与实际结果

以下相对工作目录以 `/root/moe-ub-cosim` 为根，`L` 表示 `runs/publish-maintenance-20260928T163038Z/`。日志为本机忽略产物；`record.py` 保存 cwd、命令、开始/结束时间、退出码及原始 stdout/stderr。三个子模块和主仓库的首次推送输出由执行工具捕获后保存在对应 `push-*.json`；凭证未写入日志或 Git。

| 工作目录 / 输入 / 实际命令 | 结果 | L 内记录 |
|---|---|---|
| 根目录，`bash scripts/check_deps.sh`（开始状态） | 退出 0，13 个依赖一致 | `initial-deps.json` |
| 根目录，`python3 L/prepare_repositories.py`，认证账号 `baoy21`，两个仓库参数 `private=true, auto_init=false` | 退出 0，GitHub 两次创建均返回 HTTP 201，写权限与 private 核验通过 | `prepare-repositories.json`、`maintenance-repository-metadata.json` |
| ASTRA / LLM 目录，`git switch -c cosim <原固定 SHA>`、`git remote rename origin upstream`、`git remote add origin <维护 URL>` | 均退出 0 | `astra-cosim.json`、`astra-upstream.json`、`astra-origin.json` 及对应 `llm-*.json` |
| 根目录，`git submodule sync --recursive` | 退出 0，本机 URL 配置与文件同步 | `sync-remotes.json` |
| ASTRA、UB、LLM 依次执行 `git push -u origin cosim`，然后根目录 `git push origin main` | 均退出 0；三个新建 cosim 分支，根 main 从 `925f54a` 快进到 `e3a2f2e` | `push-astra.json`、`push-ub.json`、`push-llm.json`、`push-root.json` |
| 各依赖目录，`git ls-remote origin refs/heads/cosim` | 均退出 0，远端 SHA 与上表一致 | `remote-astra.json`、`remote-ub.json`、`remote-llm.json` |
| 根目录，地址提交后的 `bash scripts/check_deps.sh` | 退出 0，13 个依赖一致 | `local-deps.json` |
| 根目录，`python3 L/audit_publication.py` | 退出 0，发布修改范围、原始基线及执行源码保留核验通过 | `scope-audit.json` |

独立验证命令为：

```bash
git clone --recurse-submodules https://github.com/baoy21/moe-ub-cosim.git \
  runs/publish-maintenance-20260928T163038Z/fresh-clone
```

该命令直接从声明的 HTTPS 远端取回主仓库与递归依赖，不使用本机对象库或 `--reference`，也不使用 `--remote`。下载原生 ns-3 后端完整历史耗时较长，本机已有包及索引约 173 MiB；为缩短获取时间，向本任务克隆进程发送 SIGINT，保留 `fresh-clone.json`（子进程退出 -2，记录工具退出 130）及原始输出。此时尚未完成所有固定版本检出，不把首次克隆记为通过。

随后在根目录执行以下命令，继续限制尚未完成依赖的历史深度；其完整源码和目标 SHA 仍由同一 gitlink 指定：

```bash
git -C runs/publish-maintenance-20260928T163038Z/fresh-clone \
  submodule update --init --recursive --depth 1
```

首次继续初始化返回 128，诊断为原生 ns-3 未完成下载留下的仓库缺少当前 revision，见 `fresh-submodules-limited.json`。确认没有本任务克隆进程继续写入后，将该验证目录内的 ns-3 工作树及 Git 元数据分别移至 `L/interrupted-ns3-worktree/` 和 `L/interrupted-ns3-git/` 保留；没有删除其他目录或改变地址。相同初始化命令的残留日志显示已完成原生 ns-3 与 UB 检出，并开始 GitCode 工具下载；其 stdout/stderr 保留为 `fresh-submodules-retry.*.log`，但未生成结束状态 JSON，因此不推定退出码。2026-09-29 恢复时确认已无相关下载进程，事实保存在 `resume-state-20260929.json`。

### 2026-09-29 恢复与最终检查

恢复后的首次依赖检查返回 1（`resumed-clone-deps.json`）：Chakra、内存 analytical、网络 analytical 三个仓库已有正确 HEAD，但中断发生在工作树展开之前，索引为空、状态仅为删除；网络 analytical 的两个更深层依赖尚未初始化，GitCode 仓库尚无 HEAD。这些都位于本任务的独立验证目录。

先核对三个仓库的固定 SHA、空索引以及工作目录中只有 `.git`，再执行 `git restore --source=HEAD --staged --worktree -- .` 展开已从远端下载的对象，没有覆盖开发目录或其他人的修改。之后补齐两个深层依赖，从 GitCode 原地址直接获取并检出锁定工具提交。没有使用替代源或本机 Git 对象。

| 工作目录 / 实际命令 | 结果 | L 内记录 |
|---|---|---|
| 根目录，`python3 L/restore_clone_checkouts.py`，内部对三个已核对的空工作树执行上述 restore | 退出 0，三个固定版本完整展开 | `restore-clone-checkouts.json` |
| 验证目录的网络 analytical 中，`timeout 300 git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 submodule update --init --recursive --depth 1` | 退出 0，googletest 与 yaml-cpp 按父 gitlink 检出 | `analytical-deps.json` |
| 验证目录的 GitCode 工具中，`timeout 300 git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 fetch --depth 1 origin 2afc7202f88c132b63c38e8c7dcfc08505b13bb2`，然后 `git checkout --detach 2afc7202f88c132b63c38e8c7dcfc08505b13bb2` | 均退出 0 | `gitcode-fixed-fetch.json`、`gitcode-fixed-checkout.json` |
| 验证目录根，`git submodule update --init --recursive --depth 1` | 退出 0，所有递归依赖完成 | `fresh-submodules-final.json` |
| 验证目录根，`bash scripts/check_deps.sh` | 退出 0，全部 13 项通过 | `fresh-deps.json` |
| 根目录，`python3 L/audit_publication.py L/fresh-clone` | 退出 0，验证目录及 13 个依赖均干净，无对象库 alternates；所有远端为 HTTPS，基线和执行源码保留 | `fresh-scope-audit.json` |
| 开发目录根，`bash scripts/check_deps.sh` | 退出 0，全部 13 项通过；仅提示待提交的三份发布文档 | `resumed-local-deps.json` |
| 四个开发仓库，`git ls-remote origin refs/heads/main` 或 `refs/heads/cosim` | 最终均退出 0，四个远端 SHA 与上表一致 | `resumed-remote-root.json`、`resumed-remote-astra.json`、`resumed-remote-ub-retry.json`、`resumed-remote-llm-retry.json` |

远端复查时 UB 曾返回认证失败（128），同地址重试通过；LLM 首次复查未及时返回，结束该只读进程（-15，记录工具 143）后，增加命令超时重试通过。原输出分别保留于 `resumed-remote-ub.json` 和 `resumed-remote-llm.json`，未修改全局认证或网络配置。

最终结论是从声明远端获得了完整固定源码并通过 13 项递归检查；初次完整历史克隆被中断，其后对部分辅助仓库采用有限历史并恢复检出。没有把一次未完成的 clone 命令报告为成功。收尾三份文档已在 `c116cddf6780731fff9e7cf5839f7c1786b33001` 提交并推送，最终 `origin/main` 与之相同；验证克隆已通过 `fetch --no-recurse-submodules origin main` 和 `merge --ff-only origin/main` 快进到同一提交。`final-push.json`、`clone-final-docs-fetch.json`、`clone-final-docs-merge.json`、`final-clone-audit.json`、`final-root-remote.json` 均记录退出 0，最后一次来源与工作区核验通过。

### 限制与停止位置

最初沙箱内的远端检查无法连接已配置代理，Git 暂存/提交因 `.git/index.lock` 只读失败；使用授权的沙箱外执行后通过。没有因此改写全局网络或认证配置。四个仓库保持 private，新克隆需要对四个仓库均有读取权限；公共上游及 GitCode 还需各自网络可达。

本次只验证源码发布和固定依赖获取，不重复构建或仿真，不包含本机 `build/`、`runs/`、虚拟环境或外部模型数据。M3.1 的功能验证仍以上节记录为准。本次发布验收完成，停止在 `publish-maintenance-repos`；下一项 M3.2 仍由用户确认；以后开发任务默认仅自动本地提交，推送继续单独授权。


## 2026-09-29：发布后的文档复核

任务 `docs-post-publish-review`，依据用户对本次 Git 操作文档的复核请求。开始于已发布的 `c116cdd`，主仓库和全部子模块工作区干净。本次只修改 README、backend/README、开发管理说明、环境记录和当前任务；不改源码、锁文件、子模块地址或指针，不重新发布。

修正 backend/README 中 M2.3、M2.4 仍称补丁仅本地保存的两处说明，保留原功能提交号并说明已纳入 cosim 发布历史。开发管理说明补充 13 项依赖的直接父仓库、三项项目维护依赖与十项原上游依赖的区别，以及新克隆通常只有 origin、子模块处于 detached HEAD、本机 upstream 和工作分支不会自动传递的事实。现有验证克隆的 remote/分支状态已只读核对。README 增加相应入口，上一节补齐最终发布提交 c116cdd 与收尾日志。AGENTS.md 的约束、技术规范的基线、bootstrap.md 和锁文件准备期记录均继续有效，保持原文。

日志目录 `runs/docs-post-publish-review-20260929T012133Z/` 保存命令、cwd、退出码和原始输出；除注明外，工作目录均为主仓库根目录。这里仅引用已保存的发布证据，没有重新联网验证远端。

| 实际检查 | 结果 | 日志 |
|---|---|---|
| `bash scripts/check_deps.sh` | 退出 0，13 项固定依赖一致，子模块干净；根目录仅有待提交文档 | `deps-check.json` |
| `git diff --exit-code c116cdd -- .gitmodules deps.lock.json third_party` | 退出 0，发布配置和指针未改 | `published-configuration-unchanged.json` |
| `git submodule status --recursive` | 退出 0，13 个依赖均在父仓库指定提交 | `recursive-pins.json` |
| 在 ASTRA 执行 `git log --oneline d346994..HEAD`，在 LLMServingSim 执行 `git log --oneline a4053bc..HEAD` | 均退出 0，文档中的历史补丁包含在当前固定版本中 | `patch-history-astra.json`、`patch-history-llm.json` |
| 核对新增本地链接的文件、开发说明第 3/4 节标题；`rg` 检查 backend/README 的两处旧表述 | 链接目标存在；旧表述无匹配，rg 退出 1 为预期结果 | `doc-link-targets.json`、`stale-backend-statements.json` |
| `git diff --check` | 退出 0，差异格式检查通过 | `final-diff-check.json` |

本次是文档修正，无需重跑构建或仿真。完成差异审阅和格式检查后本地提交，不推送；停止在本次复核，M3.2 尚未开始。

## 2026-09-29：m3-2-dynamic-arrivals

用户确认开始 M3.2。上一项文档复核已在 75b38b4 保存；本任务说明提交 31afa33。开始时工作区干净，main 有两个尚未推送的文档提交；13 项依赖检查通过。本轮只改根仓库代码、输入、测试及说明，LLMServingSim 9aed949、ASTRA e2a14f5、UB 1ba7222 与全部递归固定版本不变。没有安装、下载、初始化、修改全局配置或推送。

本轮日志根为 `runs/m3-2-dynamic-arrivals-20260929T013834Z/`，下文记为 L。record.py 保存各命令的 cwd、argv、起止 UTC、退出码和原始 stdout/stderr；各集成目录的 commands.json 保存实际子进程、输入和退出码，故障重放另存实际协议消息。下面命令均从 `/root/moe-ub-cosim` 执行。

### 实现和功能输入

Python 服务增加按 rank 保存的已就绪 KV 待接纳队列，按组管理在途 wave。请求只在实际 arrival_ns 到达时进入 pending；忙碌组不调用 schedule，也不替换图。原请求结束后，组上下一批请求才经原 Scheduler.add_decode 接纳并分配目标 KV 块，再由真实 schedule 返回单请求、单 Token 批次。首个 ITL 从本次到达开始计时，包含排队，首次完成前 latest_completion_ns 为 null；不声称测量完整模型的 TTFT/TPOT。

后端在一套 ns-3 事件队列中登记下一次外部到达，每条 COMMIT 替换此前的唤醒。每组的四个 rank 都结束后，才释放组并发送 WAVE_DONE；另一组可同时保持在途。完成通知按显式组编号汇总，已收集的同刻事件合并决策。每组先校验全部图、侧表、传输、成员和空闲状态，再全部装载和登记，最后激活。所有组共享同一次网络初始化及原有控制器、连接、队列和端口状态。前端完成后仍等待实际事务、链路包与端口排空。

`deployment.json` 明确记录组、rank、UB node 和专家放置。当前功能边界为一或两个四成员 EP 组，成员互不重叠，全体 rank 覆盖 0～N-1，UB node 恒等映射，每成员两个专家；每组同刻四个请求组成一批且剩余步数相同，每请求 2～32 步、运行最多 64 个请求。不带 groups 的 M3.1 服务输入继续可用；旧式后端直接调用不提供 deployment.json 时采用原四 rank ep0。图生成器和装载器按显式成员处理 rank，固定 M2 驱动仍限制 0～3。

新增 busy.json 和 two_groups.json。前者沿用 M2 网络；后者含八个 NPU 和两个四端口交换域，每组各接一个交换机，400 Gbps、20 ns，其 network_attribute.txt 与 M2 文件逐字一致。两域在同一仿真内执行，本轮没有共享瓶颈实验。通信、路由和计算仍是既有单层合成功能输入，calibration_status 保持 unvalidated_reference。

### 实际命令与结果位置

```bash
M32_LOG=runs/m3-2-dynamic-arrivals-20260929T013834Z
cmake -S third_party/ns-3-ub -B build/m3-dynamic-arrivals -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m3-dynamic-arrivals/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m3-dynamic-arrivals --target \
  cosim-service cosim-layer cosim-sequence cosim-lifecycle-test cosim-send-slots-test \
  cosim-layer-delay-test cosim-observation-test cosim-compute cosim-time-test \
  cosim-write cosim-write-test m0-joint-link-probe --parallel 8
cmake --build build/m3-dynamic-arrivals --target cosim-service --parallel 8
env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_dynamic_service.py \
  --build-dir build/m3-dynamic-arrivals --output-dir "$M32_LOG/dynamic-final"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_service.py \
  --build-dir build/m3-dynamic-arrivals --output-dir "$M32_LOG/service-final"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_sequence.py \
  --build-dir build/m3-dynamic-arrivals --output-dir "$M32_LOG/sequence-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_layer.py \
  --build-dir build/m3-dynamic-arrivals --output-dir "$M32_LOG/layer-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_variants.py \
  --build-dir build/m3-dynamic-arrivals --output-dir "$M32_LOG/variants-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_delay.py \
  --build-dir build/m3-dynamic-arrivals --output-dir "$M32_LOG/delay-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_compute.py \
  --build-dir build/m3-dynamic-arrivals --output-dir "$M32_LOG/compute-regression"
python3 "$M32_LOG/final_audit.py"
bash scripts/check_deps.sh
git diff --check
```

配置及构建均退出 0，保留上游 Chakra 的 binary_function 弃用警告和独立输出目录提示。没有改变编译语言或引入其他 ASTRA 网络后端。最后运行的 Python 单元、动态集成、单组服务、M2 连续图/单层/去重分块发送槽/延迟、M1 计算检查均退出 0；证据分别在同名命令 JSON 与目录 summary.json。上面的 cosim-observation-test、cosim-time-test、cosim-write-test 三个 C++ 辅助单元目标只构建，不能将构建成功算作本轮执行了其全部单测。

### 验收数据

19 项 Python 单元通过，其中新增四项验证排队不改变在途 Batch、精确到达、同刻完成排序、显式交错成员以及非法成员/输入。交错编号在前端和图导出层验证；本次真实网络集成使用两个连续四 rank 组。

| 动态场景 | 实际行为 | 最终时刻 |
|---|---|---:|
| 单组忙碌时到达 | 5200 ns 时原写在途，201～204 只排队；旧请求三步完成后在 64738 ns 接纳新批次 | 129517 ns |
| 第二空闲组到达 | ep1 在 5500 ns 开始，此时 ep0 原写仍在途，ep0 首轮到 21552 ns 才完成；ep1 三轮在 27052、48645、70238 ns 完成 | 129517 ns |
| 全空闲后到达 | 初始不提交图，直接在 9000 ns 到达并开始，无固定步长轮询 | 73738 ns |
| 远期到达重登记 | 100000 ns 的到达跨越三次旧图完成，保持一次接纳；旧工作于 64738 ns 完成后直接推进 | 164779 ns |
| 到达与完成同刻 | 新批次于首轮结束的 21552 ns 到达，只接纳一次，不回溯修改已开始图 | 129517 ns |
| 两组同刻到达 | 两组在 0 ns 都提交，三次共同完成按组顺序汇报，共四次决策 | 64738 ns |

动态套件共九次正常运行，包括双组来源开、关、不挂观测及重复执行。双组业务结果、协议、逐请求进度和原生端口跟踪一致；可观测文件在来源开关间一致，端侧记录跨组跨轮不冲突。双组九轮有 126 次真实写、1032192 业务字节、432 个 ASTRA 节点；每份传输只有一份任务和各一次收发回调，局部节点执行时间逐一由实际传输完成及计算前驱核验。十二个请求结束，pending、running、inflight、发送/计算资源、回调及 UB 待执行任务清空，实际发出和到达包计数相等。

十项动态非法提交按预期退出 1：忙碌组替换、在途时声称服务结束、过去/当前/布尔/小数/溢出唤醒、部分组提交、重复图身份、第二组末 rank 损坏。后者在另一组执行期间拒绝第二组全部图，没有先启动第二组的部分 rank。原 M3.1 七次正常运行、九项非法协议和子进程异常回归通过，三轮仍为 21552、43145、64738 ns；增加网络传播时延后仍为 100410 ns；网络收尾仍在服务 83685 ns 结束之后等到 84207 ns 排空。

M2 连续图、单层、去重/分块/发送槽、延迟反馈与 M1 计算回归均通过。链接检查仍为完整 52 个 ASTRA 核心对象及单套 10 个 UB ns-3 动态库；动态八 rank 进程也只装入本次目录的这套库。final_audit.py 已恢复原生 UB launcher lock，并逐项核对 218 个旧构建/原生产物及生成协议文件的大小与 SHA-256，全部未改变；本轮未重跑 LLMServingSim 原生服务性能样例。最终功能检查记录的实现源码哈希与当前源码相符。

### 保留的失败和停止范围

第一次 dynamic-check 在“6000 ns 到达时仍有写在途”的断言失败。实际首轮 Dispatch 为 5000～5923 ns，6000 ns 处于计算阶段；排队行为本身已执行。保留日志，将人工到达输入改到 5200/5500 ns，使测试确实覆盖实际 UB 写在途窗口，未修改网络或计算时间，也未删除在途断言；dynamic-check-2 和 dynamic-final 通过。

第一次 m31-regression 的旧式输入缺少 deployment.json，后端内置默认 rank 的 C++ JSON 字面量为 signed，而验证要求 unsigned，导致在首条 DECIDE 前错误拒绝。改为明确 unsigned 字面量，未放宽外部输入检查；build-legacy-deployment、m31-regression-final 和 service-final 通过。失败日志和原始输入继续保留。

待接纳的给定 KV 不占目标 Scheduler 块池，本轮没有研究外部 KV 存放、容量不足或迁移。每轮仍要求组内四个真实 Token，任意不均匀到达/结束、零 Token 成员及完整故障死锁覆盖留给 M3.4；共享瓶颈和完成变化传到后续调度的对比留给 M3.3。没有增加模型层、真实路由、P/D 或硬件校准。

本任务在 M3.2 completed 停止，按持续授权只本地提交，不推送，不自动开始 M3.3。提交后的 13 项依赖检查与各仓库状态保存在 L/dependency-final.json 和 L/git-final.json。

## 2026-09-29：m3-3-shared-bottleneck

用户确认开始 M3.3，上一项 M3.2 已在 d098af6 保存，本任务说明提交 55c8407。开始时 main 工作区干净，比 origin/main 多四个本地提交；13 项依赖检查通过。LLMServingSim 9aed949、ASTRA e2a14f5、UB 1ba7222 及全部递归固定版本不变。本轮只增加根仓库的共享路径输入、内部出口审计、集成检查与文档，没有安装、下载、初始化、改全局配置或推送。

日志根为 `runs/m3-3-shared-bottleneck-20260929T020246Z/`，下文记为 L。record.py 保存各命令的 cwd、argv、UTC 起止、退出码及原始 stdout/stderr；各集成目录的 commands.json 保存实际输入和子进程记录。命令均从 `/root/moe-ub-cosim` 执行，使用既有原生 Linux、GCC/CMake/Ninja 与 `.venv/llmservingsim-native` 环境。

### 实现与实际命令

沿用 M3.2 服务调度及真实 ASTRA/UB 执行，不改变完成回调或计算时长。新输入保留每组四成员、八专家、每成员一 Token、三步解码、8192 字节分块和单发送槽。偶数 NPU 接交换机 8，奇数接交换机 9；400 Gbps 接入链路汇入端口 4 之间的 10 Gbps 链路，传播时延均为 20 ns。组 ep0 在 0 ns 到达，ep1 在 5500 ns 到达；分别运行仍保留另一组四个空闲 NPU，错开对照只把 ep1 到达改到 500000 ns。请求、路由、专家、计算、端点、字节与局部依赖逐轮一致。

根后端 PortObservation 读取原生 UbEnqueue、UbDequeue 和已有成功发送事件，输出内部 egress-queue-audit.jsonl。入队/出队记录含 UID、时间、整包大小和事件后字节数；发送事件另记录由原生完成时长决定的 busy_until_ns。无新增仿真事件或包标签，观测字段不变。物理出口 FIFO 不含 VOQ、信用等待与正在发送的包，不能当作全路径排队。

```bash
M33_LOG=runs/m3-3-shared-bottleneck-20260929T020246Z
cmake -S third_party/ns-3-ub -B build/m3-shared-bottleneck -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m3-shared-bottleneck/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m3-shared-bottleneck --target \
  cosim-service cosim-observation-test cosim-write cosim-write-test cosim-layer --parallel 8
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_shared_bottleneck.py \
  --build-dir build/m3-shared-bottleneck --output-dir "$M33_LOG/shared-final"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_dynamic_service.py \
  --build-dir build/m3-shared-bottleneck --output-dir "$M33_LOG/dynamic-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_service.py \
  --build-dir build/m3-shared-bottleneck --output-dir "$M33_LOG/service-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_layer.py \
  --build-dir build/m3-shared-bottleneck --output-dir "$M33_LOG/layer-regression"
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_observation.py \
  --build-dir build/m3-shared-bottleneck --output-dir "$M33_LOG/observation-regression"
build/m3-shared-bottleneck/output/cosim/ns3.44-cosim-observation-test
python3 "$M33_LOG/final_audit.py"
bash scripts/check_deps.sh
git diff --check
```

配置与一次构建均退出 0，保留上游 Chakra binary_function 弃用警告和独立输出目录提示。最后的共享瓶颈、Python 单元、动态/单组服务、单层、观测回归和 C++ 观测单元均退出 0；结果在同名命令 JSON、原始日志及集成 summary.json 中。cosim-write-test 仅构建，未作为独立单元执行；cosim-write 的真实事务由观测套件执行。

### 共享网络与调度反馈证据

shared-final 共七次运行：solo-a、solo-b、overlap、staggered、overlap-off、overlap-absent、overlap-repeat。每次只初始化一次网络、八个 NPU 的零业务图在 0 ns 完成。两组重叠实际六轮、84 次写、688128 业务字节；每份写只有一个任务、一次接收和发送回调。八个请求结束，发送/计算资源、前端队列、在途任务归零，实际链路发送与到达计数相等。全部局部前驱、真实 issue/callback 和计算耗时通过既有 check_execution 核对。

| EP 组与步骤 | 独立完成（ns） | 重叠完成（ns） | 推迟（ns） |
|---|---:|---:|---:|
| ep0 第 1 步 | 55038 | 85934 | 30896 |
| ep0 第 2 步 | 110076 | 175244 | 65168 |
| ep0 第 3 步 | 165114 | 264460 | 99346 |
| ep1 第 1 步 | 56717 | 85997 | 29280 |
| ep1 第 2 步 | 107934 | 175181 | 67247 |
| ep1 第 3 步 | 159151 | 264523 | 105372 |

首轮专家计算开始推迟 ep0 为 13506～25936 ns、ep1 为 8394～31570 ns，详细记录在 ep0/ep1.compute-feedback.json。下一轮提交等于本组真实 WAVE_DONE；第二组无须等待第一组结束。summary.json 保存首轮 28 条传输的独立/重叠发送开始、接收完成、事务完成和时长变化。两组都存在事务耗时增加，也有不变或缩短的传输，未假设平均带宽或每条传输必然变慢。

ep0 的 UID 2 业务包在 5106 ns 入队、8444 ns 出队，跨越 ep1 在 5500 ns 的首次图提交；同时有 ep0 写仍在途。该包不是事后重建，原生队列与网络对象在边界继续存在。8:4 出口恰好包含全部偶数到奇数 rank 的业务传输，逐包范围完整覆盖对应任务。十个 5000 ns 窗口内含两组业务，例如 [15000,20000) 各贡献 4096 字节。内部按来源分组后逐窗与业务真值相等，业务、元数据、协议开销、控制及未归属字节总和与端口聚合计数相等；隐藏身份只用于内部核验。

物理 FIFO 面积按相邻事件间排队字节积分，并独立用每包等待时间乘整包大小核对。独立 ep0/ep1 为 210914259/172012812 byte·ns，重叠为 536015450，超过前两者之和；业务包平均等待由 1663.6/1341.7 ns 增为 2007.067/2233.533 ns。最长连续发送从 13732/13682 ns 增为 27564 ns。峰值却为独立 4174/4236 字节、重叠 4174 字节，不能把此预取 FIFO 的峰值当作所有入口积压。

错开时 ep0 仍在 165114 ns 完成，ep1 从 500000 ns 开始，在 665114 ns 完成。所有 ep0 事务及此前进入出口的包均在 ep1 开始前完成，两组没有混合业务窗口。ep1 三轮耗时降至 165114 ns，重叠时为 259023 ns。错开网络保留此前 ep0 的仲裁、流控等状态，因此没有断言其耗时等于新建网络独立 ep1 的 153651 ns；本步未进一步归因这 11463 ns 差值。

来源开/关、不挂观测与重复运行的 service_result、执行结果、协议及全部原生网络日志一致；来源开关间的端侧和聚合端口文件、内部 FIFO 审计也一致。内部审计未产生可测的仿真行为变化。

### 回归、检查失败与停止范围

19 项 Python 单元与 C++ 观测单元通过。M3.2 九次正常运行和十项拒绝检查通过，双组三轮加排队请求仍于 129517 ns 完成。M3.1 七次正常运行、九项拒绝和子进程异常检查通过，原三轮时刻仍为 21552/43145/64738 ns，延迟对照 100410 ns，网络收尾仍从服务 83685 ns 等到 84207 ns。M2 单层三次运行及十三项坏输入检查通过。观测七种输入的 21 次运行、十项坏配置均通过，原正常写的数据就绪/事务完成仍为 8534/8622 ns。

初次 shared-check 退出 1：检查用整数除法截断 10 Gbps 序列化时间，将 62 字节包预期为 49 ns；锁定源码 DataRate::CalculateBitsTxTime 经 Time(int64x64_t) 的 Round 得到 50 ns。依据源码把检查公式改为最近纳秒取整，未改网络实现。

shared-check-2 的七次仿真均成功，汇总断言错误要求重叠物理 FIFO 峰值大于独立峰值，退出 1。UbPort::DequeuePacket/TriggerTransmit 在出口空时触发仲裁预取，UbRoundRobinAllocator 从入口队列搬入包；该 FIFO 不代表所有排队。保留失败及峰值数据，修正这个额外的检查假设：逐包重建不变，使用累计排队量大于两组独立之和、两组实际平均等待增加，并新增首轮逐传输时长比较。规范的共享路径、旧包保留、完成与下一轮反馈要求全部保留；没有改变输入或业务实现以规避失败。shared-final 完整通过。

final_audit.py 恢复原生 UB launcher lock，并核对 244 个旧产物/生成文件的大小和 SHA-256，全部未变。实现源码与三个最终集成所记录的哈希相符。链接审计仍有全部 52 个 ASTRA 核心对象、且只加载本次构建的 10 个 UB ns-3 库。未重跑原生 LLMServingSim 性能样例，旧产物核对不代替本轮性能验证。

本轮仅验证未校准合成单层的共享链路效应；没有扩展任意不均匀请求、零 Token 成员、完整死锁/故障、P/D、真实路由或模型校准。停止在 M3.3 completed，按持续授权本地提交、不推送；M3.4 等待用户确认。提交后的依赖检查和主仓库/相关子模块状态保存在 L/dependency-final.json 与 L/git-final.json。


## 2026-09-29：m3-4-service-boundaries

用户确认开始 M3.4，上一项 M3.3 的结果在 1cf1308，任务说明提交 76596d0。开始时 main 干净、比 origin/main 多六个提交，13 项依赖检查通过。LLMServingSim 9aed949、ASTRA e2a14f5、UB 1ba7222 及递归版本不变。本次只修改根仓库的必要实现、测试、合成输入和记录，没有安装、下载、初始化、改全局配置或推送。

日志根为 `runs/m3-4-service-boundaries-20260929T022511Z/`，下文记为 L。L/record.py 为每项命令保存 cwd、完整 argv、UTC 时间、退出码、stdout/stderr；集成目录 commands.json 进一步保存子进程命令与输入，协议失败输入亦保留。除日志专项单元外，命令工作目录均为 `/root/moe-ub-cosim`，使用原有 Linux、GCC/CMake/Ninja 和 `.venv/llmservingsim-native`。

### 实现与构建

Python 服务放开同时到达、等步数的四请求限制，允许空服务和 1～32 步请求。每 rank 每轮最多一个真实 Token；整组空闲时给本地 Scheduler 已空的成员接纳一个等待请求，其他成员继续自己的旧请求。无本地 Token 不调用虚构 batch 的 schedule/add_done，但仍提交全部成员图并处理远端专家工作。没有实际工作的阶段节点为 null，图中不生成零耗时填充 COMP；完全空闲图只有元数据。Python 和 C++ 从真实 Token 路由核对传输集合和字节，零传输只用于没有远端数据的情况。

后端在真实执行事件后检查没有未来业务事件的等待，保存组、图、rank、传输和网络资源位置。UB 已接纳但尚未形成端口流量的任务仍算待执行工作；这类状态或网络未排空而无法推进时由显式仿真上限报告失败。正式程序没有测试故障开关。前端保存异常时的请求/队列/图，EOF 回收已退出进程后记录实际退出码。根日志桥接层等待异步日志 flush 队列完成后再读取执行记录，不推进仿真时间。依赖源码没有修改。

```bash
cmake -S third_party/ns-3-ub -B build/m3-service-boundaries -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m3-service-boundaries/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m3-service-boundaries --target \
  cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence \
  cosim-lifecycle-test cosim-layer-delay-test cosim-time-test cosim-write-test \
  cosim-compute cosim-write --parallel 8
cmake --build build/m3-service-boundaries --target \
  cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence \
  cosim-lifecycle-test cosim-layer-delay-test cosim-compute cosim-write \
  cosim-logging-test --parallel 8
```

独立构建不覆盖旧输出，任何时刻只运行一个构建，最多八个编译作业。configure、build-2、build-pending-ub、build-flush-2 均退出 0；初次 build 与 build-flush 的编译失败见下文。保留上游 binary_function 弃用警告和 ns-3 独立输出目录提示。

### 最终检查与服务边界

以下集成检查的实际命令统一为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本名 --build-dir build/m3-service-boundaries --output-dir L/记录名`。表中十项最终命令均退出 0，summary.json 均为 passed；输入、运行数、原始日志与拒绝原因保存在对应目录。

| 脚本名 | 记录名 | 检查结果 |
|---|---|---|
| check_service_boundaries.py | boundaries-final | 七次正常运行、七项预期故障 |
| check_service.py | service-final | 七次正常运行、九项协议拒绝及子进程故障；原三步时刻 21552/43145/64738 ns |
| check_dynamic_service.py | dynamic-final | 九次运行、十项拒绝；原双组九轮 129517 ns |
| check_shared_bottleneck.py | shared-final | 七次共享网络对照；重叠六轮仍为 264523 ns |
| check_layer.py | layer-final | 三次运行、十三项拒绝；十四次写、114688 字节 |
| check_sequence.py | sequence-final | 五次运行、十二项拒绝；28 次写、229376 字节，旧回调隔离 |
| check_variants.py | variants-final | 九次运行、十一项拒绝；去重/分块/双发送槽仍为 12/42/42 次写 |
| check_delay.py | delay-final | 五次给定事件、两次真实 UB、十项拒绝；26/38 us 与 30/42 us 依赖反馈不变 |
| check_observation.py | observation-final | 七种输入共 21 次运行、十项坏配置；原写 8534/8622 ns 数据/事务时刻 |
| check_compute.py | compute-final | 四次计算、六项拒绝；绝对时间、资源释放与唯一链接 |

另执行 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v`，python-unit-verified 记录 23 项通过。`build/m3-service-boundaries/output/cosim/ns3.44-cosim-time-test`、同一命令加 `--wrong-resolution`、`build/m3-service-boundaries/output/cosim/ns3.44-cosim-write-test` 均退出 0，记录为 time-unit、time-resolution、write-unit。新增日志专项在 L/logging-unit 目录运行 `timeout 20s /root/moe-ub-cosim/build/m3-service-boundaries/output/cosim/ns3.44-cosim-logging-test`，退出 0：阻塞异步写线程时 flush 不提前返回，放行后执行记录已写入文件。

| 新边界输入 | 轮数 / 真实写 / 业务字节 | 实际结束（ns） |
|---|---|---:|
| sparse，来源开/关/不挂观测/重复各一次 | 每次 3 / 6 / 49152 | 45502 |
| uneven，四个不等长请求、两组 | 5 / 16 / 131072 | 59263 |
| local-only，一个本地请求 | 1 / 0 / 0 | 16000 |
| empty，无请求 | 0 / 0 / 0 | 0 |

sparse 的请求仅在 rank 0，三步路由 [2,4]、[0,1]、[6,7]，分别在 12920、28920、45502 ns 完成。第一步 rank 1/2 没有本地 Token，仍分别执行 RECV、4000 ns 专家计算、1000 ns 局部合并、SEND；rank 3 空图在激活时完成。第二步只有 rank 0 本地工作、没有业务包。第三步此前空闲的 rank 3 执行两个专家，同 rank 去重后输入与结果各一份。每个真实 Token、专家计数、局部阶段时刻、原始 ASTRA issue/callback 和收发次数均核对。

uneven 的 ep0 rank 0 请求执行三步，rank 1 请求在 5200 ns 到达、执行一步，另一个 rank 0 请求在 5500 ns 排队、执行一步。四轮实际请求集合为 [101]、[101,102]、[101]、[201]，完成于 12920、30343、43263、59263 ns。ep1 独立本地请求在 16000 ns 完成，不阻挡 ep0。仅对六次真实 Token 调用调度；ITL 包括本请求实际排队时间。已耗尽外部输入时仍继续执行，最终全部请求、图、任务、槽位和网络数据归零。

七项故障分别为：初始 EOF（0 ns）、首轮完成后的 EOF（12920 ns）、未结束前端既无工作又无未来唤醒（0 ns）、损坏空成员 rank 3 的图（0 ns、全部激活前拒绝）、独立测试目标遗漏一个成员激活（19513 ns）、仿真上限（5200 ns），以及真实后端完成首轮但通知尚未转交时被 SIGTERM 终止。前六项后端退出 1，最后一项保留 -15；均没有伪造 service_result 成功。死锁快照含 rank 3 未登记 SEND、其他 rank 对应 RECV 等待及空网络状态。上限快照保留尚未完成任务，不声称这种状态已被证明死锁；信号终止也不声称执行了 C++ 正常清理。

来源开/关/不挂观测和重复运行的协议、业务结果、生成图及原生网络日志一致；端侧文件没有内部身份，逐窗口字节分类守恒。既有收尾用例仍在 83685 ns 服务结束后等到 84207 ns 网络排空。M3.3 原共享排队、完成变化和下一步调度反馈仍通过。

### 保留的失败与修复

初次 build 退出 1：将 JSON 直接传入 std::set::insert 导致重载歧义，改为显式 uint64_t 转换。初次 python-unit 有一项新测试把局部合并耗时误写成 700 ns，而未改动的输入为 1000 ns；按输入修正该测试常量。此前针对四请求同时到达、正专家工作和至少两步的拒绝用例，因本步明确允许这些场景，改为检查负到达、空路由和非法成员；原合法基线及传输/依赖断言保留。

boundaries-check 首次在 5000 ns 误报死锁：UB 已接纳任务，但尚未进入端口发送。检查加入原生任务执行器的 pending 计数后，boundaries-check-2 全部通过。这个失败还暴露 EOF 后 waitpid 尚未就绪时清理动作可能把真实退出 1 改记为 -9，前端已在 EOF 后有限等待进程退出；最终故障测试分别核对 1 与 -15。

delay-regression 初次在读取原始执行日志时报 map::at，进程退出后的文件却包含全部 48 个节点的完成事件。检查固定 LoggerFactory 确认它采用单线程异步日志，原 FlushAstraLogging 只入队 flush、不等落盘；未改源码的 delay-regression-2 虽通过，仍不能排除这条竞态。根桥接层新增同队列的完成屏障，并用阻塞日志线程的单元验证。其测试目标首次 build-flush 缺少 extern/helper 头路径，build-flush-2 补齐后通过。最后十项集成都用修复后的构建重跑并通过，没有删除执行记录检查、放宽预期时间或改成固定通信耗时。

### 产物与停止位置

L/final_audit.py 恢复原生 UB launcher lock，核对 263 个此前产物的大小和 SHA-256；检查最终测试记录的实现源码哈希、输入、52 个 ASTRA 核心对象与本构建唯一一套 10 个 UB ns-3 库。最终依赖、差异和提交后的 Git 状态分别记录在 L/dependency-final.json、L/diff-check.json、L/git-final.json。未重跑原生 LLMServingSim 性能样例；旧产物未变不代表本轮做了性能验证。

本步完成 T09 与当前范围的 T11/T14 扩展，M3 的动态服务功能验收结束。范围仍限合成单层、一或两个四成员组、已就绪 KV、每 rank 每轮最多一个真实 Token；P/D、真实数据、容量争用、丢包恢复和模型校准未验证。停止在 M3.4 completed，按持续授权只创建本地提交、不推送；M4.1 等待用户确认。


## 2026-09-29：m4-1-pd-handoff

用户要求继续 M4.1。上一项 M3.4 结果提交为 `2eec9a8`，本步任务说明提交为 `c7b274d`。开始时 main 干净、比 origin/main 多八个提交；13 项依赖检查通过。LLMServingSim `9aed949`、ASTRA `e2a14f5`、UB `1ba7222` 及全部递归依赖保持不变。本次只修改根仓库实现、测试、合成输入和文档，没有安装、下载、初始化、改全局配置或推送。

日志根为 `runs/m4-1-pd-handoff-20260929T025813Z/`，以下记为 L。L/record.py 保存每条命令的 cwd、完整 argv、UTC 起止时间、退出码与 stdout/stderr；集成目录 commands.json 保存实际子进程参数和状态，失败用例保留输入和错误。命令工作目录均为 `/root/moe-ub-cosim`，继续使用现有 Linux、GCC/CMake/Ninja 和 `.venv/llmservingsim-native`。

### 实现边界与独立构建

新增根前端 PdService 与 KV 图生成/校验，服务入口按明确 schema 选择。合成小模型为 `cosim_tiny_moe`，一层、八专家 Top-2、hidden 256、intermediate 512、MHA 八头各 32 维、TP=PP=1。P 组 rank 0～3、D 组 rank 4～7；请求只有一个，四个 prompt Token、三个输出 Token，attention/KV 源和目标分别为 rank 0/4。预填充和解码路由均在输入中逐 Token 给出，不从解码数据推造预填充。

原生 Scheduler 的 prefill 分支在批次完成时自动释放源缓存，并依赖原生流式 KV 接收语义。根联合前端改用它的 colocated 批次接口，保留真实调度、请求进度、内存模型和块池，在 P 完成后显式暂停该组。先预留 D 块，再提交覆盖两组的 KV 图；后端校验八份图及分片，全部装载后激活。全部成员、接收回调和源事务完成后才发送一个 KV_DONE、释放两组，前端再释放 P 块并让 D.add_decode 复用目标块。未修改上游，也没有调用原生解析 KV 流量生成器或重复计时。

每 rank 总预算 4194304 字节，原生 MemoryModel 权重 2233856 字节，工作区 65536 字节、通信缓冲 8192 字节，剩余 KV 池为 115 个 BF16 块、每块 16384 字节（16 Token）。两侧各占一个块，有效数据只取四个已计算 Token：`1 × 2(K/V) × 4 × 8 × 32 × 2 = 4096` 字节，K、V 各按 768/768/512 分块。只改 KV 为 fp8 时，传输为 2048 字节、块为 8192 字节；计算、权重和 EP 激活 dtype 不变。发送端等待 transaction_done，接收端等待 data_ready，两者独立保留。源缓存释放采用较晚的交接完成边界。

网络沿用八 NPU/两交换机结构，在新样例中显式增加 0/4 的路由和双向 TP（两侧 TPN=4），不修改旧样例。KV 经 `0 → 8 → 4`，本次没有 KV 与第三个独立组竞争。计算仍复用五阶段固定功能 profile，`next_layer` 是沿用的阶段名，不表示增加模型层数；本步没有逐算子或数值前向验证。

```bash
cmake -S third_party/ns-3-ub -B build/m4-pd-handoff -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m4-pd-handoff/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m4-pd-handoff --target \
  cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence \
  cosim-lifecycle-test cosim-layer-delay-test cosim-time-test cosim-write-test \
  cosim-compute cosim-write cosim-logging-test --parallel 8
```

configure、build 均退出 0，共 521 个构建步骤。只有一个构建进程，最多八个并行作业；保留上游 binary_function 弃用警告和独立输出目录提示。原生输出未覆盖。

### 验收与回归

下表实际命令统一为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本名 --build-dir build/m4-pd-handoff --output-dir L/记录名`，十一项均退出 0，各 summary.json 为 passed。

| 脚本名 | 记录名 | 实际检查 |
|---|---|---|
| check_pd_handoff.py | pd-final | 七次正常运行、十一项预期失败；单请求完整 P/KV/D 生命周期 |
| check_service.py | service-final | 七次运行、九项协议拒绝及子进程异常；三轮仍为 21552/43145/64738 ns |
| check_dynamic_service.py | dynamic-regression | 九次运行、十项拒绝；双组九轮仍为 129517 ns |
| check_shared_bottleneck.py | shared-regression | 七次独立/重叠/延后及观测对照；重叠六轮仍为 264523 ns |
| check_service_boundaries.py | boundaries-regression | 七次运行、七项故障；稀疏 45502 ns、不均匀请求 59263 ns |
| check_layer.py | layer-regression | 三次运行、十三项拒绝；十四次写、114688 字节 |
| check_sequence.py | sequence-regression | 五次运行、十二项拒绝；28 次写、229376 字节 |
| check_variants.py | variants-regression | 九次运行、十一项拒绝；去重/分块/双槽为 12/42/42 次写 |
| check_delay.py | delay-regression | 五次给定事件、两次真实 UB、十项拒绝；必要输入推迟仍传到计算 |
| check_observation.py | observation-regression | 七种输入共 21 次运行、十项拒绝；来源开关不改变原生网络执行 |
| check_compute.py | compute-regression | 四次计算、六项拒绝；绝对时间、资源释放与链接回归 |

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v` 在 python-unit 和 python-unit-final 均为 29 项通过，其中六项新增检查覆盖真实调度与容量生命周期、字节/块区别、坏分片、布局拒绝、容量超限及未来到达前不分配缓存。`build/m4-pd-handoff/output/cosim/ns3.44-cosim-time-test`、同一命令加 `--wrong-resolution`、`build/m4-pd-handoff/output/cosim/ns3.44-cosim-write-test` 均退出 0，分别记录在 time-unit、time-resolution、write-unit。logging-test 本次构建但没有单独重跑；原生性能样例未重跑。

| P/D 输入 | P 完成（ns） | KV 最后数据就绪（ns） | KV 最后事务完成 / D 开始（ns） | 请求完成（ns） | KV 有效字节 |
|---|---:|---:|---:|---:|---:|
| on/off/absent/repeat，各一次 | 40159 | 40830 | 40876 | 69994 | 4096 |
| 仅 rank 4 链路从 20 ns 改为 2020 ns | 40159 | 62830 | 64876 | 101792 | 4096 |
| 仅 KV dtype 改为 fp8 | 40159 | 40564 | 40610 | 69728 | 2048 |
| 仅到达时刻改为 9000 ns | 49159 | 49830 | 49876 | 78994 | 4096 |

基线四份逻辑图为 P、KV、D、D，各包含 4/8/4/4 个成员图，共 20 次真实写、11264 业务字节；fp8 为 18 次写、9216 字节。预填充实际处理 Token 0～3，两个解码步处理 Token 4/5，生成三个输出 Token，TTFT 40159 ns、ITL 13835/16000 ns。最后一个输出无需再次作为输入计算，因此最终 computed=6、reached=7。每份传输逐项对应真实 target write、ASTRA issue/callback、一次 UB 任务及各一次收发回调。KV 空成员在交接开始完成，源图在最后事务完成、目标图在最后数据就绪完成，后端仍等两组全部结束。

目标容量预留、交接提交和交接完成三个快照中，两端均保留 16384 字节 KV；D 尚无 running/inflight。源释放后 D 复用同一目标块，没有再次分配或报入解析传输流量。结束时请求、图、槽位、KV 块及网络数据全部清空，已发送包与到达包数一致。延迟用例 P 完成不变，KV 事务和 D 开始同时后移 24000 ns，ITL 随实际网络反馈变化。来源开/关、不挂观测及重复运行的协议、业务结果、图与原生网络日志一致；端侧记录没有隐藏身份，端口整包分类守恒。原服务收尾回归仍从服务完成 83685 ns 等待至网络排空 84207 ns。

十一项故障包括四项前端拒绝（不兼容布局、策略、dtype、超过 D 组总容量）、六项后端拒绝（KV 大小、Q、重复范围、最后成员 rank 7 图损坏、缺少完整组、忙碌组交接）和一次真实后端信号终止。坏 KV 图均在交接激活前退出 1；最后成员损坏仍未激活任何 KV 成员。信号用例在真实后端产生 40876 ns 的 KV_DONE、尚未交给前端时终止后端并保留 -15，前端失败快照仍是 KV_INFLIGHT、两侧各占一个块，没有 service_result 成功记录；不声称信号退出执行了 C++ 正常清理。

### 审计与停止位置

初次 pd-check 的七次运行、十项拒绝均通过。随后增加模型副本、失败时的内存快照及信号退出检查，pd-final 完整通过，并重新执行服务回归和全部 Python 单元。其余变更后的相关回归均通过。本轮没有编译或仿真检查的非预期失败，故障矩阵中的非零退出均是明确构造的预期结果。

`python3 L/final_audit.py`（记录 final-audit）退出 0：恢复原生 UB launcher lock，核对 288 个旧产物的大小和 SHA-256 全部不变；pd-final、service-final、shared-regression 的 93 项源码/输入哈希与当前文件一致，程序包含 52 个 ASTRA 核心对象，只加载本构建的 10 个 UB ns-3 库。核对源码和旧产物不代替目标硬件性能验证。提交前后的依赖、差异和 Git 状态分别保存到 L/dependency-precommit.json、L/diff-check.json、L/dependency-final.json 与 L/git-final.json。

M4.1 完成 T10 的单次交接部分，并扩展当前范围的 T11/T12/T14。合成模型、路由及计算参数仍标记 unvalidated_reference；多请求交接排队、当前空闲容量争用、KV 与独立组竞争、真实模型数据、MLA/布局转换及校准尚未验证。停止在 M4.1 completed，按持续授权本地提交、不推送；M4.2 等待用户确认。

## 2026-09-29：m4-2-pd-queue

用户授权按顺序完成 M4.2、M4.3，分别验证并本地提交，不推送。M4.1 完成提交 aef846e，本步任务说明 8d2bfb5；开始时主仓库干净、比 origin/main 多十个提交。LLMServingSim 9aed949、ASTRA e2a14f5、UB 1ba7222 和其余递归依赖不变。没有安装、下载、初始化依赖或修改全局配置。

日志根 `runs/m4-2-pd-queue-20260929T032759Z/` 以下记为 L。所有命令 cwd 为 `/root/moe-ub-cosim`，L/record.py 为每条命令保存完整 argv、UTC 起止、退出码及 stdout/stderr；集成目录 commands.json 记录各子进程和输入，异常输入、错误及已有日志保留。

### 改动与构建

新增根前端 QueuedPdService，复用原生请求、批次与块池，实现 FIFO 交接。P 完成后保留源 KV 和整个 P 组，D 在边界优先处理能预留容量的队首；不足时继续已有解码释放容量，不被待交接图占用。目标容量预留覆盖到最后一个解码输入所需的块，防止已接纳请求因后续增长被抢占；实际传输仅包含已经计算的 prompt KV。KV_DONE 后释放源、D 复用目标块，全部源交接完成后 P 才开始新一轮。当前最多八个 P/D 请求及八个独立组请求，P 每轮最多 16 Token，解码每 rank 每步一个 Token。

联合后端扩展到三个四成员组、十二个 rank，通过显式成员并集识别两个交接组，不把所有部署成员都卷入 KV 图。KV 图仍全部校验、装载后激活，完成通知只释放参与组，第三组图、网络与队列保留。原 M3、M4.1 前端继续使用原 schema 和边界。唯一变化的既有检查辅助函数是 audit_queue 增加带宽/端口参数，默认仍是 M3.3 的 10 Gbps、8:4。

```bash
cmake -S third_party/ns-3-ub -B build/m4-pd-queue -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m4-pd-queue/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m4-pd-queue --target \
  cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence \
  cosim-lifecycle-test cosim-layer-delay-test cosim-time-test cosim-write-test \
  cosim-compute cosim-write --parallel 8
```

configure、build 均退出 0，共 519 步；使用一个独立构建、最多八个编译作业。上游弃用警告和独立输出目录提示保留。

### 真实交接队列与竞争

模型复用 M4.1 的合成单层 tiny MoE。P 请求 501/502 各有八个 prompt Token，在 rank 0/2 进行本地专家预填充，121000 ns 同时完成；同时完成按请求编号入队。D 目标 rank 5 只有一个 16384 字节容量块（总预算 2323968，权重 2233856，工作区 65536，通信缓冲 8192）。每请求实际 KV 为 8192 字节，K/V 各按 3072/1024 分块；没有把未来容量或块填充字节放入网络。

十二 NPU 的偶/奇 rank 分别接交换机 12/13，接入 400 Gbps，交换机间 1 Gbps，链路 20 ns。rank 8/10 的独立请求在 115000 ns 到达，各执行三步；KV 与其 Dispatch 共享 12:6 出口。来源开关、控制类别和优先级均沿用原默认配置。

| 场景 | 写次数 / 业务字节 | 首个 KV 完成与 D 开始（ns） | 服务完成（ns） |
|---|---|---:|---:|
| queue，仅 P/D | 16 / 20480 | 195784 | 365024 |
| independent，仅独立组 | 24 / 12288 | 不适用 | 257656 |
| overlap；另有 off/absent/repeat 各一次 | 40 / 32768 | 221927 | 427163 |
| staggered，独立组到达改为 600000 ns | 40 / 32768 | 195784 | 742656 |
| roomy，仅 P/D，目标增加一块 | 16 / 20480 | 195784；首个 D 等第二交接至 271002 | 365458 |
| late，仅 P/D，130000 ns 新到达第三请求 | 24 / 30720 | 195784 | 560808 |

queue 第二请求在 P 保留期间等待，D 第一个请求于 243012 ns 结束释放块，第二交接同时开始，于 317796 ns 完成。overlap 第一个 KV 完成和 D 开始比独立 P/D 后移 26143 ns，第二交接到 279599 ns 才能开始。roomy 在两次交接之间不启动 D 图，验证有空间时的边界优先规则；其总耗时略高，未假定增加容量必然降低延迟。late 的第三请求在忙碌 P 组排队，到第二交接完成 317796 ns 才开始预填充，与此时独立的 D 解码可同时运行。

原生 EP 业务包 UID 1 在 120035 ns 入队、124754 ns 出队，跨越 121000 ns 的 KV 图启动。KV 包在选定物理 FIFO 的平均等待从 345.25 ns 增为 1545.75 ns；逐包重建与原生 Port Tx、总字节和排队面积相符。该 FIFO 不包含 VOQ、信用等待等全部排队。逐窗 KV/EP 有效字节混合、传输起止及 D 反馈均保存；错开无 KV/EP 混合窗口且 P/D 时刻恢复 queue。来源开启、关闭、不挂观测与重复的业务、协议和原生网络日志相同。

### 验收、修正与审计

以下命令统一为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本名 --build-dir build/m4-pd-queue --output-dir L/记录名`，十二项最终命令均退出 0，summary.json 为 passed：

| 脚本 | 记录名 | 范围 |
|---|---|---|
| check_pd_queue.py | queue-verified | 九次正常运行、八项拒绝，FIFO/容量/共享竞争与指标 |
| check_pd_handoff.py | pd-regression | 七次正常运行、十一项失败，M4.1 原时刻不变 |
| check_service.py | service-regression | 七次运行、九项协议拒绝及子进程失败；三轮 64738 ns |
| check_dynamic_service.py | dynamic-regression | 九次运行、十项拒绝；双组九轮 129517 ns |
| check_shared_bottleneck.py | shared-regression | 七次对照；共享六轮 264523 ns |
| check_service_boundaries.py | boundaries-regression | 七次正常、七项故障；稀疏 45502 ns、不均匀 59263 ns |
| check_layer.py | layer-regression | 三次正常、十三项拒绝；十四次写 114688 字节 |
| check_sequence.py | sequence-regression | 五次正常、十二项拒绝；28 次写 229376 字节 |
| check_variants.py | variants-regression | 九次正常、十一项拒绝；12/42/42 次写 |
| check_delay.py | delay-regression | 五次给定事件、两次真实 UB、十项拒绝 |
| check_observation.py | observation-regression | 21 次正常、十项拒绝；原生网络无扰动 |
| check_compute.py | compute-regression | 四次计算、六项拒绝及唯一链接 |

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v` 最终 python-unit-verified 记录 33 项通过。`build/m4-pd-queue/output/cosim/ns3.44-cosim-time-test`、加 `--wrong-resolution`、以及 `ns3.44-cosim-write-test` 分别在 time-unit、time-resolution、write-unit 退出 0。

初次 queue-check 的网络、容量和 FIFO 检查通过，但审阅原生 Request/add_done 后发现新前端错误使用 is_init=False 创建了预填充请求，TTFT 为 -1、首次 prefill 完成被记入 ITL。修正为 P/D 请求 is_init=True，独立组给定 KV 请求继续 False；新增 TTFT、ITL 数量及绝对时刻断言。queue-final 与后续 queue-verified 均通过，原有网络时刻完全不变。最终容量拒绝用例改为真实的单请求长度超过目标总块数，直接覆盖接纳前检查，而不是只让内存预算小到无法创建一个块。此前日志完整保留，没有修改业务耗时或放宽断言。

八项拒绝为请求超容量、组映射错误、身份重复、通信缓冲不足，以及坏 KV 字节、rank 7 图损坏、部分组、忙碌组交接。前四项在启动后端前失败，后四项退出 1、KV 图未激活；已有交接中信号退出检查由 M4.1 回归覆盖。所有成功运行都核对真实写/回调次数、实际 Token、容量守恒、唯一身份、图不重叠占 rank、网络排空和来源隔离。

final-audit 恢复原生 launcher lock，313 个旧产物大小/SHA-256 未变，核对最终实现与输入哈希、全部 52 个 ASTRA 核心对象及唯一一套 10 个 UB ns-3 库。依赖检查覆盖 13 项；提交前后结果与 Git 状态记录在 L/dependency-precommit.json、L/dependency-final.json、L/git-final.json。未重跑原生性能样例，不把功能正确当作模型校准。

M4.2 完成并按持续授权本地提交、不推送。用户已确认整个 M4 的顺序执行，下一步切换 M4.3；本步没有真实路由、目标 profile、缓存转换或 P/D 组内异步重叠。

## 2026-09-29：m4-3-data-inputs

按用户本次授权继续 M4.3。M4.2 完成提交 43a984f，M4.3 任务说明提交 5deef58；开始时主仓库及 LLMServingSim、ASTRA、UB 工作区干净。依赖仍为 9aed949 / e2a14f5 / 1ba7222，其余递归提交不变。本步只改根 Python 前端、输入、测试与记录，没有改后端、依赖或锁文件，没有安装、下载模型/数据或修改全局配置。

日志根 `runs/m4-3-data-inputs-20260929T035216Z/` 以下记为 L。命令 cwd 均为 `/root/moe-ub-cosim`。L/record.py 保存完整 argv、UTC 起止、退出状态和 stdout/stderr；各集成目录 commands.json 保存子命令及输入。复用 `build/m4-pd-queue/output/cosim/ns3.44-cosim-service`，本步没有重新编译；结束检查原生及联合旧产物的大小、摘要和实际链接库。

### 材料与实现范围

在本地固定材料中找到实际 Mixtral-8x7B-v0.1 配置、架构 YAML 和 H100 历史 TP1 非 attention CSV。没有找到实际专家选择轨迹；请求 JSONL 的 Token/长度字段不能替代专家路由。已询问外部材料位置，本轮未取得额外数据，因此连续片段明确使用手工合成记录，P/D 仍保留小型合成模型。

新增 DataService 和数据适配模块，复用已有真实 Scheduler 与服务图提交流程。原 Service 只提取原样的调度器创建和图生成方法，新 schema 才选择新的容量和数据校验。依次使用 model-stage、route-stage、compute-stage 三个输入；其余配置保持相同。执行仍为真实第 7 层的单层片段，完整 32 层只做结构及容量核对；清单明确 full_model_execution_validated=false、latency_scope=selected_layer_iterations_only_not_full_model_ITL_or_TTFT。

模型审计保留原配置和摘要、32 个 MoE 层的位置、八专家 Top-2、GQA 32/8 头与 128 head_dim、EP4/TP1/PP1 放置。每 rank 复制共享权重并持有每层两个专家，完整权重 25759850496 字节，完整模型 KV 为 131072 字节/Token、2097152 字节/16-Token 块。每设备预算 85899345920 字节，工作区 1073741824，通信缓冲 1048576，KV 28164 块，剩余 516096 字节不能组成整块；独立算式与真实原生池一致。每个请求在启动前检查剩余生命周期容量，每次 DECIDE 检查实际使用，最大实际占用两块，结束 KV 全释放。工作区为假设预算，未验证目标设备实际可部署性。

路由导入要求确切文件摘要、声明来源、模型和索引口径，恰好覆盖所选层各请求的连续片段。按 source_token_index 减 prompt_tokens 规范化解码索引，保留源文件行号、source_index 和原 Token 位置；非零起点单测也通过。每条记录只消费一次，缺失/重复/错误 phase、层或专家均拒绝，不补齐或复制路由。输入证据在 input_evidence.json，实际图的 input_provenance 保存本轮消费记录，传输的 source_record_index 对应同序 Token/route_records。内部来源不改变业务包。

历史 CSV 的 dtype 未独立写在文件中。审阅其 profiler 源码确认 float16、从配置初始化模型、没有加载 checkpoint；设备名称来自固定 H100 路径，缺少逐次硬件环境及层身份。因此记录为历史形状参考，不能称为目标 NPU profile。三个阶段均显式 FP16；原配置 BF16 另行保留。

只引用 input=1、kv_cache=0、TP1 的六个不读取 KV 上下文的算子：input_layernorm、Q/K/V/O 投影、post_layernorm，CSV 行 3/4/5/6/8/9，总计 82911 ns。输入 `[1,4096]`，K/V 输出 `[1,1024]`；每 rank 每轮恰好一个 Token，错误 dtype、缺行和重复键拒绝。prepare 替换为该和加 5000 ns 的显式剩余参考值，共 87911 ns，不重复保留旧阶段或添加访存时间。attention、RoPE、路由/打包、专家与合并等仍用功能参数；没有引用不匹配的 TP4 attention，也没有外推或换算 NPU。三个来源状态独立记录，calibration_status 始终 unvalidated_reference。

### 实测和命令

以下统一为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本名 --build-dir build/m4-pd-queue --output-dir L/记录名`，最终均退出 0：

| 脚本 | 记录名 | 结果 |
|---|---|---|
| check_data_inputs.py | data-verified | 六次正常、六项预期拒绝；三阶段与开关/重复检查 |
| check_pd_handoff.py | pd-verified | M4.1 七次正常、十一项故障通过，原时刻一致 |
| check_pd_queue.py | queue-verified | M4.2 九次正常、八项拒绝，原队列/竞争时刻一致 |
| check_service.py | service-verified | 三轮、协议和延迟反馈，64738 ns |
| check_dynamic_service.py | dynamic-verified | 双组九轮、动态到达，129517 ns |
| check_shared_bottleneck.py | shared-verified | 七次共享对照，重叠 264523 ns |
| check_service_boundaries.py | boundaries-verified | 稀疏、不均匀、空输入和故障均通过 |

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v` 在 python-unit 和 python-unit-verified 中均为 39 项通过，新增六项包含数个输入拒绝子例。data-unit 为仅六项新增检查，退出 0。profile 精确查表缺行和重复行测试在单测中替换读取结果，没有改依赖 CSV。

| 数据阶段 | 写次数 | 业务字节 | 片段完成（ns） |
|---|---:|---:|---:|
| 实际模型审计，固定重复路由，固定计算 | 42 | 344064 | 64738 |
| 仅改为逐步变化的连续合成路由 | 28 | 229376 | 54900 |
| 仅替换六项计算数据及声明剩余值 | 28 | 229376 | 303633 |

模型、容量、请求、部署和网络保持相同；第一轮路由相同，后两轮目的 rank 去重及本地专家比例改变，减少真实业务写。计算阶段全部操作、Token 和传输身份/字节不变，仅 prepare 计算增加 82911 ns/轮，完成恰好增加 `3 × 82911 = 248733` ns。六次运行均有同一套真实 UB 网络、正确 ASTRA 依赖及实际回调；请求和源索引逐步对应，最后已发送网络数据全部到达。来源开、关、不挂观测及重复的图、前端结果、协议、后端结果和原生网络日志相同；开/关的端侧及端口观测相同。

六项真实 CLI 拒绝为路由摘要错误、FP16 profile 配 BF16、模型/预留后容量不足、片段长度与路由多项、来源模型不匹配、通信缓冲不足，均在后端运行前明确失败。单元还检查缺失、重复、错误层/phase/专家/源索引、模型尺寸与 profile 缺项等。

### 错误保留、审计与停止位置

compute-smoke 初次命令错误地使用了不存在的简写后端路径（system/network 参数路径也需更正），Popen 退出 1，未启动仿真；failure.json 和命令日志保留。compute-smoke-corrected 使用实际 output/cosim 二进制、m1_compute/system.json 和 network 目录后退出 0，完成 303633 ns。首次 data-integration 的 model 仿真和业务断言通过，但检查脚本引用了不存在的 observations/port_windows.jsonl；按已有 observed 输出目录修正为全部观测文件摘要后，data-integration-corrected 和 data-verified 全部通过。没有删除业务断言或修改预期时刻来绕过失败。

首次 final-audit 正确发现：补记 profiler 从配置构造权重、CSV 无层身份两项来源字段后，六份较早回归的全源码摘要已经过时。保留该失败，重新执行六项回归及单元检查（*-verified），确认最终源码对应实际通过的结果。随后 final-audit-verified 检查 329 个旧产物大小和 SHA-256 未变，原生 launcher lock 未变，最终数据检查记录的源文件/输入摘要匹配；52 个 ASTRA 核心对象和本次已验证 M4.2 构建的唯一 10 个 UB ns-3 库保持一致。依赖检查仍覆盖 13 项，差异和最终 Git 状态记录在 L/diff-check.json、L/dependency-final.json、L/git-final.json。本步没有重跑原生性能样例，旧产物核对不代替性能验证。

M4.3 当前数据导入与部分参考数据替换验收完成，按授权本地提交、不推送。实际专家路由、目标 NPU profile、完整多层执行和目标通信校准仍未验证，不能把本步称为真实部署复现。停止在 M4.3 completed；用户授权的 M4 顺序执行到此结束，M5 未开始。


## 2026-09-29：m5-1-observation-export

用户确认开始 M5.1；M4.3 完成提交 ed5239f，M5.1 任务说明提交 91b0b77。开始时主仓库与相关子模块干净，依赖仍为 LLMServingSim 9aed949、ASTRA e2a14f5、UB 1ba7222，全部 13 个递归依赖保持固定提交。本步只修改根后端的被动采集、Python 研究导出、输入、测试与文档；没有改依赖源码、gitlink 或锁文件，没有安装、下载模型数据或修改全局配置。

日志根 `runs/m5-1-observation-20260929T055735Z/` 以下记为 L，命令 cwd 均为 `/root/moe-ub-cosim`。L/record.py 保存完整 argv、UTC 起止、退出码、stdout/stderr；集成目录的 commands.json 另记实际输入及全部子命令。新建 build/m5-observation，保留旧原生和联合构建。以下配置和构建实际退出 0，对应 configure、build 记录：

```bash
cmake -S third_party/ns-3-ub -B build/m5-observation -G Ninja -DCMAKE_BUILD_TYPE=release -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m5-observation/output -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m5-observation --target cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence cosim-lifecycle-test cosim-layer-delay-test cosim-time-test cosim-write-test cosim-observation-test cosim-compute cosim-write --parallel 8
```

首次构建 522 步，锁定依赖的 deprecated binary_function 等编译警告保留。之后仅增加后端实际 RNG 参数记录，`cmake --build build/m5-observation --target cosim-service cosim-service-deadlock-test --parallel 8` 在 build-rng-record 中四步完成，退出 0。最终 service 二进制 SHA-256 为 4c4da8573b98990aa43ba0ffdca020a4e2409503ecdbe4ed18b0b81a28776817。

### 采集、时钟和输出

原 service 入口新增多端口观测参数列表，沿用已有 PortObservation 的实际发送开始挂接点，同时采集 8:0、8:4、9:4 三个出口。后端内部 packet-events.jsonl 保留全局时刻、包 UID、整包和业务字节；原单端口格式不变。没有额外仿真事件、包标签或业务传输。ASTRA 原始阶段产生端侧开始/完成记录，输出时才转换设备时间，各 rank 独立从 1 编号。

Python 的时钟为 `((1000000000 + drift_ppb) * t_ns) // 1000000000 + offset_ns`，全程整数 floor；offset 和本地结果为有符号 64 位，漂移限制为 -999999999～1000000000 ppb，量化后非递减。每个 rank 与被观测交换机恰好一个模型，交换机各端口共用；本地半开观测区间在全局 0～max_simulation_ns 的变换范围内，正周期、最多 16 个端口及每端口 4096 窗口。只生成声明区间，保留零值与尾部不足整周期的窗口，采样起点不等于隐式全局零点。

新增实际入口为 `python -m moe_ub_cosim.research run/validate-run/validate-observed`，配置和命令见 [M5.1 输入说明](../fixtures/m5_observation/README.md)。当前配置只接受 service，复用已有普通 EP、P/D 和数据服务；seed 固定 10，记录并核对后端实际 seed=10/run=1，不声称支持其他 seed。mode=fixture/research 只说明用途。

正式目录为 manifest、inputs、graphs、observed、truth、metrics、logs。只有根 observed 的三个文件可供关联算法读取：端侧五字段、实际整包端口窗口 CSV、严格白名单的静态配置。真实逐 Token/KV 来源、图/操作/传输身份、全局时刻和时钟参数只进入内部资料。旧服务输出及每端口早期格式均在 logs/raw-run 内，属于内部记录。

truth 记录操作到本地端侧编号的绑定、业务传输/实际完成事件、正业务字节的操作—端口—本地窗归属以及设备时钟。record_provenance=false 时操作/传输/归属文件为空记录，CSV 保留表头，归属校验记为 disabled；不声称已检查来源。metrics 分开检查唯一交付与每窗整包分类总和，来源打开时每个业务包必须有匹配的任务/包内范围。控制包只贡献整包计数，不虚构业务归属；多端口业务经过量不等同于唯一交付。

manifest 记录项目和 13 个依赖提交、源码/后端/输入/产物摘要、实际随机设置、单位、计数位置、模板、完成语义、分块/并发、时钟文件与计算数据范围。validate-run 重建输出核对内部数据，validate-observed 只读三个观测文件，拒绝额外字段/文件、符号链接及缺失/重复/乱序窗口。测试将 observed 单独复制并限制文件读取位置后通过；这是访问契约，不是操作系统权限隔离。

### 实测与回归

最终所有测试使用以下形式，退出 0；各行给出实际脚本和输出记录名：

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本名 --build-dir build/m5-observation --output-dir L/记录名`

| 脚本 | 记录名 | 检查范围 |
|---|---|---|
| check_research_export.py | research-checked | 九次正常研究运行、一次无观测对照、十五项预期失败；精确本地时钟、窗口、真值与访问边界 |
| check_observation.py | observation-regression | 原单端口 21 次运行、十项拒绝，整包计数和业务范围 |
| check_layer.py | layer-regression | 十四次真实写、八份专家输入及图原子拒绝 |
| check_sequence.py | sequence-regression | 连续两轮、旧回调、来源无扰动 |
| check_variants.py | variants-regression | 去重、分块、单双发送槽，12/42/42 次写 |
| check_delay.py | delay-regression | 五次给定事件、两次真实 UB、十项拒绝 |
| check_compute.py | compute-regression | 四次计算、六项拒绝及链接边界 |
| check_service.py | service-regression | 实际调度、协议/故障、延迟反馈，64738 ns |
| check_dynamic_service.py | dynamic-regression | 忙碌到达排队、组间独立及唤醒，129517 ns |
| check_shared_bottleneck.py | shared-regression | 七次竞争/错开对照，重叠 264523 ns |
| check_service_boundaries.py | boundaries-regression | 稀疏、零 Token、空输入、退出及死锁位置 |
| check_pd_handoff.py | pd-regression | 七次正常、十一项故障，P/KV/D 原时刻一致 |
| check_pd_queue.py | queue-regression | 九次正常、八项拒绝，容量、FIFO 与第三组竞争 |
| check_data_inputs.py | data-regression | 六次正常、六项拒绝，三阶段输入与逐项替换 |

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v` 在 python-unit-final 中 45 项通过，含六项新增时钟/窗口/访问检查；clock-unit 也单独通过。`build/m5-observation/output/cosim/ns3.44-cosim-observation-test` 在 observation-unit 退出 0。

| 最终研究用例 | 完成（ns） | 写次数 | 唯一业务字节 | 端侧记录 | 窗口 / 零窗口 |
|---|---:|---:|---:|---:|---:|
| 双组共享网络 on/off/repeat | 264523 | 84 | 688128 | 48 | 276 / 151 |
| 零偏移零漂移 | 264523 | 84 | 688128 | 48 | 274 / 151 |
| 裁剪观测区间 | 264523 | 84 | 688128 | 48 | 81 / 65 |
| 实际首包恰好落边界 | 264523 | 84 | 688128 | 48 | 76 / 37 |
| P/D 与 KV 交接 | 69994 | 20 | 11264 | 32 | 276 / 263 |
| 空服务 | 0 | 0 | 0 | 0 | 276 / 276 |
| M4.3 compute-stage，mode=research | 303633 | 28 | 229376 | 24 | 101 / 94 |

独立 Fraction 算式核对所有端侧时间与原始 ASTRA 节点事件，包含负本地时刻及大于 2^53 的单元边界；Decimal 解析原生 Port Tx 日志与内部包事件逐条一致，再独立计算每窗整包和业务归属。真实边界用例将首包精确放在第二窗口起点，前窗口 0 包、后一窗口 1 包。来源开/关、零时钟、裁剪/边界、重复和不挂观测的业务图、协议、ASTRA 时刻与原生网络日志一致；on/off/repeat 的三个正式 observed 文件摘要相同。

十五项拒绝含八个坏配置（非单调/溢出/缺失或重复时钟、重复端口、零周期、越界区间、无效物理端口）、四类坏观测（缺窗、隐藏端侧身份、隐藏时钟字段、额外文件）及三类内部损坏（产物摘要变化、错误操作绑定、丢失包来源）。后两项主动更新损坏文件的 manifest 摘要以单独检查语义验证，仍明确失败；未知物理端口保留失败 manifest 和日志，不能输出正常零流量。

### 失败原因、产物审计与停止位置

research-smoke 的初始采样起点等于时钟偏移，276 个窗口含 154 个零值；随后将起点独立设为 -7333/18539 ns，最终为 151 个零值，两版业务均为 264523 ns。research-integration 首次比较误把 Python 字节码时间戳和 protoc 输出目录当成业务图差异；检查脚本改为逐项比较 Chakra/计划/生成与 schema 源码，单独只归一化编译记录中的本次输出路径，保留业务内容断言。

research-integration-corrected 在 PD 用例失败：误用了不含 0→4 TP 的 M3 网络，后端在 40159 ns 报 Could not reserve a TP connection 并信号退出。用例改为已有 M4.1 网络（相同拓扑且明确提供该 TP），不在运行时补连接或改用替身。research-verified 的空服务已成功导出，但测试直接打开不存在的原生端口 trace 失败；原生 trace 按事件创建，测试现仅在明确空请求、零目标写、零事件且原生计数为零时接受其不存在，非空缺日志仍失败。上述失败记录均保留。research-final 正常通过后补充三项内部损坏校验，最终 research-checked 全部通过，没有改变业务时间或删除失败断言。

final-audit 恢复本轮 CMake 覆盖的原生 launcher lock，329 个旧产物大小与 SHA-256 不变，275 项最终源码/输入摘要匹配通过记录。核对全部 52 个 ASTRA 核心对象、实际装载的唯一 10 个 ns-3 库均来自新 UB 构建；清单三个计数位置和输入摘要准确。依赖检查仍为 13 项，提交前后的差异与 Git 状态保存在 L/dependency-precommit.json、L/diff-check.json、L/dependency-final.json、L/git-final.json。没有重跑原生性能样例，旧产物未变与功能测试不能替代模型校准。

M5.1 当前范围验收完成，按持续授权本地提交、不推送，停止在 M5.1 completed。M5.2 尚未开始，仍需用户确认。真实专家路由、目标 NPU profile、完整多层执行和目标通信校准尚未验证；所有用途均保持 unvalidated_reference，未实现关联算法。


## 2026-09-29：m5-2-multilayer-continuous

用户确认开始 M5.2。M5.1 完成提交 5751cce，M5.2 任务说明提交 a779d3b；开始时主仓库与相关子模块工作区干净。依赖仍为 LLMServingSim 9aed949、ASTRA e2a14f5、UB 1ba7222，13 个固定递归提交不变。本轮只改根图生成、计划校验、数据服务、端侧事件导出、研究清单和对应测试/文档；未修改依赖、锁文件或规范，没有安装、下载数据或修改全局配置。

日志根 `runs/m5-2-multilayer-20260929T063002Z/` 以下记为 L，cwd 均为 `/root/moe-ub-cosim`。L/record.py 保存实际 argv、UTC 起止、退出码与 stdout/stderr，各集成目录 commands.json 保存子命令，失败后端重放另存 stdin.jsonl。新建 build/m5-multilayer，最多八个构建作业；旧构建产物不覆盖。configure 与 build-corrected 实际命令均退出 0：

```bash
cmake -S third_party/ns-3-ub -B build/m5-multilayer -G Ninja -DCMAKE_BUILD_TYPE=release -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m5-multilayer/output -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m5-multilayer --target cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence cosim-lifecycle-test cosim-layer-delay-test cosim-time-test cosim-write-test cosim-observation-test cosim-compute cosim-write --parallel 8
```

首次 build 在本轮新增 C++ 代码中失败：set<string>::insert 接收 JSON 值时重载歧义。明确调用 get<string>() 后 build-corrected 剩余 503 步完成；原失败日志保留，未改变验证条件或依赖。锁定 Chakra 的 binary_function 弃用等警告仍保留。最终后端 SHA-256 为 0f4fadf51793290236d25899c6a7e8e5221ffa31223975be79929d9a2e3767c2。

### 实现范围与模型依据

原 DataService 每次 Token 只生成一层，不能用它的三步单层结果代替多层。新增 moe-ub-multilayer-service/1，在已有模型/容量核对、请求队列和真实 Scheduler 上导入连续层段。一个解码步生成一份多层计划和每 rank 一张 Chakra 图；全部层完成才调用一次 add_done，不在层间重新组批、握手或初始化网络。

层间只连接本 rank 前层末节点到下一层根节点，空层传递之前的末节点集合。旧 next_layer 参考耗时在多层配置中强制为零并省略节点，由实际下一层计算衔接；其他四个阶段仍为显式正参考值，不计固定网络等待。单层校验允许 next_layer=0 表示省略，原单层输入与原时刻保持不变。

Python 合并局部节点号、分配运行内唯一传输/任务/tag，保存逐层计划、输入和来源。新增 C++ 多层校验逐层复用原单层语义检查，再独立重建全部映射和本地边界，比较合并图及所有 Chakra 文件，校验通过后才原子装载和激活。分层计划只用于审计，实际执行的是合并图。坏层序、跨层重号或末 rank 坏图不能让前面 rank 提前执行。

端侧记录按每 rank/每层的实际 ASTRA phase_nodes 与层入口前驱产生，不从包倒推；跨层、跨图记录号独立绑定，正式观测继续使用 M5.1 的本地编号和本地时钟。研究清单明确 executed_layer_ids、latency_scope 与 full_model_execution_validated，全局层/图/源记录不进入 observed。

主样例使用已核对的实际 Mixtral 结构、一个四成员 EP 组、第 6/7/8 层及四个给定 KV 请求，每请求三个解码步骤。四个请求规范化解码起点分别为 1、2、0、1；routes.jsonl 的 36 条记录各自包含请求、绝对 Token 位置、层和 source_index 4000～4035。导入保留文件摘要、源行号及原位置，按本次实际批次逐层消费且不补缺项。业务来源下标以所属层的 route_records 为范围，另有 layer_id 区分；服务历史的 tokens 保留调度器绝对位置，层计划 tokens 使用解码索引。

没有取得新的真实专家轨迹，本样例手工声明远端参考、同目的 rank 去重、本地专家三张表，并在三步按层轮换 A-B-C / B-C-A / C-A-B。运行时分别读每层记录，不复制首层。各层采用明确的固定参考计算，未外推历史 H100 profile，未加载模型权重或执行数值前向。容量沿用完整 32 层权重/KV 审计；通信缓冲按层数保守扩大下界，实际原生池和预算仍逐次核对。

### 实测与检查命令

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.research run --config fixtures/m5_multilayer/run.json --backend build/m5-multilayer/output/cosim/ns3.44-cosim-service --output-dir L/research-smoke` 退出 0，实际 193789 ns 完成。此前 python-smoke 只检查前端首步生成和十二条路由消费，不作为真实仿真结果。新增完整命令与字段见 [M5.2 样例](../fixtures/m5_multilayer/README.md)。

以下集成检查统一使用 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本名 --build-dir build/m5-multilayer --output-dir L/记录名`，全部实际退出 0：

| 脚本 | 记录名 | 范围 |
|---|---|---|
| check_multilayer.py | multilayer-integration | 八次研究运行、无观测对照、十项拒绝，逐层依赖/来源/实际完成/端侧与端口 |
| check_layer.py | layer-regression | 原单层十四次写、专家输入及原子图校验 |
| check_sequence.py | sequence-regression | 两步图生命周期、旧回调、来源无扰动 |
| check_variants.py | variants-regression | 去重、分块、发送槽 |
| check_delay.py | delay-regression | 给定事件与真实 UB 依赖反馈 |
| check_service.py | service-regression | 调度/协议/延迟反馈，三步 64738 ns |
| check_dynamic_service.py | dynamic-regression | 动态到达和独立组，129517 ns |
| check_shared_bottleneck.py | shared-regression | 共享网络七个对照，重叠 264523 ns |
| check_service_boundaries.py | boundaries-regression | 稀疏/不均匀/纯本地/空输入与故障 |
| check_pd_handoff.py | pd-regression | P/D 单次交接，原就绪/完成/解码时刻保持一致 |
| check_pd_queue.py | queue-regression | FIFO、容量等待及第三组竞争 |
| check_data_inputs.py | data-regression | M4.3 单层三阶段，64738/54900/303633 ns |
| check_research_export.py | observation-regression | M5.1 九个正常研究运行、无观测对照和十五项拒绝 |
| check_observation.py | m1-observation-regression | 原单端口 21 次运行、十项拒绝，实际整包计数 |

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v` 在 python-unit 中 51 项通过；`-p multilayer_test.py` 在 multilayer-unit 中新增六项通过。包含局部跨层依赖、空 rank/空层连续性、逐层路由完整性、错误 profile、重复身份与编号耗尽时状态不变。

| 多层用例 | 层数 / 组批轮数 | 源路由数 | 写次数 | 唯一业务字节 | 完成（ns） |
|---|---:|---:|---:|---:|---:|
| on / repeat / off / zero | 3 / 3 | 36 | 78 | 638976 | 193789 |
| 仅 rank 1 接入传播时延改为 2020 ns | 3 / 3 | 36 | 78 | 638976 | 256536 |
| 两请求，第二请求 1000 ns 到达 | 3 / 4 | 18 | 42 | 344064 | 216871 |
| 分块上限 3072 字节 | 3 / 3 | 36 | 234 | 638976 | 199717 |
| 第 0～31 层、每请求两步 | 32 / 2 | 256 | 558 | 4571136 | 1367802 |

主例各层对应的 14/12/0 次业务写随步轮换，每步交付 212992 字节；全部 Token/层/专家与源记录逐条核对。本地专家计入计算而不产生写，分块仅改变写次数和网络过程，不复制专家计算。全部请求恰好消费三步，完成时刻为 64310、128538、193789 ns；相应间隔为 64310、64228、65251 ns。主例 18 个内部操作对应 72 条端侧记录、2004 个窗口，其中 1961 个为零。

五处实测层间重叠证明没有新增全组屏障，例如首步下一层最早于 16675 ns 开始，而前层全组最晚在 20552 ns 结束。网络延迟仅变 rank 1 链路，其图/业务量不变，首个完整 Token 由 64310 ns 后移至 86274 ns，下一步提交随之移动，最终增加 62747 ns。稀疏例第二请求先排队，完成四轮组批后两个请求各自完成三步；零 Token 成员保留必要专家工作。

相同输入重复、来源开关、零时钟及无观测运行的业务图、协议、前端/后端结果和原生网络日志完全一致；on/off/repeat 的三个 observed 文件摘要一致。独立 Fraction 时钟算式核对逐层端侧事件，Decimal 解析原生端口发送 trace 再重算每窗整包与业务归属。所有路由恰好消费一次，唯一交付、端口整包分类与操作归属分别通过；请求/KV/执行资源/任务/链路队列在正常结束前全部清空。

十项预期拒绝为缺路由、重复路由、错层、层序错误、保留 next_layer 占位，以及后端跨层依赖损坏、跨层 tag 重号、层序倒置、末 rank Chakra 截断和 10000 ns 仿真上限。前五项在后端创建前失败；后端四种结构错误在 0 ns 被拒绝且 g0 无实际执行，超限在 10000 ns 保存未完成传输并报错。没有通过删除断言、补路由或固定通信耗时绕过。

### 审计与停止位置

final-audit 恢复本轮 CMake 覆盖的原生 launcher lock，361 个原生与旧联合产物大小/摘要未变，331 项最终源码/输入摘要匹配通过记录。全部 52 个 ASTRA 核心对象保留，实际只装载新构建的一套 10 个 UB ns-3 库。固定依赖检查、差异和提交后 Git 状态保存在 L/dependency-precommit.json、L/diff-check.json、L/dependency-final.json、L/git-final.json。没有重跑原生性能样例，也没有改动子模块提交。

M5.2 的多层连续功能数据管线验收完成，本地提交、不推送，停止在 M5.2 completed。32 层测试覆盖全部 MoE 块的依赖与通信，但没有 embedding/输出头等完整模型算子图、真实路由和目标 NPU profile；仍保留 full_model_execution_validated=false、multilayer_segment_iterations_not_full_model_ITL_or_TTFT 与 unvalidated_reference。M5.3 目标通信行为核对尚未开始，仍需用户确认。

## 2026-09-29：M5.3 目标通信源码核对与参考运行

用户确认优先完成 v0.2，并将多请求连续批处理、具体集合通信库复现加入后续候选清单。旧完成记录已保存，候选项 F03/F04 与 M5.3 开始说明提交为 `2275ff23abfdee13bfef64c883644847b4aa58ee`。本轮只新增审计资料、独立检查器和文档，没有修改运行源码、技术规范、依赖锁或 gitlink。

工作目录均为 `/root/moe-ub-cosim`。本段以 R 表示 `runs/m5-3-model-audit-20260929`，L 表示 R/logs。该目录中的原始资料按既有规则不提交 Git；固定审计输入在 `fixtures/m5_model_audit/audit.json`，中文结论在 [模型核对记录](model_validation.md)。

### 来源与运行版本

目标为官方 DeepEP v1.2.1，提交 `9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee`，单机四 rank、8 专家、Top-K=2、hidden=4096、BF16 普通 Dispatch/Combine。全部调用参数和排除项保存在审计输入，不代表实测设备配置。

取得 `https://api.github.com/repos/deepseek-ai/DeepEP/tags` 与固定提交的 `/git/trees/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee?recursive=1` 元数据，保存在 R/sources/deepep-tags.json 和 deepep-tree.json。首次 tags 请求因沙箱无法连接配置的代理退出 7，获准使用 curl 网络权限后重试退出 0，tree 请求退出 0；没有更换源码来源或版本。此次中断发生于元数据读取期间，继续后取回源文件。

13 个小型公开文件共 253461 字节，逐个使用 `curl --fail --silent --show-error --location --max-time 40 <固定 raw URL> -o <R/sources/deepep/相对路径>`，全部退出 0。每个实际 argv、cwd、退出状态及最初失败原因在 R/sources/fetch-record.json；来源文件包含 LICENSE、README、Python API、C++/CUDA 布局/通信/事件/缓冲代码及上游单机测试。审计保存每份文件的 SHA-256、Git blob SHA-1、大小与固定 URL；Git blob 与上述 tree 一致。源代码只阅读，没有导入、编译或执行上游测试。

参考运行使用既有 `build/m5-multilayer/output/cosim/ns3.44-cosim-service`，SHA-256 为 `0f4fadf51793290236d25899c6a7e8e5221ffa31223975be79929d9a2e3767c2`；本轮没有重新构建。运行清单记录主仓库 `2275ff2`、13 个依赖提交和完整输入/源码摘要。LLMServingSim `9aed949c8e66fd1424d48365af64bfc0fe0b2ed1`、ASTRA `e2a14f55d9a45db8f3831bda7035af3e191e4bb7`、UB `1ba722217ef94716a07a56a2388be2c7dddd8e0b` 均未改变；实际仍只装载该构建的一套 10 个 UB ns-3 库。

### 实际命令与检查结果

命令由本任务临时记录器 `/tmp/moe_m53_run.py` 在根目录执行，环境 `PYTHONPATH=python`。记录器不改变被测代码，将准确 argv、cwd、起止 UTC、退出状态、stdout/stderr 路径保存为 `L/<标签>.command.json`，两个输出流分别保存为同名 `.stdout.log` / `.stderr.log`。下表是实际被执行命令；Python 为 `.venv/llmservingsim-native/bin/python`。

| 标签 | 命令与输入 | 退出状态 / 结果位置 |
|---|---|---|
| deps-pre | `bash scripts/check_deps.sh` | 0，13 个检出一致 |
| multilayer-regression | `PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_multilayer.py --build-dir build/m5-multilayer --output-dir runs/m5-3-model-audit-20260929/multilayer-regression`；输入从 fixtures/m5_multilayer/run.json 及其 service/layer/routes 构造各对照 | 0，R/multilayer-regression/summary.json；每条子命令在同目录 commands.json 与对应 stdout/stderr 日志 |
| model-audit | `PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_model_audit.py --source-dir runs/m5-3-model-audit-20260929/sources/deepep --regression-dir runs/m5-3-model-audit-20260929/multilayer-regression --output-dir runs/m5-3-model-audit-20260929/audit-check` | 0，首次独立布局核对；R/audit-check/result.json |
| model-audit-final | 上一条相同命令，仅 output-dir 改为 `runs/m5-3-model-audit-20260929/audit-final`；增加 Config 字段引用并更正本地函数定位后的最终记录 | 0，R/audit-final/result.json，摘要绑定最终审计文件和检查器 |
| deps-final | `bash scripts/check_deps.sh` | 0，13 个子模块版本和源码状态一致；根仓库本任务文档/审计改动按脚本规则报告 |

`check_multilayer.py` 的 8 次研究运行、1 次无观测服务运行和 10 项预期拒绝通过。正常主例/重复/来源关闭/零时钟/无观测均在 193789 ns 完成；主例 78 次写、638976 业务字节、36 条源路由、72 条端侧记录、2004 个窗口。传播时延对照为 256536 ns，稀疏请求为 216871 ns/344064 字节，3072 字节分块为 199717 ns/234 次写/638976 字节，32 层两步为 1367802 ns/558 次写/4571136 字节。

回归逐项核对实际 UB 交付、数据就绪/事务完成/接收回调、局部阶段和跨层依赖、资源排空、被动观测与端口来源守恒。10 项错误各退出 1 属于预期拒绝，原因在 summary.json 和子命令日志：五个前端输入错误、四个后端图错误和一次仿真时间上限。没有修改这些预期结果。此次没有重跑全部原生和旧阶段回归；运行源码未变，选择已有连续多层回归覆盖当前证据链。

新增检查器校验 13 个目标文件及本地审阅文件摘要、八项证据引用和分离的验证状态。独立 CPU 布尔 rank 掩码覆盖 117 个参考计划与空布局算式；8 次研究运行的 130 个层实例逐一比较专家输入、矩阵、实际交付与全部产物摘要。基础例 7 个远端行，去重例 6 个；主运行共 39 个远端行和 18 个本地行，业务字节 638976。目标额外元数据按源码逻辑存储推导为 Dispatch 1092、Combine 156 字节，不是 NVLink 实测或 UB 已模拟的开销。

### 有效范围与停止位置

八项行为源码核对完成；去重与业务向量量有依据，回传量以调用者局部加权合并为条件。索引/权重元数据、通道分块、环形缓冲、通知屏障与流事件等和参考执行存在差异。各项证据及其影响见 model_validation.md，没有通过改变 direct_rank_dedup_v1 或插入猜测控制流量来弥合差异。

实际输入仍为合成路由 `synthetic_indexed_file` 与逐层固定计算 `per_layer_fixed_reference`，执行范围为连续 MoE 层片段。没有目标库执行轨迹、专家数值前向或设备测量；阶段时序、端口时窗、端侧计算和硬件校准均保持未验证，未拟合参数或预设误差阈值。模型继续记录 `reference_model`、`unvalidated_reference` 与 `full_model_execution_validated=false`。

M5.3 按上述范围验收完成，按持续授权本地提交、不推送，停止在 M5.3 completed。v0.2 的编号任务按各自范围完成；F01/F02/F03/F04 留待后续版本确认，未开始采集、GPU 测量、多请求批处理或具体通信库复现。

## 2026-09-29：发布截至 M5.3 的最新版

用户明确要求将最新版推送到 GitHub。起点为 M5.3 完成提交 `b033a2c19d3c1d740277e1f254f25c8443e95888`，主仓库及子模块工作区干净，main 比上次发布的 `c116cddf6780731fff9e7cf5839f7c1786b33001` 多 21 个提交。旧任务记录已经提交，本次发布说明另存于 `420be920a3b7503e3fbed01adf45f811eea94d63`。

本轮范围为既有维护仓库的普通快进发布与必要文档，不升级依赖、不改源码或可见性、不创建新仓库。开发结果仍限 M5.3 已说明的未校准参考模型。本段以 R 表示 `runs/publish-v02-20260929`，L 表示 R/logs。临时命令记录器 `/tmp/moe_publish_v02_run.py` 保存每条实际 argv、cwd、起止 UTC、退出码及 stdout/stderr 路径，格式为 `L/<标签>.json` 与同名 `.stdout.log` / `.stderr.log`。

| 检查或操作 | 工作目录与实际命令 | 结果与日志标签 |
|---|---|---|
| 固定依赖 | 根目录，`bash scripts/check_deps.sh` | 退出 0，13 个检出一致；deps-pre |
| 主仓库原远端 | 根目录，`git ls-remote --exit-code origin refs/heads/main` | 首次退出 128；同命令重试退出 0，确认为 c116cdd；remote-root-before / remote-root-retry |
| ASTRA 远端 | third_party/LLMServingSim/astra-sim，`git ls-remote --exit-code origin refs/heads/cosim` | 退出 0，e2a14f55d9a45db8f3831bda7035af3e191e4bb7；remote-astra-before |
| UB 远端 | third_party/ns-3-ub，同上 cosim 查询 | 首次退出 128；重试退出 0，1ba722217ef94716a07a56a2388be2c7dddd8e0b；remote-ub-before / remote-ub-retry |
| LLMServingSim 远端 | third_party/LLMServingSim，同上 cosim 查询 | 退出 0，9aed949c8e66fd1424d48365af64bfc0fe0b2ed1；remote-llm-before |
| 发布配置未变 | 根目录，`git diff --exit-code c116cddf6780731fff9e7cf5839f7c1786b33001 b033a2c19d3c1d740277e1f254f25c8443e95888 -- .gitmodules deps.lock.json third_party` | 退出 0；pins-unchanged |
| 快进关系 | 根目录，`git merge-base --is-ancestor c116cddf6780731fff9e7cf5839f7c1786b33001 b033a2c19d3c1d740277e1f254f25c8443e95888` | 退出 0；fast-forward-check |
| 主仓库成果推送 | 根目录，`git push --recurse-submodules=check origin b033a2c19d3c1d740277e1f254f25c8443e95888:refs/heads/main` | 退出 0，c116cdd → b033a2c；push-v02-code |
| 成果远端核验 | 根目录，`git ls-remote --exit-code origin refs/heads/main` | 退出 0，b033a2c19d3c1d740277e1f254f25c8443e95888；remote-root-code |

两次首次失败均为 GitHub 返回 Invalid username or token，未将其误报为通过；使用相同远端和查询命令重试后成功。没有显示或写入凭证，没有修改认证配置。主仓库和三个维护依赖均无可执行的 pre-push hook。

三个维护子仓库已在 GitHub cosim 保存所需提交，本次没有新的子模块提交或推送。其余十个递归上游版本与先前发布相同；没有重新查询全部上游远端或再次完整克隆，源码可获取性沿用本日首次发布的独立检出证据。本轮再次确认了本地 13 个依赖状态、父仓库指针及三个维护分支在线状态。

README 同步截至 M5.3 的发布范围，当前任务保存发布结果。发布说明及收尾记录随主仓库 main 一并推送；准确收尾提交、远端一致性与干净工作区由 `R/final-state.json` 保存，对应的记录提交、推送与最终检查日志为 `records-commit`、`push-records`、`remote-root-final`、`deps-final` 和 `final-state`。本轮不重复构建或仿真，不将 build、runs、虚拟环境、下载源码或外部数据加入 Git。停止在本次发布完成，后续功能仍需用户确认，后续推送仍需单独授权。

## 2026-10-03：v0.3 里程碑编号整理

用户要求按 M0 至 M5 的方式划分下一阶段，且不再细分。起点为已同步的主仓库 `5880cbde68b10cebae0e6247864ff35899fce740`，工作区干净。本轮将 v0.3 原五个阶段依次命名为 M6 至 M10，同步增量规范、后续事项和当前任务中的引用，不新增下级编号或子任务清单。既定交付内容、技术行为和验收要求不变；已有 `v03-01-execution-parameters` 归属 M6，仍为 `pending`，其单层执行范围不代表整个 M6。

检查工作目录为 `/root/moe-ub-cosim`，记录位于 `runs/v03-milestone-labels-20261003/`。每项的 `.json` 保存实际 argv、cwd、起止时间、退出状态及同名 `.stdout.log`、`.stderr.log` 路径。

| 检查 | 实际命令与输入 | 退出状态与记录标签 |
|---|---|---|
| 固定依赖 | `bash scripts/check_deps.sh` | 0；13 个依赖版本及源码状态一致，主仓库文档修改按规则提示；`dependencies` |
| 修改边界 | `git diff --exit-code -- AGENTS.md docs/development.md docs/moe_ub_cosim_dev_v0.2.md docs/scenarios python backend tests fixtures scripts deps.lock.json .gitmodules third_party` | 0；列出的规则、技术基线、场景、源码、测试及依赖未变；`implementation-unchanged` |
| 编号引用 | `rg -n 'M[6-9]\|M10' docs/moe_ub_cosim_dev_v0.3.md docs/future_iterations.md docs/tasks/current.md`，并人工核对完整差异 | 0；五个里程碑和 F01 至 F04 的对应关系一致；`milestone-references` |
| 差异格式 | `git diff --check` | 0；`diff-check` |

本轮仅修改上述三份文档及本记录，不执行构建或仿真，不将文档检查计为功能验收。按持续授权本地提交、不推送；停止在编号整理完成，M6 功能实现等待用户确认开始。


## 2026-10-03：M6 单层固定轮次执行参数化

用户确认开始 M6，本轮按已经准备的 `v03-01-execution-parameters` 执行。起点为 `a3cf678ff5c8869e9d143b5c315bc8ce43a2c1bd`，主仓库工作区干净，main 比 origin/main 多一个文档提交。任务限定单层固定轮次，不将本次完成记为整个 M6 完成。

Python 计划与 C++ 语义校验从显式成员、专家放置长度、Top-K 和端点映射读取参数；补充实际逐 rank 计算量的溢出检查，以及来源 Token/专家与传输侧表的一致性核验。UB 传输描述保存独立物理端点，真实提交、目标完成过滤和应用/观测挂接使用该映射，图匹配键与端侧操作仍使用逻辑 rank。固定两轮入口按实际成员装载与回收。服务入口保留原部署边界并明确拒绝与其不同的映射；本轮没有扩展服务模型、容量、同芯片本地路径或场景 YAML 导入。

### 构建及日志

工作目录均为 `/root/moe-ub-cosim`；以下 R 表示 `runs/v03-01-execution-parameters`，L 表示 R/logs。R/record.py 保存每条实际 argv、cwd、起止 UTC、退出状态和 stdout/stderr 到 `L/<标签>.json` 及同名日志。集成检查的子命令另存各输出目录的 commands.json。起始固定依赖检查 deps-before 退出 0，13 个检出一致。

新建 `build/m6-execution-parameters`，configure、build、build-current、build-final 均退出 0。后两次仅增量编译审阅过程中补充的来源校验、空闲成员发送检查和服务部署拒绝，未覆盖旧构建目录。实际配置与最终构建命令为：

```bash
cmake -S third_party/ns-3-ub -B build/m6-execution-parameters -G Ninja -DCMAKE_BUILD_TYPE=release -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m6-execution-parameters/output -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m6-execution-parameters --target cosim-layer cosim-sequence cosim-lifecycle-test cosim-layer-delay-test cosim-write-test cosim-observation-test cosim-write cosim-service --parallel 8
```

下表集成检查均使用 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/<脚本> --build-dir build/m6-execution-parameters --output-dir runs/v03-01-execution-parameters/<标签>`，全部退出 0。主要输入为脚本列出的 fixtures；新增人工参数输入位于 `fixtures/m6_execution_parameters/ep2.json`、ep3.json，复用原 m2_single_layer 网络。

| 标签 / 脚本 | 实际结果 |
|---|---|
| parameters-final / check_execution_parameters.py | 9 次运行、13 项预期拒绝通过，含非恒等映射、非连续逻辑编号、来源开/关/不挂观测、无本地 Token 的专家成员、全空闲成员、纯本地工作及两轮执行。 |
| layer-final / check_layer.py | 3 次真实运行、13 项非法输入/覆盖拒绝通过；原输入仍为 14 次写、114688 业务字节、48 个 ASTRA 节点。 |
| sequence-final / check_sequence.py | 5 次真实运行及非法图/身份与覆盖拒绝通过；原两轮 28 次写、229376 字节，忙碌替换、原子装载和旧回调拒绝保持。 |
| delay-regression / check_delay.py | 5 次给定事件、2 次真实 UB 运行及 10 项预期拒绝通过；网络延迟实际移动依赖计算。给定事件部分继续明确标为测试替身。 |
| pd-regression / check_pd_handoff.py | 7 次正常运行、11 项预期失败通过；主例 P 完成 40159 ns，KV 数据就绪 40830 ns、事务完成/D 开始 40876 ns，请求结束 69994 ns。 |
| multilayer-regression / check_multilayer.py | 8 次研究运行、1 次无观测运行、10 项预期失败通过；主例 78 次写、638976 字节、193789 ns，原 32 层回归为 558 次写、4571136 字节。 |

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v` 在 python-parameters 中通过 56 项，包含五项新增参数检查。`build/m6-execution-parameters/output/cosim/ns3.44-cosim-write-test` 与同目录的 ns3.44-cosim-observation-test 分别在 write-unit、observation-unit 中退出 0，覆盖晚到 RECV、独立完成、重复/过期事件、资源释放与端口字节口径。早期 python-initial、parameters、layer-regression、sequence-regression 也通过；执行入口新增最终检查后，对相应集成检查重新运行，最终证据以上表为准。

### 新参数结果与有效范围

三成员样例使用逻辑 rank `[2,7,12]`、UB 节点 `[3,0,2]`、五专家及 Top-3，专家输入为 `[2,3,1]`。非均匀放置保留本地输入和同目的 rank 去重，Dispatch/Combine 采用不同元素字节数和 1500 字节分块，真实执行 15 次写、18432 字节，最后图完成于 20604 ns。两成员样例为逻辑 rank `[4,10]`、UB 节点 `[1,3]`、三专家及 Top-1，专家输入 `[0,2]`，3 次写、1536 字节，最后图完成于 16291 ns。来源开/关/不挂观测的执行结果相同。

新增全空闲 rank 20 映射节点 1，其空图在 0 ns 完成，不创建填充计算或业务写；单 rank 本地样例在 12000 ns 完成，业务写为零。非恒等映射的连续两轮共 30 次写、36864 字节，第二轮由第一轮实际完成时刻推进，一份网络及其对象保留。端口来源核验按物理目的节点筛选实际传输，内部注册表同时保留逻辑 rank 和 UB 节点；没有把新身份字段放入 observed。

13 项新增拒绝覆盖重复/缺失/越界映射、交换机或不存在的端点、rank 范围、专家放置与 Top-K、重复或非法专家、计算/字节溢出和伪造来源。端点类型与存在性在 UB 节点创建后、图激活前拒绝，其余侧表错误在创建运行目录前拒绝；没有发起业务传输。所有预期失败的具体 stderr 和退出码保留，未降低预期结果。

`python3 runs/v03-01-execution-parameters/audit.py` 在 final-audit 中退出 0：恢复本轮 CMake 改写的原生启动锁，308 个旧产物的 SHA-256 未变，最终通过记录中的源码摘要匹配；链接核验仍为 52 个 ASTRA 核心对象和唯一一套 10 个 UB ns-3 库。`bash scripts/check_deps.sh` 在 deps-final 中退出 0，13 个固定依赖一致，无子模块源码、gitlink、锁文件或来源修改。未重跑原生性能样例，未安装软件或下载模型/数据。

按持续授权整理记录并本地提交、不推送。当前任务置为 completed 后停止；M6 的模型与容量、部署本地性和场景输入适配仍待确认。当前路由与计算仍为合成参考，模型未校准，完整 CM384/R1 目标规模未运行。

## 2026-10-03：M6 服务模型与容量参数化

当前范围为 `v03-service-model-capacity`，属于 M6。起始实现提交 `3e98d9e42c13e675ac26351c1ff992690aafe10a`；旧任务已完成提交后，按用户确认在 `a03561b` 保存新任务说明并开始。主仓库 Git 身份为用户已提供的 baoy21 / baoy21@mails.tsinghua.edu.cn。本轮只改主仓库，不推送、不升级依赖，不进行模型下载、系统安装或全局配置变更。

新增 `moe-ub-configured-service/1`、`moe-ub-model/1` 与主仓库模型/容量初始化适配。声明模型的全部层用于容量核算，专家权重按每层实际落在 rank 上的数量计算，另计共享权重、workspace 与通信缓冲区。支持的声明结构是无 bias GQA/MHA、RMSNorm、门控 FFN；其他结构不套用此公式。KV 按本 rank 本地请求的完整层缓存计算，不除以 EP；接纳前预留到 `output - 1`，预留不改变 computed/reached 进度。原 Scheduler 的 `schedule/add_decode/add_done`、Request、BlockPool 和 TieredKVCacheManager 保留，容量适配只初始化参数与唯一块池，没有修改依赖或 monkeypatch。

`cosim-service` 的成员、应用挂接、控制器及回调改按逻辑 rank 与 UB 映射查找，不再固定四成员和恒等映射。组间也拒绝重复 rank 或重复端点；每轮计划与部署逐一核对。UB 实际事务、data_ready/send_done、图装载及网络排空继续使用原执行语义。

### 实际构建与日志

所有命令 cwd 为 `/root/moe-ub-cosim`。任务日志根 `runs/v03-service-model-capacity/`；`logs/<label>.json` 保存 argv、cwd、UTC 起止和退出状态，配套 `.stdout.log/.stderr.log` 保存输出。各集成目录的 `commands.json` 保存每个子进程及输入路径，生成输入、协议记录、ASTRA/UB 原始日志及预期失败均保留。依赖开始检查 label 为 `deps-start`，退出 0，13 个检出与锁文件/gitlink 一致。

审阅 `backend/inject.cmake`、`backend/CMakeLists.txt`、`scripts/m0_joint_build/` 后配置独立目录，命令如下，label `configure` 退出 0：

```bash
cmake -S third_party/ns-3-ub -B build/m6-service-model-capacity -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m6-service-model-capacity/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m6-service-model-capacity \
  --target cosim-service cosim-service-deadlock-test --parallel 8
```

首次构建 466 项完成，label `build` 退出 0。审阅中将无 deployment 文件时兼容输入的专家编号显式写为 unsigned，以满足新增类型检查；同一构建命令再次退出 0，label `build-fallback-fix`。兼容输入经过旧服务的实际协议拒绝回归。只构建受影响的服务入口及其故障测试目标。

### 实际验证

Python 使用 `.venv/llmservingsim-native/bin/python` 和 `PYTHONPATH=python`。下表命令中 `PY` 表示 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python`，`B` 表示 `build/m6-service-model-capacity`，`R` 表示 `runs/v03-service-model-capacity`，只为表格简写，完整 argv 见对应 JSON。所有表列检查入口退出 0；内部预期失败的真实退出状态另存，不能将它们理解为正常运行。

| 日志 label | 实际命令（根目录） | 结果 |
|---|---|---|
| `unit-final` | `PY -m unittest discover -s tests/unit -p '*test.py' -v` | 61 项通过；新模型的 5 项测试含 25 个非法结构/容量/输入变体。 |
| `model-final` | `PY tests/integration/check_service_model_capacity.py --build-dir B --output-dir R/parameters-final` | 6 次真实运行、9 项预期拒绝。 |
| `service` | `PY tests/integration/check_service.py --build-dir B --output-dir R/service` | 7 次实际运行、9 项协议拒绝及子进程失败传播通过；三轮 42 次写、344064 字节、64738 ns，延迟对照 100410 ns。链接检查仍为 52 个 ASTRA 核心对象、10 个本次 UB ns-3 动态库。 |
| `dynamic` | `PY tests/integration/check_dynamic_service.py --build-dir B --output-dir R/dynamic` | 9 次运行、10 项拒绝通过；双组独立推进、执行中到达排队及同刻到达保持。 |
| `boundaries` | `PY tests/integration/check_service_boundaries.py --build-dir B --output-dir R/boundaries` | 7 次运行、7 项失败检查通过；稀疏/纯本地/空服务、EOF、未激活成员与截止检查保持。 |
| `pd` | `PY tests/integration/check_pd_handoff.py --build-dir B --output-dir R/pd` | 7 次运行、11 项失败检查通过；基线 P 完成 40159、KV 数据就绪 40830、事务完成及 D 开始 40876、服务完成 69994 ns。故障代理仅终止其自己启动的测试子进程，源 KV 未提前释放。 |
| `pd-queue` | `PY tests/integration/check_pd_queue.py --build-dir B --output-dir R/pd-queue` | 三组共十二 rank 的 9 次运行、8 项拒绝通过；已有 KV 容量等待、源端保留与共享队列不变，重叠流量使首个 KV 完成延后 26143 ns。 |
| `multilayer` | `PY tests/integration/check_multilayer.py --build-dir B --output-dir R/multilayer` | 8 次研究运行、1 次无观测运行、10 项失败检查通过；三层三轮 78 次写、638976 字节、193789 ns，既有 32 层两轮回归仍为 558 次写、4571136 字节、1367802 ns。 |

`unit-initial`、`unit-all`、`model-integration` 也退出 0，保留初次检查记录。随后补充 dtype 非字符串格式的拒绝，重跑上述 `unit-final` 与 `model-final`；旧共享入口回归所执行代码未再修改。最终源码摘要见各 summary 及 `audit.json`，记录不把先前运行伪写为最终再次执行。

GQA 输入来自 `fixtures/m6_service_model/gqa-service.json` 与 `gqa-layer.json`：完整三层 H=64 模型包括稠密层、五专家 MoE 层和另一个含共享专家的 MoE 层，只执行中间层。rank 2/7/12 映射到 UB 3/0/2，权重独立核算为 255872/280448/231296 字节；每 KV Token 为 384 字节，每块四 Token，容量 3/3/2 块。六轮完成时刻为 19202、38404、57606、72712、87818、102924 ns，共 24 次写、4608 字节；rank 12 无本地请求仍完成专家工作。来源开启、关闭和不挂观测的执行与容量结果一致，来源开/关的公开观测相同。

请求 101、102 在 0 ns 接纳，分别预留到最后输入而不增加原计算进度。102 在 57606 ns 结束释放，101 在 72712 ns 结束释放；1000 ns 到达的请求 103 此时才接纳，102924 ns 结束。记录清楚区分这是当前单请求序列槽的排队，以及已保留的旧 P/D 多请求容量等待测试。新配置入口尚未实现 M7 的同 rank 连续组批。

MHA 输入 H=32、共享 embedding/head、FP32 权重、FP8 KV 字节口径，逻辑 rank 4/10 映射到 UB 1/3。权重为 74496/49920 字节，每 KV Token 为 64 字节，八 Token 一块；两轮 4 次写、256 字节、22192 ns。容量降到一块且保留两步时，初始上下文虽能容纳，最后输入需求仍为两块，因此在启动后端前拒绝；只剩一步时最后输入长度八恰好占一块，实际一轮两次写、128 字节、11096 ns。空服务在 0 ns 正常结束。后端另外独立拒绝重复/不存在/交换机端点、映射不符、放置不符或组外专家、重复/溢出 rank，拒绝时未发起业务传输。

### 产物保护与未覆盖范围

`baseline.json` 在本轮配置前保存原生启动锁和 9074 个已有构建产物的 SHA256。`audit.py` 只恢复这次备份的 UB 原生启动锁，并逐一核对旧产物与测试结果、受测源码摘要；本轮实际审计退出 0，9074 个摘要全部一致，启动锁已恢复，结果保存在 `audit.json`。`deps-final` 再次检查 13 个依赖通过；最终差异与本地提交日志保存在同一 `logs/` 下。本轮不清理旧运行或构建产物。

当前容量是声明式结构的解析核算，工作区为用户参考预留，FP8 未建模设备特有 scale/对齐存储，计算仍为显式参考阶段耗时。没有把模型结构事实、真实路由或硬件校准互相替代。新入口限一个 EP 组、一层 MoE、每 rank 同时一个请求；模型其他层只计容量，稠密层与共享专家没有伪造成该执行层。MLA、目标模型算子组合、连续组批、同芯片本地性、场景 YAML 适配和完整 CM384/R1 场景没有在本轮完成。旧输入的原有限制保留；新 schema 尚未适配研究导出入口。后续范围继续由用户确认。

## 2026-10-03：M6 部署本地性分类

用户确认继续，起点为已完成服务模型任务的 `cf2b9dae627f8aa930ada25c8389a617d894a156`，主仓库工作区干净；任务说明先提交于 `28bd58b`。本轮没有修改子模块、gitlink、锁文件或技术规范。新增 layer/plan v2 及后端 deployment v2，显式声明全局 die/chip/server 归属；旧 layer v1 保留全部跨 rank 使用 UB 的行为。

同芯片跨 rank 的准备时间进入源 rank 的 COMP 节点，之后由独立 LocalDelivery 发布数据并通过 ns-3 当前时刻事件完成双方回调，早到和晚到 RECV 都只完成一次。没有 WQE、目标写提交或伪造的 TransactionDone。跨芯片仍使用原 UB WRITE/RTP；所有专家输入和 Combine 贡献保留。单层、固定两轮与一组一层的配置服务已实现此路径，P/D、旧来源入口及跨层组合明确保留原输入范围。

### 实际命令与日志

工作目录均为 `/root/moe-ub-cosim`。以下 R 为 `runs/v03-deployment-locality`，L 为 R/logs；`R/record.py` 保存完整 argv、cwd、起止时间、退出状态和 stdout/stderr，集成目录另有 commands.json。初始两条辅助脚本启动误用了系统未提供的 `python`，退出 127；改用 `python3` 后执行正常，没有安装软件或修改全局 Python。首次和最终编译均成功，没有仿真失败后修改预期值的情况。

独立配置命令（L/configure.json，退出 0）：

```bash
cmake -S third_party/ns-3-ub -B build/m6-deployment-locality -G Ninja -DCMAKE_BUILD_TYPE=release -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m6-deployment-locality/output -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m6-deployment-locality --target cosim-layer cosim-sequence cosim-service cosim-service-deadlock-test cosim-local-test cosim-time-test cosim-write-test cosim-observation-test -j 8
cmake --build build/m6-deployment-locality --target cosim-layer cosim-sequence cosim-service cosim-service-deadlock-test cosim-lifecycle-test -j 8
```

两次 build 对应 L/build.json 与 L/build-final.json，均退出 0；第二次加入旧生命周期测试目标，并编译多层入口对未实现的新本地性 schema 的显式拒绝。全程只使用新构建目录。

Python 命令为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py'`；L/python-unit-initial 为旧 61 项通过，L/python-unit-locality 为增加六项本地性检查后的 67 项通过。C++ 命令分别为 `build/m6-deployment-locality/output/cosim/ns3.44-cosim-local-test`、`ns3.44-cosim-time-test`、`ns3.44-cosim-write-test`、`ns3.44-cosim-observation-test`，四项同目录执行均退出 0，对应 L/local-unit、time-unit、write-unit、observation-unit。新单元覆盖早到/晚到接收、非同步重入、重复登记、类型/字节匹配、仅一次回调与回收。

下列集成命令均使用 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/<脚本> --build-dir build/m6-deployment-locality --output-dir R/<目录>`，完整命令保存在同名 L/标签.json；全部退出 0。

| 日志标签 / 目录 | 脚本 | 实际结果 |
|---|---|---|
| locality-final | check_deployment_locality.py | 14 次正常运行、9 项后端拒绝；此前 locality-integration / locality 也通过，最终版补核对独立计算槽、原容量及真正的本地时间溢出拒绝。 |
| execution-parameters | check_execution_parameters.py | 原 EP3/EP2/空闲成员/同 rank/固定两轮与 13 项拒绝通过。 |
| sequence | check_sequence.py | 原两轮和生命周期故障测试五次运行、12 项拒绝通过，28 次写交付 229376 字节。 |
| service-model | check_service_model_capacity.py | 原 GQA/MHA 六次运行、九项拒绝通过；GQA 仍为 24 次写、4608 字节、102924 ns。 |
| boundaries | check_service_boundaries.py | 七次运行及七项故障检查通过。 |
| service | check_service.py | 七次运行和原协议拒绝通过；三轮 42 次写、344064 字节、64738 ns。 |
| dynamic | check_dynamic_service.py | 九次运行及十项拒绝通过，两组仍在同一网络推进。 |
| pd | check_pd_handoff.py | 七次运行及十一项拒绝通过。 |
| pd-queue | check_pd_queue.py | 九次运行及八项拒绝通过，共享链路使 KV 完成后移 26143 ns。 |
| multilayer | check_multilayer.py | 八次研究运行、一次无观测对照及十项拒绝通过，原三层与 32 层范围不变。 |

### 新路径的实际证据

`R/locality-final/summary.json` 保存汇总，逐次命令、输入、计划、结果及观测留在同目录。`fixtures/m6_locality/` 的小输入提供独立手算依据，字段与完成条件见[样例说明](../fixtures/m6_locality/README.md)。

| 对照 | 实际结果 |
|---|---|
| 三类混合 | 四 Token、12 份专家输入，逐 rank 为 2/5/3/2；去重后同 rank / 同芯片 / 跨芯片贡献为 4/2/5。两阶段产生 4 次本地交付、10 次 UB 写、1280 fabric 业务字节，1376 ns 完成。 |
| 纯本地及因果反馈 | 同芯片两个专家去重为每阶段一份向量；两次本地交付，零 UB 写、零目标写提交、零被观测端口字节，750 ns 完成。准备增加 1000 ns 后消费者及完成时间均后移 1000 ns。 |
| 零参考成本及同 rank | 显式零准备成本仍保留两次本地交付依赖，570 ns 完成；同 rank 对照无交付节点，同为 570 ns。 |
| 固定两轮 | 局部图节点号复用，四个本地交付身份不重复；第二轮由第一轮在 750 ns 的实际完成触发，最终 1500 ns，无 UB 写。 |
| 配置服务 | 六轮 18 次本地交付、6 次 UB 写、1152 fabric 字节、103158 ns。增加本地准备使下一轮提交后移，最终 133080 ns；仅保留第一个请求时四轮八次本地交付、60720 ns，网络发送/到达包计数均为零。 |
| 资源与观测 | 同芯片的独立 rank 可同时计算，同 rank 计算区间不重叠；权重 255872/280448/231296 字节与 KV 3/3/2 块不变，结束全部释放。混合单层与服务的来源开/关/不挂观测执行完全一致；来源侧表只记录实际 fabric 写。 |
| 拒绝 | 缺失本地依赖、伪造本地 UB task、错误分类、降级 schema、删除交付、重复 die、同一全局芯片跨服务器、本地准备时间溢出和计划改变既定部署均被拒绝；后者在 0 ns、业务激活前终止。 |

R/audit.py（L/audit.json，退出 0）核验 9540 个旧构建产物摘要未变，恢复本次备份的原生 `.lock-ns3_linux_build`；核验当前集成源码摘要、全部测试状态及链接边界。单层、两轮、服务均链接 52 个 ASTRA 核心对象，实际装载的十个 ns-3 动态库都来自本次 UB 构建。`bash scripts/check_deps.sh` 的最终记录为 L/deps-final.json，退出 0，13 个依赖检出与固定版本一致。构建、日志和备份留在忽略目录，没有清理旧产物。

当前仍是一组、一个选定 MoE 层、每 rank 同时一个请求的配置服务，时间与路由均为未校准参考输入；没有执行完整 CM384/R1、真实路由或硬件测量。M6 整体未完成，下一项建议继续场景 YAML 与实际后端 WRITE/RTP 参数适配，仍等待用户确认。

## 2026-10-03：M6 场景 YAML 与后端参数适配

用户确认开始，起点为部署本地性任务 `b4792824e1c079a768510fb8d7fd1f2507a10032`，工作区干净；任务说明先提交于 `6c34ed1`。本轮只修改主仓库，13 个依赖保持锁定提交。新增 `moe-ub-scenario-run/1`，引用既有 configured-service、单层和显式网络，导入所选参考后端段；原 CM384/R1 YAML 保持参考规格，不声称完整场景可执行。使用现有虚拟环境 PyYAML 6.0.1，没有安装软件、修改全局配置或下载模型。

原生运行时目录现有 96 项 UB 属性、21 项 UB 全局值。部分全局值由 UbUtils 单例构造时注册，UbEgressQueue 类型需显式调用原生 GetTypeId；默认值全部来自注册信息。解析后由原生 checker 规范化，联合约束独立列出。新路径使用 backend.json，TXT 仅作完整配置审计副本；旧入口没有该文件时继续原配置流程。运行时核对继承属性、实际节点与链路覆盖、全局值，以及 WQE 启动时动态 Jetty 的 TA/inflight，不能只把 YAML 选择抄到结果。

### 实际命令与日志

工作目录 `/root/moe-ub-cosim`，下文 R 为 `runs/v03-scenario-backend`，L 为 R/logs。`R/record.py` 保存每条完整 argv、cwd、起止 UTC、退出状态与 stdout/stderr；集成目录有逐条 commands.json，场景目录有 resolve_commands.json 和 run/manifest.json。所有旧构建产物保留，仅使用新独立目录。

```bash
cmake -S third_party/ns-3-ub -B build/m6-scenario-backend -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m6-scenario-backend/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m6-scenario-backend --target cosim-service cosim-service-deadlock-test -j 8

env PYTHONPATH=python .venv/llmservingsim-native/bin/python \
  -m unittest discover -s tests/unit -p '*test.py'
```

配置记录 L/configure.json，退出 0；最终编译 L/build-final.json，退出 0；Python 记录 L/python-units.json，67 项通过。此前同一 build 命令的 build-current、build-catalog、build-queue-catalog、build-live-validation、build-inherited、build-safe-time 也退出 0；首次 L/build 失败及原因见下文。没有重新构建旧目录或运行上游独立仿真来拼接结果。

场景实际直接命令为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.scenario run --input fixtures/m6_scenario_backend/reference.yaml --backend build/m6-scenario-backend/output/cosim/ns3.44-cosim-service --output-dir R/sixth`，L/scenario-sixth.json 退出 0。最终验收使用下表脚本；统一命令为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/<脚本> --build-dir build/m6-scenario-backend --output-dir R/<标签>`，L 中同名 JSON 保存精确命令。全部退出 0。

| 标签 / 目录 | 脚本 | 实际结果 |
|---|---|---|
| scenario-final | check_scenario_backend.py | 11 次真实运行、1 次仅解析、24 项拒绝；目录、实例覆盖、分段/分包、槽位、因果反馈、观测无扰动和本地交付通过。 |
| service-model-final | check_service_model_capacity.py | 6 次运行、9 项拒绝；逐 rank 容量、稀疏映射与完整回收保持。 |
| service-final | check_service.py | 7 次运行、9 项拒绝及 EOF 边界通过。 |
| dynamic-service-final | check_dynamic_service.py | 9 次运行、10 项拒绝；动态到达和共享网络保持。 |
| service-boundaries-final | check_service_boundaries.py | 7 次运行、7 项故障检查，包括新构建的 deadlock-test 目标。 |
| pd-handoff-final | check_pd_handoff.py | 7 次运行、11 项拒绝；KV 目标预留与事务完成保持。 |
| pd-queue-final | check_pd_queue.py | 9 次运行、8 项拒绝；容量等待、FIFO 和第三组竞争保持。 |
| multilayer-final | check_multilayer.py | 8 次研究运行、1 次不挂观测、10 项拒绝；逐层因果与研究输出保持。 |

较早的 scenario-integration-v3 及七套无 `-final` 后缀的旧入口回归也通过。后来补充负信用值拒绝、解析子进程原始日志及执行 CSV 摘要，再以最终构建完成上述八套检查；没有删除断言或改变旧案例预期。

### 执行证据

| 对照 | 实际结果 |
|---|---|
| 参数来源与实例覆盖 | 全部属性从运行时目录出发，按 reference/scenario/joint constraint 记录来源链；默认仲裁 10 ns，实际节点 CSV 为 1 ns，读取对象证实覆盖。单节点 200 ns 仲裁、30 ns 转发及单链路 12 Gbit/s、500 ns 覆盖与实际一致；出口队列、TP/Jetty 额度及 RNG seed=7/run=3 也核对实际值。 |
| 基准 | 224 Gbit/s、20 ns，六轮 6 次写、1152 业务字节、6 段目标提交、18 次同芯片交付，完成 103188 ns。 |
| 网络扰动 | 全链路 1 Gbit/s 完成 118290 ns；延迟 2000 ns 完成 126948 ns。数据就绪、rank 12 依赖计算和第二轮提交均后移，业务字节与完成回调数不变。 |
| 三种分包口径 | TA=64 时 6 次写变为 18 段；chunk=64 时变为 18 次写，发送槽 2/1 的实际 WQE 峰值分别 2/1，完成 103191/103488 ns。隐藏维度 4096 的合成模型声明独立充足预算，用 chunk=8192 执行 9 次写、73728 字节、9 段，所观测数据包有效载荷均为编译常量 4096 字节，完成 104487 ns。 |
| 本地性与观测 | 来源开/关/不挂观测的服务与后端执行结果一致，开/关的 observed 文件逐字节一致；内部包/业务/控制字节核算通过。纯同芯片请求四轮八次交付、60720 ns，UB 写与端口发送包均为零。 |
| 错误配置 | READ、CTP、native LD/ST、多路径/喷洒、CC、故障、重传、未知属性、TP 常量冲突、按需通道、非正资源、负信用、非法时间/速率、缓冲预算、重复键、旧目录及错误槽位均被拒绝。后端还独立拒绝 READ profile；解析后的 CSV 被改变时，在图激活前拒绝实例不一致。 |

### 开发中失败与处理

首次编译 L/build 在新增导出代码使用了不存在的 UbController::GetNode，改用聚合的 Node 对象后通过。L/scenario-first 的 8192 字节 chunk 超出原小模型通信缓冲预算，被既有容量检查拒绝；小样例改用原预算内的显式 1024 字节，没有改变容量公式或原参考规格。L/scenario-second 因延迟注册的出口队列不在目录而失败，随后注册其原生 TypeId。

L/scenario-third/fourth 在校验固定约束时遇到临时 JSON 的 items 代理生命周期错误，错误消息又触发 Python 的 UTF-8 解码异常；改用存续的 catalog 对象遍历，并让解析子进程保存原始 stdout/stderr、退出/超时状态。fourth 在修复构建结束前启动，仍执行旧二进制；后续等待编译成功后再验收。L/scenario-fifth 的实例读取漏掉继承的 AllocationTime，改为沿 TypeId 父链读取；sixth 正常运行。失败输出均保留，没有改为固定网络时间。

L/scenario-integration 与 L/scenario-integration-v2 在新增大向量测试中，分别因所声明通信缓冲不足、KV 块数超过显式主机预算被拒绝；测试模型补充 1 MiB 通信缓冲和 65536 块预算后按原公式校验，scenario-integration-v3 通过。此处调整的是新增测试输入的显式资源预算，不截断 KV、不更改检查预期；普通小样例预算未变。

### 产物与停止位置

`python3 R/audit.py` 对应 L/audit.json，退出 0：10062 个旧构建产物摘要未变，恢复备份的 `.lock-ns3_linux_build`，核对最终八套集成的代码摘要；服务链接 52 个 ASTRA 核心对象，实际装载十个 ns-3 库均来自 `build/m6-scenario-backend/output/lib`。`bash scripts/check_deps.sh` 对应 L/deps-final.json，退出 0，全部 13 个依赖与固定版本一致。任务生成的构建、日志、输入和备份留在忽略目录。

本步补齐 M6 表中场景后端参数适配的交付范围，M6 按既定里程碑验收完成。当前配置服务仍只执行一组、一层、每 rank 同时一个请求；真实路由、MLA、目标完整模型/CANN/CM384 行为和硬件校准未在本轮完成，`unvalidated_reference` 不变。本轮整理记录后本地提交，不推送；下一项 M7 同 rank 多请求连续批处理待用户确认，尚未开始。

## 2026-10-03：M7 同 rank 多请求连续批处理

用户确认开始 M7，基线为 `a3bdf90`，任务说明先提交于 `6d6ce57`。本轮仅修改主仓库适配、计划验证、小型样例及相关记录；锁定 Scheduler 已有多请求批次选择和完成逻辑，因而没有修改子模块。使用现有原生 Linux 工具链、`.venv/llmservingsim-native` 及其 protobuf/PyYAML；未安装软件、修改全局配置、下载模型或初始化升级依赖。

以下命令 cwd 均为 `/root/moe-ub-cosim`。记 `R=runs/v03-continuous-batching`、`L=R/logs`；这些是本节路径缩写。`R/record.py` 保存各次实际 argv、cwd、开始/结束 UTC、退出状态和 stdout/stderr 路径；各集成目录的 commands.json 另记逐次输入与子命令。运行、构建、备份和摘要留在忽略目录。

### 实现与独立构建

configured-service/2 与 layer/3 显式开启批次，服务保存 deployment/3，生成 execution-plan/3；旧 schema 继续原行为。新批次仍是一组、一个选定 MoE 层、每 rank 一张活动图和一个计算槽。根适配预留最终输入 KV，原生 Scheduler 选批和逐请求完成；暂时不足的队首等待容量，未选中的驻留请求仍计入唯一块池。图保存实际请求上下文、dtype 和逐专家输入量，C++ 独立核验；参考计算仍按本地 Token 数与本 rank 专家输入总量线性计时，未增加上下文或专家分布性能模型。

先保存旧产物摘要和原生启动锁，实际执行的配置与构建如下，分别记录为 L/configure.json、L/build.json，均退出 0：

```bash
cmake -S third_party/ns-3-ub -B build/m7-continuous-batching -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m7-continuous-batching/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m7-continuous-batching \
  --target cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence cosim-lifecycle-test -j 8
```

### 检查命令与结果

实际单元命令 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py'`，L/python-units.json 退出 0，72 项通过。其中新增五项分别验证 A/B/C 的路由和进度、KV 等待、驻留但未选中的容量、逐专家形状/跨请求向量，以及非法输入；给定边界时间的单元测试与真实 UB 集成分别记录。

下表每行实际命令均为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本 --build-dir build/m7-continuous-batching --output-dir R/记录名`；均退出 0。该行 summary.json 位于 R/记录名，外层命令记录为 L/记录名.json。

| 脚本 | 记录名 | 实际检查 |
|---|---|---|
| check_continuous_batching.py | batching-final | 15 次正常执行、11 项预期拒绝；独立业务量、逐请求进度和 KV 生命周期。 |
| check_scenario_backend.py | scenario-final | 11 次旧场景运行、24 项拒绝，WRITE/RTP 参数和完成反馈。 |
| check_service_model_capacity.py | service-model-final | 6 次原配置服务运行、9 项拒绝，旧单请求和完整模型容量。 |
| check_deployment_locality.py | locality-final | 14 次单层/连续图/服务本地性运行、9 项拒绝。 |
| check_execution_parameters.py | execution_parameters-final | 9 次固定图参数对照、13 项拒绝；原 14 次写、114688 字节保持。 |
| check_sequence.py | sequence-final | 5 次连续图运行、12 项拒绝及生命周期检查。 |
| check_service.py | service-final | 7 次普通服务运行、9 项拒绝，实际完成与排空。 |
| check_dynamic_service.py | dynamic_service-final | 9 次动态到达运行、10 项拒绝。 |
| check_service_boundaries.py | service_boundaries-final | 7 次边界运行、7 项拒绝，空输入/稀疏成员/故障快照。 |
| check_pd_handoff.py | pd_handoff-final | 7 次 P/D 运行、11 项拒绝。 |
| check_pd_queue.py | pd_queue-final | 9 次交接队列运行、8 项拒绝。 |
| check_multilayer.py | multilayer-final | 8 次研究运行、1 次不挂观测运行、10 项拒绝。 |
| check_research_export.py | research_export-final | 旧研究导出、隔离观测及 15 项错误数据检查。 |
| check_shared_bottleneck.py | shared_bottleneck-final | 7 次双组共享竞争与来源开关对照。 |

专项固定输入位于 fixtures/m7_continuous_batching/service.json、layer.json；派生预算、网络、本地性及错误输入均保存在 R/batching-final。输入值和计算依据详见该样例 README。

| 对照 | 实际结果 |
|---|---|
| A/B/C | 剩余 3/1/2 Token，C 于 1000 ns 在首轮中到达；实际 AB、AC、AC，完成 21100/38196/55292 ns。逐请求 reached 为 6/7/11，每批生成 2，共 6 次写、512 业务字节；B 先释放，A/C 最后一轮释放。 |
| KV 暂时不足 | rank 4 三块减为两块，批次为 AB、A、A、C、C，65292 ns 结束；A 继续执行，释放后才接纳 C，无重算或抢占。 |
| 两个预算 | max_num_seqs=1/max_num_batched_tokens=2 时每 rank 只有一个驻留请求；3/1、四块容量且同刻到达时，首轮驻留 A/B/C 占 1/1/2 块，只推进 A。两者均依次 A、A、A、B、C、C，完成 66384 ns。 |
| 批次和分块 | 不均匀两 rank 批次为 2/1，单轮 384 字节；减去一个请求后 256 字节。ABC 的 chunk 从 256 减到 32，UB 写从 6 增至 16，仍为 512 字节，完成 56196 ns。 |
| 真实网络反馈 | 400 Gbit/s、20 ns 改为 1 Gbit/s、2000 ns，数据就绪、依赖计算和下一轮启动推迟，整体完成变为 105978 ns；字节和批次成员保持。 |
| 本地性 | 纯同 rank 的 ABC 没有 UB 写或发送包。另一个 Top-3 三请求单轮混合本地性输入，4 次真实写、1152 字节、4 次同芯片交付，完成 25360 ns；专家贡献保留。 |
| 到达和结束 | 空输入在 0 ns 结束；C 改为 500000 ns 到达时，空闲后的下一轮恰在该时刻启动，522096 ns 完成。所有请求结束，KV 和计算/发送/事件资源归零。 |
| 场景/观测 | 新 schema 通过 M6 YAML 参数入口真实运行；来源开启、关闭和不挂观测的服务/执行结果一致，开关的 observed 文件逐字节一致；整包、业务与控制字节分别核对。 |
| 独立拒绝 | 前端拒绝不可能的最终 KV、预算、缓冲、路由和新旧 schema 混用；后端独立拒绝重复请求、伪造形状、预算超额/中途改变、合并请求来源及篡改计算耗时，失败均在 0 ns、业务写提交前。 |

### 开发中失败与处理

L/batching-unit 首次运行时，新增 Token 预算为 1 的单元输入仍保留两个占位 Token，被层配置验证拒绝。把该测试层的占位 Token 改为一个后，L/batching-unit-v2 五项通过；实际三请求输入和驻留/选中断言未删改。

L/batching-integration 漏设 PYTHONPATH，系统 Python 找不到主仓库模块；L/batching-integration-v2 加入路径后仍使用系统 Python，缺少 protobuf。随后改用现有 `.venv/llmservingsim-native/bin/python`，未安装新依赖。L/batching-integration-v3 所有正常运行均通过，但最后一项拒绝检查把错误消息写成 computation，后端实际报告 compute phase work/duration/dependency mismatch；修正检查的准确消息，保留其退出码、0 ns 和无传输断言。最终 batching-final 还加入实际分块和纯本地对照，通过 15 次正常执行及 11 项预期拒绝。上述失败目录和日志均保留。

### 产物与停止位置

`python3 R/audit.py` 对应 L/audit.json，退出 0：10532 个旧构建产物摘要未变，恢复已备份的 `.lock-ns3_linux_build`，最终测试代码摘要一致。服务链接 52 个 ASTRA 核心对象，实际装载的十个 ns-3 库全来自 build/m7-continuous-batching/output/lib，没有编入其他后端。`bash scripts/check_deps.sh` 的 L/deps-initial.json 与 L/deps-final.json 均退出 0，13 个固定依赖源码和 gitlink 未变。

M7 在当前一组、一个选定层的范围内完成。路由仍为合成数据、计算为线性参考、硬件未校准；完整模型性能及目标 CM384/CANN 场景未验证。本轮整理记录、本地提交，不推送；M8 多层、多请求、多组组合待用户确认，未开始。

## 2026-10-03：M8 多层、多请求、多组联合执行

用户确认继续 M8，基线为 `e785680`，任务说明提交于 `1eae15e`。本轮修改主仓库服务适配、组合计划、后端校验及小型样例/检查；13 个固定依赖、gitlink 和锁文件未变。使用现有 Linux 工具链及 `.venv/llmservingsim-native`，未安装软件、修改全局配置、初始化升级依赖或下载模型/数据。

以下实际命令 cwd 均为 `/root/moe-ub-cosim`。记 `R=runs/v03-composed-service`、`L=R/logs`，只是本节路径缩写。已审阅的 R/record.py 保存 argv、cwd、开始/结束 UTC、退出状态和 stdout/stderr 路径，各集成目录的 commands.json 另记子命令及输入。原始输出和开发失败均留在忽略目录。

### 实现与独立构建

configured-service/3 和 layer-segments/1 声明同模型结构、同连续 MoE 层段的多个独立组，各组可配置不同放置、容量和批次预算。生成 deployment/4 和 multilayer-plan/2；同组一次选批在各层保持请求与 Token 位置，完整层段结束后才完成一次迭代。合并图只增加 rank 本地层间依赖，旧队列与 UB 对象跨组启动、跨轮保留。同 chip 交付的描述与回调随局部节点号一起组合，不生成 UB task。后端逐层重建图并检查固定配置；端侧操作按实际逐层节点取时间。

前端拆出配置/容量纯校验，复用原生 Scheduler 和每 rank 唯一 KV 池。权重与 KV 按完整模型核算；通信缓冲取选定各层保守界之和，覆盖不同 rank 所处层不同时的重叠。场景入口向每层应用 chunk/发送槽，并按各独立组检查必需预建通道。旧单层、普通服务、P/D、研究导出和多层输入保留原行为。

先运行 R/baseline.py 保存 11035 个旧构建产物摘要及原生启动锁。配置与构建分别为 L/configure.json、L/build.json，均退出 0；开发中后续两次构建 L/build-current.json、L/build-endpoints.json 也退出 0。最终二进制来自最后一次构建：

```bash
cmake -S third_party/ns-3-ub -B build/m8-composed-service -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m8-composed-service/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF
cmake --build build/m8-composed-service \
  --target cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence cosim-lifecycle-test -j 8
```

### 最终检查命令与结果

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py'` 对应 L/python-verified.json，退出 0，77 项通过。新增五项覆盖完整容量/层段缓冲、给定边界下的独立组与逐 Token 进度、KV 暂时等待、各组独立预算以及非法组合。给定时间的单元测试不替代下列真实 UB 执行。

下表每行均实际执行 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/脚本 --build-dir build/m8-composed-service --output-dir R/记录名`，退出 0。summary.json 位于各 R/记录名，外层命令与输出索引为 L/记录名.json。表中全部最终结果对应同一份最终代码，早期通过或失败的目录未覆盖。

| 脚本 | 记录名 | 实际检查 |
|---|---|---|
| check_composed_service.py | composed_service-verified | 15 次正常执行、11 项预期拒绝；两组、三层、多请求、真实队列与逐层来源。 |
| check_continuous_batching.py | continuous_batching-verified | 15 次 M7 运行、11 项拒绝。 |
| check_scenario_backend.py | scenario_backend-verified | 11 次场景运行、24 项拒绝。 |
| check_service_model_capacity.py | service_model_capacity-verified | 6 次原配置服务运行、9 项拒绝。 |
| check_deployment_locality.py | deployment_locality-verified | 14 次本地性运行、9 项拒绝。 |
| check_execution_parameters.py | execution_parameters-verified | 9 次固定图参数对照、13 项拒绝。 |
| check_sequence.py | sequence-verified | 5 次连续图运行、12 项拒绝及生命周期检查。 |
| check_service.py | service-verified | 7 次普通服务运行、9 项拒绝及尾部排空。 |
| check_dynamic_service.py | dynamic_service-verified | 9 次动态到达运行、10 项拒绝。 |
| check_service_boundaries.py | service_boundaries-verified | 7 次边界运行、7 项拒绝。 |
| check_pd_handoff.py | pd_handoff-verified | 7 次 P/D 运行、11 项拒绝。 |
| check_pd_queue.py | pd_queue-verified | 9 次交接队列运行、8 项拒绝。 |
| check_multilayer.py | multilayer-verified | 8 次旧多层研究运行、1 次不挂观测、10 项拒绝。 |
| check_research_export.py | research_export-verified | 9 次旧研究运行、1 次不挂观测、15 项拒绝。 |
| check_shared_bottleneck.py | shared_bottleneck-verified | 7 次原共享瓶颈/来源开关对照。 |

专项输入及独立容量/字节计算依据见 fixtures/m8_composed_service/README.md。service-first 也实际执行了该目录 service.json、layers.json，配合 M1 system、M3 共享瓶颈网络和观测；L/service-first.json 退出 0。最终派生输入、网络、场景 YAML、预算及非法图均保存在 R/composed_service-verified。

| 对照 | 实际结果 |
|---|---|
| 重叠基准 | 两组三层，ep0 三轮、ep1 两轮；七请求生成 12 个 Token、逐层消费 36 条合成路由，124 次真实写交付 31744 业务字节，38 次同 chip 交付，39951 ns 完成。 |
| 独立组与层 | ep0 在 18442 ns 开第二轮，ep1 首轮到 19752 ns 才结束；后者第二轮在 ep0 第二轮的 29731 ns 结束前启动。首轮存在第二层 6284 ns 开始、远端第一层 7874 ns 才结束的交叠，逐节点依赖及计算槽无冲突。 |
| 共享网络因果 | 单独运行两组首轮分别为 11570/12170 ns，重叠为 18442/19752 ns，逐组工作内容不变。交换机 8 端口 4 有 334 字节包在 497 ns 入队、763 ns 出队，跨第二组 600 ns 启动；原生 PortTrace、成功发送与队列积分一致。瓶颈降至 1 Gbit/s 后完成 339142 ns，两组后续提交均推迟。 |
| 容量与预算 | rank 2 为五块时实际 ABD、ACD、AC；减为四块后 ABD、AD、A、C、C，A 释放后接纳 C。ep1 Token 预算为 1 时仅选 EG，F 驻留但不推进且继续计 KV。单独降低 ep0 序列预算也按配置执行，各组预算不相互覆盖。 |
| 本地性与边界 | 同 rank 不传输，同 chip 不进入 fabric；移除 chip 声明后变为 200 次写、51200 字节、零本地交付。空输入在 0 ns 结束；迟到 C 在 500000 ns 才启动；只选第二层正常结束。所有请求/KV、计算/发送槽、事件及网络尾部排空。 |
| 场景与观测 | M6 YAML 入口得到相同三层执行。来源开/关/不挂观测的执行与协议结果一致，开/关 observed 文件一致；逐层端侧时间、业务/整包/控制字节独立核对。 |
| 拒绝检查 | 前端六项拒绝涵盖层路由、组间映射、放置、层段缓冲、最终 KV 和缺少组内通道；后端五项涵盖边界、上下文、跨层重复 tag、静态放置与层序，均在 0 ns 且无业务传输。 |

### 开发中失败与处理

L/frontend-first 发现空的 local_prepare_nodes 值为 null，组合移位时不能直接加偏移；Python/C++ 改为保留 null，仅移动实际节点号，frontend-second 和后续真实执行通过。

L/composed-integration 与 v2 的新检查分别误把本地交付限制为 fabric chunk、使用了本地记录没有的 send_done_ns 字段；按已有本地合同校验单份完整 pair，以及 send_callback_ns/recv_callback_ns 和 `max(ready_ns, recv_registered_ns)`。v3 对空输入错误要求存在原生 PortTrace 文件，改为核验空队列事件和零成功发送。业务量、依赖、回调和非空运行的原生 trace 断言均保留。

L/composed-integration-v4 发现场景解析原先要求全部组的端点两两预建通道。独立 EP 组之间没有业务传输，现明确检查每组端点对的通道覆盖，同时继续核对所有已声明通道的实际路由；新增缺少组内通道的拒绝对照，没有跳过通道检查。

L/composed-integration-v5 与 L/composed-final 已完成场景仿真，但检查读取了错误的原生日志目录。读取锁定 UB 源码确认 backend.json 路径将 trace 写在 execution/runlog，旧 TXT 路径在 execution/network/runlog。队列审计增加显式目录参数，新场景使用实际目录；没有修改 UB、关闭 trace 或删除对照。临时尝试打开已有 trace 开关无助于路径问题，最终未保留该配置变更。composed-final-v2 随后通过全部 15/11 项。

L/pd_handoff-final、L/pd_queue-final、L/research_export-final 的回归发现新入口错误调用旧 PdService/QueuedPdService 并不存在的 deployment 方法。恢复旧 schema 原有部署文件生成路径，仅组合服务调用新方法；最终重新执行上表全部 15 套，代码摘要一致且全部通过。上述失败及首次成功输出均保留，没有改用固定网络时间或修改旧行为预期。

### 产物与停止位置

`python3 R/audit.py` 对应 L/audit.json，退出 0：11035 个旧构建产物摘要未变，恢复已备份的 `.lock-ns3_linux_build`，最终测试的代码摘要一致。链接审计确认 52 个 ASTRA 核心对象全部进入服务，实际装载的十个 ns-3 库均来自 build/m8-composed-service/output/lib，没有编入其他后端。`bash scripts/check_deps.sh` 的 L/deps-initial.json、L/deps-final.json 均退出 0，13 个固定依赖一致且源码未变。

M8 在已声明的连续 MoE 层段范围内完成。完整模型容量包含未执行 dense 层，输出明确区分完整容量和局部执行；真实路由、计算 profile、MLA、多层 P/D、完整 CM384/R1/CANN 行为及目标硬件校准未在本轮完成。路由仍为合成、计算仍为参考，`unvalidated_reference` 不变。本轮整理记录后本地提交，不推送；停在 M8，下一项 M9 等待用户确认。

## 2026-10-03：M9 路由片段与计算依据（首次验收，真实数据访问受阻）

工作目录 `/root/moe-ub-cosim`，M8 基线 `25c252b`，M9 任务说明 `10e0286`。下文 R 为 `runs/v03-route-compute-evidence`，L 为 `R/logs`；记录器保存实际 argv、cwd、开始/结束时间、退出状态和 stdout/stderr，既有日志与失败目录保留。源码仅改主仓库，固定子模块只读。

### 数据访问与导入边界

候选是公开可见的 [core12345/MoE_expert_selection_trace](https://huggingface.co/datasets/core12345/MoE_expert_selection_trace)。2026-10-03 对文件目录的匿名 HTTP 请求为 200；对 `cognitivecomputations/DeepSeek-R1-AWQ/mmlu/abstract_algebra/0.json` 的匿名 resolve GET 返回 401，响应要求获准访问并登录。目录报告文件大小 1922982 字节，但未取得原文件内容。命令 `python3 R/probe_dataset_access.py` 对应 L/dataset-access.json，探测正常退出 0，HTTP 结果及时间另存 R/dataset-access.json；退出 0 表示完成探测，不表示下载成功。

公开页面、文件列表与可匿名下载不是同一权限。HF 的 [gated 数据集说明](https://huggingface.co/docs/hub/datasets-gated) 要求账户获准及认证，hf CLI 也受此约束。本轮未申请访问、接受账户条件、下载模型或整个数据集，没有存储令牌。需要已获准账户的本机登录或合法取得的本地文件后，才能固定原文件修订、摘要、实际布局及选择清单。发布方未披露的采集环境继续记未知。

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.route_evidence --manifest fixtures/m9_evidence/manifest.json --output-dir R/import-functional-routes` 对应 L/import-functional-routes.json，退出 0。七份人工文件共导出 39 条 decode 层记录，服务显式选择其中 36 条。CLI 验证的是本项目 token_layer_map_v1 合同，尚未证明发布方原文件符合该格式；不能把人工输入标为真实模型路由。原始数组入口 0 为 prefill、入口 1 起为 decode 的候选解释仍需原文件核验。

### 构建与非 GPU 检查

配置入口为 `cmake -S third_party/ns-3-ub -B build/m9-route-compute-evidence -G Ninja`，release、根仓库 backend/inject.cmake，输出指向本次 build 下 output。其余开关与 M8 同一联合构建边界，完整实际 argv 保存在 L/configure.json，退出 0。`cmake --build build/m9-route-compute-evidence --target cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence cosim-lifecycle-test -j 8` 对应 L/build.json，退出 0。

`env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py'` 对应 L/python-final.json，退出 0，89 项通过。新增 12 项覆盖观察终点、真实/未知目标、窗口 KV、来源摘要/索引、非法专家、精确形状及统计量、模型维度和节点耗时篡改。给定完成时间的单元测试不替代实际网络执行。

回归统一执行 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_NAME.py --build-dir build/m9-route-compute-evidence --output-dir R/NAME-final`。以下 16 个 NAME 全部退出 0，各自 summary.json、commands.json 及 L/NAME-final.json 保留输入、子命令及拒绝对照：composed_service、continuous_batching、scenario_backend、service_model_capacity、deployment_locality、execution_parameters、sequence、service、dynamic_service、service_boundaries、pd_handoff、pd_queue、multilayer、research_export、shared_bottleneck、data_inputs。外层 `python3 R/verify.py` 的 L/regressions-final.json 退出 0。

`check_evidence_service.py` 的 evidence-distributions-v2 检查退出 0，十次片段服务和两次专家分布执行、八项预期拒绝通过；最终带 GPU 表的检查另记下节。基准仍为两组三层、七请求、12 次 Token 推进、36 条来源记录、124 次真实写和 31744 业务字节。原参考耗时为 39951 ns，人工表为 63256 ns；七请求都只结束观察，正常完成数为零。将请求 201 的真实目标设为 100 后仍只观察至 6；请求 202 真实目标设为 7 时只有它正常完成。KV 缩减、Token 预算、短窗口、场景 YAML 及来源开/关/不挂观测均通过。

将瓶颈从 10 降至 1 Gbit/s，人工表运行结束变为 356759 ns，两组的后续提交推迟；实际队列、原生 PortTrace、数据就绪与事务完成分别核对。来源记录开关不改变协议及执行结果。四份专家输入按 `[4,0]` 或 `[2,2]` 分布时，通信对端、6144 业务字节不变，真实 ASTRA 节点分别执行人工表的 1400/2400 ns。摘要、越界窗口、过短目标、错误 profile 摘要在前端拒绝；节点耗时、求值结果、统计量及缺形状在后端 0 ns 且无业务传输时拒绝。

### 开发中失败

L/window-unit.json 的首轮检查错误地把“文件内容相同”等同于“重复源请求”。不同请求可以选择完全相同的专家，现按规范核对实际路径、原始文件身份及每文件单次分配，继续拒绝同一源请求被重复使用；window-unit-v2 及最终检查通过。

L/profile-unit.json 暴露测试输入缺少 `[3,3]` 上下文和 `[4]` 专家形状。将这些形状明确加入人工表及待测输入，没有开启线性外推或回退。profile-unit-v2 及最终检查通过。

L/evidence-distributions.json 的新增固定图对照误读取仅服务结果才有的 normal_exit。改为检查固定图现有的三个 rank 完成、无剩余节点、空计算/发送资源及实际传输，evidence-distributions-v2 通过；保留逐节点时间、等总量不同分布及等通信量断言。

### A100 实测与最终联合执行

六张 A100 初查均空闲；实际测量使用 GPU 0 NVIDIA A100 80GB PCIe、compute capability 8.0、驱动 610.43.02。L/gpu-before.json、L/gpu-after.json 的 nvidia-smi 查询均退出 0，前后 GPU 0 都为 4 MiB、0% 利用率。没有修改驱动、频率、电源策略或全局环境。

系统与原仿真 venv 无 torch。本次 `python3 -m venv --system-site-packages .venv/m9-profile`（L/profile-venv.json）及以下隔离安装（L/install-profile-torch.json）退出 0：

```bash
.venv/m9-profile/bin/python -m pip install --disable-pip-version-check --no-input \
  --index-url https://download.pytorch.org/whl/cu126 torch==2.8.0 \
  --report runs/v03-route-compute-evidence/profile-install.json
```

采用 [PyTorch 官方版本安装说明](https://pytorch.org/get-started/previous-versions/) 的 2.8.0/cu126 组合。实际 torch 为 2.8.0+cu126、CUDA runtime 12.6、Python 3.12.3；安装报告固定本次所有分发版本与下载摘要，`pip freeze --local` 的 L/profile-freeze.json 退出 0。既有仿真 venv 未改，未安装系统包、下载模型或接受受限数据条件。

```bash
PYTHONPATH=python .venv/m9-profile/bin/python scripts/measure_operators.py \
  --input fixtures/m9_evidence/measure.json \
  --output runs/v03-route-compute-evidence/a100-profile.json --device 0

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_evidence_service.py \
  --build-dir build/m9-route-compute-evidence \
  --output-dir runs/v03-route-compute-evidence/evidence-final \
  --measured-profile runs/v03-route-compute-evidence/a100-profile.json
```

两条命令对应 L/measure-a100.json、L/evidence-final.json，均退出 0。profile 的 SHA-256 为 `6416aa09e0e677134ef6facfd46dbeabc026e3eff70201ec2be9e98f5de50ffb`；原始 22×11 个样本、seed 21、预热 20 次、每样本内部重复 10 次及脚本/输入摘要完整保留。测量张量的 H/F 为 256，Q/KV heads 为 4/2，head_dim 为 64，dtype 为 float16，TP=1。权重、计算与 KV 精度不符均拒绝。实际计时包含 CUDA 执行及 eager 发射间隙，不包含分配/初始化、通信等待、量化或分布式同步。

最终 M9 共 15 次正常执行、8 项预期拒绝通过；11 次为服务（包括 A100 表），4 次为相同总输入的专家分布对照。A100 服务显式使用 10000000 ns 仿真上限和观测结束时间，实际于 3608806 ns 结束，124 次写、31744 业务字节、36 条逐层路由均守恒。七请求均为观察结束而非正常完成；每个实际计算节点与所选原始表行一致，范围外推次数零。

| A100 算子与形状 | 本次 ceil-median ns | 验证范围 |
|---|---:|---|
| attention_decode `[3]` | 151348 | 单请求 GQA SDPA MATH，含发射间隙。 |
| attention_decode `[3,3]` | 295936 | 两请求串行；不是生产批量 attention。 |
| expert_ffn `[4,0]` | 52327 | 一个非空专家、四份输入；实际图节点时长相同。 |
| expert_ffn `[2,2]` | 102196 | 两专家串行、各两份输入；实际图节点时长相同。 |

后两项对应实际通信均为 6144 字节，区别来自专家分布的算子执行方式。结果不能推广为“热点必然更慢”，也不是完整模型或目标 NPU 校准。其余准备残差、局部合并及同 chip 处理继续明确使用参考值，通信等待仍由本次 UB 完成事件产生。

### 最终审计与停止位置

`python3 R/audit.py` 的 L/audit.json 退出 0：11538 个旧产物摘要不变，原生 `.lock-ns3_linux_build` 已从本次备份恢复；89 项 Python 测试、17 套集成检查（M9 加 16 套回归）的最终源码摘要一致。52 个 ASTRA 核心对象全部进入服务，实际装载的十个 ns-3 库只来自 build/m9-route-compute-evidence/output/lib，没有编入其他后端。实测 profile 的输入/脚本摘要、22 行及原始重复样本再次核对通过。

`bash scripts/check_deps.sh` 的 L/deps-initial.json、L/deps-final.json 均退出 0，13 个依赖的固定版本及源码状态一致。未修改子模块源码、gitlink 或锁文件。

本阶段停止时，M9 已验证的代码、人工输入和测量方法保存本地；外部文件、安装报告及实测原始结果保留在忽略目录。真实专家原文件尚未取得，因此未标记整体完成；随后保存为本地进度提交 `81bfc41`。登录后的恢复结果见下节，没有自动开始 M10 或推送。

## 2026-10-03：M9 恢复真实数据访问与验收

用户完成本机 Hugging Face 登录后要求重新检查，账户认证及实际文件下载成功。仍以 `/root/moe-ub-cosim` 为 cwd，沿用上节 R/L 和记录器。凭据由本机标准缓存读取，仅用于 Hugging Face 认证请求；跨域下载重定向不携带认证头，没有将凭据写入命令行、输出或 Git。本轮不申请访问或接受条款，不下载模型权重及整个数据集，不安装软件或改动固定依赖。

### 实际取得的来源

数据集固定修订为 `27febb7b2d24169560a9f6f83d38eb53f94916cf`，接口返回 gated=auto。两份轨迹与数据卡共约 4 MB，保存在忽略目录 `data/m9-route-evidence/<修订>/`。下表的轨迹相对路径均从 cognitivecomputations/DeepSeek-R1-AWQ/mmlu/abstract_algebra 起算。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| 0.json | 1922982 | eb34107bacbd9eae88e475952e08d2beb4ea898e3fbf6d11165555023833e238 |
| 1.json | 2046738 | 2034f7ce869e57d0c145c8ae560fc08cb9a05273338e70f93249ff928d392ddd |
| 数据卡 README.md | 5984 | fdda34b32358d13804976dea8229d2e1b2b8f05df7e2a87336effbdf311009fd |
| deepseek-r1-config.json | 1660 | 79ddea672a62e95d3f0be27be434375538e1988975971cffcf87c96c1de84a65 |

结构配置来自 deepseek-ai/DeepSeek-R1 的固定修订 `56d4cbbb4d29f4355bab4b9a39ccb717a14ad5ad`，仅用于核对 H=7168、61 层、前三层 dense、256 routed 专家与 Top-8，不视作发布者采集时的权重修订。来源报告 R1-AWQ 4bit；采集权重/软件修订、设备、激活精度、EOS 原因和明确许可字段仍未知，不能视为目标 INT8 同条件输入。

仅将元数据写入 fixtures/m9_evidence/published-r1-manifest.json 和 published-r1-selection.json，分别固定来源与选择，摘要为 `43a17f0be71b2cb3870fb05d84cd8ed88b5053a7e336f91b1ddffb14e28ff8ed`、`84ae0989a2e226ff91fc60010978f8e361827781b06d34057362bb59b0859d0f`。原始专家数组与规范化输出均留在忽略目录。

### 命令、输入与结果

下表各命令实际 cwd 均为仓库根；日志索引为 L/记录名.json，包含完整 argv、退出状态及 stdout/stderr 路径。下载与审计辅助脚本位于本机 R，执行前已审阅；可复用的真实路由检查提交到 tests/integration。

| 实际命令（R 为本节路径缩写） | 记录名 | 退出状态与结果 |
|---|---|---|
| `python3 R/check_logged_access.py` | logged-access | 1；认证成功，默认仓库元数据超过脚本 4 MiB 上限，未判作权限错误。 |
| `python3 R/check_logged_access_v2.py` | logged-access-v2 | 0；改为仅请求 sha/gated 元数据，取得数据卡和 0.json，实际下载成功。 |
| `python3 R/download_pair.py` | route-pair-download | 0；取得 1.json 和公开的结构配置，固定所有摘要。 |
| `python3 R/prepare_published_manifest.py` | published-manifest | 0；依据实际文件和结构生成来源/选择清单。 |
| `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_published_routes.py --build-dir build/m9-route-compute-evidence --output-dir R/published-routes` | published-routes | 0；全部源记录、四次联合执行和三项非法选择拒绝通过。 |
| `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py'` | python-published | 0；89 项通过。 |
| `python3 R/audit_published.py` | audit-published | 0；新旧输入、运行时代码及产物摘要核对通过。 |
| `bash scripts/check_deps.sh` | deps-published | 0；13 个固定依赖版本和源码状态一致。 |

原始失败输出保留。第二次访问仅缩小仓库元数据响应，没有改变数据源或关闭文件摘要校验。两轨迹各有 129 个入口，prompt 分别为 26/37 个 Token，后续各有 128 个 Decode 入口。检查全部层、行数、Top-8 互异及专家范围；生产 CLI 导出的 14848 条 Decode 层记录与独立扫描原始数组的结果逐条一致。

选择请求 801 的入口 1/2 和请求 802 的入口 3/4，绝对 Token 位置分别为 26/27、39/40，均在 rank 2；层 3/4/5 共 12 条记录、96 份专家输入。保留原始专家编号，固定放置到 rank [2,7,12]，沿用 M8 同 chip 声明和参考计算；激活 float16 是明确选定的参考精度，不冒充采集精度。

测试驱动复用 M8 图生成器、deployment/4 和 cosim/1 接口，只在实际 WAVE_DONE 后提交下一固定批次，每次运行只有一份 UB 网络。逐条核对路由只消费一次、专家负载、业务矩阵、计算时长与依赖、实际收发回调、原生队列和最终资源排空。请求目标保持 null，不从两步窗口或文件末尾推断 EOS。子命令保存在 R/published-routes/commands.json，各次协议保存在对应 protocol.jsonl。

| 实际对照 | 迭代边界 ns | 业务结果 |
|---|---|---|
| 来源开 / 关 / 不挂观测 | 0 / 147462 / 270334 | 三次执行结果和协议一致；各 78 次写、315392 fabric 业务字节、12 次本地交付，来源开/关 observed 文件相同。 |
| 链路降到 1 Gbit/s | 0 / 1425546 / 2614912 | 业务量不变，下一轮提交随真实网络完成推迟。 |
| 非法选择 | 未启动仿真 | 重用来源文件、越界窗口和错误模型维度均拒绝。 |

### 审计与停止位置

R/audit-published.json 确认此前受检的 112 个源码文件全部未变，11538 个旧构建产物和原生启动锁共 11539 个保护项摘要一致；四次新执行装载的十个 ns-3 库均来自 M9 独立构建。A100 profile 摘要仍为 `6416aa09e0e677134ef6facfd46dbeabc026e3eff70201ec2be9e98f5de50ffb`。本轮没有改动生产代码，因此复用此前 17 套集成检查及链接审计，不重复构建；重新运行单元测试和新增真实来源验收。L/deps-published.json 退出 0，13 个固定依赖未变，未修改子模块源码、gitlink 或锁文件。

M9 完成本步的真实数据导入与计算依据验收。真实 R1 检查仅是 routed 层通信重放，没有用 GQA 替代其 MLA，没有验证完整 R1 的 shared expert、容量或原生调度组合；原生 Scheduler、窗口退出与唯一 KV 池分别已有配置服务验收。A100 小算子维度不同，未套用于 R1；目标 CM384/INT8/CANN 校准仍为 unvalidated_reference。整理记录后本地提交，不推送，停在 M9，M10 仍需用户确认。

## 2026-10-03：M10 固定 CANN 路径的小型行为验收

### 任务与环境

用户确认开始 M10。按当前步骤范围，先完成 `v03-cann-behavior`，不把小型行为检查等同完整目标规模验收。起点为 M9 本地提交 3246f27，工作区干净；任务说明提交为 5863d50。主仓库、13 个固定依赖、根 AGENTS.md、development/current、v0.3 对应章节及继承的 v0.2 行为均已核对，目标目录没有其他适用 agent 文件。子模块只读，未升级或初始化依赖。

本节 cwd 均为 `/root/moe-ub-cosim`，记 R=runs/v03-cann-behavior、L=R/logs；只是路径缩写。经审阅的 R/record.py 保存实际 argv、cwd、UTC 起止、退出状态及 stdout/stderr 文件；集成目录的 commands.json 另存输入和逐次子命令。原始源码、输出及失败证据保留在忽略目录，没有进入 Git。

没有安装系统软件、修改全局配置或下载模型/轨迹数据。公开 CANN 文件仅用于读取，不编译、不执行，不加入 deps.lock.json。固定证据见 fixtures/m10_cann/source-evidence.json：ops-transformer v8.5.0 的 tag 对象为 9efe27c941bc0dbb271c0d3a34a82021b031f0c1，实际提交为 6ead121aded45355043b502756b6592fd7c30b14，八个文件（含 LICENSE）保存大小与摘要。

| 实际命令 | 日志标签 | 退出及结果 |
|---|---|---|
| `bash scripts/check_deps.sh` | deps-initial、deps-final | 均 0，13 个固定提交匹配。 |
| `python3 R/baseline.py` | baseline | 0，保护 12041 份旧构建产物及一份原生启动锁。 |
| `git clone --depth 1 --filter=blob:none --sparse --branch v8.5.0 https://gitcode.com/cann/ops-transformer.git R/cann-source` | cann-source-clone | 0，只读浅克隆固定源码。 |
| `git -C R/cann-source sparse-checkout set mc2/moe_distribute_dispatch_v2 mc2/moe_distribute_combine_v2 mc2/common` | cann-source-select | 0，选择对应文件，没有构建该仓库。 |

### 独立构建与功能检查

L/configure.json 退出 0，实际配置命令如下；生成目录与以前各阶段隔离。

```bash
cmake -S third_party/ns-3-ub -B build/m10-cann-behavior -G Ninja \
  -DCMAKE_BUILD_TYPE=release \
  -DCMAKE_PROJECT_NS3_INCLUDE=/root/moe-ub-cosim/backend/inject.cmake \
  -DNS3_OUTPUT_DIRECTORY=/root/moe-ub-cosim/build/m10-cann-behavior/output \
  -DNS3_ENABLED_MODULES=unified-bus -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
  -DNS3_MPI=OFF -DNS3_MTP=OFF -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
  -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_NATIVE_OPTIMIZATIONS=OFF -DNS3_CCACHE=OFF \
  -DNS3_PRECOMPILE_HEADERS=OFF -DNS3_FETCH_OPTIONAL_COMPONENTS=OFF \
  -DNS3_NETANIM=OFF -DNS3_CPM=OFF -DNS3_VCPKG=OFF

cmake --build build/m10-cann-behavior --target cosim-service cosim-service-deadlock-test \
  cosim-layer cosim-sequence cosim-lifecycle-test cosim-observation-test \
  cosim-write cosim-write-test cosim-layer-delay-test -j 8
```

初次 build 与最终增量 build-final 均退出 0；初次构建完成 534 个目标。最终只修正 CANN Combine 的源 EP 排序表达，不改变已排序输入的结果。没有把其他网络后端编入联合程序。

| 实际命令/输入 | 日志标签 | 退出及结果 |
|---|---|---|
| `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m unittest discover -s tests/unit -p '*test.py' -v` | python-final | 0，95 项通过；包含 6 项 CANN 布局、依赖、拒绝及容量检查。 |
| `build/m10-cann-behavior/output/cosim/ns3.44-cosim-observation-test` | observation-unit | 0，业务/元数据/填充、协议控制及窗口边界检查通过。 |
| `build/m10-cann-behavior/output/cosim/ns3.44-cosim-write-test` | write-unit | 0，写事务匹配、重复/迟到完成及资源回收检查通过。 |
| `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service --input fixtures/m10_cann/service.json --layer fixtures/m10_cann/layers.json --backend build/m10-cann-behavior/output/cosim/ns3.44-cosim-service --system fixtures/m1_compute/system.json --network fixtures/m3_shared_bottleneck/network --observation fixtures/m3_shared_bottleneck/observation.json --output-dir R/service-first` | service-first | 0，首次实际联合执行成功。 |
| `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_cann_behavior.py --build-dir build/m10-cann-behavior --output-dir R/cann-final` | cann-final | 0，13 次服务、2 次单层执行、2 次图生成、8 项预期拒绝，共 25 条子命令。 |

CANN 基准为两组三层、七请求、五轮、36 条逐层路由及 72 份专家贡献：同 rank 14、同 chip 21、跨 chip 37；多个专家命中同一目的 rank 的五处输入仍各自复制。126 次零计数发布保留。305 次实际 UB 写交付 120316 B，其中业务 113664 B、元数据 5616 B、填充 1036 B，另有 123 次同 chip 交付；全部 WQE/回调唯一，结束资源归零并排空网络。

基准完成于 131530 ns；独立 ep0/ep1 分别为 99513/52604 ns，共享运行两组首轮完成及下一轮提交均后移。10 Gbps 改 1 Gbps 后为 1214814 ns、字节相同；原生 FIFO 重建的 queue_byte_ns 从 11686030 增至 121197223。chunk=257 时写次数为 638、字节不变、176178 ns；一个发送槽为 143044 ns、峰值 WQE≤1；一个参考 lane 为 144155 ns；局部成本×100 为 475621 ns。全部同 chip 时无 UB 写，354 次本地交付和全部专家贡献保留，完成于 13330 ns。

源记录开、关、不挂观测的服务结果、实际事件及原生网络日志相同，来源开/关公开观测相同。场景 YAML 正常运行的结果与基准一致。端侧 Dispatch 区间包含重排，Combine 包含归约；十处后层提前于其他 rank 前层结束，未添加全 rank 逐层屏障。按 1 μs 和 10 μs 重采样后归属总量不变，公开输出不含内部来源。

H=1025、两专家同目的 rank 的检查按 chunk=1024/17 分别执行 20/382 次写。两者都是 6150 B 业务、352 B 元数据、118 B 填充；端口 8/4 分别观察业务 2050 B、元数据 160 B、填充 118 B。包内业务范围不含 scale、三元组或填充；32 B 状态不被向量分块参数拆分。三个前端配置和五个 C++ 计划篡改均拒绝，后端拒绝时 now_ns=0 且 transfers 为空。

### 开发失败与受影响回归

首次 cann-integration（退出 1）发现旧 observed 校验仍要求 rank∈[0,11]，而执行层与本场景使用非连续编号 2、7、12、20、25、30。依据 v0.3 §3 的参数化要求，将公开 rank 校验改为与 ExecutionPlan 同一整数范围，保留非空、唯一、有序及类型检查；未增加公开字段。cann-integration-v2（退出 1）在已完成的单层运行上误用了服务结果的 normal_exit/network_drain 字段；改为检查该驱动实际具有的全部图完成、迟后事件、活动 WQE、回调和资源归零字段。失败日志保留，未修改业务预期数值或删除依赖断言；最终 cann-final 通过。

旧回归统一命令为 `env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_NAME.py --build-dir build/m10-cann-behavior --output-dir R/NAME-final`。以下 16 套均退出 0，原脚本及预期未改，各目录 summary.json、commands.json 和 L/NAME-final.json 保存实际输入、子命令和预期拒绝：composed_service、continuous_batching、scenario_backend、service_model_capacity、deployment_locality、execution_parameters、sequence、service、dynamic_service、service_boundaries、pd_handoff、pd_queue、multilayer、research_export、shared_bottleneck、data_inputs。外层 `python3 R/verify.py` 的 regressions-final 退出 0。

另外两套既有验收实际运行并退出 0：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_evidence_service.py \
  --build-dir build/m10-cann-behavior --output-dir runs/v03-cann-behavior/evidence-final \
  --measured-profile runs/v03-route-compute-evidence/a100-profile.json

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_published_routes.py \
  --build-dir build/m10-cann-behavior --output-dir runs/v03-cann-behavior/published-final
```

evidence-final 使用已有 22 行 A100 表，不重新测量；published-final 使用已有固定版本的真实 R1-AWQ 本地文件，不新增下载。分别保留 M9 的窗口退出/精确形状使用与真实来源逐条核对。上述旧 16 套部分运行先于 rank 白名单修复；按最终 observation.py 另行完整重跑 check_research_export.py，输出 R/research-export-ranks、日志同名，退出 0。新 CANN 导出和最终 95 项单元测试也使用最终版本。R/audit.json 逐项标记这一已复验的后续差异；其余生产代码摘要一致。

### 最终审计与停止位置

`python3 R/audit.py`（L/audit-final.json）退出 0：12041 份旧构建产物摘要未变，一份原生启动锁从已验证备份恢复；libAstraSim.a 的 52 个核心对象全部进入联合链接，实际装载的 10 个 ns-3 库全部来自 build/m10-cann-behavior/output/lib，没有其他 ASTRA 网络后端。固定 CANN 提交及八文件摘要再次匹配，源码 checkout 干净。源码、测试、夹具最终摘要及检查记录在 R/audit.json；主仓库依赖锁和全部子模块未改。

本小步完成，整理记录后本地提交、不推送，下一步仍由用户确认。当前仅执行源码适配的声明式 CANN 行为和 URMA_WRITE/RTP 参考事务；逻辑 lane、保守缓冲/归约屏障及局部耗时仍是近似。未执行或校准原生 CANN/NPU，未将 A100 小算子或合成小模型改名为 R1；原生事务对应、完整 R1/MLA/shared expert 及 CM384/128 rank 目标规模检查仍未完成，M10 整体保持未完成。

## 2026-10-03：M10 目标参考合同与实现缺项核对

用户确认继续后，执行上次 current.md 指定的事务选择与模型缺项核对，任务 v03-target-contract-audit。起点 14c1c8f、工作区干净，任务说明提交 e7d646a。本步只新增离线审计脚本/参考合同及文档，生产前后端、规范、依赖、模型文件均未修改，不实施完整模型或新事务模式。

本节 cwd 均为 `/root/moe-ub-cosim`，R=runs/v03-target-contract、L=R/logs。审阅后复用 R/record.py，JSON 保存实际 argv、cwd、UTC 起止、退出状态及 stdout/stderr；实际命令如下，均退出 0。

| 命令 | 标签/结果 |
|---|---|
| `bash scripts/check_deps.sh` | deps-initial、deps-final；13 个固定依赖匹配。 |
| `python3 R/baseline.py` | baseline；保护 12575 份已有构建产物和一份原生启动锁。 |
| `git ls-remote https://github.com/deepseek-ai/DeepSeek-V3.git refs/heads/main` | source-revision；固定提交 9b4e9788e4a3a731f7567338ed15d3ec549ce03b。 |
| `python3 R/fetch_source.py` | source-download；从上述固定提交的 raw.githubusercontent.com URL 读取 inference/model.py、LICENSE-CODE，单文件上限 256 KiB；实际分别为 32831、1064 B。完整 URL/摘要见 R/source.json。 |
| `python3 R/prepare_reference.py` | prepare-reference；生成 fixtures/m10_target_contract/reference.json，保留 selected_reference 与非执行状态。 |
| `env PYTHONPATH=python .venv/llmservingsim-native/bin/python scripts/audit_target_contract.py --source-dir R/source --output-dir R/audit` | contract-audit；离线算式、源摘要、14848 条 Decode 层记录与七项实际拒绝通过。 |
| `env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service --input fixtures/m10_cann/service.json --layer fixtures/m10_cann/layers.json --backend build/m10-cann-behavior/output/cosim/ns3.44-cosim-service --system fixtures/m1_compute/system.json --network fixtures/m3_shared_bottleneck/network --observation fixtures/m3_shared_bottleneck/observation.json --output-dir R/cann-smoke` | cann-smoke；复用已有构建，完成真实小型 ASTRA/UB 服务。 |
| `python3 R/final_audit.py` | final-audit；生产与依赖未变、全部受保护文件一致、联合结果与上一阶段相同输入完全一致。 |

没有下载模型权重或新的轨迹，没有使用 GPU、安装软件、修改全局配置、重新构建或执行外部源码。新取得的 DeepSeek-V3 文件只用于核对 R1 所采用结构的投影/FFN 形状、LoRA norm、路由修正向量以及 absorbed MLA 的 latent+RoPE 缓存；版本、摘要和适用范围保存在参考合同。没有将该实现的 TP/EP 绑定、FP8 算子或其软件版本宣称为本场景原生运行。

R/audit/audit.json 的完整权重形状账目包含 61 层（3 dense、58 MoE）、每 rank 两个 routed/一个 shared expert、embedding、独立 lm_head、norm/router/路由修正。参考存储为主要矩阵对称 INT8+每输出通道 FP32 scale，非量化类型逐组件列出：权重 24400011264 B、scale 24624384 B，合计 24424635648 B/rank；参数总量排除 MTP、包括路由修正，为 671026419200。

参考 BF16 压缩 MLA KV 为每 Token `61*(512+64)*2=70272 B`，每 16 Token 块 1124352 B；没有套用 GQA 的多头 K/V 公式。选定 64 GB 条件预算、4 GiB kernel/runtime reserve、逐层 64 MiB 数据窗口+1 MiB 状态窗口+28928 B staging，并按 58 层保守求和，条件剩余 KV 为 31325587712 B、27861 个整块。这些仅为显式参考政策的离线结果，不是服务已接入、权重已加载、实际可分配 HBM 已测或部署已证明。

七项调用现有验证器的拒绝分别为 MLA 架构、INT8 权重、所选层 shared expert、dense 层执行、一个小输入产生超过 4096 条 transfer、17 个观测端口和 4097 个窗口。另独立计数证明目标 128 rank 时，每层仅 Dispatch 状态就有 32512 条非同 rank 交付（同 chip 256、跨 chip 32256），58 层为 1885696 条，尚不含向量和 Combine；未生成该目标图或据此宣称实际内存耗尽。目标端口选择、采样时域、全 fabric 多平面路由与记录开销仍需后续验证。

两份既有真实路由文件的摘要、61 层格式、所有专家 ID/Top-8 与 14848 条 Decode 层记录再次核验。prompt=26/37，各 128 步，window64 按块预留分别为 6746112/7870464 B；两者符合最小步数，但 manifest 只有两个独立请求，不足目标的 1024 个。未扫描完整数据集，未重复样本补数量，未发起扩大下载。

真实小型复查仍为 305 次 UB 写、120316 B 载荷、131530 ns，R/cann-smoke/service_result.json 与 execution/result.json 分别和 runs/v03-cann-behavior/service-first 的结果完全相同；十个 ns-3 库仍全部来自 M10 构建，网络正常排空。生产源码未改，因此复用前步完整单元/集成/链接验收，不重复编译或扩大回归。R/final-audit.json 记录源码与合同摘要、12575 份旧产物及原生锁原样未变；本步不需要恢复启动锁。

审计结论与代码位置见 [target_readiness.md](target_readiness.md)：当前参考实验继续使用已选 WRITE/RTP，原生 LD/ST 未启用、不是前置阻塞；下一首要实现是 MLA/量化权重容量及原生 KV 接纳。审计完成后本地提交，再将唯一下一实现任务置为 pending，等待用户确认，不推送、不自动开发。完整模型计算、目标图/观测规模、拓扑和足够独立真实样本仍待后续；M10 整体未完成，校准状态不变。

## 2026-10-03：M10 MLA/量化权重容量与原生 KV 接纳

用户确认后执行 `v03-mla-model-capacity`，起点 `9c487ed`、工作区干净。依次读取开发说明、当前任务、v0.3 §4.1/4.3/4.4/4.8/6、v0.2 §9 和目标参考合同。新增独立 model/2 MLA 存储及容量审计，model/1 GQA 含义不变；小模型审计结果进入已有 ConfiguredScheduler/ConfiguredMemory 的原生唯一块池，原生 Scheduler 与 KV 实现未修改。完整 dense/shared 库存可以离线核算，执行层继续拒绝 dense/selected shared expert 和不匹配的 GQA/A100 profile。

本节 cwd 均为 `/root/moe-ub-cosim`；R=`runs/v03-mla-model-capacity`，L=`R/logs`，P=`.venv/llmservingsim-native/bin/python`。审阅后复用 `R/record.py`；每条记录包含实际 argv/cwd/UTC 起止/退出码及 stdout/stderr 路径。未安装软件、修改全局配置、下载权重/轨迹或调用 GPU；未改规范、后端、子模块、锁文件或 gitlink。后端沿用已验证的 `build/m10-cann-behavior/output/cosim/ns3.44-cosim-service`，无需重新构建。

### 命令与结果

下表 P/R/L 均按上文展开；外层记录方式为 `python3 R/record.py 标签 实际命令`。全部最终检查退出 0；集成内部非法输入的退出 1 为显式预期，逐条记录在各输出目录 commands.json。

| 实际命令 | 标签及结果 |
|---|---|
| `bash scripts/check_deps.sh` | dependencies-before、dependencies-final，13 个递归固定依赖匹配、工作区干净。 |
| `python3 R/baseline.py` | baseline，记录 12575 份旧 `.o/.a/.so/ns3.44-cosim*` 产物及一份原生启动锁摘要。 |
| `python3 R/fixtures.py` | fixtures，生成显式小型及 R1 容量输入，不调用被测函数产生预期。 |
| `python3 R/routes.py` | synthetic-routes，人工路由专家 3→5，固定新摘要并声明 synthetic_test。 |
| `env PYTHONPATH=python P -m moe_ub_cosim.capacity_audit --input fixtures/m10_mla_capacity/toy-capacity.json --output R/toy-audit.json` | toy-audit，独立双 rank 手算核对；早期报告在调整纯离线 compute_validation 文案之前。 |
| `env PYTHONPATH=python P -m moe_ub_cosim.capacity_audit --input fixtures/m10_mla_capacity/r1-capacity.json --output R/r1-final.json` | r1-final，完整 128 rank 库存，scope=capacity_only、compute_validation=not_executed，未创建执行图。初次 r1-audit 报告另保留。 |
| `env PYTHONPATH=python P scripts/audit_target_contract.py --source-dir runs/v03-target-contract/source --output-dir R/target-crosscheck` | target-crosscheck，沿用前步独立公式和已有固定源文件；七项旧 schema/执行限制拒绝仍成立。 |
| `env PYTHONPATH=python P -m unittest discover -s tests/unit -p '*test.py' -v` | unit-all，102 项通过，包括新增 7 项 MLA 测试。 |
| `env PYTHONPATH=python P -m unittest discover -s tests/unit -p mla_capacity_test.py -v` | unit-mla-first、unit-mla-final，分别 6/7 项；最终新路由夹具与量化/非量化算式均通过。 |
| `env PYTHONPATH=python P tests/integration/check_mla_capacity.py --build-dir build/m10-cann-behavior --output-dir R/mla-ub-final` | mla-ub-final，七次实际服务命令全部退出 0。 |
| `env PYTHONPATH=python P R/reject.py` | reject-inputs，七项 MLA 错误输入在创建运行目录/启动后端之前退出 1，检查脚本退出 0。 |
| `python3 R/final_audit.py` | final-audit，19 类 tensor ×128 rank 与旧独立审计逐项比较；源结构与固定 R1 配置匹配；受保护产物和启动锁全部未变。 |

最终 unit-all 使用的生产代码与提交版本一致；其运行后只增加观察夹具的跨芯片专家选择，最终 unit-mla-final 与 mla-ub-final 再核验该变更。`R/final-audit.json` 记录最终代码、测试、夹具摘要、源文件与报告摘要；源码注释/文档不替代运行结果。

### 独立算式与真实接纳结果

手算 H=8 双层 MLA 的 rank 2/7 分别有一/两个 routed expert，另有本地 shared expert，权重为 1748/1908 B（含 448/512 B scale）。KV/token=16 B、block=64 B；KV dtype 改 FP32 后四块变两块，绑定头或改 BF16 非量化矩阵的权重差值分别核验。错误维度、布局、scale/zero point/padding、dtype、重复 rank、shared 结构、整数溢出、无可用块、host 块上限等均拒绝。CLI 的输入/输出与服务输入分离，不允许借离线库存标志执行未实现层。

完整 R1 的 61 层按 3 dense+58 MoE、每 rank 两个 routed/一个 shared expert、全部 MLA 投影/norm、embedding/head/router/修正和 scale 计算。128 个 rank 的 19 类 tensor 形状、副本、参数、张量字节和 scale 均匹配旧独立审计，权重张量 24400011264 B、scale 24624384 B、合计 24424635648 B/rank；压缩 BF16 latent+RoPE KV/token=70272 B，16 Token 块=1124352 B。条件 device=64000000000 B、workspace=4294967296 B、通信=3954809344 B 时可容纳 27861 个完整块。通信仍是离线声明的预算，并未生成/验证完整 R1 通信图。

小型参考模型每 rank 权重 10408520 B、KV/token=384 B、block=1536 B，workspace=4096 B，三层通信预算=6304512 B；未执行的 dense 0 仍计容量。默认八块，七请求、两组三层服务共 36 条路由/72 份贡献，305 次 UB 写、120316 B 总载荷（113664 B 业务），完成于 131530 ns。三块预算时等待至释放后接纳，八轮、377 次写、143142 ns；业务字节不变，增加的轮次计数消息被真实发送。两个各需两块的请求占四块，没有对它们合并取整。

同一 device 预算下，MLA 五个投影由 BF16 存储改 INT8+输出通道 FP32 scale，权重减少 2836736 B，块数从 3 变 1849，实际首轮请求从 [201] 变 [201,202]，KV 等待消失；计算阶段参考成本没有随存储假设伪装成实测 INT8 算子。慢网 1 Gbps 为 1214814 ns，业务/总载荷不变，本组后续提交随 UB 实际完成后移。

最终窗口对照传输 61440 B 跨芯片业务：window-resident 为 244 次写、67568 B 载荷、82614 ns；window-wait 为 316 次写、69872 B 载荷、100610 ns。Token 预算为 1 时，rank 20 首次仅调度 401，但 401/402 同时驻留且分别占 1/2 块；402 未计算仍占 KV。全部七请求结束为 OBSERVATION_ENDED，target=null、eos_known=false、done=0，恰好一次 observation_cleanup，无正常 release；容量收紧时 cleanup 后同一边界接纳下一个请求。所有场景坚持唯一池、账目守恒、一个网络实例、实际数据就绪/事务完成/发送回调，结束清空 KV 并排空网络。

### 开发期问题与受影响回归

首次 `mla-ub-first` 的七种对照通过，但复查发现其观察夹具沿用专家 0..3，恰好都位于同芯片，跨芯片只有计数状态。为加强业务数据覆盖，增加 `business_bytes>0` 断言并构造专家 3→5 的合成输入；第一次内联构造脚本（python3 标准输入，退出 1）遇到 dense 层显式 null，抛出 TypeError，尚未写入任何路由文件或 manifest。随后启动的 `mla-ub`（退出 1）使用旧夹具，正确触发新增业务字节断言，日志在 L/mla-ub.*，没有改变断言或降低预期。

修复后的 R/routes.py 只跳过规范明确的 dense null，对 MoE ID 进行映射，固定新文件摘要，仍声明 synthetic_test。最终 mla-ub-final 全部通过。原中间目录和失败记录保留，未清理未知文件、修改预期数值或用固定网络耗时代替 UB。

五套旧回归脚本及原预期未改。统一命令为 `env PYTHONPATH=python P tests/integration/check_NAME.py --build-dir build/m10-cann-behavior --output-dir R/标签`；evidence_service 另加 `--measured-profile runs/v03-route-compute-evidence/a100-profile.json`，只复用既有 A100 小形状表，不测量、不用于 MLA。

| NAME / 标签 | 子命令数（成功 / 预期拒绝） | 检查结果 |
|---|---:|---|
| service_model_capacity / regression-capacity | 15（6 / 9） | GQA/MHA、稀疏映射、最终 KV 预留与观测不变。 |
| continuous_batching / regression-batching | 26（15 / 11） | 连续批次、未选驻留容量、回调与释放通过。 |
| composed_service / regression-composed | 26（15 / 11） | 多层多组共享执行、来源与资源边界通过。 |
| evidence_service / regression-evidence | 27（19 / 8） | 窗口退出、已知目标和既有计算表使用通过。 |
| cann_behavior / regression-cann | 25（17 / 8） | CANN 字节、贡献、依赖、观测/真值与实际 UB 回归通过。 |

本任务已完成，记录与代码本地提交，不推送；当前任务停在 completed，不自动进入下一项。完整 R1 只完成容量核算，小型 MLA 只执行既有 MoE 图和参考阶段成本，未执行/校准 MLA 或量化内核；完整 61 层计算图、dense/shared 执行、目标规模图/观测、CM384 拓扑与足够真实路由仍待后续确认任务。M10 整体未完成。

## 2026-10-04：M10 完整 Decode 参考计算图

用户确认回到 M10 主线后，完成 `v03-full-model-graph`。起点为上一容量提交 `72be3bd`，任务说明单独提交 `71918e0`。读取开发说明、任务和 v0.3 §4.1–4.6/4.8/6、继承的 v0.2 §7/§9；模型结构参考已固定的本地 DeepSeek-V3 源码和 model/2 合同，没有下载新源码、模型或轨迹，没有安装软件或使用 GPU。

新 configured-service/4 将 embedding、逐块 norm/MLA/残差、dense FFN 或 router+CANN routed/shared 汇合、final norm/head/sampling 组合为 full-model-plan/1。逐阶段参考成本绑定模型结构/精度；注意力按实际上下文长度、专家按实际贡献数计算。Python 与 C++ 独立重建和核对图，deployment/5 固定服务模型/成本，Chakra 必须逐节点匹配。参考 sampling 不执行数值文本生成。每 rank 仍是原有一个计算槽，shared/routed 两路都完成才进入下一块；没有新增逐层全 rank 屏障。服务仍复用原生 Scheduler、唯一 KV 池和实际 UB 事件。

本节 cwd 均为 `/root/moe-ub-cosim`。R=`runs/v03-full-model-graph`，L=`R/logs`，P=`.venv/llmservingsim-native/bin/python`。审阅后复用 R/record.py，记录实际 argv、cwd、UTC 起止、退出码和 stdout/stderr；集成脚本另记录各次子命令及输入。旧测试脚本未改。新构建为 `build/m10-full-model`，其 output 也单独保存；配置产生的原生启动锁在构建后用本任务开始前的副本恢复。子模块源码、gitlink、deps.lock.json 和技术规范均未改。

### 实际命令与检查

下表所有最终命令退出 0；集成子命令中的退出 1 是明确的非法输入验收。命令前均由 `python3 R/record.py 标签` 记录。

| 命令 | 标签与结果 |
|---|---|
| `bash scripts/check_deps.sh` | dependencies-before、dependencies-final；13 个递归依赖保持固定版本。 |
| `python3 R/baseline.py` | baseline；记录 12575 份旧产物和一份原生启动锁摘要。 |
| `cmake -S third_party/ns-3-ub -B build/m10-full-model -G Ninja ...` | configure；完整实际选项在 L/configure.json，沿用已验证的仅 unified-bus 模块、backend/inject.cmake、独立 output 和禁用依赖自动获取配置。 |
| `cmake --build build/m10-full-model --target cosim-service cosim-service-deadlock-test cosim-layer cosim-sequence cosim-lifecycle-test cosim-observation-test cosim-write cosim-write-test cosim-layer-delay-test -j8` | build；540 步通过，既有依赖/CANN 警告保留。后续 build-final 重编六个使用完整图校验的目标，仅修正无共享专家时的零成本校验。 |
| `env PYTHONPATH=python P -m unittest discover -s tests/unit -p '*test.py' -v` | unit-final；108 项通过（新增 6 项完整图测试）。较早 unit-all 为 107 项。 |
| `env PYTHONPATH=python P tests/integration/check_full_model.py --build-dir build/m10-full-model --output-dir R/full-final-v2` | full-final-v2；8 次场景对照、1 次空请求服务和 6 次后端预期拒绝通过。 |
| `env PYTHONPATH=python P tests/integration/check_full_model_structure.py --build-dir build/m10-full-model --output-dir R/structure-final` | structure-final；61 块和 MoE/dense/MoE 两次真实小 EP 联合执行通过。 |
| `python3 R/final_audit.py` | final-audit-final；旧产物/锁原样未变，实际装载的十个 ns-3 库仅来自新的 ns-3-UB 构建，记录源码摘要。 |

### 完整图实际结果

四块合成 MLA 模型（H=1024，1 dense + 3 MoE，各 1 shared expert，六个 routed expert/Top-2）在两组三 rank 上运行。每 rank 权重及 scale 为 12786248 B，KV/token=384 B、块=1536 B；基准为 8 块，收紧后为 3 块。原生池的接纳、等待、按请求分别取整和回收逐事件核对，最终占用均清零。36 条 Decode 层路由、72 份专家贡献均只消费一次。

| 对照 | 完成 ns | UB 写次数 | 载荷 B | 业务 B |
|---|---:|---:|---:|---:|
| 基准 / 关闭来源记录 / scenario 入口 | 131279 | 305 | 120316 | 113664 |
| shared expert 单 Token 成本从 500 增到 100000 ns | 1809914 | 305 | 120316 | 113664 |
| sampling 单 Token 成本从 19 增到 100000 ns | 727340 | 305 | 120316 | 113664 |
| 每 rank KV 从 8 块收紧到 3 块 | 148489 | 377 | 122620 | 113664 |
| 窗口轨迹、第二组每轮 Token 预算为 1 | 83098 | 244 | 67568 | 61440 |
| 瓶颈 10 Gbps → 1 Gbps | 1214788 | 305 | 120316 | 113664 |

基准五轮记录 630 个外层阶段，其中 294 个为空批次零成本记录、不创建 COMP。CANN 子图的量化准备、计数/重排、专家、打包和归约另独立核算，无重复注意力或 router 成本。24 个有本地 Token 的 MoE 汇合点均等两路；实际出现六次 rank 间层执行重叠，未添加全局层屏障。所有 COMP 时长、依赖、SEND transaction_done、RECV 数据就绪/登记时刻和最终反馈均与原始执行记录核对。

来源开关前后 execution/result.json 完全相同；导出的公共操作仅 Dispatch/Combine，内部模型阶段不进入公共观测。窗口场景七个请求均为 OBSERVATION_ENDED、target_tokens=null、eos_known=false，done 数为零，观察清理不伪装成完成释放；未选中的驻留请求仍计入 KV。六类篡改（阶段时长、shared/routed 依赖、块覆盖、成本域、运行中声明、有效 protobuf 的 COMP 时长）均在 0 ns、尚未交付业务前被拒绝。

61 块结构采用 EP=3、E=6、H=1024、一个合成 Token，前三块 dense、后 58 块 MoE。Python 与 C++ 共同验证并实际执行 3102 个节点、986 次交付（754 次 UB 写），于 315509 ns 完成。另一 MoE/dense/MoE 场景验证不连续 MoE 层号和无 shared expert；共享 FFN 与 shared/routed 相加均为零成本且无 COMP。未使用完整 R1 维度、256 专家、128 rank 或 CM384 网络，也没有提高 4096 transfer 上限。

### 开发期问题与回归

初次手工初始化 evidence 时保留了旧 manifest 内的模型名，前端明确报 Route model differs from execution；随后为复用的人工轨迹创建新的显式 synthetic manifest 和摘要，没有把人工路由声明为真实模型采集。

unit-initial 退出 1：新增测试误将 CANN 内部量化阶段又计入外层 model_stages，并读取不存在的 cur_output；正确外层计数为 4 个首尾 + 8 个 dense + 3×10 个 MoE 外层阶段。unit-full-v2 又错误假定初始 reached 等于 prompt；原生请求已有 generated_tokens，最终测试按输入的 prompt+generated 检查尚无反馈时进度不变。失败日志保留，未修改生产进度语义。unit-full-v3 五项通过，补充无共享专家零成本检查后 unit-final 全部 108 项通过。

full-integration 首次退出 1，原因是新测试错误要求慢 shared 分支必须最后结束。实际单计算槽中 shared FFN 会阻塞本地路由分支的重排/归约，最后结束的仍可为 routed。后续验收按资源语义检查增加 shared 耗时确实推迟完成，并保留两路独立依赖与所有实际时长检查；没有增加计算槽或固定网络延迟。full-integration-v2 通过。复核无共享专家模型后进一步要求 shared_routed_add 也为零，防止多算不存在的相加；build-final、unit-final、full-final、structure-final 使用最终代码全部通过。早期 full-structure 结果保留，不用其无共享专家耗时作为最终结果。最终差异复查补充空请求服务：正常退出但没有提交图时 full_model_graph_execution_validated=false，初始/失败清单也明确为 false；full-final-v2 九次正常运行和六次拒绝通过，unit-full-final 六项再次通过。

以下七套旧回归使用原脚本和预期，统一命令为 `env PYTHONPATH=python P tests/integration/check_NAME.py --build-dir build/m10-full-model --output-dir R/标签`。evidence_service 另加 `--measured-profile runs/v03-route-compute-evidence/a100-profile.json`，只复用已有旧域数据，不用于新的 MLA 图。

| NAME | 标签 | 结果 |
|---|---|---|
| mla_capacity | regression-mla | 七种原生池/MLA 库存对照保持原结果。 |
| service_model_capacity | regression-capacity | 15 条子命令（6 成功、9 预期拒绝）。 |
| continuous_batching | regression-batching | 26 条（15 成功、11 预期拒绝）。 |
| composed_service | regression-composed | 26 条（15 成功、11 预期拒绝）。 |
| evidence_service | regression-evidence | 27 条（19 成功、8 预期拒绝）。 |
| cann_behavior | regression-cann | 25 条（17 成功、8 预期拒绝），含实际 scenario 与来源隔离。 |
| write | regression-write | 18 条（7 成功、11 预期拒绝）；早/晚接收、TP 回收、单字节及十个库来源检查通过。 |

这些旧路径不调用完整模型成本校验；最后仅收紧无共享专家成本条件，使用完整图最终两套集成重新验证，没有无理由重复旧回归。

本步完整参考图任务完成，本地提交、不推送，current.md 保持 completed，不开始下一任务。完整目标 R1/CM384、目标图/观测规模、完整 fabric、多平面和足够独立真实路由仍未验收；NPU/MLA/量化内核未执行或校准，M10 整体未完成。下一首要工作是目标规模图与观测管理，等待用户确认。
