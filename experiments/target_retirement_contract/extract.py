"""Select exact named C/C++ function text; no provider code is imported."""
import re


def function(text,name):
    pattern=r'(?m)^(?:static )?(?:[A-Za-z_][\w ]*\s+)?\*?'+re.escape(name)+r'\s*\('
    for match in re.finditer(pattern,text):
        opening=text.find('{',match.end())
        if opening>=0 and ';' not in text[match.end():opening]:break
    else:raise ValueError('function definition not found: '+name)
    start=match.start()
    depth=0;state='code';escaped=False;i=opening
    while i<len(text):
        ch=text[i];nxt=text[i:i+2]
        if state in ('string','char'):
            if escaped:escaped=False
            elif ch=='\\':escaped=True
            elif ch==('"' if state=='string' else "'"):state='code'
        elif state=='line':
            if ch=='\n':state='code'
        elif state=='comment':
            if nxt=='*/':state='code';i+=1
        elif nxt=='//':state='line';i+=1
        elif nxt=='/*':state='comment';i+=1
        elif ch=='"':state='string'
        elif ch=="'":state='char'
        elif ch=='{':depth+=1
        elif ch=='}':
            depth-=1
            if depth==0:return dict(text=text[start:i+1],line=text.count('\n',0,start)+1,
                                     end_line=text.count('\n',0,i)+1)
        i+=1
    raise ValueError('unterminated function: '+name)
