# M5.3 固定目标源码与参考模型核对

`audit.json` 固定 DeepEP v1.2.1 的单机四 rank 普通路径、调用配置、13 个公开源文件摘要、八项行为证据和未验证状态。中文结论见 [模型核对记录](../../docs/model_validation.md)。它是审计输入，不是 DeepEP 运行配置或新通信模板。

检查器只用 Python 标准库和本地参考计划生成器，不导入目标代码、不运行 CUDA，也不发起网络请求。`--source-dir` 必须包含 audit.json 的 `source_files` 所列文件，保留各相对路径；可从每项固定 `url` 取得，校验器检查 SHA-256、大小与 Git blob SHA-1。不得用默认分支源码替代。

本次保存路径为 `runs/m5-3-model-audit-20260929/sources/deepep`；版本元数据、原始文件和获取命令位于同级 `sources/`。这些下载文件与运行产物按仓库约定不提交 Git，重要实验需另行备份。

从根目录执行；使用已经按 [多层样例](../m5_multilayer/README.md) 构建的联合后端。本次实际命令如下，日志和退出状态见 [环境记录](../../docs/environment.md)。重跑需换用新的输出目录，检查器拒绝覆盖旧结果。

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_multilayer.py \
  --build-dir build/m5-multilayer \
  --output-dir runs/m5-3-model-audit-20260929/multilayer-regression

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_model_audit.py \
  --source-dir runs/m5-3-model-audit-20260929/sources/deepep \
  --regression-dir runs/m5-3-model-audit-20260929/multilayer-regression \
  --output-dir runs/m5-3-model-audit-20260929/audit-final
```

第一条实际调用研究入口并检查连续执行、局部阶段依赖、端侧与端口、被动观测和错误输入。第二条独立重算布尔 rank 布局与专家计数，检查 117 个参考计划、空布局算式和上述运行的 130 个层实例；再核对真实 UB 唯一交付、输入/产物摘要、版本与未校准状态。结果写入 `audit-final/result.json`，保留每例矩阵、专家数、字节量、研究清单及检查器摘要。

其中目标元数据数值是源码逻辑存储的推导值，不是目标执行或物理链路测量。`functional_checks_passed=true` 与 `target_validation` 分开；已知通信控制、同步及缓冲区差异不会被功能通过覆盖。将来被审阅源码发生变化时应重新核对并保存新记录，不能只更新摘要来消除检查失败。
