"""Render the review Markdown to a searchable vector PDF using local fonts only.

Requires existing matplotlib, Pillow and a CJK font; never installs or downloads.
Pagebreak comments delimit the deliberately composed review pages.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from PIL import ImageFont

W, H = 595.276, 841.89
LEFT, RIGHT, BOTTOM = 44, 44, 47
WIDTH = W - LEFT - RIGHT
INK, MUTED, BLUE = '#192c3c', '#647386', '#174d71'
PALE, RULE = '#edf4f8', '#dce6ed'


class Page:
    def __init__(self, number, count, font, revision='v0.1', footer='拟实施方案，非实验结果', date='2026-10-16'):
        self.number = number
        self.fig = plt.figure(figsize=(W / 72, H / 72), facecolor='white')
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W); self.ax.set_ylim(H, 0); self.ax.axis('off')
        self.font = FontProperties(fname=str(font))
        self.metric = ImageFont.truetype(str(font), size=1000)
        self.y = 71
        self.text(LEFT, 29, 'MOE NETWORK RESEARCH  /  SCALE-OUT', 8, MUTED)
        self.text(W - RIGHT, 29, f'方法审阅稿 · {revision}', 8, BLUE, align='right')
        self.ax.plot([LEFT, W - RIGHT], [46, 46], color=RULE, linewidth=.8)
        self.ax.plot([LEFT, W - RIGHT], [H - 37, H - 37], color=RULE, linewidth=.8)
        self.text(LEFT, H - 25, f'{date}   |   {footer}', 8, MUTED)
        self.text(W - RIGHT, H - 25, f'{number:02d} / {count:02d}', 8, MUTED, align='right')

    def text(self, x, y, value, size=10, color=INK, align='left'):
        self.ax.text(x, y, value, fontsize=size, fontproperties=self.font, color=color,
                     va='top', ha=align, linespacing=1.2, parse_math=False)

    def wrap(self, value, width, size):
        value = value.replace('**', '').replace('`', '')
        rows = []
        for paragraph in value.split('\n'):
            current = ''
            for char in paragraph:
                if current and self.metric.getlength(current + char) * size / 1000 > width:
                    split = len(current)
                    # Keep closing CJK punctuation off the start of a line.
                    if char in '，。；：！？、）》」』】％%,.;:!?)]}' and split > 1:
                        split -= 1
                    if current[split - 1] in '（《「『【([' and split > 1:
                        split -= 1
                    # Move a short Latin word as a unit when the line permits it.
                    word = re.search(r'[A-Za-z0-9_./=-]+$', current[:split])
                    if word and word.start() > len(current) // 2 and char.isascii() and char.isalnum():
                        split = word.start()
                    rows.append(current[:split].rstrip()); current = (current[split:] + char).lstrip()
                else:
                    current += char
            rows.append(current.rstrip())
        return rows

    def check(self, height):
        if self.y + height > H - BOTTOM:
            raise ValueError(f'Page {self.number} overflow at y={self.y:.1f}, need {height:.1f}pt')

    def paragraph(self, value, size=10.2, leading=16.1, gap=8):
        rows = self.wrap(value, WIDTH, size)
        self.check(len(rows) * leading + gap)
        for row in rows:
            self.text(LEFT, self.y, row, size)
            self.y += leading
        self.y += gap

    def heading(self, value, level):
        size, leading, gap = (20, 29, 13) if level == 1 else (12.3, 19, 7)
        rows = self.wrap(value, WIDTH, size)
        self.check(len(rows) * leading + gap)
        for row in rows:
            self.text(LEFT, self.y, row, size, BLUE)
            self.y += leading
        self.y += gap

    def note(self, value):
        rows = self.wrap(value, WIDTH - 25, 9.6)
        height = len(rows) * 15.2 + 19
        self.check(height + 10)
        self.ax.add_patch(Rectangle((LEFT, self.y), WIDTH, height, facecolor=PALE, edgecolor='none'))
        self.ax.add_patch(Rectangle((LEFT, self.y), 3, height, facecolor=BLUE, edgecolor='none'))
        for i, row in enumerate(rows):
            self.text(LEFT + 12, self.y + 9 + i * 15.2, row, 9.6, BLUE)
        self.y += height + 10

    def table(self, rows):
        cols = len(rows[0])
        # Give explanatory columns space; no cell may escape its explicit width.
        if cols == 2:
            weights = [.23, .77]
        elif cols == 3:
            weights = [.20, .35, .45]
        elif cols == 4:
            weights = [.15, .25, .49, .11]
        elif cols == 5:
            weights = [.075, .20, .19, .14, .395]
        else:
            weights = [1 / cols] * cols
        widths = [WIDTH * x for x in weights]
        wrapped = [[self.wrap(cell, width - 14, 9.1) for cell, width in zip(row, widths)] for row in rows]
        heights = [max(map(len, row)) * 13.7 + 13 for row in wrapped]
        self.check(sum(heights) + 11)
        for index, (row, height) in enumerate(zip(wrapped, heights)):
            self.ax.add_patch(Rectangle((LEFT, self.y), WIDTH, height,
                              facecolor=BLUE if index == 0 else ('#f5f8fa' if index % 2 else 'white'),
                              edgecolor='none'))
            x = LEFT
            for cell, width in zip(row, widths):
                for line, value in enumerate(cell):
                    self.text(x + 7, self.y + 6 + line * 13.7, value, 9.1, 'white' if index == 0 else INK)
                x += width
            self.y += height
            self.ax.plot([LEFT, W - RIGHT], [self.y, self.y], color=RULE, linewidth=.5)
        self.y += 11

    def flow(self):
        height = 116
        self.check(height + 8)
        y = self.y + 4
        gap = 11
        width = (WIDTH - 3 * gap) / 4
        labels = [('LLMServingSim', '请求 / 组批 / KV'), ('逻辑 trace / DAG', '计算 / 消息 / 依赖'),
                  ('ASTRA-sim', '就绪事件推进'), ('ns-3 RDMA', '真实包 / 队列 / ACK')]
        centers = []
        for i, (title, subtitle) in enumerate(labels):
            x = LEFT + i * (width + gap)
            centers.append(x + width / 2)
            self.ax.add_patch(FancyBboxPatch((x, y), width, 47, boxstyle='round,pad=0,rounding_size=4',
                                           facecolor=PALE, edgecolor=RULE, linewidth=.8))
            self.text(x + width / 2, y + 8, title, 9.4, BLUE, align='center')
            self.text(x + width / 2, y + 27, subtitle, 8, MUTED, align='center')
            if i:
                self.ax.add_patch(FancyArrowPatch((x - gap + 1, y + 23), (x - 1, y + 23),
                                                 arrowstyle='-|>', mutation_scale=8, linewidth=.8, color=BLUE))
        # Data/transport events return to the dependency executor, then the wave returns to serving.
        self.ax.plot([centers[3], centers[3]], [y + 47, y + 65], color=BLUE, linewidth=.8)
        self.ax.add_patch(FancyArrowPatch((centers[3], y + 65), (centers[2], y + 65),
                                         arrowstyle='-|>', mutation_scale=8, linewidth=.8, color=BLUE))
        self.ax.add_patch(FancyArrowPatch((centers[2], y + 65), (centers[2], y + 47),
                                         arrowstyle='-|>', mutation_scale=8, linewidth=.8, color=BLUE))
        self.text((centers[2] + centers[3]) / 2, y + 71, '网络完成事件', 8, BLUE, align='center')
        self.ax.plot([centers[2], centers[2]], [y + 65, y + 90], color=MUTED, linewidth=.8)
        self.ax.add_patch(FancyArrowPatch((centers[2], y + 90), (centers[0], y + 90),
                                         arrowstyle='-|>', mutation_scale=8, linewidth=.8, color=MUTED))
        self.ax.add_patch(FancyArrowPatch((centers[0], y + 90), (centers[0], y + 47),
                                         arrowstyle='-|>', mutation_scale=8, linewidth=.8, color=MUTED))
        self.text((centers[0] + centers[2]) / 2, y + 96, '波次完成 → 更新请求 → 下一轮组批', 8, MUTED, align='center')
        self.y += height + 8

    def blocks(self, markdown):
        lines = markdown.strip().splitlines()
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if not line:
                index += 1; continue
            if line.startswith('# '):
                self.heading(line[2:], 1)
            elif line.startswith('## '):
                self.heading(line[3:], 2)
            elif line == '::: flow':
                self.flow()
            elif line.startswith('> '):
                self.note(line[2:])
            elif line.startswith('|'):
                rows = []
                while index < len(lines) and lines[index].strip().startswith('|'):
                    row = [x.strip() for x in lines[index].strip().strip('|').split('|')]
                    if not all(re.fullmatch(r':?-+:?', x) for x in row):
                        rows.append(row)
                    index += 1
                self.table(rows)
                continue
            else:
                self.paragraph(line)
            index += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'plan.md')
    parser.add_argument('--output', type=Path, required=True, help='New PDF path')
    parser.add_argument('--font', type=Path, default=Path('/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'))
    parser.add_argument('--revision', default='v0.3')
    parser.add_argument('--date', default='2026-10-16')
    parser.add_argument('--title', default='MoE Scale-Out 联合仿真实验方法')
    parser.add_argument('--footer', default='拟实施方案，非实验结果')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('PDF output already exists; select a new revision/path')
    if not args.font.is_file():
        raise ValueError('Pass --font for an existing CJK TTF/TTC font')
    source = args.source.read_bytes()
    pages = re.sub(r'<!-- source-index[\s\S]*?-->', '', source.decode()).split('<!-- pagebreak -->')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    matplotlib.rcParams.update({'pdf.fonttype': 42, 'pdf.compression': 6, 'text.usetex': False})
    with PdfPages(args.output, metadata=dict(Title=f'{args.title} · 审阅稿 {args.revision}',
                  Author='MoE Scale-Out Research', Subject='Method review; no new simulation results',
                  CreationDate=None, ModDate=None)) as pdf:
        for number, content in enumerate(pages, 1):
            page = Page(number, len(pages), args.font, args.revision, args.footer, args.date)
            page.blocks(content)
            pdf.savefig(page.fig)
            plt.close(page.fig)
    manifest = dict(source=args.source.name, source_sha256=hashlib.sha256(source).hexdigest(),
                    renderer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    font_file=args.font.name, font_sha256=hashlib.sha256(args.font.read_bytes()).hexdigest(),
                    pages=len(pages), pdf_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
                    network_executed=False)
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
