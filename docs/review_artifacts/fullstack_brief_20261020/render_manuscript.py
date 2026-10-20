"""Typeset the three-slide speech as a searchable three-page PDF.

The machine has LibreOffice Impress but no Writer module. DOCX is delivered as
an editable OOXML manuscript; this PDF is independently typeset from 讲稿.md.
No claim is made that Writer/Word rendered or visually verified the DOCX.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from hashlib import sha256
import json
import re
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['pdf.fonttype'] = 42
from matplotlib import pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
from PIL import ImageFont
from build_materials import HERE, FONT_PATH, manuscript_sections


def main():
    width,height=595.276,841.89
    left,right,top,bottom=46,46,105,782
    body_size,leading=12.2,19.1
    props=FontProperties(fname=str(FONT_PATH))
    metric=ImageFont.truetype(str(FONT_PATH),size=round(body_size*8))
    records=[]
    def wrap(text):
        lines=[];line=''
        for char in text:
            if line and metric.getlength(line+char)/8>width-left-right:
                # Keep closing punctuation with the preceding text where possible.
                word = re.search(r'[A-Za-z0-9_/-]+$', line)
                if char.isascii() and (char.isalnum() or char in '_/-') and word and word.start()>0:
                    lines.append(line[:word.start()]);line=word[0]+char
                elif char in '，。；：？！、）》”’' and len(line)>1:
                    lines.append(line[:-1]);line=line[-1:]+char
                else:
                    lines.append(line);line=char
            else:line+=char
        if line:lines.append(line)
        assert ''.join(lines)==text
        return lines
    date=datetime(2026,10,20,tzinfo=timezone(timedelta(hours=8)))
    metadata={'Title':'MoE推理全栈联合仿真配套讲稿','Author':'李润宇','Subject':'三页材料逐页讲稿；机内与跨机网络细节',
              'CreationDate':date,'ModDate':date}
    output=HERE/'配套讲稿.pdf'
    with PdfPages(output,metadata=metadata) as pdf:
        for number,(title,paragraphs) in enumerate(manuscript_sections(),1):
            fig=plt.figure(figsize=(width/72,height/72),facecolor='white')
            ax=fig.add_axes([0,0,1,1]);ax.set_xlim(0,width);ax.set_ylim(height,0);ax.axis('off')
            def text(x,y,value,size=body_size,color='#26232c'):
                ax.text(x,y,value,fontproperties=props,fontsize=size,color=color,ha='left',va='top',parse_math=False)
            text(left,25,'MoE推理全栈联合仿真  /  李润宇  /  配套讲稿',10,'#7030a0')
            text(left,52,title,17,'#7030a0')
            ax.plot([left,width-right],[86,86],color='#7030a0',linewidth=1)
            y=top;rows=[]
            for p in paragraphs:
                lines=wrap(p)
                for line in lines:
                    assert y+leading<=bottom,(number,y)
                    text(left,y,line)
                    rows.append({'text':line,'baseline_top_pt':y});y+=leading
                y+=9
            ax.plot([left,width-right],[802,802],color='#d6c8df',linewidth=.6)
            text(left,812,'2026.10.20  |  按三页PPT顺序讲述',9,'#64606c')
            text(width-66,812,str(number),9,'#64606c')
            pdf.savefig(fig);plt.close(fig)
            records.append({'page':number,'title':title,'body_size_pt':body_size,'last_body_bottom_pt':y-9,
                            'page_height_pt':height,'lines':rows})
    (HERE/'manuscript_layout.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    (HERE/'manuscript_render.json').write_text(json.dumps({
        'input':'讲稿.md','input_sha256':sha256((HERE/'讲稿.md').read_bytes()).hexdigest(),
        'output':output.name,'output_sha256':sha256(output.read_bytes()).hexdigest(),
        'renderer':'existing Matplotlib PDF backend with embedded local CJK font',
        'command':'python3 docs/review_artifacts/fullstack_brief_20261020/render_manuscript.py',
        'renderer_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
        'pages':3,'docx_word_writer_rendered':False},ensure_ascii=False,indent=2)+'\n')
    print('Rendered 3-page manuscript PDF from the same per-slide text')


if __name__=='__main__':main()
