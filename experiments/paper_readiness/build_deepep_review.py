"""Render the G1 review with the existing offline document renderer."""
import argparse
import importlib.util
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    renderer = ROOT / 'docs/review_artifacts/paper_readiness_20261022/build_review.py'
    spec = importlib.util.spec_from_file_location('document_renderer', renderer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = a.output.resolve()
    report = a.output / 'report.html'
    module.render(ROOT / 'docs/reviews/DeepEP低延迟通信语义与建模修正_20261022.md', report,
                  'DeepEP低延迟通信语义与Scale-Out建模修正')
    text, count = re.subn(r'<nav>.*?</nav>',
        '<nav><a href="../paper_readiness_20261022/report.html">前阶段论文评估</a> · '
        '<a href="audit.json">固定源码与路由证据</a> · '
        '<a href="verification.json">离线检查范围</a></nav>', report.read_text(), count=1)
    assert count == 1
    report.write_text(text)


if __name__ == '__main__':
    main()
