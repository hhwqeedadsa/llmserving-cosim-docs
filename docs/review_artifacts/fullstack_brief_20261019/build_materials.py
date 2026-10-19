"""Build exactly two content slides from the user's actual template and a DOCX script.

Uses the Python standard library plus existing Pillow for text measurements.
The template masters, layouts, theme, media and original file are preserved.
Rendering/visual verification is separate, using the existing LibreOffice.
"""
import argparse
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import re
from xml.etree import ElementTree as E
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from PIL import ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TEMPLATE_HASH = 'fb20958128a2d2bac52d37fb679a6a452bfb22bb199cac9da27d56b08910d550'
TEMPLATE_DEFAULT = '/home/lry/.codex/attachments/5e59ce9c-1aac-4954-818d-099f8154183a/PPT模板-用这个！！！.pptx'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
for prefix, uri in [('p', P), ('a', A), ('r', R), ('w', W)]:
    E.register_namespace(prefix, uri)
NS = {'p': P, 'a': A, 'r': R, 'w': W}
FONT_PATH = Path('/usr/share/fonts/truetype/wqy/wqy-microhei.ttc')
FONT = 'WenQuanYi Micro Hei'
PURPLE, INK, MUTED, PALE, LINE = '7030A0', '26232C', '64606C', 'F6F0FA', 'DDD3E5'
EMU = 12700
WIDTH, HEIGHT = 960, 540
TITLE = '全栈仿真思路与拟增细节_两页'


def sub(parent, namespace, tag, attrs=None, text=None):
    node = E.SubElement(parent, '{' + namespace + '}' + tag, attrs or {})
    if text is not None:
        node.text = str(text)
    return node


def xml(root):
    # Use the conventional default namespaces for OPC package metadata.
    if root.tag in ('{' + CT + '}Types', '{' + REL + '}Relationships'):
        E.register_namespace('', root.tag[1:].split('}')[0])
    return E.tostring(root, encoding='utf-8', xml_declaration=True)


def zip_write(path, entries):
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        for name, value in entries.items():
            info = ZipInfo(name, (2026, 10, 19, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            z.writestr(info, value)


def paragraphs(shape, lines, size=16, color=INK, bold=False, align='l', inset=0, anchor='t', leading=1.25):
    old = shape.find('p:txBody', NS)
    if old is not None:
        shape.remove(old)
    tx = sub(shape, P, 'txBody')
    body = sub(tx, A, 'bodyPr', dict(wrap='square', anchor=anchor,
        lIns=str(round(inset * EMU)), rIns=str(round(inset * EMU)),
        tIns=str(round(inset * EMU)), bIns=str(round(inset * EMU))))
    sub(body, A, 'noAutofit')
    sub(tx, A, 'lstStyle')
    for line in lines:
        paragraph = sub(tx, A, 'p')
        properties = sub(paragraph, A, 'pPr', {'algn': align})
        spacing = sub(properties, A, 'lnSpc')
        sub(spacing, A, 'spcPts', {'val': str(round(size * leading * 100))})
        sub(sub(properties, A, 'spcBef'), A, 'spcPts', {'val': '0'})
        sub(sub(properties, A, 'spcAft'), A, 'spcPts', {'val': '0'})
        sub(properties, A, 'buNone')
        run = sub(paragraph, A, 'r')
        props = sub(run, A, 'rPr', dict(lang='zh-CN', sz=str(round(size * 100)), b='1' if bold else '0'))
        sub(sub(props, A, 'solidFill'), A, 'srgbClr', {'val': color})
        for tag in ('latin', 'ea', 'cs'):
            sub(props, A, tag, {'typeface': FONT})
        sub(run, A, 't', text=line)
        sub(paragraph, A, 'endParaRPr', {'lang': 'zh-CN', 'sz': str(round(size * 100))})


class Slide:
    def __init__(self, base, title, number):
        self.root = E.fromstring(base)
        self.tree = self.root.find('p:cSld/p:spTree', NS)
        # Keep the title and page-number placeholders from the supplied content slide.
        for shape in list(self.tree):
            if shape.tag == '{' + P + '}sp':
                ph = shape.find('p:nvSpPr/p:nvPr/p:ph', NS)
                if ph is None or ph.get('type') not in ('title', 'sldNum'):
                    self.tree.remove(shape)
        timing = self.root.find('p:timing', NS)
        if timing is not None:
            self.root.remove(timing)
        for shape in self.tree.findall('p:sp', NS):
            ph = shape.find('p:nvSpPr/p:nvPr/p:ph', NS)
            if ph.get('type') == 'title':
                paragraphs(shape, [title], size=31, color=PURPLE, bold=True, anchor='ctr')
            else:
                paragraphs(shape, [str(number)], size=10, color='96929B', align='r', anchor='ctr')
        self.next_id = 20
        self.records = []
        self.text('2026.10.19  |  MoE推理 · Scale-Out网络', 48, 514, 600, 15, size=9.5, color=MUTED)

    def shape(self, name, x, y, width, height, fill=None, border=None, geom='rect'):
        shape = sub(self.tree, P, 'sp')
        nv = sub(shape, P, 'nvSpPr')
        sub(nv, P, 'cNvPr', {'id': str(self.next_id), 'name': name})
        self.next_id += 1
        sub(nv, P, 'cNvSpPr', {'txBox': '1'})
        sub(nv, P, 'nvPr')
        props = sub(shape, P, 'spPr')
        transform = sub(props, A, 'xfrm')
        sub(transform, A, 'off', {'x': str(round(x * EMU)), 'y': str(round(y * EMU))})
        sub(transform, A, 'ext', {'cx': str(round(width * EMU)), 'cy': str(round(height * EMU))})
        sub(sub(props, A, 'prstGeom', {'prst': geom}), A, 'avLst')
        if fill:
            sub(sub(props, A, 'solidFill'), A, 'srgbClr', {'val': fill})
        else:
            sub(props, A, 'noFill')
        ln = sub(props, A, 'ln', {'w': '9525'})
        if border:
            sub(sub(ln, A, 'solidFill'), A, 'srgbClr', {'val': border})
        else:
            sub(ln, A, 'noFill')
        assert x >= 0 and y >= 0 and x + width <= WIDTH and y + height <= HEIGHT
        return shape

    def text(self, value, x, y, width, height, size=16, color=INK, bold=False,
             align='l', fill=None, border=None, inset=0, anchor='t', leading=1.25):
        lines = value.split('\n') if isinstance(value, str) else value
        font = ImageFont.truetype(str(FONT_PATH), size=round(size * 4))
        measured = [font.getlength(t) / 4 for t in lines]
        # Explicit line breaks and no autofit: reject text that would need clipping.
        assert max(measured, default=0) <= width - 2 * inset, (lines, measured, width)
        assert len(lines) * size * leading <= height - 2 * inset + 1, (lines, height)
        shape = self.shape('文本 ' + ' / '.join(lines)[:60], x, y, width, height, fill, border)
        paragraphs(shape, lines, size, color, bold, align, inset, anchor, leading)
        self.records.append(dict(text=lines, box_pt=[x, y, width, height], font_pt=size,
                                 measured_width_pt=measured))
        return shape

    def line(self, x1, y1, x2, y2, arrow=False, color=PURPLE, thickness=1.7):
        conn = sub(self.tree, P, 'cxnSp')
        nv = sub(conn, P, 'nvCxnSpPr')
        sub(nv, P, 'cNvPr', {'id': str(self.next_id), 'name': '连接箭头' if arrow else '连接线'})
        self.next_id += 1
        sub(nv, P, 'cNvCxnSpPr')
        sub(nv, P, 'nvPr')
        props = sub(conn, P, 'spPr')
        xf = sub(props, A, 'xfrm', {'flipH': '1' if x2 < x1 else '0', 'flipV': '1' if y2 < y1 else '0'})
        sub(xf, A, 'off', {'x': str(round(min(x1, x2) * EMU)), 'y': str(round(min(y1, y2) * EMU))})
        sub(xf, A, 'ext', {'cx': str(round(abs(x2-x1) * EMU)), 'cy': str(round(abs(y2-y1) * EMU))})
        sub(sub(props, A, 'prstGeom', {'prst': 'line'}), A, 'avLst')
        ln = sub(props, A, 'ln', {'w': str(round(thickness * EMU))})
        sub(sub(ln, A, 'solidFill'), A, 'srgbClr', {'val': color})
        if arrow:
            sub(ln, A, 'tailEnd', {'type': 'triangle', 'w': 'sm', 'len': 'sm'})


def slide_one(base):
    s = Slide(base, '全栈仿真：让网络等待回到推理服务', 1)
    s.text('目标：解释MoE跨机通信为何拖慢Decode，并比较等资源优化方案', 48, 97, 864, 30, size=19, bold=True)
    cards = [
        ('请求与组批', 'LLMServingSim', '模型 / batch / KV\n逐请求专家选择'),
        ('任务与字节生成', '本项目生成器', '逐层路由＋专家放置\nCOMP / SEND / RECV'),
        ('依赖执行', 'Chakra → ASTRA', '计算与通信任务图\n前驱满足才发起'),
        ('网络仿真', '原生RDMA ns-3', '机内抽象＋跨机RDMA\nNIC、交换机、队列'),
        ('完成与服务推进', '实际完成回调', '数据就绪→后继计算\n整轮完成→下轮组批'),
    ]
    x0, gap, width = 48, 22, 155.2
    for i in range(4):
        start = x0 + i * (width + gap) + width
        s.line(start + 3, 213, start + gap - 3, 213, arrow=True)
    for i, (label, component, details) in enumerate(cards):
        x = x0 + i * (width + gap)
        s.shape('链路卡片', x, 153, width, 126, fill=PALE, border=LINE, geom='roundRect')
        s.text(label, x+7, 166, width-14, 24, size=16, color=PURPLE, bold=True, align='ctr')
        s.text(component, x+6, 197, width-12, 21, size=13.2, bold=True, align='ctr')
        s.text(details, x+6, 233, width-12, 39, size=12.8, align='ctr', leading=1.35)
    first, last = 48 + width/2, 48 + 4*(width+gap) + width/2
    s.line(last, 279, last, 298)
    s.line(last, 298, first, 298, arrow=True)
    s.line(first, 298, first, 279, arrow=True)
    s.text('实际完成事件反馈：多个副本共用同一持续网络，网络等待改变后续进度', 77, 310, 806, 26,
           size=16, color=PURPLE, align='ctr', bold=True)
    s.text('已有验证', 48, 357, 420, 25, size=18, color=PURPLE, bold=True)
    s.text('关注的网络结果', 501, 357, 411, 25, size=18, color=PURPLE, bold=True)
    s.text('真实路由：4卡 / 2机 / 2Pod，两层两步\n规模：128卡单EP；256/512卡多EP4副本',
           48, 391, 434, 50, size=15.2, leading=1.45)
    s.text('Dispatch/Combine等待 → Decode步长\n固定任务与资源，定位NIC及各层交换出口',
           501, 391, 411, 50, size=15.2, leading=1.45)
    s.text('当前范围：KV已就绪的Decode；计算为声明成本；千卡两次超时，尚未验收。',
           48, 464, 864, 29, size=13.3, color=MUTED, fill=PALE, inset=5)
    return s


def slide_two(base):
    s = Slide(base, '拟增加细节：从流量热点走向网络机制', 2)
    s.text('优先深化Scale-Out网络：先证明瓶颈，再选择一种优化机制', 48, 97, 864, 30, size=19, bold=True)
    cols = [(48, 173), (221, 439), (660, 252)]
    for (x,w),label in zip(cols, ['拟补细节', '在现有基础上增加什么', '要回答的问题']):
        s.text(label, x, 142, w, 32, size=16, bold=True, color='FFFFFF', fill=PURPLE, inset=6)
    rows = [
        ('01  路由与通信组织',
         '扩展真实样本与热点时序；补目的节点去重、\n机内汇聚—跨机—机内分发，计入准备/转发成本。',
         '热点是否跨机集中？\n分层通信能否减少等待？'),
        ('02  连接与分层容量',
         '已有Clos/rail/plane输入；补共享NIC与跨rail中继，\n明确GPU亲和、逐层带宽与等资源分流。',
         '瓶颈在哪个共享出口？\n多路径何时有效？'),
        ('03  协议等待与归因',
         '已有完成回调与摘要；补有界队列时序、\nPFC/ECN反馈和慢轮次的关键依赖链。',
         '慢在发送、排队还是确认？\n哪段等待真正拖慢服务？'),
        ('04  校准与规模效率',
         '补匹配A100/H100及消息形状的测量；\n拆分构图、执行与写出成本，再验收千卡。',
         '预测与实机相差多少？\n千卡能否在预算内完成？'),
    ]
    for i,(label,details,question) in enumerate(rows):
        y = 174 + i*65
        fill = 'FAF7FC' if i%2==0 else 'FFFFFF'
        for x,w in cols:
            s.shape('表格底色',x,y,w,65,fill=fill,border=LINE)
        s.text(label, 56, y+20, 157, 24, size=14.2, color=PURPLE, bold=True)
        s.text(details, 231, y+13, 419, 44, size=13.7, leading=1.4)
        s.text(question, 670, y+13, 232, 44, size=13.7, leading=1.4)
    s.text('验证路径：真实 / 打乱 / 均匀路由＋等容量放置＋固定带宽预算',
           48, 454, 864, 28, size=16, color=PURPLE, bold=True)
    s.text('先做小图机制对照，再做硬件校准与代表性规模验证；拟增细节不作为已完成能力。',
           48, 486, 864, 22, size=12.7, color=MUTED)
    return s


def manuscript_sections():
    text = (HERE/'讲稿.md').read_text()
    sections=[]
    for block in re.split(r'^## ', text, flags=re.M)[1:]:
        title, body = block.split('\n', 1)
        sections.append((title.strip(), [p.replace('\n',' ').strip() for p in body.strip().split('\n\n') if p.strip()]))
    assert len(sections)==2
    return sections


def notes(base, paragraphs_text, number):
    root = E.fromstring(base)
    for shape in root.findall('p:cSld/p:spTree/p:sp', NS):
        ph = shape.find('p:nvSpPr/p:nvPr/p:ph', NS)
        if ph is not None and ph.get('type')=='body':
            paragraphs(shape, paragraphs_text, size=12)
        elif ph is not None and ph.get('type')=='sldNum':
            paragraphs(shape,[str(number)],size=10)
    return xml(root)


def docx(sections, output):
    root=E.Element('{'+W+'}document')
    body=sub(root,W,'body')
    for i,(title,paras) in enumerate(sections):
        heading=sub(body,W,'p')
        pp=sub(heading,W,'pPr')
        sub(pp,W,'pStyle',{'{'+W+'}val':'Heading1'})
        if i:
            sub(pp,W,'pageBreakBefore')
        sub(sub(heading,W,'r'),W,'t',text=title)
        for text in paras:
            p=sub(body,W,'p')
            sub(sub(p,W,'r'),W,'t',text=text)
    sect=sub(body,W,'sectPr')
    sub(sect,W,'pgSz',{'{'+W+'}w':'11906','{'+W+'}h':'16838'})
    sub(sect,W,'pgMar',{ '{'+W+'}'+k:v for k,v in dict(top='1080',right='1080',bottom='1080',left='1080',header='360',footer='360',gutter='0').items()})
    styles=E.Element('{'+W+'}styles')
    default=sub(styles,W,'docDefaults')
    rpr=sub(sub(default,W,'rPrDefault'),W,'rPr')
    sub(rpr,W,'rFonts',{'{'+W+'}'+k:FONT for k in ('ascii','hAnsi','eastAsia','cs')})
    sub(rpr,W,'sz',{'{'+W+'}val':'23'})
    sub(rpr,W,'lang',{'{'+W+'}val':'zh-CN','{'+W+'}eastAsia':'zh-CN'})
    ppr=sub(sub(default,W,'pPrDefault'),W,'pPr')
    sub(ppr,W,'spacing',{'{'+W+'}before':'0','{'+W+'}after':'95','{'+W+'}line':'320','{'+W+'}lineRule':'auto'})
    normal=sub(styles,W,'style',{'{'+W+'}type':'paragraph','{'+W+'}default':'1','{'+W+'}styleId':'Normal'})
    sub(normal,W,'name',{'{'+W+'}val':'Normal'})
    h=sub(styles,W,'style',{'{'+W+'}type':'paragraph','{'+W+'}styleId':'Heading1'})
    sub(h,W,'name',{'{'+W+'}val':'heading 1'})
    sub(h,W,'basedOn',{'{'+W+'}val':'Normal'})
    hp=sub(h,W,'pPr');sub(hp,W,'keepNext')
    sub(hp,W,'spacing',{'{'+W+'}before':'0','{'+W+'}after':'180'})
    hr=sub(h,W,'rPr');sub(hr,W,'b');sub(hr,W,'sz',{'{'+W+'}val':'32'});sub(hr,W,'color',{'{'+W+'}val':PURPLE})
    types=E.Element('{'+CT+'}Types')
    for ext,typ in [('rels','application/vnd.openxmlformats-package.relationships+xml'),('xml','application/xml')]:
        sub(types,CT,'Default',{'Extension':ext,'ContentType':typ})
    for part,typ in [('document','application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'),('styles','application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml')]:
        sub(types,CT,'Override',{'PartName':'/word/'+part+'.xml','ContentType':typ})
    rels=E.Element('{'+REL+'}Relationships');sub(rels,REL,'Relationship',{'Id':'rId1','Type':R+'/officeDocument','Target':'word/document.xml'})
    wordrels=E.Element('{'+REL+'}Relationships');sub(wordrels,REL,'Relationship',{'Id':'rId1','Type':R+'/styles','Target':'styles.xml'})
    zip_write(output,{'[Content_Types].xml':xml(types),'_rels/.rels':xml(rels),'word/document.xml':xml(root),'word/styles.xml':xml(styles),'word/_rels/document.xml.rels':xml(wordrels)})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template',type=Path,default=Path(TEMPLATE_DEFAULT))
    args=parser.parse_args()
    template=args.template.read_bytes()
    assert sha256(template).hexdigest()==TEMPLATE_HASH,'Template differs from the supplied original'
    assert FONT_PATH.exists()
    with ZipFile(args.template) as z:
        entries={n:z.read(n) for n in z.namelist()}
    original=dict(entries)
    sections=manuscript_sections()
    slides=[slide_one(entries['ppt/slides/slide2.xml']),slide_two(entries['ppt/slides/slide2.xml'])]
    for i,slide in enumerate(slides,1):
        entries[f'ppt/slides/slide{i}.xml']=xml(slide.root)
        rels=E.Element('{'+REL+'}Relationships')
        sub(rels,REL,'Relationship',{'Id':'rId1','Type':R+'/slideLayout','Target':'../slideLayouts/slideLayout2.xml'})
        sub(rels,REL,'Relationship',{'Id':'rId2','Type':R+'/notesSlide','Target':f'../notesSlides/notesSlide{i}.xml'})
        entries[f'ppt/slides/_rels/slide{i}.xml.rels']=xml(rels)
        entries[f'ppt/notesSlides/notesSlide{i}.xml']=notes(original['ppt/notesSlides/notesSlide1.xml'],[sections[i-1][0]]+sections[i-1][1],i)
        nr=E.Element('{'+REL+'}Relationships')
        sub(nr,REL,'Relationship',{'Id':'rId1','Type':R+'/notesMaster','Target':'../notesMasters/notesMaster1.xml'})
        sub(nr,REL,'Relationship',{'Id':'rId2','Type':R+'/slide','Target':f'../slides/slide{i}.xml'})
        entries[f'ppt/notesSlides/_rels/notesSlide{i}.xml.rels']=xml(nr)
    types=E.fromstring(entries['[Content_Types].xml'])
    sub(types,CT,'Override',{'PartName':'/ppt/notesSlides/notesSlide2.xml','ContentType':'application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml'})
    entries['[Content_Types].xml']=xml(types)
    presentation=E.fromstring(entries['ppt/presentation.xml']);presentation.set('firstSlideNum','1')
    entries['ppt/presentation.xml']=xml(presentation)
    # Remove the cover thumbnail so the OS does not show obsolete template content.
    relroot=E.fromstring(entries['_rels/.rels'])
    for rel in list(relroot):
        if rel.get('Type','').endswith('/thumbnail'):
            target=rel.get('Target','').lstrip('/')
            entries.pop(target,None);relroot.remove(rel)
    entries['_rels/.rels']=xml(relroot)
    # Replace the old presentation's metadata without changing its design.
    core_ns='http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
    dc='http://purl.org/dc/elements/1.1/'
    dt='http://purl.org/dc/terms/'
    xsi='http://www.w3.org/2001/XMLSchema-instance'
    for prefix, uri in [('cp',core_ns),('dc',dc),('dcterms',dt),('xsi',xsi)]:
        E.register_namespace(prefix,uri)
    core=E.Element('{'+core_ns+'}coreProperties')
    sub(core,dc,'title',text='MoE推理全栈仿真思路与拟增加的仿真细节')
    sub(core,dc,'subject',text='两页研究材料；当前链路与后续网络建模')
    for tag in ('created','modified'):
        sub(core,dt,tag,{'{'+xsi+'}type':'dcterms:W3CDTF'},text='2026-10-19T00:00:00Z')
    entries['docProps/core.xml']=xml(core)
    app=E.fromstring(entries['docProps/app.xml'])
    app_ns='http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'
    for item in list(app):
        tag=item.tag.split('}')[-1]
        if tag in ('TotalTime','Words','Paragraphs','HeadingPairs','TitlesOfParts'):
            app.remove(item)
        elif tag in ('Slides','Notes'):
            item.text='2'
        elif tag=='Application':
            item.text='Template-based research materials generator'
    entries['docProps/app.xml']=xml(app)
    # All template design parts remain byte-identical.
    design=[n for n in original if n.startswith(('ppt/slideMasters/','ppt/slideLayouts/','ppt/theme/','ppt/media/'))]
    assert all(entries[n]==original[n] for n in design)
    output=HERE/(TITLE+'.pptx')
    zip_write(output,entries)
    docx(sections,HERE/'配套讲稿.docx')
    sources=['docs/architecture.md','experiments/scaleout_e2e/conclusions.md',
             'experiments/moe_trace_hotspots/results/four_requests_20261018/report.md',
             'experiments/scaleout_scaling/results/runtime_20261016/report.md',
             'docs/reviews/scaleout_topology_20261015.md',
             'docs/reviews/scaleout_configuration_and_research_scope.md']
    manifest=dict(date='2026-10-19',timezone='Asia/Shanghai',scope='presentation_and_manuscript_only_no_simulation',
        template=dict(path=str(args.template),sha256=TEMPLATE_HASH,used_layout='ppt/slideLayouts/slideLayout2.xml',
                      preserved_design_parts=len(design),design_byte_identical=True),
        slide_count=2,content_slides=2,font=dict(family=FONT,file=str(FONT_PATH),sha256=sha256(FONT_PATH.read_bytes()).hexdigest()),
        generate_command='python3 docs/review_artifacts/fullstack_brief_20261019/build_materials.py --template /path/to/original.pptx',
        source_hashes={s:sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
        outputs={p.name:sha256(p.read_bytes()).hexdigest() for p in [output,HERE/'配套讲稿.docx',HERE/'讲稿.md']},
        generator_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        script_characters=[sum(len(p) for p in paras) for _,paras in sections])
    (HERE/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (HERE/'layout.json').write_text(json.dumps([s.records for s in slides],ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'pptx':str(output),'slides':2,'design_parts_preserved':len(design),'script_characters':manifest['script_characters']},ensure_ascii=False))


if __name__=='__main__':
    main()
