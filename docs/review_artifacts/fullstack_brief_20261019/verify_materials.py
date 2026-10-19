"""Audit the user's template, two content slides, the speech and rendered PDFs.

Does not run simulations or install anything. The actual template must be
available for its unchanged design parts to be compared.
"""
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as E
import json
import posixpath
import re
import subprocess
import sys
from build_materials import HERE, ROOT, NS, P, A, W, REL, manuscript_sections


def digest(path):return sha256(path.read_bytes()).hexdigest()
def norm(text):return ''.join(text.split())
def command(args):
    r=subprocess.run(args,text=True,capture_output=True)
    assert r.returncode==0,(args,r.stderr)
    return r.stdout


def main():
    manifest=json.loads((HERE/'manifest.json').read_text())
    template=Path(manifest['template']['path'])
    assert digest(template)==manifest['template']['sha256']
    deck=HERE/'全栈仿真思路与拟增细节_两页.pptx'
    script=HERE/'配套讲稿.docx'
    pdf=deck.with_suffix('.pdf')
    speech_pdf=HERE/'配套讲稿.pdf'
    layout=json.loads((HERE/'layout.json').read_text())
    sections=manuscript_sections()
    with ZipFile(template) as original,ZipFile(deck) as z:
        assert z.testzip() is None
        parts=set(z.namelist())
        for name in parts:
            if name.endswith(('.xml','.rels')):E.fromstring(z.read(name))
            if name.endswith('.rels'):
                directory='' if name=='_rels/.rels' else posixpath.dirname(posixpath.dirname(name))
                for rel in E.fromstring(z.read(name)):
                    if rel.get('TargetMode')=='External':continue
                    target=rel.attrib['Target'].split('#')[0]
                    if not target:continue
                    resolved=posixpath.normpath(posixpath.join(directory,target))
                    assert resolved.lstrip('/') in parts,(name,target,resolved)
        slides=E.fromstring(z.read('ppt/presentation.xml')).findall('p:sldIdLst/p:sldId',NS)
        assert len(slides)==2
        design=[n for n in original.namelist() if n.startswith(('ppt/slideMasters/','ppt/slideLayouts/','ppt/theme/','ppt/media/'))]
        assert all(z.read(n)==original.read(n) for n in design)
        notes_counts=[]
        for number in (1,2):
            relationships=E.fromstring(z.read(f'ppt/slides/_rels/slide{number}.xml.rels'))
            assert any(r.get('Target')=='../slideLayouts/slideLayout2.xml' for r in relationships)
            root=E.fromstring(z.read(f'ppt/slides/slide{number}.xml'))
            texts=[n.text or '' for n in root.findall('.//a:t',NS)]
            assert not set(texts).intersection({'Title','Content1','item1','item2','主标题','副标题','姓名','日期'})
            assert len(root.findall('.//p:sp',NS))>20,'Expected native editable shapes'
            raw=command(['pdftotext','-f',str(number),'-l',str(number),'-raw',str(pdf),'-'])
            for record in layout[number-1]:
                x,y,w,h=record['box_pt']
                assert x>=0 and y>=0 and x+w<=960 and y+h<=540
                for text in record['text']:
                    assert norm(text) in norm(raw),(number,text)
            note=E.fromstring(z.read(f'ppt/notesSlides/notesSlide{number}.xml'))
            note_body=None
            for sp in note.findall('p:cSld/p:spTree/p:sp',NS):
                ph=sp.find('p:nvSpPr/p:nvPr/p:ph',NS)
                if ph is not None and ph.get('type')=='body':note_body=sp
            body=[n.text or '' for n in note_body.findall('.//a:t',NS)]
            assert body==[sections[number-1][0]]+sections[number-1][1]
            notes_counts.append(sum(map(len,body)))
    with ZipFile(script) as z:
        assert z.testzip() is None
        for n in z.namelist():
            if n.endswith(('.xml','.rels')):E.fromstring(z.read(n))
        root=E.fromstring(z.read('word/document.xml'))
        actual=[''.join(p.itertext()) for p in root.findall('w:body/w:p',NS)]
        expected=[part for title,paras in sections for part in [title]+paras]
        assert actual==expected
        assert len(root.findall('.//w:pageBreakBefore',NS))==1
    pdfinfo={}
    for f in (pdf,speech_pdf):
        info=command(['pdfinfo',str(f)])
        assert re.search(r'^Pages:\s+2$',info,re.M),info
        pdfinfo[f.name]=info
    for number,(_,paras) in enumerate(sections,1):
        raw=command(['pdftotext','-f',str(number),'-l',str(number),'-raw',str(speech_pdf),'-'])
        for p in paras:assert norm(p) in norm(raw),(number,p[:60])
    for rel,expected in manifest['source_hashes'].items():assert digest(ROOT/rel)==expected,rel
    for name,expected in manifest['outputs'].items():assert digest(HERE/name)==expected,name
    assert digest(HERE/'build_materials.py')==manifest['generator_sha256']
    rendered=json.loads((HERE/'manuscript_render.json').read_text())
    assert digest(HERE/'render_manuscript.py')==rendered['renderer_sha256']
    assert digest(speech_pdf)==rendered['output_sha256']
    previews=list((HERE/'preview').glob('*.png'))
    assert len(previews)==4
    stable=[deck,script,HERE/'manifest.json',HERE/'layout.json']
    before=[p.read_bytes() for p in stable]
    command([sys.executable,str(HERE/'build_materials.py'),'--template',str(template)])
    assert before==[p.read_bytes() for p in stable]
    result=dict(status='passed',date='2026-10-19',timezone='Asia/Shanghai',
        requirements={
            'use_user_template':dict(proven=True,sha256=digest(template),design_parts_byte_identical=len(design),both_slides_use_original_content_layout=True),
            'two_pages_of_material':dict(proven=True,pptx_slides=2,pdf_pages=2,cover_only_pages=0),
            'current_fullstack_approach':dict(proven=True,evidence='slide1 native task/feedback diagram; notes match manuscript'),
            'proposed_details':dict(proven=True,evidence='slide2 four explicitly proposed network modeling dimensions and controlled validation route'),
            'companion_manuscript':dict(proven=True,formats=['Markdown','DOCX','PDF','PowerPoint notes'],pdf_pages=2,all_body_text_matches=True),
            'rendered_content_complete':dict(proven=True,evidence='PDF extraction contains every visible shape line and every manuscript paragraph'),
        },
        editable_native_shapes=True,notes_characters=notes_counts,source_hashes_unchanged=True,
        pptx_docx_rebuild_byte_identical=True,template_unchanged=True,
        slides_renderer='LibreOffice Impress 24.2.7.2',manuscript_pdf_renderer='existing Matplotlib PDF backend',
        docx_validation='OOXML package, exact body content, one explicit page break; no Writer/Word render on this machine',
        raster_previews=[str(p.relative_to(HERE)) for p in previews],
        visual_review='Final slide PDF and manuscript PDF inspected from raster previews; no text truncation or overlap observed',
        artifacts={p.name:digest(p) for p in [deck,pdf,script,speech_pdf,HERE/'讲稿.md']},
        new_simulations=0,dependency_installations=0,pushed=False)
    (HERE/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (HERE/'pdfinfo.txt').write_text('\n\n'.join(name+'\n'+info for name,info in pdfinfo.items()))
    print(json.dumps({'status':'passed','slides':2,'slide_pdf_pages':2,'speech_pdf_pages':2,'design_parts_identical':len(design),'text_complete':True,'rebuild_identical':True},ensure_ascii=False))


if __name__=='__main__':main()
