复现、验证与网页构建
====================

固定版本
--------

.. list-table:: 当前锁定依赖
   :header-rows: 1
   :widths: 28 72

   * - 组件
     - commit
   * - LLMServingSim
     - ``a4053bc1161872420e1e0607cb3409ef659b828e``
   * - casys-kaist/astra-sim
     - ``d3469945c50410cac2e727c9ab627670c965db2a``
   * - casys-kaist/chakra
     - ``30221ab8bfd9212aa9c063cd0b14a7d0a2ea6dd7``
   * - ns-3-UB
     - ``581bf28a90740def0e79eafe5423af838c6f687d``

复现 LLMServingSim
------------------

仓库：

.. code-block:: bash

   export LLMSERVINGSIM_ROOT=/path/to/LLMServingSim
   cd "$LLMSERVINGSIM_ROOT"

构建 ASTRA analytical 后端需要固定 protobuf、编译器和子模块。当前环境记录的核心命令是：

.. code-block:: bash

   cd astra-sim/build/astra_analytical
   export TOOLCHAIN_ROOT=/path/to/toolchain
   export PATH="$TOOLCHAIN_ROOT/venv/bin:$TOOLCHAIN_ROOT/protobuf/bin:$PATH"
   export PROTOBUF_FROM_SOURCE=True
   export CMAKE_PREFIX_PATH="$TOOLCHAIN_ROOT/protobuf"
   export CC=/usr/bin/gcc
   export CXX=/usr/bin/g++
   export CMAKE_GENERATOR=Ninja
   bash ./build.sh

运行功能示例：

.. code-block:: bash

   ./serving/run.sh

运行确定性回归：

.. code-block:: bash

   PYTHON=/path/to/llmservingsim-venv/bin/python \
     ./serving/validate.sh --clocks-only

该回归比较 58 个场景的固定 cycle 基线；环境缺包造成的 exit 1 不能解释为模型回归。

LLMServingSim 每个组件的检查点
------------------------------

* Workload：确认请求数量、arrival、input/output token；
* Router/Scheduler：确认 waiting/running、batch token budget 和 KV block；
* TraceGenerator：使用 ``--save-trace-text`` 保留每轮 trace；
* GraphGenerator：使用 ``--keep-inputs`` 保留 Chakra 图；
* Controller/ASTRA：检查 ``Waiting ... cycle=<ns>``；
* 输出：检查 request CSV、TTFT/TPOT/ITL 和总 cycle；
* Bench：使用同一 token IDs、sampling 与 KV block 数对照真实 vLLM。

复现联合框架
------------

.. code-block:: bash

   export MOE_UB_COSIM_ROOT=/path/to/moe-ub-cosim
   cd "$MOE_UB_COSIM_ROOT"
   export PYTHONPATH="$PWD/python"
   PY=/path/to/llmservingsim-venv/bin/python

   "$PY" -m moe_ub_cosim validate-config \
     --config fixtures/m5/service_t12.json

   "$PY" -m moe_ub_cosim run \
     --config fixtures/m5/service_t12.json \
     --run-dir runs/my_t12 \
     --ranks 6

   "$PY" -m moe_ub_cosim validate-run \
     --run-dir runs/my_t12

成功条件：

* ``validate-config`` 输出 OK；
* 联合后端 exit code 为 0；
* ``validate-run`` 输出 OK；
* manifest ``clean_finish=true``；
* 输入 hash 与快照一致；
* 逐 transfer 和逐端口窗口守恒；
* observed 白名单无全局时间泄漏。

完整测试套件
------------

.. code-block:: bash

   bash scripts/run_m2_tests.sh   # SEND/RECV、字节矩阵、依赖、发送槽、生命周期
   bash scripts/run_m3_tests.sh   # 共享竞争、反馈调度、忙闲到达、失败路径
   bash scripts/run_m4_tests.sh   # P/D、KV 容量、Profile、布局
   bash scripts/run_m5_tests.sh   # 守恒、观测隔离、时钟、可复现

网页文档
--------

.. code-block:: bash

   git clone https://github.com/hhwqeedadsa/llmserving-cosim-docs.git
   cd llmserving-cosim-docs
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   make SPHINXBUILD=.venv/bin/sphinx-build html
   .venv/bin/python -m http.server 8000 --directory build/html

浏览 ``http://127.0.0.1:8000/``。

更新交互示例
------------

.. code-block:: bash

   .venv/bin/python tools/build_sample_data.py \
     --run-dir /path/to/moe-ub-cosim/runs/m5_t12 \
     --output source/_static/sample_run.js

生成器会：

* 检查 manifest ``clean_finish``；
* 读取请求指标、transfer registry 和 port windows；
* 计算 ready/transaction latency quantiles；
* 只保留最繁忙的若干端口时间序列；
* 输出不依赖 fetch 的 JavaScript 快照，使构建后的站点可离线浏览。

复现时应保留的证据
------------------

* 原始输入、commit、工具链和命令；
* wall clock、peak RSS、simulated time；
* manifest 和 input SHA-256；
* 完整 stdout/protocol/endpoint logs；
* observed/truth/metrics；
* 对照实验的唯一改变量、预测与证伪条件；
* 任何垫片、外推、未校准字段和失败运行。
