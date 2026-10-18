/* Dependency-free interaction checks. This is a DOM stub, not a browser render. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [htmlPath,sourcePath]=process.argv.slice(2);
assert(htmlPath&&sourcePath,'usage: node check_runtime_html.cjs report.html timings.json');
const html=fs.readFileSync(htmlPath,'utf8');
const scripts=[...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
const embedded=id=>scripts.find(m=>m[1].includes(`id="${id}"`))[2];
assert.deepEqual(JSON.parse(embedded('runtime-data')),JSON.parse(fs.readFileSync(sourcePath,'utf8')));
assert(!/@@[A-Z_]+@@/.test(html),'unresolved template');
assert(!/<(?:script|img|link)\b[^>]*(?:src|href)\s*=/i.test(html),'external resources');
const elements=new Map(),downloads=[],blobs=new Map();let printed=0;
for(const match of html.matchAll(/\bid="([^"]+)"/g)){
 const item={id:match[1],innerHTML:'',textContent:'',hidden:false,style:{},handlers:{},_value:'',
  addEventListener(kind,fn){this.handlers[kind]=fn;},click(){this.handlers.click?.({});}};
 Object.defineProperty(item,'value',{get(){return this._value;},set(v){this._value=String(v);}});
 elements.set(match[1],item);
}
elements.get('runtime-data').textContent=embedded('runtime-data');
elements.get('network-data').textContent=embedded('network-data');
for(const [id,value] of Object.entries({family:'all',version:'all',metric:'worker',combinations:'12',repeats:'3','network-src':'0','network-dst':'1'}))elements.get(id).value=value;
const document={getElementById(id){assert(elements.has(id),`missing element ${id}`);return elements.get(id);},
 body:{appendChild(){}},createElement(){return {click(){downloads.push({name:this.download,blob:blobs.get(this.href)});},remove(){}};}};
const context=vm.createContext({document,window:{print(){printed++;}},Blob,
 URL:{createObjectURL(blob){const key='blob:'+blobs.size;blobs.set(key,blob);return key;},revokeObjectURL(){}},setTimeout(){}});
const code=scripts.filter(m=>!m[1].includes('application/json')).map(m=>m[2]).join('\n');
new vm.Script(code);vm.runInContext(code,context);
const evaluate=script=>vm.runInContext(script,context);
const get=id=>elements.get(id),checks=[];
assert.equal(evaluate('current.length'),8);assert.equal(evaluate('ALL.filter(r=>r.right_censored).length'),2);
assert(evaluate('ALL.filter(r=>r.right_censored).every(r=>r.simulation_finish_ns===null&&!r.independently_verified)'));
checks.push('8 unchanged measurements, 2 timeouts with no final time');
for(const row of JSON.parse(embedded('runtime-data')).rows){
 evaluate(`selected=${JSON.stringify(row.id)};render();`);
 assert.equal(get('network-body').hidden,false);
 assert(get('network-svg').innerHTML.includes('<svg'));
 assert(!/NaN|Infinity|undefined/.test(get('network-svg').innerHTML));
 evaluate(`(()=>{const c=NETWORKS.cases[selected],t=NETWORKS.profiles[c.profile],r=rowById(selected);
 if(t.links.length!==r.links||t.nodes.filter(n=>n.kind==='gpu').length!==r.gpus)throw Error('physical input/count mismatch');
 const src=Number($('network-src').value),dst=Number($('network-dst').value),p=networkPath(t,src,dst);
 if(p.nodes[0]!==src||p.nodes.at(-1)!==dst)throw Error('path endpoints');
 if(p.kind==='external')for(let i=1;i<p.nodes.length;i++){
  if(!t.links.some(e=>(e.src===p.nodes[i-1]&&e.dst===p.nodes[i])||(e.dst===p.nodes[i-1]&&e.src===p.nodes[i])))throw Error('invented edge');
  if(i<p.nodes.length-1&&t.nodes.find(n=>n.id===p.nodes[i]).kind==='gpu')throw Error('endpoint as router');
 }
})()`);
 get('network-local-example').click();
 if(evaluate('NETWORKS.profiles[NETWORKS.cases[selected].profile].intra_host?.enabled'))assert(get('network-path').textContent.includes('机内织构'));
 get('network-pod-example').click();assert(get('network-path').textContent.includes('CORE'));
 get('network-src').value='-1';get('network-src').handlers.input();assert(get('network-path').textContent.includes('有效GPU'));
 get('network-src').value='';get('network-src').handlers.input();assert(get('network-path').textContent.includes('有效GPU'));
}
checks.push('all 8 actual layouts; physical paths; no GPU transit; local/cross-Pod buttons; invalid/empty rank');
evaluate("selected=ALL.find(r=>r.gpus===1024).id;networkActiveCase=null;renderNetwork();");
assert(get('network-ep').textContent.includes('EP4 × 256'));
assert(get('network-capacity').textContent.includes('16.0:1'));
assert(get('network-capacity').textContent.includes('64.0:1'));
get('network-src').value='0';get('network-dst').value='2';get('network-src').handlers.input();
assert(get('network-ep').textContent.includes('跨EP组'));
get('network-dst').value='0';get('network-dst').handlers.input();assert(get('network-path').textContent.includes('本地工作'));
checks.push('1024 EP4 x256; 16:1 and 64:1; cross-group physical-only and same-GPU cases');
get('family').value=evaluate('ALL[0].family');get('family').handlers.change();assert(evaluate('current.every(r=>r.family===$("family").value)'));
get('version').value=evaluate('ALL[0].implementation');get('version').handlers.change();assert(evaluate('current.every(r=>r.implementation===$("version").value)'));
get('metric').value='simulation';get('metric').handlers.change();assert(get('chart-note').textContent.includes('最终模拟时间'));
get('family').value='all';get('version').value='all';evaluate('render()');
get('rows').handlers.keydown({key:'Enter',preventDefault(){},target:{closest(){return {dataset:{case:evaluate('ALL[0].id')}};}}});
assert.equal(evaluate('selected'),evaluate('ALL[0].id'));
get('combinations').value='0';get('combinations').handlers.input();assert(get('estimate').textContent.includes('正整数'));
get('combinations').value='12';get('combinations').handlers.input();assert(get('estimate').innerHTML.includes('预计顺序执行'));
get('download-json').click();get('download-csv').click();get('print').click();assert.equal(printed,1);
assert.equal(downloads.length,2);
get('family').value='missing';get('family').handlers.change();assert.equal(evaluate('current.length'),0);assert(get('network-body').hidden);
checks.push('filters, metric, keyboard selection, estimate, downloads, print, empty selection');
Promise.all(downloads.map(async d=>({name:d.name,text:await d.blob.text()}))).then(outputs=>{
 assert.deepEqual(JSON.parse(outputs[0].text),JSON.parse(embedded('runtime-data')));
 assert(outputs[1].text.includes('baseline512'));
 console.log(JSON.stringify({passed:true,checks,browser_screenshot_taken:false,simulation_executed:false},null,2));
});
