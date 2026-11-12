// The /3d page: a live 3D view of every agent cluster, manager and pipeline stage, with TradingView charts.
//
// Each department is a cluster of dots, one per agent, coloured by the agent's latest vote
// (green = buy / trending / full size, red = sell / choppy / shrink, grey = warming up or sitting out).
// Managers float above their departments. Pulses travel along the pipeline when ticks and orders
// happen: market feed -> departments -> desk -> risk gate -> Jev gate -> paper broker.
// Libraries are vendored under /vendor: three.js r128 (MIT) and lightweight-charts 4.1.3 (Apache-2.0).
(function(){
// Signed out (cloud login on): go to the sign-in page instead of drawing an empty view.
function api(url){return fetch(url).then(r=>{if(r.status===401){location.href='/login';throw new Error('signed out')}return r.json()})}
if(!window.THREE||!window.LightweightCharts){document.getElementById('err').style.display='block'}
const DEPTS={trend:{pos:[-40,0,-32],label:'Trend Trading'},reversion:{pos:[40,0,-32],label:'Reversion Trading'},
 research:{pos:[-40,0,32],label:'Research'},risk:{pos:[40,0,32],label:'Risk'}};
const MANAGERS={Atlas:{pos:[0,36,-32],depts:['trend','reversion']},Sage:{pos:[-40,36,32],depts:['research']},
 Sentinel:{pos:[40,36,32],depts:['risk']}};
const STAGES=[['feed','Market feed',[-95,0,0]],['desk','Desk',[85,0,0]],['riskgate','Risk gate',[118,0,0]],
 ['jev','Jev gate',[151,0,0]],['broker','Paper broker',[184,0,0]]];
const MAX_PULSES=40;
const scene_el=document.getElementById('scene');
let scene,camera,renderer,clusters={},stageNodes={},labels=[],pulses=[];
let yaw=0.3,pitch=0.5,dist=235,target=new (window.THREE?THREE.Vector3:Object)(44,6,0);

function color(v){if(v===null||v===undefined)return [0.29,0.31,0.36];const a=Math.min(1,Math.abs(v));
 return v>=0?[0.15+0.1*(1-a),0.45+0.3*a,0.25]:[0.55+0.4*a,0.2,0.2]}

function addLabel(html,pos){const el=document.createElement('div');el.className='label';el.innerHTML=html;
 scene_el.appendChild(el);const l={el,pos:new THREE.Vector3(...pos)};labels.push(l);return l}

function line(a,b,col){const g=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...a),new THREE.Vector3(...b)]);
 const m=new THREE.Line(g,new THREE.LineBasicMaterial({color:col,transparent:true,opacity:0.35}));scene.add(m);return m}

function node(pos,col,r){const m=new THREE.Mesh(new THREE.SphereGeometry(r,24,16),new THREE.MeshBasicMaterial({color:col}));
 m.position.set(...pos);scene.add(m);return m}

function buildCluster(key,n){const c=clusters[key];if(c&&c.n===n)return c;if(c){scene.remove(c.points)}
 const pos=new Float32Array(n*3),col=new Float32Array(n*3),R=4+Math.cbrt(n)*1.6,base=DEPTS[key].pos;
 for(let i=0;i<n;i++){ // fibonacci sphere, filled by radius so the cluster has volume
  const y=1-(i+0.5)/n*2,rad=Math.sqrt(1-y*y),th=i*2.399963,r=R*Math.cbrt((i*7919%n+0.5)/n);
  pos[i*3]=base[0]+Math.cos(th)*rad*r;pos[i*3+1]=base[1]+y*r;pos[i*3+2]=base[2]+Math.sin(th)*rad*r;
  col.set(color(null),i*3)}
 const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(pos,3));
 g.setAttribute('color',new THREE.BufferAttribute(col,3));
 const pts=new THREE.Points(g,new THREE.PointsMaterial({size:1.25,vertexColors:true,transparent:true,opacity:0.95}));
 scene.add(pts);return clusters[key]={points:pts,n,label:c?c.label:null}}

function init3d(){scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(50,1,0.1,2000);
 renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(2,devicePixelRatio));
 scene_el.prepend(renderer.domElement);
 for(const [k,d] of Object.entries(DEPTS)){buildCluster(k,350);clusters[k].label=addLabel(`<b>${d.label}</b><br><span class="mut">–</span>`,[d.pos[0],d.pos[1]-16,d.pos[2]])}
 for(const [k,label,pos] of STAGES){stageNodes[k]={mesh:node(pos,0x58a6ff,3),pos};addLabel(`<b>${label}</b>`,[pos[0],pos[1]-7,pos[2]])}
 for(const d of Object.values(DEPTS)){line(STAGES[0][2],d.pos,0x58a6ff);line(d.pos,STAGES[1][2],0x58a6ff)}
 for(let i=1;i<STAGES.length-1;i++)line(STAGES[i][2],STAGES[i+1][2],0x58a6ff);
 for(const [name,m] of Object.entries(MANAGERS)){m.mesh=node(m.pos,0xd2a8ff,3.4);
  m.label=addLabel(`<b>${name}</b><br><span class="mut">–</span>`,[m.pos[0],m.pos[1]+8,m.pos[2]]);
  for(const k of m.depts)line(m.pos,DEPTS[k].pos,0xd2a8ff)}
 resize();addEventListener('resize',resize);controls();requestAnimationFrame(frame)}

function resize(){const w=scene_el.clientWidth,h=scene_el.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix()}

function controls(){let drag=null;
 scene_el.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];scene_el.setPointerCapture(e.pointerId);scene_el.style.cursor='grabbing'});
 scene_el.addEventListener('pointermove',e=>{if(!drag)return;yaw-=(e.clientX-drag[0])*0.006;
  pitch=Math.max(-1.3,Math.min(1.3,pitch+(e.clientY-drag[1])*0.006));drag=[e.clientX,e.clientY]});
 scene_el.addEventListener('pointerup',()=>{drag=null;scene_el.style.cursor='grab'});
 scene_el.addEventListener('wheel',e=>{e.preventDefault();dist=Math.max(60,Math.min(500,dist*(1+Math.sign(e.deltaY)*0.08)))},{passive:false})}

function pulse(path,col){if(pulses.length>=MAX_PULSES)return;const m=node(path[0],col,1.4);pulses.push({m,path,t:0})}

function frame(now){requestAnimationFrame(frame);
 camera.position.set(target.x+dist*Math.cos(pitch)*Math.sin(yaw),target.y+dist*Math.sin(pitch),target.z+dist*Math.cos(pitch)*Math.cos(yaw));
 camera.lookAt(target);
 for(let i=pulses.length-1;i>=0;i--){const p=pulses[i];p.t+=0.04;const seg=Math.floor(p.t);
  if(seg>=p.path.length-1){scene.remove(p.m);pulses.splice(i,1);continue}
  const a=p.path[seg],b=p.path[seg+1],f=p.t-seg;p.m.position.set(a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f,a[2]+(b[2]-a[2])*f)}
 renderer.render(scene,camera);
 const w=scene_el.clientWidth,h=scene_el.clientHeight;
 for(const l of labels){const v=l.pos.clone().project(camera);const vis=v.z<1&&Math.abs(v.x)<1.1&&Math.abs(v.y)<1.1;
  l.el.style.display=vis?'block':'none';if(vis){l.el.style.left=((v.x+1)/2*w)+'px';l.el.style.top=((1-v.y)/2*h)+'px'}}}

function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function pct(v){return v===null||v===undefined?'–':Math.round(100*v)+'%'}
function renderSchool(org){
 document.getElementById('school').innerHTML=Object.values(org.departments).map(d=>{const s=d.school||{};
  const courses=Object.values(s.courses||{}).map(c=>`${esc(c.name)} ${pct(c.avg)}`).join(' · ');
  const practice=d.traders?`<br><span class="mut">practice trades this hour: avg ${(d.trades_hour_sum/d.traders).toFixed(0)}, min ${d.trades_hour_min??0}</span>`:'';
  const used=Object.entries(d.masters_used||{}).map(([k,n])=>`${esc((s.courses||{})[k]?.name||k)} ${n}`).join(' · ');
  const masters=used?`<br><span class="mut">masters' rules used: ${used}</span>`:'';
  return `<div class="row"><span><b>${esc(d.name)}</b><br><span class="mut">${courses||'not trained'}</span>${practice}${masters}</span>`+
   `<span class="right">${s.graduates||0}/${s.trained||0} passed<br><span class="mut">exam ${pct(s.avg_exam)}</span></span></div>`}).join('');
 const p=org.practice;if(p){const lh=p.last_hour;
  document.getElementById('school').innerHTML=`<div class="mut">practice hour ${p.hour} · ${Math.round(100*p.ticks_into_hour/p.hour_ticks)}% done · at least ${p.min_trades} trades per trader per hour`+
   (lh?` · last upgrade: ${lh.back_in} benched agents back in`:'')+`</div>`+document.getElementById('school').innerHTML}
 const news=org.news||[];
 document.getElementById('news').innerHTML=news.length?news.map(n=>
  `<div class="row"><span>${esc(n.text)}</span><span class="right mut">${esc(n.kind)} · t${n.tick}</span></div>`).join(''):'no headlines yet'}
function fmt(v){return v===null||v===undefined?'–':v.toFixed(2)}
function renderProgram(org){const p=org.practice||{};const rows=[];
 const m=p.memory;rows.push(['Memory',!m||!m.on?'off':`${m.remembered} agents remembered at start`+(m.saved_tick!=null?` · saved at tick ${m.saved_tick}`:'')]);
 const r=p.last_review;if(r)rows.push(['Self-checks',`review ${r.review}: ${r.changes_kept||0} changes kept, ${r.changes_undone||0} undone`]);
 const c=p.checks;if(c)rows.push(['Order checks',`this hour: ${c.blocked} blocked, ${c.shrunk} cut in size · desk trades ${p.desk_trades||0}`]);
 const acts=(p.managers&&p.managers.actions)||[];rows.push(['Managers acted',acts.length?acts.map(a=>`${esc(a.manager)} ${esc(a.did)}`).join(' · '):'nothing this hour']);
 const st=p.coach_status||{};const co=p.coach;
 rows.push(['Claude coach',!st.claude?'off (needs ANTHROPIC_API_KEY)':co?esc(co.summary||'')+(co.looked_at&&co.looked_at.length?` <span class="mut">(looked at ${co.looked_at.length} things)</span>`:'')+
  (co.trial?' · trial: '+Object.entries(co.trial).map(([d,t])=>`${esc(d)} ${t.kept?'kept':'undone'}`).join(', '):''):`on · ${st.calls||0} calls, waiting for the first hour`]);
 document.getElementById('program').innerHTML=rows.map(([k,v])=>`<div class="row"><span><b>${k}</b><br><span class="mut">${v}</span></span></div>`).join('')}
function renderOrg(org){let agents=0,active=0;renderSchool(org);renderProgram(org);
 for(const [k,d] of Object.entries(org.departments)){agents+=d.agents;active+=d.active;
  if(window.THREE&&scene&&d.agents){const c=buildCluster(k,d.agents);const col=c.points.geometry.attributes.color;
   d.votes.forEach((v,i)=>{if(i<c.n)col.setXYZ(i,...color(v))});col.needsUpdate=true;
   c.label.el.innerHTML=`<b>${d.name}</b><br><span class="mut">${d.agents} agents · avg ${fmt(d.avg_vote)}</span>`}}
 document.getElementById('tick').textContent=org.tick;document.getElementById('agents').textContent=agents;
 document.getElementById('active').textContent=active;
 document.getElementById('depts').innerHTML=Object.values(org.departments).map(d=>
  `<div class="row"><span><b>${d.name}</b><br><span class="mut">${d.job}</span><br>`+
  `<span class="mut">brains: trust ${fmt(d.avg_weight)} · right ${d.skill===null?'–':(100*d.skill).toFixed(1)+'%'} · ${d.sitting_out} sitting out</span>`+
  (d.trade_win_rate!==null&&d.trade_win_rate!==undefined?`<br><span class="mut">trades won ${(100*d.trade_win_rate).toFixed(1)}% of ${(d.trade_wins+d.trade_losses).toLocaleString()} · ${d.reviews_learned||0} learned from reviews</span>`:'')+
  (d.held?`<br><span class="mut">held ${d.held} weak calls</span>`:'')+
  (d.macro!==null&&d.macro!==undefined?`<br><span class="mut">news read ${d.macro>0?'bullish':'bearish'} (${fmt(d.macro)})</span>`:'')+`</span>`+
  `<span class="right">${d.agents} agents<br><span class="mut">avg ${fmt(d.avg_vote)}</span> · `+
  `<span class="${d.pnl>=0?'up':'dn'}">${d.pnl.toFixed(2)}</span></span></div>`).join('');
 const sl=org.slider;if(sl&&!sliding){const v=sl.per_department||Math.round(agents/sl.departments)||sl.min;
  slider.min=sl.min;slider.max=sl.max;slider.value=v;showSlider(v)}
 document.getElementById('managers').innerHTML=org.managers.map(m=>
  `<div class="row"><span><b>${m.manager}</b> <span class="mut">${m.title}</span><br>${m.call}<br><span class="mut">${esc(m.training||'')}</span></span>`+
  `<span class="right">${m.agents} agents<br><span class="${m.pnl>=0?'up':'dn'}">${m.pnl.toFixed(2)}</span></span></div>`).join('');
 for(const m of org.managers){const mm=MANAGERS[m.manager];if(mm&&mm.label)mm.label.el.innerHTML=`<b>${m.manager}</b><br><span class="mut">${m.call}</span>`}}

let priceChart,priceSeries,pnlSeries,history=[],sym=null;
function initCharts(){if(!window.LightweightCharts)return;
 const opts={layout:{background:{color:'transparent'},textColor:'#8a8f98'},grid:{vertLines:{color:'#1d2230'},horzLines:{color:'#1d2230'}},
  rightPriceScale:{borderColor:'#232836'},timeScale:{borderColor:'#232836',timeVisible:true,secondsVisible:true},autoSize:true};
 priceChart=LightweightCharts.createChart(document.getElementById('price'),opts);
 priceSeries=priceChart.addAreaSeries({lineColor:'#58a6ff',topColor:'rgba(88,166,255,.35)',bottomColor:'rgba(88,166,255,0)',lineWidth:2});
 const pc=LightweightCharts.createChart(document.getElementById('pnl'),opts);
 pnlSeries=pc.addBaselineSeries({baseValue:{type:'price',price:0},topLineColor:'#3fb950',bottomLineColor:'#f85149',
  topFillColor1:'rgba(63,185,80,.3)',topFillColor2:'rgba(63,185,80,0)',bottomFillColor1:'rgba(248,81,73,0)',bottomFillColor2:'rgba(248,81,73,.3)'})}
function drawPrice(){if(!priceSeries||!sym)return;let last=0;
 priceSeries.setData(history.filter(h=>h.prices[sym]!==undefined).map(h=>{const t=Math.max(last+1,Math.floor(h.ts));last=t;return {time:t,value:h.prices[sym]}}))}
function loadPrices(){api('/api/prices').then(d=>{history=d.history;
 const sel=document.getElementById('sym');const syms=history.length?Object.keys(history[history.length-1].prices):[];
 if(syms.length&&!sym){sym=syms[0];sel.innerHTML=syms.map(s=>`<option>${s}</option>`).join('');sel.onchange=()=>{sym=sel.value;drawPrice()}}
 drawPrice();if(pnlSeries){const t0=Math.floor(Date.now()/1000)-d.pnl.length;pnlSeries.setData(d.pnl.map((p,i)=>({time:t0+i,value:p.pnl})))}})}

const slider=document.getElementById('slider');let sliding=false,sliderTimer;
function showSlider(v){document.getElementById('perdept').textContent=v;document.getElementById('sliderTotal').textContent=v*4}
slider.addEventListener('input',()=>{sliding=true;showSlider(+slider.value);clearTimeout(sliderTimer);
 sliderTimer=setTimeout(()=>{fetch('/api/agents',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({per_department:+slider.value})}).then(r=>r.json()).then(d=>{
  document.getElementById('sliderMsg').textContent=d.error?d.error:`Saved. Departments resize to ${d.per_department} each within a few seconds.`;
  setTimeout(()=>{sliding=false},6000)}).catch(()=>{document.getElementById('sliderMsg').textContent='Could not save. Is the dashboard running?';sliding=false})},400)});
function loadOrg(){api('/api/org').then(renderOrg)}

if(window.THREE)init3d();initCharts();loadOrg();loadPrices();
setInterval(loadOrg,2000);setInterval(loadPrices,3000);
const es=new EventSource('/events');
es.onmessage=m=>{if(!scene)return;const e=JSON.parse(m.data);const P=k=>stageNodes[k].pos;
 if(e.type==='tick'&&e.shard===0){const d=Object.values(DEPTS)[e.tick%4];pulse([P('feed'),d.pos,P('desk')],0x58a6ff)}
 if(e.type==='order')pulse([P('desk'),P('riskgate'),P('jev'),P('broker')],0x3fb950);
 if(e.type==='risk')pulse([P('desk'),P('riskgate')],0xf0b429);
 if(e.type==='jev'&&e.decision==='rejected')pulse([P('riskgate'),P('jev')],0xf85149)};
})();
