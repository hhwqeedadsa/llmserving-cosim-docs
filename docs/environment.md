# 环境与运行记录

## 当前状态

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
