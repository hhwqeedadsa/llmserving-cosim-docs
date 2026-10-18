const NETWORKS=JSON.parse(document.getElementById('network-data').textContent);
let networkActiveCase=null;
const edgeKey=(a,b)=>[a,b].sort((x,y)=>x-y).join(':');
function networkPath(t,src,dst){
 const nodes=new Map(t.nodes.map(n=>[n.id,n]));
 if(src===dst)return {kind:'same_gpu',nodes:[src]};
 if(t.intra_host?.enabled&&nodes.get(src).server===nodes.get(dst).server)return {kind:'intra_host',nodes:[src,dst]};
 const adjacency=new Map(t.nodes.map(n=>[n.id,[]]));
 for(const e of t.links){adjacency.get(e.src).push(e.dst);adjacency.get(e.dst).push(e.src);}
 const queue=[src],previous=new Map([[src,null]]);
 for(let i=0;i<queue.length&&!previous.has(dst);i++)for(const peer of adjacency.get(queue[i]).slice().sort((a,b)=>a-b)){
  if(previous.has(peer)||(nodes.get(peer).kind==='gpu'&&peer!==dst))continue;
  previous.set(peer,queue[i]);queue.push(peer);
 }
 if(!previous.has(dst))return {kind:'unreachable',nodes:[]};
 const path=[];for(let n=dst;n!==null;n=previous.get(n))path.push(n);
 return {kind:'external',nodes:path.reverse()};
}
function networkExample(kind){
 const c=NETWORKS.cases[selected];if(!c)return;
 const t=NETWORKS.profiles[c.profile],nodes=new Map(t.nodes.map(n=>[n.id,n]));
 for(const group of c.groups)for(const src of group)for(const dst of group){
  if(src!==dst&&(kind==='local'?nodes.get(src).server===nodes.get(dst).server:nodes.get(src).pod!==nodes.get(dst).pod)){
   $('network-src').value=src;$('network-dst').value=dst;renderNetwork();return;
  }
 }
 $('network-path').textContent='当前EP组中没有这种放置。';
}
function renderNetwork(){
 const c=NETWORKS.cases[selected],row=rowById(selected);
 if(!c||!row){$('network-body').hidden=true;$('network-missing').hidden=false;return;}
 $('network-body').hidden=false;$('network-missing').hidden=true;
 const t=NETWORKS.profiles[c.profile],nodes=new Map(t.nodes.map(n=>[n.id,n]));
 const gpus=t.nodes.filter(n=>n.kind==='gpu'),pods=[...new Set(gpus.map(n=>n.pod))].sort((a,b)=>a-b);
 for(const id of ['network-src','network-dst']){$(id).max=Math.max(...gpus.map(n=>n.id));}
 if(networkActiveCase!==selected){
  networkActiveCase=selected;const group=c.groups[0],src=group[0];
  $('network-src').value=src;$('network-dst').value=group.find(r=>nodes.get(r).pod!==nodes.get(src).pod)??group[1];
 }
 const src=Number($('network-src').value),dst=Number($('network-dst').value);
 if(!$('network-src').value.trim()||!$('network-dst').value.trim()||!Number.isInteger(src)||!Number.isInteger(dst)||nodes.get(src)?.kind!=='gpu'||nodes.get(dst)?.kind!=='gpu'){
  $('network-path').textContent='请输入本次拓扑中的有效GPU编号。';return;
 }
 const route=networkPath(t,src,dst),active=new Set();
 for(let i=1;i<route.nodes.length;i++)active.add(edgeKey(route.nodes[i-1],route.nodes[i]));
 const group=c.groups.find(g=>g.includes(src)),sameGroup=group.includes(dst);
 const label=id=>{const n=nodes.get(id);return `${n.kind==='gpu'?'GPU':n.kind.toUpperCase()} ${id}`;};
 $('network-path').textContent=route.kind==='intra_host'?`GPU ${src} → 服务器 ${nodes.get(src).server} 的机内织构 → GPU ${dst}；不走外部交换机。`:route.kind==='same_gpu'?`GPU ${src}本地工作，不发网络。`:route.kind==='unreachable'?'两端没有可用分组网络路径。':route.nodes.map(label).join(' → ');
 $('network-ep').textContent=`${row.gpus}卡 · EP${row.ep} × ${row.replicas}副本；源GPU所属组：[${group.join(', ')}]。${sameGroup?'所选两端属于同一个EP组。':'所选两端跨EP组；当前业务不直接交换数据，此处仅展示物理可达性。'}`;
 const planes=new Set(t.nodes.filter(n=>n.kind!=='gpu').map(n=>n.plane)),rails=new Set(t.nodes.filter(n=>n.kind==='tor').map(n=>n.rail));
 $('network-title').textContent=`${row.gpus} GPU / ${row.servers}台服务器 / ${pods.length} Pod · ${rails.size} rail / ${planes.size} plane`;
 const tierRows=[];for(const tier of [...new Set(t.links.map(l=>l.tier))]){
  const links=t.links.filter(l=>l.tier===tier),rates=[...new Set(links.map(l=>l.rate_bps/1e9))],delays=[...new Set(links.map(l=>l.delay_ns/1000))];
  tierRows.push(`<tr><td>${esc(tier)}</td><td>${links.length}</td><td>${rates.map(r=>number(r,0)).join(' / ')} Gb/s</td><td>${delays.map(d=>number(d,2)).join(' / ')} μs</td></tr>`);
 }
 $('network-links').innerHTML=tierRows.join('');
 const ratios=t.nodes.filter(n=>n.kind==='tor').map(n=>{
  const down=t.links.filter(l=>l.dst===n.id&&nodes.get(l.src).kind==='gpu').reduce((s,l)=>s+l.rate_bps,0);
  const up=t.links.filter(l=>l.src===n.id&&nodes.get(l.dst).kind!=='gpu').reduce((s,l)=>s+l.rate_bps,0);return up?down/up:null;
 }).filter(x=>x!==null);
 const exits=pods.map(p=>{
  const access=t.links.filter(l=>l.tier==='access'&&nodes.get(l.src).pod===p).reduce((s,l)=>s+l.rate_bps,0);
  const exit=t.links.filter(l=>l.tier==='agg_core'&&nodes.get(l.src).pod===p).reduce((s,l)=>s+l.rate_bps,0);return exit?access/exit:null;
 }).filter(x=>x!==null);
 $('network-capacity').textContent=`ToR最大下/上容量比 ${number(Math.max(...ratios),1)}:1；Pod接入/跨Pod出口最大容量比 ${number(Math.max(...exits),1)}:1。它们是容量比，不是实测吞吐下降倍数。`;
 $('network-local').textContent=t.intra_host?.enabled?`机内：每GPU每方向 ${number(t.intra_host.rate_bps/8e9,0)} GB/s；每段传播 ${t.intra_host.delay_ns} ns；分块 ${number(t.intra_host.chunk_bytes/1024,0)} KiB。它是带宽抽象，不是完整NVLink协议。`:'本输入没有机内织构模型，同机不同GPU也按外部网络处理。';
 const W=Math.max(1050,pods.length*150+40),H=385,positions=new Map();
 const cores=t.nodes.filter(n=>n.kind==='core');cores.forEach((n,i)=>positions.set(n.id,{x:W*(i+1)/(cores.length+1),y:40}));
 for(const p of pods){
  const x=20+pods.indexOf(p)*(W-40)/pods.length,width=(W-40)/pods.length;
  for(const [kind,y] of [['agg',145],['tor',225]]){
   const list=t.nodes.filter(n=>n.kind===kind&&n.pod===p);
   list.forEach((n,i)=>positions.set(n.id,{x:x+width*(i+1)/(list.length+1),y}));
  }
 }
 let svg=`<svg viewBox="0 0 ${W} ${H}" style="min-width:${W}px" role="img" aria-label="实际三层网络的交换机连接，GPU按Pod汇总"><title>实际网络连接及一条计算所得的可达路径</title>`;
 for(const p of pods){const x=20+pods.indexOf(p)*(W-40)/pods.length,width=(W-40)/pods.length;
  svg+=`<rect x="${x+3}" y="95" width="${width-6}" height="275" rx="8" fill="none" stroke="#d5e0e5"/><text x="${x+width/2}" y="116" text-anchor="middle" font-size="12" fill="#142d3c">Pod ${p}</text>`;
 }
 for(const e of t.links)if(positions.has(e.src)&&positions.has(e.dst)){
  const a=positions.get(e.src),b=positions.get(e.dst),hot=active.has(edgeKey(e.src,e.dst));
  svg+=`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${hot?'#be6129':'#c5d5dd'}" stroke-width="${hot?3:1}"/>`;
 }
 for(const n of t.nodes.filter(n=>n.kind==='tor')){
  const a=positions.get(n.id),edges=t.links.filter(l=>l.dst===n.id&&nodes.get(l.src).kind==='gpu');
  const hot=edges.some(e=>active.has(edgeKey(e.src,e.dst)));
  svg+=`<line x1="${a.x}" y1="${a.y+13}" x2="${a.x}" y2="300" stroke="${hot?'#be6129':'#b8cbd5'}" stroke-width="${hot?3:1}"/><text x="${a.x}" y="280" text-anchor="middle" fill="#617381" font-size="11">${edges.length}卡</text>`;
 }
 for(const n of t.nodes.filter(n=>positions.has(n.id))){const a=positions.get(n.id),width=n.kind==='core'?72:30,hot=route.nodes.includes(n.id);
  svg+=`<g><title>${label(n.id)}</title><rect x="${a.x-width/2}" y="${a.y-13}" width="${width}" height="26" rx="4" fill="${hot?'#fff0df':'#edf4f7'}" stroke="${hot?'#be6129':'#a8bfcc'}"/><text x="${a.x}" y="${a.y+4}" text-anchor="middle" font-size="11" fill="#142d3c">${n.kind==='core'?'Core ':n.kind==='agg'?'A':'T'}${n.index??n.id}</text></g>`;
 }
 for(const p of pods){const x=20+pods.indexOf(p)*(W-40)/pods.length,width=(W-40)/pods.length,local=gpus.filter(g=>g.pod===p),servers=new Set(local.map(g=>g.server));
  const hot=route.nodes.some(id=>nodes.get(id).kind==='gpu'&&nodes.get(id).pod===p);
  svg+=`<rect x="${x+10}" y="300" width="${width-20}" height="54" rx="5" fill="${hot?'#fff3e5':'#eef4f6'}" stroke="${hot?'#be6129':'#b9ccd5'}"/><text x="${x+width/2}" y="322" text-anchor="middle" font-size="12" fill="#142d3c">${local.length} GPU</text><text x="${x+width/2}" y="342" text-anchor="middle" font-size="11" fill="#617381">${servers.size}台服务器</text>`;
 }
 $('network-svg').innerHTML=svg+'</svg>';
}
for(const id of ['network-src','network-dst'])$(id).addEventListener('input',renderNetwork);
$('network-local-example').addEventListener('click',()=>networkExample('local'));
$('network-pod-example').addEventListener('click',()=>networkExample('pod'));
