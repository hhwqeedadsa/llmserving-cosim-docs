# 持久工作队列的功能检查

对象是[无时间C++核心](../../backend/rdma/gin_work_queue.hh)，本检查独立于ns-3。当前持久字节与事务适配器已使用该核心，不能把早期“尚未接入”描述当作整个当前平台状态。检查不加载模型、真实路由或研究输入，不表示现代DeepEP、NCCL或物理QP运行成功。源码语义依据见[固定GIN/GDAKI核查](../../docs/review_artifacts/nccl_gin_20261022/report.html)，完整后端尚待实现的范围见[实施规范](../../experiments/paper_readiness/modern_backend_contract.md)。本例没有前端任务生成或性能结果。

从仓库根使用已有C++17编译器：

```bash
timeout 60s python3 tests/integration/check_gin_work_queue.py \
  --output runs/my-gin-work-queue
```

`--output`必须是新目录；`--cxx`可选择已存在的编译器，默认`c++`。不安装、不构建依赖、不执行在线服务。编译使用`-Wall -Wextra -Werror -pedantic`。全命令60秒，子进程地址2GiB、CPU50秒，RSS验收≤1GiB，输出≤8MiB且卷预留8GiB；失败保留日志，不自动重试或扩限。

输出包含编译器版本、编译/检查stdout/stderr、二进制及`verification.json`，记录实际命令、cwd、退出状态、子进程资源和源码hash。版本化[验收摘要](verification.json)对应多条批量reservation实现；旧单槽阶段保留在Git历史与原运行目录，没有覆盖失败或修改旧网络结果。

| 独立检查 | 预期行为 |
|---|---|
| 深度2、3个reservation | 第三个拿到ticket，但旧槽完成前不能prepare |
| 较后描述符先准备 | 不能跨过未准备的前项发布；prepare本身不触发NIC消费 |
| 完成反馈延迟 | publication和NIC消费均不释放槽；只有合法CQ前缀允许复用 |
| 两工作累计完成 | 允许一次覆盖多个工作；重复、倒退或超过已消费工作的完成明确拒绝 |
| flush快照后再reserve | 后来的工作不扩大既有flush目标，但保持队列未排空 |
| 空闲后再追加 | ticket保持递增，连接资源对象不会自动关闭；未排空不得close |
| 同operation多个WQE | work_id不同可引用同一应用operation；重复work_id拒绝 |
| 两个队列与NIC映射 | 完成相互隔离；不同GIN连接可绑定同一声明设备 |
| 环形槽长期复用 | 深度4追加1,024个工作，反序prepare、按序消费、分批完成均正确 |
| 独立生命周期枚举 | 深度2/3工作全部合法交错共49个状态，逐状态核对前缀、槽owner、flush和排空 |
| 连续多条reservation | 一次取得整段ticket；末尾槽仍被占用时，即使段首物理槽空闲也不能prepare |
| 跨环边界与分批CQ | ticket3/4跨深度4环边界；ticket0完成后才允许整段准备；可独立完成部分WQE |
| 完整区间发布 | 拒绝把发布位置截在reservation中间；允许一个doorbell覆盖多个完整区间 |
| 批量拒绝的原子性 | 空区间、超过深度/累计预算、段内/历史重复ID均拒绝，不消耗ticket |
| 批量独立状态枚举 | 深度4，reservation长度2/1/2，122个合法状态；oracle按整段旧槽owner和每条生命周期核对 |

枚举的oracle用每工作阶段与前一槽owner完成状态表达，不调用被测类的可写判断来生成预期；当前共4,976项断言。覆盖有限规模，不是任意并发或整个provider的形式化证明。

源码依据是固定NCCL `12df1a1`内`doca_gpunetio_dev_verbs_qp.cuh`：`reserve_wq_slots`原子增加count并等待`base+count-1`可用，常规`mark_wqes_ready`按完整区间推进前缀。`onesided.cuh`的thread put按chunk数预留，put_signal额外预留原子条目；这说明API操作与WQE条目不应等同，不表示当前小token put必然需要多条。固定文件身份与定位见[来源记录](source_basis.json)。

本核心支持显式前缀发布和正常availability检查，不覆盖CPU_PROXY_FREE_FLOW的逐条可见路径及SKIP_AVAILABILITY_CHECK。`prepared_prefix`只是描述符已准备前缀，不等同provider的`sq_ready_index`。每次区间长度≤depth是当前支持范围；不声称provider拒绝更长请求，也不推断其实际可执行性。每条core work对应一个WQE条目，不按描述符字节或MTU计算槽位。原子副作用、远端GPU可见、CQE格式、并发线程内存序、NIC仲裁、源buffer寿命、包重试和真实队列深度仍不在该核心内；不能据断言数推导硬件准确性或论文贡献。
