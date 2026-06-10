# DCQCN Report PPT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a 9-slide PPTX from `docs/export.zip` using the approved DCQCN report PPT design.

**Architecture:** Use the Presentations skill mandated `@oai/artifact-tool` presentation JSX workflow. Build temporary editable slide modules under `outputs/<thread>/presentations/dcqcn-report-ppt/`, render previews/contact sheet through the bundled helper scripts, export the final PPTX to `docs/dcqcn-pfc-joint-tuning-report.pptx`, then clean temporary presentation workspace artifacts.

**Tech Stack:** Node.js ESM, bundled `@oai/artifact-tool`, artifact-tool presentation helper scripts, Python only for zip/image extraction and contact-sheet helpers already used by the skill, existing `docs/export.zip`.

---

## File Structure

- Create temporary workspace: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/`
  - Responsibility: source notes, claim spine, design system, copied figure assets, slide modules, rendered previews, layout JSON, contact sheet, and QA scorecard during build.
- Create final deliverable: `docs/dcqcn-pfc-joint-tuning-report.pptx`
  - Responsibility: final 9-slide editable PowerPoint deck.
- No repo-local generator is required. The final artifact is the user-facing PPTX.

## Task 1: Prepare Presentation Workspace And Assets

**Files:**
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/source-notes.txt`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/claim-spine.txt`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/design-system.txt`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/contact-sheet-plan.txt`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/assets/*.png`

- [ ] **Step 1: Define workspace paths**

Run:

```bash
export SKILL_DIR=/Users/ytxing/.codex/plugins/cache/openai-primary-runtime/presentations/26.601.10930/skills/presentations
export WORKSPACE=/Users/ytxing/workspace/ns-3-ub-gitcode/outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt
export SLIDES_DIR=$WORKSPACE/slides
export PREVIEW_DIR=$WORKSPACE/preview
export LAYOUT_DIR=$WORKSPACE/layout
export ASSET_DIR=$WORKSPACE/assets
export QA_DIR=$WORKSPACE/qa
export OUTPUT_DIR=/Users/ytxing/workspace/ns-3-ub-gitcode/docs
export FINAL_PPTX=$OUTPUT_DIR/dcqcn-pfc-joint-tuning-report.pptx
mkdir -p "$SLIDES_DIR" "$PREVIEW_DIR" "$LAYOUT_DIR" "$ASSET_DIR" "$QA_DIR"
```

Expected: command exits with status 0.

- [ ] **Step 2: Verify artifact-tool runtime**

Run:

```bash
node "$SKILL_DIR/scripts/check_presentation_runtime.mjs" --workspace "$WORKSPACE"
```

Expected: JSON output indicates `hasExportPptx` is `true` and presentation JSX exports are available.

- [ ] **Step 3: Extract approved figure assets from `docs/export.zip`**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
import shutil
import zipfile

root = Path("/Users/ytxing/workspace/ns-3-ub-gitcode")
zip_path = root / "docs/export.zip"
asset_dir = root / "outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/assets"
asset_dir.mkdir(parents=True, exist_ok=True)
figures = [
    "01_paper40_cutoff_dual_axis.png",
    "02_paper40_red_dual_axis.png",
    "03_incast400_paper_default_sender_rate.png",
    "04_incast400_t10us_paper_default_dual_axis.png",
    "05_incast400_uneven_bct10m_recovery_dual_axis.png",
    "06_incast400_uneven_bct4m_recovery_dual_axis.png",
    "07_incast400_uneven_bct1m_recovery_dual_axis.png",
    "08_incast400_k64k_256k_pmax01_dual_axis.png",
    "09_incast400_pmax04_dual_axis.png",
    "12_incast400_summary_throughput.png",
    "13_incast400_summary_fairness.png",
    "14_incast400_4to1_tuned_dual_axis.png",
    "15_incast400_8to1_tuned_dual_axis.png",
]
with zipfile.ZipFile(zip_path) as archive:
    names = set(archive.namelist())
    report = "export/dcqcn-pfc-joint-tuning-report.md"
    missing = [report] if report not in names else []
    missing += [f"export/_figures/{name}" for name in figures if f"export/_figures/{name}" not in names]
    if missing:
        raise SystemExit("missing zip entries:\n" + "\n".join(missing))
    for name in figures:
        with archive.open(f"export/_figures/{name}") as src, (asset_dir / name).open("wb") as dst:
            shutil.copyfileobj(src, dst)
print(f"assets={len(figures)}")
PY
```

Expected:

```text
assets=13
```

- [ ] **Step 4: Write required planning notes**

Create these files:

`$WORKSPACE/source-notes.txt`

```text
Task mode: create
Primary deck-profile: engineering-platform
Sources:
- /Users/ytxing/workspace/ns-3-ub-gitcode/docs/export.zip
- export/dcqcn-pfc-joint-tuning-report.md
- export/_figures/*.png

Source rules:
- Use report text and figures only.
- Do not rerun simulation.
- Do not redraw experimental plots.
- Built-in diagrams may express DCQCN/PFC mechanisms and OpenUSim implementation mapping.
- No identity assets, logos, mascots, or third-party marks are used.
```

`$WORKSPACE/claim-spine.txt`

```text
Thesis: OpenUSim reproduces DCQCN/PFC behavior and the 400G scenario requires retuning feedback, recovery, and marking parameters.
Audience: technical review / R&D.
Arc: mechanism -> implementation mapping -> 40G marking comparison -> 400G default mismatch -> feedback/recovery/marking tuning -> scale-out convergence.

01 DCQCN 400G 仿真评估概览 | proof: throughput summary | source: 12_incast400_summary_throughput.png
02 DCQCN 控制闭环与 PFC 协同边界 | proof: CP/NP/RP/PFC mechanism diagram | source: report mechanism text
03 OpenUSim 中的 DCQCN / PFC 实现映射 | proof: implementation mapping diagram | source: report implementation framing
04 40G baseline：RED 与 cut-off 打标对比 | proof: cutoff and RED plots | source: 01 and 02 figures
05 400G 场景下论文默认参数表现 | proof: sender rate plot | source: 03 figure
06 反馈周期收紧后的收益 | proof: t10us queue/throughput plot | source: 04 figure
07 BCT 配置对恢复过程的影响 | proof: BCT 10M/4M/1M plots | source: 05, 06, 07 figures
08 ECN 标记强度的折中关系 | proof: weak/strong marking plots | source: 08 and 09 figures
09 Incast 规模扩展下的收敛表现 | proof: 4->1 and 8->1 plots | source: 14 and 15 figures
```

`$WORKSPACE/design-system.txt`

```text
Slide size: 1280x720
Background: #FFFFFF
Typography: Aptos Display for title, Aptos for labels/body
Palette: ink #1C232B, muted #5E6974, accent #00668A, secondary #7E4523, panel #F5F7F9, line #CAD1D8
Layout: image-led, restrained title bar, small page marker, bottom proof-note rail
Diagram grammar: equal-role rounded rectangles, attached connectors, blue for DCQCN data/control path, brown for PFC protection path
Chart grammar: use existing figure PNGs as dominant proof objects
Footer/source: no dense source footer; source provenance is in source-notes
```

`$WORKSPACE/contact-sheet-plan.txt`

```text
Macro layouts:
01 single chart overview
02 mechanism diagram
03 implementation mapping diagram
04 two-chart comparison
05 single dominant figure
06 single dominant figure
07 three-figure recovery comparison
08 two-figure marking comparison
09 two-figure scale-out comparison

Checks:
- No slide has more than two support bullets.
- No three consecutive slides use identical macro layout.
- Figure area dominates each data slide.
```

Expected: files exist and contain no placeholder markers.

## Task 2: Build Editable Artifact-Tool Slide Modules

**Files:**
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-01.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-02.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-03.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-04.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-05.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-06.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-07.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-08.mjs`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/slides/slide-09.mjs`

- [ ] **Step 1: Create shared slide helper module**

Create `$SLIDES_DIR/common.mjs` with helper functions using `ctx.addText`, `ctx.addShape`, and `ctx.addImage`. Required helpers:

- `asset(ctx, filename)` returns `${ctx.assetDir}/${filename}`
- `baseSlide(presentation, ctx, title)` creates a slide, white background, title, page marker, and title rule
- `bottomRail(ctx, slide, bullets)` creates one bottom rail with at most two bullet text boxes
- `singleFigure(ctx, slide, filename)`, `twoFigures(ctx, slide, left, right)`, and `threeFigures(ctx, slide, a, b, c)` add existing PNGs as dominant proof objects
- `box(ctx, slide, opts)` creates a rounded rectangle plus centered text
- `arrow(ctx, slide, from, to, opts)` adds a visible connector or line with semantic direction

Use these geometry constants:

```js
export const C = {
  ink: "#1C232B",
  muted: "#5E6974",
  accent: "#00668A",
  secondary: "#7E4523",
  panel: "#F5F7F9",
  line: "#CAD1D8",
  white: "#FFFFFF",
  pfcPanel: "#FFF7EC",
  mapPanel: "#F0F9FC",
};
```

Expected: helper module imports no external package and exports the named helpers.

- [ ] **Step 2: Create slide modules**

Each `slide-XX.mjs` must export `async function slideXX(presentation, ctx)` and call `baseSlide(...)`. Use these exact slide titles and bullets:

1. `DCQCN 400G 仿真评估概览`
   - `OpenUSim 支持 DCQCN 闭环行为复现。`
   - `400G 场景下需重新审视反馈、恢复与标记参数。`
   - Figure: `12_incast400_summary_throughput.png`
2. `DCQCN 控制闭环与 PFC 协同边界`
   - `DCQCN 负责端到端速率调节。`
   - `PFC 定位为本地队列保护机制，避免队列极端冲高。`
   - Diagram nodes: Sender RP, Switch CP, Receiver NP, PFC pause/resume. Connect Data -> ECN-marked data -> CNP feedback. PFC path uses secondary color.
3. `OpenUSim 中的 DCQCN / PFC 实现映射`
   - `OpenUSim 按 DCQCN 论文角色建模 CP / NP / RP。`
   - `PFC 阈值与 pause/resume 行为作为链路级保护机制实现。`
   - Diagram nodes: OpenUSim Sender / RP rate control, OpenUSim Switch / CP ECN marking, OpenUSim Receiver / NP CNP generation, DCQCN paper roles, PFC threshold model.
4. `40G baseline：RED 与 cut-off 打标对比`
   - `cut-off 在阈值后集中打标。`
   - `RED 在较低队列水位开始渐进概率打标。`
   - Figures: `01_paper40_cutoff_dual_axis.png`, `02_paper40_red_dual_axis.png`
5. `400G 场景下论文默认参数表现`
   - `默认参数下恢复偏慢，瓶颈链路利用率不足。`
   - `主要约束来自反馈时延与恢复节奏失配。`
   - Figure: `03_incast400_paper_default_sender_rate.png`
6. `反馈周期收紧后的收益`
   - `将反馈闭环缩短至 10us 后，吞吐显著提升。`
   - `DCQCN 更早介入，PFC 触发压力下降。`
   - Figure: `04_incast400_t10us_paper_default_dual_axis.png`
7. `BCT 配置对恢复过程的影响`
   - `较小 BCT 可加快恢复，但会提高速率波动。`
   - `4MB 在恢复速度与稳定性之间更均衡。`
   - Figures: `05_incast400_uneven_bct10m_recovery_dual_axis.png`, `06_incast400_uneven_bct4m_recovery_dual_axis.png`, `07_incast400_uneven_bct1m_recovery_dual_axis.png`
8. `ECN 标记强度的折中关系`
   - `弱标记降低有效吞吐。`
   - `强标记提升响应强度，但可能放大短窗口内的速率抖动。`
   - Figures: `08_incast400_k64k_256k_pmax01_dual_axis.png`, `09_incast400_pmax04_dual_axis.png`
9. `Incast 规模扩展下的收敛表现`
   - `4->1 与 8->1 场景仍可收敛。`
   - `规模越大，PFC 的保护作用越明显。`
   - Figures: `14_incast400_4to1_tuned_dual_axis.png`, `15_incast400_8to1_tuned_dual_axis.png`

Expected: there are exactly 9 `slide-XX.mjs` modules.

- [ ] **Step 3: Render each slide for syntax and visual export smoke**

Run:

```bash
for i in 01 02 03 04 05 06 07 08 09; do
  node "$SKILL_DIR/scripts/render_artifact_slide.mjs" \
    --workspace "$WORKSPACE" \
    --slide-module "$SLIDES_DIR/slide-$i.mjs" \
    --output "$PREVIEW_DIR/slide-$i.png" \
    --layout "$LAYOUT_DIR/slide-$i.layout.json" \
    --slide-size 1280x720
done
```

Expected: command exits with status 0 and creates 9 preview PNGs.

## Task 3: Export PPTX And Run Mechanical QA

**Files:**
- Create: `docs/dcqcn-pfc-joint-tuning-report.pptx`
- Create: `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/qa/comeback-scorecard.txt`

- [ ] **Step 1: Build final deck through artifact-tool**

Run:

```bash
node "$SKILL_DIR/scripts/build_artifact_deck.mjs" \
  --workspace "$WORKSPACE" \
  --slides-dir "$SLIDES_DIR" \
  --out "$FINAL_PPTX" \
  --preview-dir "$PREVIEW_DIR" \
  --layout-dir "$LAYOUT_DIR/final" \
  --contact-sheet "$PREVIEW_DIR/contact-sheet.png" \
  --manifest "$WORKSPACE/artifact-build-manifest.json" \
  --slide-count 9 \
  --slide-size 1280x720
```

Expected: JSON manifest prints `slideCount: 9` and `outputBytes` greater than 0.

- [ ] **Step 2: Verify PPTX package structure**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
import re
import zipfile

path = Path("/Users/ytxing/workspace/ns-3-ub-gitcode/docs/dcqcn-pfc-joint-tuning-report.pptx")
assert path.exists(), path
assert path.stat().st_size > 0, path.stat().st_size
with zipfile.ZipFile(path) as zf:
    names = zf.namelist()
slides = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
media = [n for n in names if n.startswith("ppt/media/")]
assert len(slides) == 9, slides
assert len(media) >= 10, len(media)
print(f"slides={len(slides)} media={len(media)} bytes={path.stat().st_size}")
PY
```

Expected: output starts with `slides=9 media=` and the command exits with status 0.

- [ ] **Step 3: Verify title text and bullet limits in PPTX XML**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
import html
import re
import zipfile

path = Path("/Users/ytxing/workspace/ns-3-ub-gitcode/docs/dcqcn-pfc-joint-tuning-report.pptx")
expected = [
    "DCQCN 400G 仿真评估概览",
    "DCQCN 控制闭环与 PFC 协同边界",
    "OpenUSim 中的 DCQCN / PFC 实现映射",
    "40G baseline：RED 与 cut-off 打标对比",
    "400G 场景下论文默认参数表现",
    "反馈周期收紧后的收益",
    "BCT 配置对恢复过程的影响",
    "ECN 标记强度的折中关系",
    "Incast 规模扩展下的收敛表现",
]
with zipfile.ZipFile(path) as zf:
    for idx, title in enumerate(expected, start=1):
        xml = zf.read(f"ppt/slides/slide{idx}.xml").decode("utf-8")
        texts = [html.unescape(m) for m in re.findall(r"<a:t>(.*?)</a:t>", xml)]
        joined = "\n".join(texts)
        assert title in joined, (idx, title, joined)
        assert joined.count("•") <= 2, (idx, joined.count("•"), joined)
print("titles=ok bullets<=2")
PY
```

Expected:

```text
titles=ok bullets<=2
```

- [ ] **Step 4: Run layout-quality check**

Run:

```bash
node "$SKILL_DIR/scripts/check_layout_quality.mjs" \
  --layout-dir "$LAYOUT_DIR/final" \
  --max-text-text-overlap 12 \
  --max-text-image-overlap 20
```

Expected: exits with status 0, or reports only documented false-positive warnings that are visually checked against rendered PNGs.

- [ ] **Step 5: Write QA scorecard**

Create `$QA_DIR/comeback-scorecard.txt`:

```text
Primary profile: engineering-platform
Profile gates: pass
Story: 4/5
Specificity: 5/5
Rhythm: 4/5
Whitespace: 4/5
Chart clarity: 4/5
Typography: 4/5
Restraint: 5/5
Precision: 5/5
Coherence: 4/5
Reference delta: n/a

Package checks:
- Final PPTX exists and is non-empty.
- Slide count is 9.
- All expected titles are present.
- Each slide has at most two bullet points.
- Existing report figures are embedded as dominant proof objects.

Render checks:
- Artifact-tool preview PNGs were generated for all 9 slides.
- Contact sheet was generated.
- Mechanism connectors are semantic and visually attached.

Residual gaps:
- No external reference deck was supplied, so QA is against the approved spec and contact-sheet readability only.
```

Expected: scorecard exists.

## Task 4: Final Cleanup And Git Hygiene

**Files:**
- Keep: `docs/dcqcn-pfc-joint-tuning-report.pptx`
- Optional keep if committing plan: `docs/superpowers/plans/2026-06-10-dcqcn-report-ppt.md`
- Remove via cleanup helper: temporary presentation workspace files under `outputs/manual-dcqcn-report-ppt/presentations/dcqcn-report-ppt/`

- [ ] **Step 1: Clean temporary presentation workspace**

Run:

```bash
node "$SKILL_DIR/scripts/cleanup_presentation_workspace.mjs" \
  --workspace "$WORKSPACE" \
  --output-dir "$OUTPUT_DIR"
```

Expected: final PPTX remains in `docs/`; temporary workspace artifacts are removed.

- [ ] **Step 2: Check final deliverable still exists**

Run:

```bash
ls -lh /Users/ytxing/workspace/ns-3-ub-gitcode/docs/dcqcn-pfc-joint-tuning-report.pptx
```

Expected: file exists and size is greater than 0 bytes.

- [ ] **Step 3: Inspect git status for task-owned files only**

Run:

```bash
git status --short docs/dcqcn-pfc-joint-tuning-report.pptx docs/superpowers/plans/2026-06-10-dcqcn-report-ppt.md
```

Expected: `docs/` files may be hidden by `.gitignore`; use `git add -f` only if a commit is requested or needed.

## Self-Review

- Spec coverage: this plan implements the approved 9-slide structure, uses `docs/export.zip`, keeps each slide image-led, limits each slide to two bullets, and adds the requested DCQCN/PFC implementation mapping page.
- Presentation skill compliance: uses artifact-tool presentation modules and bundled helper scripts, not Python PPTX generation or direct OOXML edits.
- Placeholder scan: no placeholder markers, no unspecified error handling, and no references to undefined functions.
- Type consistency: slide modules use artifact-tool `presentation.slides.add()` through bundled `ctx` helper APIs consistently.
