"""Render the review as an offline reading page, preserving its local links."""
import argparse
import hashlib
import html
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from markdown_it import MarkdownIt


def main(source, output):
    source, output = source.resolve(), output.resolve()
    parser = MarkdownIt('commonmark', {'html': False}).enable('table')
    tokens = parser.parse(source.read_text())
    for token in tokens:
        for child in token.children or []:
            if child.type != 'link_open':
                continue
            ref = urlsplit(child.attrGet('href'))
            if not ref.scheme and ref.path:
                target = (source.parent/unquote(ref.path)).resolve()
                if target != output and not target.is_file():
                    raise ValueError('missing review target '+str(target))
                child.attrSet('href', urlunsplit(('', '', os.path.relpath(target, output.parent), ref.query, ref.fragment)))
    body = parser.renderer.render(tokens, parser.options, {})
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>专家副本恢复：已有工作与实施边界</title>
<style>
:root{color-scheme:light}body{margin:0;background:#f3f5f8;color:#1c2d40;font:17px/1.85 system-ui,sans-serif}
main{max-width:1080px;margin:32px auto;padding:32px 44px;background:white;border:1px solid #dce4ec;border-radius:12px}
h1{font-size:30px;line-height:1.4}h2{font-size:23px;border-top:1px solid #dde5ee;padding-top:24px;margin-top:36px}
h3{font-size:19px}a{color:#155ca0;overflow-wrap:anywhere}code{background:#eef2f7;padding:2px 5px;overflow-wrap:anywhere}
pre{overflow:auto;background:#eef2f7;padding:18px;font-size:14px}pre code{padding:0}
table{display:block;overflow:auto;border-collapse:collapse;font-size:15px;line-height:1.7;margin:22px 0}
th,td{border:1px solid #dce4ec;padding:12px;min-width:140px;vertical-align:top}th{background:#edf3fa;text-align:left}
strong{color:#173d65}li{margin:8px 0}footer{border-top:1px solid #dde5ee;margin-top:28px;padding-top:18px;color:#5a6978;font-size:12px;overflow-wrap:anywhere}
@media(max-width:700px){main{margin:0;padding:20px 16px;border:0;border-radius:0}body{font-size:16px}h1{font-size:26px}}
</style><main>'''+body+'<footer>报告原文 SHA-256：'+html.escape(digest)+'</footer></main></html>\n'
    with output.open('x') as stream:
        stream.write(page)
    print(str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.source, args.output)
