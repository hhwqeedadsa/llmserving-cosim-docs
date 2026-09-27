# 环境与运行记录

## 当前状态

目标开发环境为原生 Linux，本机是 LXC 容器中的 Linux。2026-09-27 已完成独立沙箱诊断、`repo-local-check` 及 M0.1 的 UB、LLMServingSim 两项原生样例。13 个依赖仓库静态检查通过；两项样例分别构建并重复运行通过。用户授权后为 LLMServingSim 安装了 Protobuf 系统依赖，新增 Python 依赖放在项目专用虚拟环境，未升级已有系统软件。

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
| 联合编译与仿真 | 未实现、未执行 |

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
