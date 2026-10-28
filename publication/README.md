# 完整源码分支与固定依赖

此分支提供完整联合仿真工程：`python/`、`backend/`、`scripts/`、`tests/`、`fixtures/`、`examples/`、`experiments/`和`docs/`，不是只有报告或补丁。13个递归子模块的原始固定Git提交也发布在同一GitHub仓库的`codex/deps/*`分支中；`dependencies.json`逐项记录路径、提交、源码树和原始来源。固定SimAI完整源码归档保存在`third_party/source_archives/`，保留上游许可证。

## 取得工程和全部源码依赖

```bash
git clone --branch codex/full-simulator-20261029 https://github.com/hhwqeedadsa/llmserving-cosim-docs.git moe-cosim
cd moe-cosim
python3 scripts/init_published_deps.py
python3 scripts/init_published_deps.py --verify-only
bash scripts/check_deps.sh
```

初始化命令从当前公开仓库取得13个固定提交，以本克隆对象库作为共享缓存，然后初始化递归子模块；不会要求访问师兄的私有分支。原`.gitmodules`与`deps.lock.json`保持来源和版本记录，URL替换只对这条Git命令生效，不改全局Git配置。已有子模块HEAD不同或有本地改动时明确拒绝，不替换用户工作。

使用SSH时可传`--mirror git@github.com:hhwqeedadsa/llmserving-cosim-docs.git`。所有子模块保留原作者、许可证和Git历史；原生ns-3和ns-3-UB仍是独立依赖，不能混链。

## 准备SimAI源码

```bash
mkdir -p runs/simai-source
tar -xzf third_party/source_archives/SimAI-a57d765c.tar.gz -C runs/simai-source
```

解压后的上游根目录名称由归档保留；按[SimAI准备小例](../examples/simai_frontend/README.md)将该目录传给`scaleout simai-prepare --source-root`。公开准备命令会逐项核固定Git blob，并仅适配两个延迟导入入口；没有把人工调度器冒充SimAI。依赖包安装、原生构建和实验执行继续使用[用户指南](../docs/user_guide.md)，源码初始化成功不等于已经编译或硬件校准。

## 代码与运行产物

包含可复现所需的版本化样例、实验配置、测试、精简报告及固定第三方源码自带的测试/profile数据。模型权重、用户原始专家路由、虚拟环境、编译缓存和完整`runs/`工作目录按原仓库规则保留在本机。真实路由实验仍需按来源清单取得相应原件；没有用重复请求替代真实输入。

文档站继续保存在仓库的原有文档分支；此分支可直接查看和克隆全部仿真工程源码。
