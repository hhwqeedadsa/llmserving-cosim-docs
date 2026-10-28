# 准备真实SimAI前端

本入口核对Alibaba SimAI固定提交`a57d765c5478f319c5f5ab6fdc53f9cc7f461743`，准备其Vidur分支的真实Cluster、Orca调度器、Batch、Request和显存规划代码。本阶段只完成CPU调度功能验收，尚未提供与ns-3联动的SimAI运行命令，不代表300卡实验完成。

已有该固定版本源码时，从仓库根目录运行：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout simai-prepare \
  --source-root /path/to/fixed-SimAI-source --output /path/to/new-simai-runtime
```

`--source-root`是SimAI仓库根目录，含`vidur-alibabacloud/`与`LICENSE`。输入可以是该提交的Git检出或源码归档解压目录；按固定Git树的165个blob验证内容，不能以路径名或当前HEAD代替内容核对。代码、模型配置和Apache许可复制到新的运行目录，记录原Git blob SHA1与原/有效SHA256。

固定源码归档可从[GitHub API](https://api.github.com/repos/aliyun/SimAI/tarball/a57d765c5478f319c5f5ab6fdc53f9cc7f461743)取得。本轮已取得完整归档并按其Git树验证所用文件；不要下载模型权重或使用会随HEAD变化的主分支归档替代。

只有两个包初始化文件改为延迟导入未使用的注册器，避免仅加载调度器时强制引入sklearn预测器。调度、请求、组批、显存规划方法保持上游原字节；不伪造缺失的预测器或模块。加载器再次核对实际内容，拒绝改动的方法、未知Python文件和混入的其他Vidur路径。准备过程不安装软件，不执行SimAI或网络。

实际加载调度器需要既有`networkx`；后续Chakra导出还需要`protobuf`。可选依赖集名为`simai`，本轮使用既有环境，不自动安装。来源固定清单是`python/moe_ub_cosim/simai_source.json`，不更新主仓库现有子模块。

本轮验证调用SimAI原生Cluster初始化EP64及容量属性，使用其真实Orca调度器处理三个声明ready-KV请求。batch上限2，批次依次为前两个请求的两轮、第三个请求的两轮，最后全部完成且KV块全部释放；验证没有导入LLMServingSim调度器或sklearn。初始KV状态和正时间原点为显式声明，未声称执行Prefill或实机算子。

完整CPU验收命令（使用含networkx的既有Python）：

```bash
PYTHONPATH=python python3 tests/integration/check_simai_frontend.py \
  --source-root /path/to/fixed-SimAI-source --output /path/to/new-check
```

测试还拒绝方法篡改和额外模块。300卡级真实路由仍需要稀疏请求映射、统一ns-3完成反馈和本次执行的包级累计到达记录，不能用前轮4卡结果代替。
