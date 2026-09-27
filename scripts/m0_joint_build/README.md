# M0 联合链接探针

本目录验证固定 ASTRA 核心、Chakra 读取器与 ns-3-UB 能否在同一程序中链接。它不实现后端适配器，不运行计算图或网络通信。

- `inject.cmake`：通过 UB 主项目的 `CMAKE_PROJECT_NS3_INCLUDE` 入口加载原 ASTRA 库，再延迟加入探针目标。
- `CMakeLists.txt`：在 UB 已创建模块后调用其 `build_exec`，用 whole-archive 引入 ASTRA 全部核心对象，输出源文件清单和 link map。
- `link_probe.cc`：写入并读取一个元数据节点，构造 ASTRA 资源对象，核对 UB 类型和初始 ns-3 时钟，报告进程实际装载库。
- `audit.py`：核对编译源文件、C++ 标准、静态库全部成员、关键符号、ELF 依赖、link map 和运行时库路径。

先读取仓库开发说明与当前任务。已验证的环境、完整配置/构建/运行命令和失败记录见 [M0.3 环境记录](../../docs/environment.md#2026-09-27m0-3-joint-build-boundary)。CMake 至少为 3.24；本次使用 3.28.3、GCC 13.3.0 和 C++ Protobuf 3.21.12。配置依赖 M0.1 已生成的固定 Chakra protobuf C++ 文件。

UB 作为 CMake 主项目；不能直接以本目录作为 `cmake -S` 入口。只构建 `m0-joint-link-probe` 目标，使用独立缓存和输出目录。探针输出文件、审计输出目录必须是新路径，避免覆盖旧证据。

UB 配置会重写子模块中的忽略文件 `.lock-ns3_linux_build`。执行前备份，构建结束后恢复已确认的原文件，并核对原生程序与库的校验值。本次备份、恢复与检查记录见上述环境记录；构建缓存、产物、启动锁和本机库路径清单不提交 Git。

通过条件仅限本机的构建、链接、读写检查。1,000 ns COMP 的事件执行和 UB 业务收发由 M1 单独验证。
