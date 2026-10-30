# 备用准入时刻的严格核验

修复前，HelperObserver只核对事件单调和同步回复身份，未将accepted绑定到选择该候选的HELPER_COMMIT.now_ns，CPU反例能回填过去的accepted；延后accepted和input_post也没有严格拒绝。原生当前模型在同刻执行Start且声明零控制准入成本，因此这些记录都不合法。

修复后在修改贡献状态前检查accepted等于对应同步COMMIT时刻，input_post等于accepted。没有改变原生执行、已有包、专家计算或任务完成；不是新恢复机制，也不宣称安全漏洞或硬件收益。

85项不同相关单元检查通过，新增2项。公开rdma-check离线核验原正常/故障/备用三组及迁移目录通过；三个双流同步篡改（回填accepted、延后accepted、延后input_post）分别因新约束被拒绝。原件SHA保持，零新网络/编译/依赖修改/GPU。初始失败反例保留于`runs/recovery_semantics_20261030/monitors/reproduce`。

CPU检查使用`PYTHONPATH=python .venv/frontend/bin/python -B -m unittest discover -s tests/unit -p inflight_helpers_test.py -v`；保存原件核验使用：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B \
  tests/integration/check_helper_admission_time.py \
  --runs /path/to/normal-local-helper-runs --output /path/to/new-audit
```

[验证与命令](verification.json)与[源码、资源收据](receipt.json)保存实际范围；[接口](../../docs/inflight_helpers.md)说明未来非零控制成本须另行声明。原尝试实际开始观测的另一个限制见[贡献绑定](../../docs/contribution_binding.md)，不能因本次准入修复就宣称该问题已解决。
