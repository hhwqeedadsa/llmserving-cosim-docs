# LLM Serving / ASTRA-sim / MoE-UB 联合仿真文档站

这是一个独立的 Sphinx 文档工程，使用 Furo 主题。内容覆盖：

- ASTRA-sim、LLMServingSim 与 MoE-UB 联合仿真器的组件输入输出；
- 构造一次仿真实验所需的系统信息；
- 通信需求如何展开、竞争并形成尾时延；
- `observed/`、`truth/`、中间图与服务指标的可视化方法；
- SimAI/LLMServingSim 的必要实验、代表性 GPU/NIC 时间切片，以及 SimCCL→ns-3 逐流证据链；
- 三套系统之间的能力增量、减量、粒度、限制与规模上限；
- 已验证结论与尚未校准部分的证据等级。

## 构建

```bash
git clone https://github.com/hhwqeedadsa/llmserving-cosim-docs.git
cd llmserving-cosim-docs
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
make html
.venv/bin/python -m http.server 8000 --directory build/html
```

然后访问 `http://127.0.0.1:8000/`。

也可以使用：

```bash
./serve.sh
```

## 更新示例可视化数据

站内交互图使用 `moe-ub-cosim/runs/m5_t12` 的压缩快照。更新方式：

```bash
.venv/bin/python tools/build_sample_data.py \
  --run-dir /path/to/moe-ub-cosim/runs/m5_t12 \
  --output source/_static/sample_run.js
make html
```

数据脚本只复制绘图需要的字段，不复制完整运行目录。

## 重建研究报告静态图

报告中的 11 张 SVG 使用仓库内的精简 CSV/JSON 快照，可离线、确定性重建：

```bash
.venv/bin/python tools/build_study_figures.py
```

如需从第 06 轮完整实验目录刷新快照：

```bash
.venv/bin/python tools/build_study_figures.py \
  --analysis-dir /path/to/第06轮_SimAI与LLMServingSim_trace分析_20260924
```

每张图在报告正文中均标注数据来源、物理量与单位、证据类型和不可推导的结论。

## 目录

```text
source/                 Sphinx 文档源文件
source/_static/         样式、交互图脚本与示例数据快照
source/_static/study/   研究报告 SVG 与精简证据快照
tools/                  从标准 run/实验目录生成可视化快照
build/html/             构建后的网页
```

## GitHub Pages

推送到 `main` 分支后，`.github/workflows/pages.yml` 会严格构建文档并部署到
GitHub Pages。仓库首次发布时，需要在 GitHub 的 **Settings → Pages → Build and
deployment → Source** 中选择 **GitHub Actions**。

公开网页地址：<https://hhwqeedadsa.github.io/llmserving-cosim-docs/>
