/* LinkedIn Scraper — page logic. Data is fetched live from window.__LI_DATA_URL__ (see template). */
let D = {posts:[],people:[],companies:[],company_lb:[],stats:{}};
/* ── helpers ── */
const esc=s=>(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const ini=n=>(n||'?').trim().split(/\s+/).map(w=>w[0]||'').join('').toUpperCase().slice(0,2);
const GRAD=[['#6366f1','#22d3ee'],['#8b5cf6','#06b6d4'],['#059669','#818cf8'],['#d97706','#f472b6'],['#0ea5e9','#a78bfa'],['#10b981','#f59e0b']];
function gradFor(name){const i=Math.abs((name||'').split('').reduce((a,c)=>a+c.charCodeAt(0),0))%GRAD.length;return GRAD[i]}
function av(name,pic,sz,cls){
  const s=sz||44,c=cls||'pcav';
  const g=gradFor(name);
  const img=(pic&&pic.startsWith('http'))?`<img src="${esc(pic)}" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;border-radius:inherit" onerror="this.style.display='none'">`:'';
  return`<div class="${c}" style="width:${s}px;height:${s}px;position:relative;background:linear-gradient(135deg,${g[0]},${g[1]})">${ini(name)}${img}</div>`;
}
const RICO={LIKE:'👍',PRAISE:'🙌',INTEREST:'🤔',APPRECIATION:'❤️',EMPATHY:'🫂'};
const RCLS={LIKE:'blike',PRAISE:'bprs',INTEREST:'bint',APPRECIATION:'bapp',EMPATHY:'bemp'};
const SCLS=s=>!s?'bic':s.includes('C-Level')||s.includes('Founder')?'bcs':s.includes('VP')?'bvp':s.includes('Director')?'bvp':s.includes('Manager')?'bmg':'bic';
const SLBL=s=>(s||'Unknown').replace('IC / Individual Contributor','IC').replace('C-Level / Founder','C-Level');
const DLBL=d=>d?d.replace('SECOND_DEGREE','2nd').replace('THIRD_DEGREE','3rd').replace('FIRST_DEGREE','1st'):'';
const fmtN=n=>{n=+n||0;return n>=1000?(n/1000).toFixed(n>=10000?0:1).replace(/\.0$/,'')+'k':String(n);};
/* Target-company labels (per surface): /p2 = Position², a client portal = that
   client, e.g. NorthStar. Set by the template via window.__LI_CFG__. "Employee"
   in the sheet's Relationship-to-Target column means an employee of the *target*
   company, so the badge must name that company, not always Position². */
const LI_CFG=(window.__LI_CFG__)||{};
const EMP_FULL=LI_CFG.employer||'Position²';
const EMP_SHORT=LI_CFG.employerShort||'P²';
const EMP_TOKENS=(LI_CFG.employerTokens&&LI_CFG.employerTokens.length?LI_CFG.employerTokens:['position']).map(t=>String(t).toLowerCase());
const isEmployee=p=>p&&(p.relationship==='Employee'||EMP_TOKENS.some(t=>(p.company||'').toLowerCase().includes(t)));

/* ── state ── */
let PF={sen:'all',deg:'all',react:'all',q:'',comp:'',dFrom:'',dTo:'',sort:'none'};
let _filteredPosts=[];
let PLF={sen:'all',deg:'all',minP:0,country:'all',city:'all',dFrom:'',dTo:'',q:'',excludeP2:false,dm:false,rel:'all'};
const jsstr=s=>String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
let CF={q:'',dm:false,minPpl:0};
let activeStatFilter=null;
let _liLoading=false;

/* ── counter animation ── */
function countUp(){
  document.querySelectorAll('[data-count]').forEach(el=>{
    const t=parseInt(el.dataset.count)||0;let s=0;const inc=t/600*16;
    const run=()=>{s=Math.min(s+inc,t);el.textContent=Math.round(s);if(s<t)requestAnimationFrame(run)};
    requestAnimationFrame(run);
  });
}

/* ── tab switch ── */
function switchTab(t){
  ['overview','post','people','companies'].forEach(k=>{
    document.getElementById('tab-'+k).classList.toggle('active',k===t);
    document.getElementById('nt-'+k).classList.toggle('active',k===t);
  });
  setTimeout(countUp,50);
}

/* ═══════════════════════════════════
   CROSS-FILTER NAV — overview blocks → detail tabs, filter pre-applied
═══════════════════════════════════ */
function _scrollTabs(){var m=document.querySelector('.mega-tabs');if(m)m.scrollIntoView({behavior:'smooth',block:'start'});}
function _blankPLF(){return {sen:'all',deg:'all',minP:0,country:'all',city:'all',dFrom:'',dTo:'',q:'',excludeP2:false,dm:false,rel:'all'};}
function _blankPF(){return {sen:'all',deg:'all',react:'all',q:'',comp:'',dFrom:'',dTo:'',sort:'none'};}
function goPeople(patch){
  PLF=Object.assign(_blankPLF(),patch||{});
  switchTab('people');buildPeopleTab();_syncPeopleControls();_scrollTabs();
}
function _syncPeopleControls(){
  var senTxt={all:'All',csuite:'C-Suite',vpdirector:'VP/Dir',managers:'Manager',ics:'IC'}[PLF.sen]||'All';
  var grp=document.querySelector('#tab-people .fbar-primary .chips');
  if(grp)grp.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('on',c.textContent.indexOf(senTxt)>=0);});
  var dm=document.getElementById('plf-dm');if(dm)dm.classList.toggle('on',!!PLF.dm);
  var p2=document.getElementById('plf-p2');if(p2)p2.classList.toggle('on',!!PLF.excludeP2);
  var sels=document.querySelectorAll('#tab-people .fbar-secondary .lkf-sel');
  if(sels[0])sels[0].value=(PLF.country&&PLF.country!=='all')?PLF.country:'all';
}
function goPosts(patch){
  PF=Object.assign(_blankPF(),patch||{});
  switchTab('post');buildPostTab();_syncPostControls();_scrollTabs();
}
function _syncPostControls(){
  var g=document.getElementById('pf-sen');
  var lbl={'all':'All','C-Level / Founder':'C-Level','VP':'VP','Director':'Director','Manager':'Manager'}[PF.sen]||'All';
  if(g)g.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('on',c.textContent.trim()===lbl);});
  var rg=document.getElementById('pf-react');
  if(rg){var rl={'all':'All','LIKE':'Like','PRAISE':'Praise','EMPATHY':'Empathy','APPRECIATION':'Appreciation','INTEREST':'Interest'}[PF.react]||'All';
    rg.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('on',c.textContent.indexOf(rl)>=0);});}
}
function goCompanies(){switchTab('companies');_scrollTabs();}
/* map a seniority-breakdown label to the People-tab bucket key */
function _senToBucket(k){
  if(/C-Level|Founder/.test(k))return'csuite';
  if(/^VP$|Director/.test(k))return'vpdirector';
  if(/Manager/.test(k))return'managers';
  if(/IC|Individual/.test(k))return'ics';
  return'all';
}

/* ═══════════════════════════════════
   LIVE DATA — load / refresh / toast
═══════════════════════════════════ */
function liToast(msg,kind){
  const t=document.getElementById('liToast');if(!t)return;
  t.textContent=msg;t.className='li-toast show'+(kind?' '+kind:'');
  clearTimeout(t.__hideT);t.__hideT=setTimeout(()=>t.classList.remove('show'),3200);
}
function timeAgo(iso){
  if(!iso)return'never';
  const d=new Date(iso);if(isNaN(d))return'never';
  const s=Math.max(0,Math.round((Date.now()-d.getTime())/1000));
  if(s<45)return'just now';
  if(s<3600)return Math.round(s/60)+'m ago';
  if(s<86400)return Math.round(s/3600)+'h ago';
  return Math.round(s/86400)+'d ago';
}
function updateSyncLabel(){
  const el=document.getElementById('liSyncLabel'),dot=document.getElementById('liSyncDot');
  if(!el)return;
  const st=D.stats||{};
  if(!st.last_synced){el.textContent='Not synced yet';dot.classList.remove('bad');return;}
  const ok=D.synced_ok!==false;
  dot.classList.toggle('bad',!ok);
  el.textContent=(ok?'Synced ':'Last good sync ')+timeAgo(st.last_synced)+(st.source_accounts&&st.source_accounts.length>1?' · '+st.source_accounts.length+' seats':'');
  el.title='Sheet extracted at '+st.last_synced;
}
function setLoading(on){
  _liLoading=on;
  const btn=document.getElementById('liRefreshBtn'),ic=document.getElementById('liRefreshIc');
  if(btn)btn.disabled=on;
  if(ic)ic.classList.toggle('spin',on);
}
function renderAll(){
  buildOverviewTab();buildPostTab();buildPeopleTab();buildCompaniesTab();
  document.getElementById('tc-post').textContent=D.posts.length;
  document.getElementById('tc-companies').textContent=D.companies.filter(c=>c.name!=='(Unknown)').length;
  document.getElementById('tc-overview').textContent=(D.stats&&D.stats.total_engagements)||0;
  updateSyncLabel();
  setTimeout(countUp,50);
}
async function loadLinkedInData(force){
  if(_liLoading)return;
  setLoading(true);
  try{
    const url=window.__LI_DATA_URL__+(force?'?fresh=1':'');
    const res=await fetch(url,{headers:{'Accept':'application/json'}});
    if(!res.ok)throw new Error('HTTP '+res.status);
    const data=await res.json();
    D=data;
    if(force)liToast(D.synced_ok===false?'Could not reach the sheet — showing last good data':'Refreshed from Google Sheet','ok');
  }catch(e){
    console.error('LinkedIn data load failed',e);
    liToast('Refresh failed — check connection','err');
  }finally{
    renderAll();
    setLoading(false);
  }
}
function refreshLI(){loadLinkedInData(true);}

/* ═══════════════════════════════════
   OVERVIEW
═══════════════════════════════════ */
function buildOverviewTab(){
  const el=document.getElementById('tab-overview');if(!el)return;
  const st=D.stats||{};
  const kpis=[
    {v:st.total_engagements||0,l:'Total Engagements',c:'vi',kc:'#7c83f5',ic:'⚡',go:'goPosts({})',t:'See every post & engager'},
    {v:st.total_people||0,l:'Unique People',c:'vg',kc:'#2dd4aa',ic:'👥',go:'goPeople({})',t:'Browse all people'},
    {v:st.total_dms||0,l:'Decision Makers',c:'va',kc:'#f5a623',ic:'🎯',go:'goPeople({dm:true})',t:'People flagged as decision-makers'},
    {v:st.csuite_count||0,l:'C-Suite Reached',c:'vp',kc:'#b78bfa',ic:'👑',go:"goPeople({sen:'csuite'})",t:'C-level & founders'},
    {v:st.total_companies||0,l:'Companies Reached',c:'vc',kc:'#22d3ee',ic:'🏢',go:'goCompanies()',t:'Company intelligence'},
    {v:st.total_comments||0,l:'Comments',c:'vr',kc:'#fb7185',ic:'💬',go:'goPosts({})',t:'Posts & comment signal'},
  ];
  /* navFn(key) → a JS onclick string, or null for non-clickable rows */
  function barList(counts,total,colorFn,navFn){
    const entries=Object.entries(counts||{}).sort((a,b)=>b[1]-a[1]);
    if(!entries.length)return'<div class="nores" style="padding:24px"><p>No data yet</p></div>';
    const max=Math.max(...entries.map(e=>e[1]),1);
    return entries.map(([k,v],i)=>{
      const widthPct=Math.round(v/max*100);
      const shareLbl=total?(Math.round(v/total*100)+'%'):'';
      const barColor=colorFn?('background:'+colorFn(k)+';'):'';
      const nav=navFn?navFn(k):'';
      const clickable=nav?' lbitem-click" onclick="'+nav+'" title="Filter by '+esc(k)+'':'';
      return'<div class="lbitem lbitem-static'+clickable+'">'
        +'<div class="lbname" style="flex:0 0 130px">'+esc(k)+'</div>'
        +'<div class="lbtrack"><div class="lbfill" style="width:'+widthPct+'%;'+barColor+'"></div></div>'
        +'<div class="lbnum">'+v+(shareLbl?' <span class="lbnum-pct">&middot; '+shareLbl+'</span>':'')+'</div>'
        +'</div>';
    }).join('');
  }
  const relTotal=Object.values(st.relationship_breakdown||{}).reduce((a,b)=>a+b,0);
  el.innerHTML=`
  <div class="ov-kpis">
    ${kpis.map(k=>`<div class="ov-kpi ov-kpi-click" style="--kc:${k.kc}" onclick="${k.go}" title="${k.t}"><div class="ov-kpi-ic">${k.ic}</div><div class="ov-kpi-val ${k.c}" data-count="${k.v}">0</div><div class="ov-kpi-lbl">${k.l}</div><div class="ov-kpi-go">View →</div></div>`).join('')}
  </div>
  <div class="ov-grid">
    <div class="lbcard ov-card" style="--kc:#22d3ee">
      <div class="lbtitle"><span class="ov-chip">⚡</span> Reaction Mix</div>
      ${barList(st.reaction_breakdown,st.total_engagements,k=>({LIKE:'#60a5fa',PRAISE:'#fbbf24',INTEREST:'#a5b4fc',APPRECIATION:'#fca5a5',EMPATHY:'#f9a8d4'}[k]||'var(--accent)'),k=>"goPosts({react:'"+jsstr(k)+"'})")}
    </div>
    <div class="lbcard ov-card" style="--kc:#f5a623">
      <div class="lbtitle"><span class="ov-chip">🏅</span> Seniority Mix</div>
      ${barList(st.seniority_breakdown,st.total_people,k=>({'C-Level / Founder':'#fbbf24','VP':'#818cf8','Director':'#a5b4fc','Manager':'#2dd4aa','IC / Individual Contributor':'#94a3b8'}[k]||'var(--accent)'),k=>"goPeople({sen:'"+_senToBucket(k)+"'})")}
    </div>
    <div class="lbcard ov-card" style="--kc:#2dd4aa">
      <div class="lbtitle"><span class="ov-chip">🌍</span> Top Locations</div>
      ${barList(st.country_breakdown,st.total_people,()=>'linear-gradient(90deg,#2dd4aa,#22d3ee)',k=>"goPeople({country:'"+jsstr(k)+"'})")}
    </div>
    <div class="lbcard ov-card" style="--kc:#fb7185">
      <div class="lbtitle"><span class="ov-chip">🎯</span> Employee vs. External</div>
      ${barList(st.relationship_breakdown,relTotal,k=>k==='Employee'?'#f5a623':'linear-gradient(90deg,#fb7185,#b78bfa)',k=>"goPeople({rel:'"+jsstr(k)+"'})")}
      <div class="ov-note">External reactions are real prospect signal — employee amplification is tracked separately so it doesn't inflate reach. <span class="ov-note-hint">Click any row to drill in.</span></div>
    </div>
  </div>
  <div class="sec-hdr" style="margin-top:22px"><div class="sec-ttl"><span class="ov-chip" style="--kc:#7c83f5">🏆</span> Top Companies by Engagement</div></div>
  <div class="lbcard ov-card ov-card-lb" style="--kc:#7c83f5">
    ${(D.company_lb||[]).length?D.company_lb.map(([name,cnt],i)=>{
      const maxLb=D.company_lb[0][1]||1;
      return`<div class="lbitem" onclick="switchTab('companies');setTimeout(()=>openCompanyDrawerByName('${esc(name)}'),120)">
        <div class="lbrank ${i===0?'rank-g':i===1?'rank-s':i===2?'rank-b':''}">${i+1}</div>
        <div class="lbname">${esc(name)}</div>
        <div class="lbtrack"><div class="lbfill" style="width:${Math.round(cnt/maxLb*100)}%"></div></div>
        <div class="lbnum">${cnt}</div>
      </div>`;
    }).join(''):'<div class="nores" style="padding:24px"><p>No company data yet</p></div>'}
  </div>`;
}

/* ═══════════════════════════════════
   POST INTELLIGENCE
═══════════════════════════════════ */
function buildPostTab(){
  const {posts,stats}=D;
  const el=document.getElementById('tab-post');
  const _cos=[...new Set(D.posts.flatMap(p=>p.engagers.map(e=>e.company)).filter(Boolean))].sort();
  const _compOpts='<option value="">All Companies</option>'+_cos.map(co=>'<option value="'+esc(co)+'">'+esc(co)+'</option>').join('');
  el.innerHTML=`
  <div class="fbar">
    <div class="fgroup">
      <div class="chips" id="pf-sen">
        <button class="chip on" onclick="setPF('sen','all',this)">All</button>
        <button class="chip" onclick="setPF('sen','C-Level / Founder',this)">C-Level</button>
        <button class="chip" onclick="setPF('sen','VP',this)">VP</button>
        <button class="chip" onclick="setPF('sen','Director',this)">Director</button>
        <button class="chip" onclick="setPF('sen','Manager',this)">Manager</button>
      </div>
    </div>
    <div class="fgroup">
      <div class="chips" id="pf-react">
        <button class="chip on" onclick="setPF('react','all',this)">All reactions</button>
        <button class="chip" onclick="setPF('react','LIKE',this)">👍 Like</button>
        <button class="chip" onclick="setPF('react','PRAISE',this)">🙌 Praise</button>
        <button class="chip" onclick="setPF('react','EMPATHY',this)">🫂 Empathy</button>
        <button class="chip" onclick="setPF('react','APPRECIATION',this)">❤️ Appreciation</button>
        <button class="chip" onclick="setPF('react','INTEREST',this)">🤔 Interest</button>
      </div>
    </div>
    <div class="sep"></div>
    <select class="comp-sel" id="pf-comp" onchange="PF.comp=this.value;applyPF()">${_compOpts}</select>
    <div class="lkf-dwrap" title="Filter posts by date"><span class="lkf-dlbl">📅</span><input type="date" class="lkf-date" onchange="PF.dFrom=this.value;applyPF()"><span class="lkf-dsep">→</span><input type="date" class="lkf-date" onchange="PF.dTo=this.value;applyPF()"></div>
    <button class="chip" id="pf-sort" onclick="PF.sort=(PF.sort==='eng'?'none':'eng');this.classList.toggle('on',PF.sort==='eng');applyPF()">↕ Most engaged</button>
    <div class="srch">
      <span class="srch-ico">🔍</span>
      <input placeholder="Search people…" oninput="PF.q=this.value;applyPF()">
    </div>
  </div>

  <div class="sec-hdr">
    <div class="sec-ttl">Post Performance</div>
    <div class="sec-cnt" id="pst-cnt">${posts.length} posts</div>
  </div>
  <div class="pgrid" id="pgrid"></div>`;
  countUp();
  applyPF();
}

function postStatClick(mode,el){
  if(mode==='people'){switchTab('people');return;}
  document.querySelectorAll('#tab-post .scard').forEach(c=>c.classList.remove('active-filter'));
  if(activeStatFilter===mode){activeStatFilter=null;PF.sen='all';PF.sort='none';resetChips('pf-sen');return applyPF();}
  activeStatFilter=mode;el.classList.add('active-filter');
  if(mode==='csuite'){PF.sen='C-Level / Founder';PF.sort='none';setActiveChip('pf-sen','C-Level / Founder');}
  else if(mode==='eng'){PF.sen='all';PF.sort='eng';resetChips('pf-sen');}
  else{PF.sen='all';PF.sort='none';resetChips('pf-sen');}
  applyPF();
}
function resetChips(id){const g=document.getElementById(id);if(!g)return;g.querySelectorAll('.chip').forEach((c,i)=>{c.classList.toggle('on',i===0)});}
function setActiveChip(id,val){const g=document.getElementById(id);if(!g)return;g.querySelectorAll('.chip').forEach(c=>{c.classList.toggle('on',c.textContent.includes(val.replace('IC / Individual Contributor','IC').replace('C-Level / Founder','C-Level')))});}

function setPF(key,val,el){
  PF[key]=val;
  const g=el.closest('.chips');g.querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));el.classList.add('on');
  applyPF();
}
function togglePFDM(){PF.dm=!PF.dm;document.getElementById('pf-dm').classList.toggle('on',PF.dm);applyPF();}

function applyPF(){
  const g=document.getElementById('pgrid');if(!g)return;
  const {sen,deg,react,q,comp}=PF;const ql=q.toLowerCase();
  const filtered=D.posts.filter(p=>{if(PF.dFrom&&p.date<PF.dFrom)return false;if(PF.dTo&&p.date>PF.dTo)return false;return true;}).map(p=>{
    let eng=p.engagers.filter(e=>{
      if(sen!=='all'&&e.seniority!==sen)return false;
      if(deg!=='all'&&e.degree!==deg)return false;
      if(react!=='all'&&e.reaction!==react)return false;
      if(comp&&e.company!==comp)return false;
      if(ql){const match=e.name.toLowerCase().includes(ql)||e.company.toLowerCase().includes(ql)||e.title.toLowerCase().includes(ql);if(!match&&!p.snippet.toLowerCase().includes(ql))return false;}
      return true;
    });
    if(ql&&p.snippet.toLowerCase().includes(ql)&&!eng.length)eng=p.engagers;
    return{...p,fe:eng};
  }).filter(p=>p.fe.length>0);
  document.getElementById('pst-cnt').textContent=filtered.length+' posts';
  document.getElementById('tc-post').textContent=filtered.length;
  if(!filtered.length){g.innerHTML='<div class="nores" style="grid-column:1/-1"><div class="nores-ico">🔍</div><h3>No results</h3><p>Try adjusting filters</p></div>';return;}
  if(PF.sort==='eng')filtered.sort((a,b)=>b.fe.length-a.fe.length);
  _filteredPosts=filtered;
  g.innerHTML=filtered.map((p,pi)=>{
    const eng=p.fe,dmN=eng.filter(e=>e.dm?.toLowerCase()==='yes').length;
    const csN=eng.filter(e=>e.seniority?.includes('C-Level')||e.seniority?.includes('Founder')).length;
    const cmN=eng.filter(e=>e.commented).length;
    const reacts=[...new Set(eng.map(e=>e.reaction).filter(Boolean))].slice(0,2);
    const ori=D.posts.indexOf(p);
    return`<div class="pcard" id="pc-${pi}" onclick="openPostModal(${pi})" style="animation-delay:${pi*.05}s">
      <div class="ptop">
        <div class="pdot"></div>
        <div class="pauth">${esc(p.author||EMP_FULL)}</div>
        <div class="pdate">${esc(p.date)}</div>
      </div>
      <div class="psnip">${esc(p.snippet)}</div>
      <div class="pmets">
        <div class="mpill mp-g">${eng.length} Engager${eng.length!==1?'s':''}</div>
        ${cmN?`<div class="mpill mp-c">💬 ${cmN} Comment${cmN!==1?'s':''}</div>`:''}
        ${dmN?`<div class="mpill mp-a">${dmN} DM${dmN!==1?'s':''}</div>`:''}
        ${csN?`<div class="mpill mp-p">${csN} C-Suite</div>`:''}
        ${p.url?`<a href="${esc(p.url)}" target="_blank" class="post-view-btn" onclick="event.stopPropagation()">View Post ↗</a>`:''}
      </div>
    </div>`;
  }).join('');
}
function filterToDM(postIdx){PF.dm=true;document.getElementById('pf-dm').classList.add('on');applyPF();}
function togglePost(pi){
  const el=document.getElementById('el-'+pi),btn=document.getElementById('eb-'+pi);
  const op=el.classList.contains('open');
  el.classList.toggle('open',!op);btn.textContent=op?'Show ▾':'Hide ▴';
  document.getElementById('pc-'+pi).classList.toggle('expanded',!op);
}

/* ═══════════════════════════════════
   PEOPLE & COMPANIES
═══════════════════════════════════ */
const BKT=[
  {k:'csuite',l:'C-Suite / Founders',i:'👑',c:'#fbbf24'},
  {k:'vpdirector',l:'VP / Director Level',i:'📈',c:'#818cf8'},
  {k:'managers',l:'Managers',i:'🏗️',c:'#00e5a0'},
  {k:'ics',l:'Individual Contributors',i:'💼',c:'#94a3b8'},
  {k:'unknown',l:'Unknown Seniority',i:'❓',c:'#64748b'},
];
let maxP=1;

function buildPeopleTab(){
  const {people,companies,company_lb,stats}=D;
  maxP=Math.max(...people.map(p=>p.posts_engaged),1);
  const maxLb=company_lb[0]?company_lb[0][1]:1;
  const el=document.getElementById('tab-people');
  var _KNOWN_C=['India','United States','United Kingdom','Canada','Australia','Germany','France','Singapore','Netherlands','Ireland','Spain','Italy','Brazil','Mexico','Japan','China','United Arab Emirates','Philippines','Israel','Sweden'];
  D.people.forEach(function(p){ if(p._aug)return; p._aug=1;
    var parts=(p.location||'').split(',').map(function(s){return s.trim();}).filter(Boolean);
    p._city=parts.length?parts[0]:'';
    var last=parts.length>1?parts[parts.length-1]:'';
    p._country=p.country||(_KNOWN_C.indexOf(last)>=0?last:'');
  });
  if(!window.PDATES){window.PDATES={};(D.posts||[]).forEach(function(po){(po.engagers||[]).forEach(function(e){var k=(e.name||'').toLowerCase();(window.PDATES[k]=window.PDATES[k]||[]).push(po.date);});});}
  var _countries=[...new Set(D.people.map(function(p){return p._country;}).filter(Boolean))].sort();
  var _cities=[...new Set(D.people.map(function(p){return p._city;}).filter(Boolean))].sort();
  var _ctryOpts='<option value="all">🌍 All countries</option>'+_countries.map(function(x){return '<option value="'+esc(x)+'">'+esc(x)+'</option>';}).join('');
  var _cityOpts='<option value="all">📍 All locations</option>'+_cities.map(function(x){return '<option value="'+esc(x)+'">'+esc(x)+'</option>';}).join('');
  el.innerHTML=`

  <div class="fbar-wrap">
    <div class="fbar fbar-primary">
      <div class="fgroup">
        <span class="flabel">Seniority</span>
        <div class="chips">
          <button class="chip on" onclick="setPLF('sen','all',this)">All</button>
          <button class="chip" onclick="setPLF('sen','csuite',this)">👑 C-Suite</button>
          <button class="chip" onclick="setPLF('sen','vpdirector',this)">📈 VP/Dir</button>
          <button class="chip" onclick="setPLF('sen','managers',this)">🏗 Manager</button>
          <button class="chip" onclick="setPLF('sen','ics',this)">💼 IC</button>
        </div>
      </div>
      <div style="flex:1"></div>
      <div class="srch"><span class="srch-ico">🔍</span><input placeholder="Search name, title, company…" oninput="PLF.q=this.value;applyPLF()"></div>
      <span class="lkf-count" id="plf-count">—</span>
    </div>
    <div class="fbar fbar-secondary">
      <select class="lkf-sel" onchange="PLF.country=this.value;applyPLF()">${_ctryOpts}</select>
      <select class="lkf-sel" onchange="PLF.city=this.value;applyPLF()">${_cityOpts}</select>
      <select class="lkf-sel" onchange="PLF.deg=this.value;applyPLF()">
        <option value="all">All degrees</option><option value="SECOND_DEGREE">2nd degree</option><option value="THIRD_DEGREE">3rd degree</option>
      </select>
      <select class="lkf-sel" onchange="PLF.minP=+this.value;applyPLF()">
        <option value="0">Any activity</option><option value="1">1+ posts</option><option value="2">2+ posts</option><option value="3">3+ posts</option>
      </select>
      <div class="lkf-dwrap" title="Filter by engagement date">
        <span class="lkf-dlbl">📅</span>
        <input type="date" class="lkf-date" onchange="PLF.dFrom=this.value;applyPLF()">
        <span class="lkf-dsep">→</span>
        <input type="date" class="lkf-date" onchange="PLF.dTo=this.value;applyPLF()">
      </div>
      <button class="chip" id="plf-dm" onclick="PLF.dm=!PLF.dm;this.classList.toggle('on',PLF.dm);applyPLF()">🎯 Decision Makers</button>
      <button class="chip p2-exclude-btn" id="plf-p2" onclick="PLF.excludeP2=!PLF.excludeP2;this.classList.toggle('on',PLF.excludeP2);applyPLF()">🚫 Hide ${esc(EMP_SHORT)}</button>
      <button class="lkf-reset" onclick="resetPLF()">↺ Reset</button>
    </div>
  </div>
  <div class="peoplayout">
    <div id="people-main"></div>
    <div>
      <div class="lbcard">
        <div class="lbtitle">🏆 Company Leaderboard</div>
        ${company_lb.map(([name,cnt],i)=>`
        <div class="lbitem" onclick="openCompanyDrawerByName('${esc(name)}')">
          <div class="lbrank ${i===0?'rank-g':i===1?'rank-s':i===2?'rank-b':''}">${i+1}</div>
          <div class="lbname">${esc(name)}</div>
          <div class="lbtrack"><div class="lbfill" style="width:${Math.round(cnt/maxLb*100)}%"></div></div>
          <div class="lbnum">${cnt}</div>
        </div>`).join('')}
      </div>
    </div>
  </div>`;

  countUp();
  applyPLF();

  // Mouse glow
  document.addEventListener('mousemove',e=>{
    const c=e.target.closest('.pcrd');
    if(c){const r=c.getBoundingClientRect();c.style.setProperty('--mx',((e.clientX-r.left)/r.width*100)+'%');c.style.setProperty('--my',((e.clientY-r.top)/r.height*100)+'%');}
  });
}

function buildCompaniesTab(){
  const {companies,stats}=D;
  const el=document.getElementById('tab-companies');
  el.innerHTML=`

  <div class="fbar">
    <div class="fgroup">
      <span class="flabel">Min People</span>
      <div class="chips">
        <button class="chip on" onclick="setCF('minPpl',0,this)">All</button>
        <button class="chip" onclick="setCF('minPpl',2,this)">2+</button>
        <button class="chip" onclick="setCF('minPpl',3,this)">3+</button>
        <button class="chip" onclick="setCF('minPpl',5,this)">5+</button>
      </div>
    </div>
    <div class="sep"></div>
    <div class="srch"><span class="srch-ico">🔍</span><input placeholder="Search companies…" oninput="CF.q=this.value;applyCF()"></div>
  </div>
  <div class="sec-hdr"><div class="sec-ttl">Company Intelligence</div><div class="sec-cnt" id="co-cnt">${companies.length} companies</div></div>
  <div class="cgrid" id="cgrid"></div>`;
  countUp();
  applyCF();
}

function switchSub(t){
  ['people','companies'].forEach(k=>{
    document.getElementById('sp-'+k).classList.toggle('active',k===t);
    document.getElementById('st-'+k).classList.toggle('active',k===t);
  });
}

let _pSF=null;
function switchSection(sec, el){
  var allCards = document.querySelectorAll('#tab-people .stat-row .scard');
  allCards.forEach(c=>c.classList.remove('active-filter'));
  el.classList.add('active-filter');
  var spPeople  = document.getElementById('sp-people');
  var spCompanies = document.getElementById('sp-companies');
  var lrPeople  = document.getElementById('lr-people');
  var lrCompanies = document.getElementById('lr-companies');
  if(sec==='people'){
    spPeople.style.display=''; lrPeople.style.display='';
    spCompanies.style.display='none'; lrCompanies.style.display='none';
    peoplStatClick('all', el);
  } else {
    spPeople.style.display='none'; lrPeople.style.display='none';
    spCompanies.style.display=''; lrCompanies.style.display='';
  }
}
function peoplStatClick(mode,el){
  document.querySelectorAll('#tab-people .scard').forEach(c=>c.classList.remove('active-filter'));
  if(_pSF===mode){_pSF=null;PLF.sen='all';return applyPLF();}
  _pSF=mode;el.classList.add('active-filter');
  if(mode==='csuite'){PLF.sen='csuite';}
  else{PLF.sen='all';}
  applyPLF();
}
function setPLF(k,v,el){PLF[k]=v;el.closest('.chips').querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));el.classList.add('on');applyPLF();}
function resetPLF(){PLF=_blankPLF();buildPeopleTab();}
function plStat(mode,el){document.querySelectorAll('#tab-people .scard').forEach(c=>c.classList.remove('active-filter'));if(el)el.classList.add('active-filter');if(mode==='dm'){PLF.dm=true;PLF.sen='all';}else if(mode==='csuite'){PLF.dm=false;PLF.sen='csuite';}else{PLF.dm=false;PLF.sen='all';}applyPLF();}
function togglePLFDM(){PLF.dm=!PLF.dm;document.getElementById('plf-dm').classList.toggle('on',PLF.dm);applyPLF();}

function applyPLF(){
  const main=document.getElementById('people-main');if(!main)return;
  const {sen,deg,minP,q,country,city,dFrom,dTo}=PLF;const ql=q.toLowerCase();
  const fil=D.people.filter(p=>{
    if(sen!=='all'&&p.bucket!==sen)return false;
    if(deg!=='all'&&p.degree!==deg)return false;
    if(minP>0&&p.posts_engaged<minP)return false;
    if(PLF.dm&&(p.dm||'').toLowerCase()!=='yes')return false;
    if(country&&country!=='all'&&p._country!==country)return false;
    if(city&&city!=='all'&&p._city!==city)return false;
    if(dFrom||dTo){var ds=(window.PDATES&&window.PDATES[(p.name||'').toLowerCase()])||[];if(!ds.some(function(d){return (!dFrom||d>=dFrom)&&(!dTo||d<=dTo);}))return false;}
    if(PLF.excludeP2&&isEmployee(p))return false;
    if(PLF.rel==='Employee'&&!isEmployee(p))return false;
    if(PLF.rel==='External'&&isEmployee(p))return false;
    if(ql&&!(p.name.toLowerCase().includes(ql)||p.company.toLowerCase().includes(ql)||(p.title||'').toLowerCase().includes(ql)))return false;
    return true;
  });
  var _tc=document.getElementById('tc-people');if(_tc)_tc.textContent=fil.length;
  var _pc=document.getElementById('plf-count');if(_pc)_pc.textContent=fil.length+' '+(fil.length===1?'person':'people');
  if(!fil.length){main.innerHTML='<div class="nores"><div class="nores-ico">🔍</div><h3>No results</h3><p>Adjust filters</p></div>';return;}
  const bkts={};BKT.forEach(b=>{bkts[b.k]=fil.filter(p=>p.bucket===b.k);});
  main.innerHTML=BKT.map(b=>{
    const grp=bkts[b.k];if(!grp||!grp.length)return'';
    return`<div class="bsect">
      <button class="bhdr" onclick="toggleBucket('${b.k}')">
        <div class="bico" style="background:${b.c}22">${b.i}</div>
        <div class="binfo"><div class="bname">${b.l}</div><div class="bsub">${grp.length} people</div></div>
        <div class="bchev up" id="bc-${b.k}">▾</div>
      </button>
      <div class="bbody" id="bb-${b.k}" style="max-height:4000px">
        <div class="pgrd">
          ${grp.map((p,pi)=>{
            const idx=D.people.indexOf(p);
                      const pct=Math.round(p.posts_engaged/maxP*100);
            return`<div class="pcrd" onclick="openPersonDrawer(${idx})" style="animation-delay:${pi*.04}s">
              <div class="pctop">${av(p.name,p.pic,44,'pcav')}<div><div class="pcn">${esc(p.name)}</div><div class="pct">${esc(p.title||p.headline)}</div></div></div>
              <div class="pcbdg">
                ${p.seniority?`<span class="b ${SCLS(p.seniority)}">${esc(SLBL(p.seniority))}</span>`:''}
                ${p.dm==='Yes'?`<span class="b bdm">🎯 DM</span>`:''}
                ${isEmployee(p)?`<span class="b brel-emp">🏢 ${esc(EMP_SHORT)} Employee</span>`:''}
                ${p.degree?`<span class="b bdeg">${DLBL(p.degree)}</span>`:''}
                ${p.country?`<span class="b bic">🌏 ${esc(p.country)}</span>`:''}
                ${p.industry?`<span class="b bic" style="background:rgba(79,70,229,0.18);border-color:rgba(79,70,229,0.35)">${esc(p.industry)}</span>`:''}
              </div>
              <div class="pcmeta">
                ${p.company?`<div class="pcmi">🏢 <span>${esc(p.company)}</span></div>`:''}
                ${p.location?`<div class="pcmi">📍 <span>${esc(p.location)}</span></div>`:''}
                ${p.size?`<div class="pcmi">👥 <span>${esc(p.size)}</span></div>`:''}
                ${p.followers?`<div class="pcmi">📶 <span>${fmtN(p.followers)} followers</span></div>`:''}
                ${p.url?`<div class="pcmi"><a href="${esc(p.url)}" target="_blank" onclick="event.stopPropagation()" style="color:var(--accent);text-decoration:none;font-size:10px;font-weight:600">↗ LinkedIn</a></div>`:''}
              </div>
              ${p.posts_engaged>0?`<div class="actbar"><div class="acttrack"><div class="actfill" style="width:${pct}%"></div></div><div class="actlbl">${p.posts_engaged} post${p.posts_engaged!==1?'s':''}</div></div>`:''}
            </div>`;
          }).join('')}
        </div>
      </div>
    </div>`;
  }).join('');
}

function toggleBucket(k){
  const b=document.getElementById('bb-'+k),c=document.getElementById('bc-'+k);
  const isOpen=!b.classList.contains('closed');
  b.style.maxHeight=isOpen?'0':b.scrollHeight+'px';
  b.classList.toggle('closed',isOpen);
  c.classList.toggle('up',!isOpen);
  if(!isOpen)setTimeout(()=>b.style.maxHeight='4000px',420);
}

/* ── COMPANY FILTERS ── */
function setCF(k,v,el){CF[k]=v;el.closest('.chips').querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));el.classList.add('on');applyCF();}
function toggleCFDM(){CF.dm=!CF.dm;document.getElementById('cf-dm').classList.toggle('on',CF.dm);applyCF();}

function applyCF(){
  const g=document.getElementById('cgrid');if(!g)return;
  const {q,dm,minPpl}=CF;const ql=q.toLowerCase();
  const SEN_C=['C-Level / Founder','VP','Director','Manager','IC / Individual Contributor'];
  const SEN_COLS=['#fbbf24','#818cf8','#818cf8','#00e5a0','#94a3b8'];
  const fil=D.companies.filter(c=>{
    if(c.name==='(Unknown)')return false;
    if(minPpl>0&&c.people_count<minPpl)return false;
    if(dm&&c.dm_count===0)return false;
    if(ql&&!c.name.toLowerCase().includes(ql))return false;
    return true;
  });
  document.getElementById('co-cnt').textContent=fil.length+' companies';
  var tcco=document.getElementById('tc-companies');if(tcco)tcco.textContent=fil.length;
  if(!fil.length){g.innerHTML='<div class="nores" style="grid-column:1/-1"><div class="nores-ico">🔍</div><h3>No results</h3></div>';return;}
  const maxCnt=Math.max(...fil.map(c=>c.people_count),1);
  g.innerHTML=fil.map((c,ci)=>{
    const idx=D.companies.indexOf(c);
    const icos=ini(c.name);
    const [cc1,cc2]=gradFor(c.name);
    // seniority bar
    const total=c.people_count||1;
    const segs=SEN_C.map((s,i)=>{
      const cnt=c.seniority_map[s]||0;
      if(!cnt)return'';
      return`<div class="cc-bar-seg" style="width:${Math.round(cnt/total*100)}%;background:${SEN_COLS[i]}"></div>`;
    }).join('');
    // mini avatars
    const ppl=c.people.slice(0,5);
    const avs=ppl.map(p=>{const[c1,c2]=gradFor(p.name);return`<div class="mini-av" style="background:linear-gradient(135deg,${c1},${c2})" title="${esc(p.name)}">${ini(p.name)}</div>`;}).join('');
    return`<div class="ccrd" onclick="openCompanyDrawer(${idx})" style="animation-delay:${ci*.04}s">
      <div class="cctop">
        <div class="ccico" style="background:linear-gradient(135deg,${cc1},${cc2})">${icos}</div>
        <div><div class="ccn">${esc(c.name)}</div><div class="ccsub">${esc(c.industry||'-')}${c.size?' · '+esc(c.size):''}</div></div>
      </div>
      <div class="cc-stats">
        <div class="cc-stat"><div class="cc-stat-val vg" data-count="${c.people_count}">0</div><div class="cc-stat-lbl">People</div></div>
        <div class="cc-stat"><div class="cc-stat-val va" data-count="${c.dm_count}">0</div><div class="cc-stat-lbl">Decision Makers</div></div>
      </div>
      ${(c.hq||c.country)?`<div class="pcmeta" style="margin-top:6px;gap:6px">
        ${c.hq?`<div class="pcmi">🏙 <span>${esc(c.hq)}</span></div>`:''}
      </div>`:``}
      <div class="cc-bar">${segs}</div>
    </div>`;
  }).join('');
  setTimeout(countUp,50);
}

/* ═══════════════════════════════════
   DRAWERS
═══════════════════════════════════ */
function openDrawer(){document.getElementById('overlay').classList.add('open');document.getElementById('drawer').classList.add('open');}
function closeDrawer(){document.getElementById('overlay').classList.remove('open');document.getElementById('drawer').classList.remove('open');}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});

function openEngDrawer(pi,ei){
  const p=D.posts[pi],e=p?.engagers[ei];if(!e)return;
  const isDM=e.dm?.toLowerCase()==='yes';
  document.getElementById('dhdr').innerHTML=`
    ${av(e.name,e.pic,58,'dav')}
    <div style="flex:1"><div class="dname">${esc(e.name)}</div><div class="dsub">${esc(e.title||e.headline)}</div>
    ${e.url?`<a href="${esc(e.url)}" target="_blank" class="lilink">🔗 LinkedIn Profile</a>`:''}</div>
    <button class="dclose" onclick="closeDrawer()">✕</button>`;
  const badges=[
    e.seniority?`<span class="b ${SCLS(e.seniority)}" style="font-size:12px;padding:4px 11px">${esc(SLBL(e.seniority))}</span>`:'',
    isEmployee(e)?`<span class="b brel-emp" style="font-size:12px;padding:4px 11px">🏢 ${esc(EMP_FULL)} Employee</span>`:'',
    e.degree?`<span class="b bdeg" style="font-size:12px;padding:4px 11px">${DLBL(e.degree)} degree</span>`:'',
    e.reaction?`<span class="b ${RCLS[e.reaction]||'bic'}" style="font-size:12px;padding:4px 11px">${RICO[e.reaction]||''} ${e.reaction.charAt(0)+e.reaction.slice(1).toLowerCase()}</span>`:'',
    e.commented?`<span class="b blike" style="font-size:12px;padding:4px 11px">💬 Commented</span>`:'',
  ].filter(Boolean);
  document.getElementById('dbody').innerHTML=`
    <div class="dsec"><div class="tagrow">${badges.join('')}</div></div>
    <div class="dsec"><div class="dslbl">Company & Location</div>
      <div class="igrid">
        <div class="ibox"><div class="ilbl">Company</div><div class="ival">${esc(e.company||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Industry</div><div class="ival">${esc(e.industry||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Location</div><div class="ival">${esc(e.location||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Reaction</div><div class="ival">${RICO[e.reaction]||''} ${esc(e.reaction||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Followers</div><div class="ival">${fmtN(e.followers||0)}</div></div>
        <div class="ibox"><div class="ilbl">Connections</div><div class="ival">${fmtN(e.connections||0)}</div></div>
      </div>
    </div>
    <div class="dsec"><div class="dslbl">Post Engaged With</div>
      <div class="snippetbox">${esc(p.snippet)}</div>
      <div style="margin-top:8px;font-size:12px;color:var(--muted)">📅 ${esc(p.date)} · ${esc(p.author)}</div>
      ${p.url?`<a href="${esc(p.url)}" target="_blank" class="lilink" style="margin-top:8px">↗ View Post</a>`:''}
    </div>
    ${e.headline&&e.headline!==e.title?`<div class="dsec"><div class="dslbl">Headline</div><div class="snippetbox">${esc(e.headline)}</div></div>`:''}`;
  openDrawer();
}

function openPersonDrawer(idx){
  const p=D.people[idx];if(!p)return;
  const pct=Math.round(p.posts_engaged/maxP*100);
  document.getElementById('dhdr').innerHTML=`
    ${av(p.name,p.pic,58,'dav')}
    <div style="flex:1"><div class="dname">${esc(p.name)}</div><div class="dsub">${esc(p.title||p.headline)}</div>
    ${p.url?`<a href="${esc(p.url)}" target="_blank" class="lilink">🔗 LinkedIn Profile</a>`:''}</div>
    <button class="dclose" onclick="closeDrawer()">✕</button>`;
  const badges=[
    p.seniority?`<span class="b ${SCLS(p.seniority)}" style="font-size:12px;padding:4px 11px">${esc(SLBL(p.seniority))}</span>`:'',
    p.dm==='Yes'?`<span class="b bdm" style="font-size:12px;padding:4px 11px">🎯 Decision Maker</span>`:'',
    isEmployee(p)?`<span class="b brel-emp" style="font-size:12px;padding:4px 11px">🏢 ${esc(EMP_FULL)} Employee</span>`:'',
    p.degree?`<span class="b bdeg" style="font-size:12px;padding:4px 11px">${DLBL(p.degree)} degree</span>`:'',
    p.posts_engaged>0?`<span class="b bmg" style="font-size:12px;padding:4px 11px">⚡ ${p.posts_engaged} post${p.posts_engaged!==1?'s':''}</span>`:'',
  ].filter(Boolean);
  document.getElementById('dbody').innerHTML=`
    <div class="dsec"><div class="tagrow">${badges.join('')}</div></div>
    ${(p.followers||p.connections)?`<div class="dsec"><div class="dslbl">LinkedIn Reach</div>
      <div class="igrid">
        <div class="ibox"><div class="ilbl">Followers</div><div class="ival">${fmtN(p.followers||0)}</div></div>
        <div class="ibox"><div class="ilbl">Connections</div><div class="ival">${fmtN(p.connections||0)}</div></div>
      </div>
    </div>`:''}
    ${p.posts_engaged>0?`<div class="dsec"><div class="dslbl">Engagement Activity</div>
      <div style="display:flex;align-items:center;gap:12px;padding:14px;background:rgba(255,255,255,.03);border:1px solid var(--card-b);border-radius:10px">
        <div style="flex:1"><div style="font-size:11px;color:var(--muted);margin-bottom:6px">Relative activity vs. most active person</div>
          <div class="acttrack" style="height:5px"><div class="actfill" style="width:${pct}%"></div></div>
        </div>
        <div style="font-size:26px;font-weight:700;color:var(--green)">${p.posts_engaged}</div>
      </div>
      ${p.comments_count?`<div style="margin-top:8px;font-size:11px;color:var(--muted)">💬 Commented ${p.comments_count} time${p.comments_count!==1?'s':''}</div>`:''}
    </div>`:''}
    <div class="dsec"><div class="dslbl">Profile Details</div>
      <div class="igrid">
        <div class="ibox"><div class="ilbl">Company</div><div class="ival">${esc(p.company||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Industry</div><div class="ival">${esc(p.industry||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Company Size</div><div class="ival">${esc(p.size||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Location</div><div class="ival">${esc(p.location||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Country</div><div class="ival">${esc(p.country||'-')}</div></div>
        <div class="ibox"><div class="ilbl">Connection</div><div class="ival">${DLBL(p.degree)||'-'}</div></div>
      </div>
    </div>
    ${p.headline&&p.headline!==p.title?`<div class="dsec"><div class="dslbl">Headline</div><div class="snippetbox">${esc(p.headline)}</div></div>`:''}`;
  openDrawer();
}

function openCompanyDrawer(idx){
  const c=D.companies[idx];if(!c)return;
  const SEN_C=['C-Level / Founder','VP','Director','Manager','IC / Individual Contributor'];
  const SEN_COLS=['#fbbf24','#818cf8','#818cf8','#00e5a0','#94a3b8'];
  const [cc1,cc2]=gradFor(c.name);
  const coLinks=[
    c.website?`<a href="${esc(/^https?:/.test(c.website)?c.website:'https://'+c.website)}" target="_blank" class="lilink">🌐 Website</a>`:'',
    c.li_url?`<a href="${esc(c.li_url)}" target="_blank" class="lilink">🔗 LinkedIn Page</a>`:'',
  ].filter(Boolean).join('');
  document.getElementById('dhdr').innerHTML=`
    <div class="dav" style="border-radius:14px;background:linear-gradient(135deg,${cc1},${cc2})">${ini(c.name)}</div>
    <div style="flex:1"><div class="dname">${esc(c.name)}</div><div class="dsub">${esc(c.industry||'-')} ${c.size?'· '+esc(c.size)+' employees':''}</div>
    ${c.hq?`<div class="dsub" style="margin-top:2px">🏙 ${esc(c.hq)}</div>`:''}
    ${coLinks?`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">${coLinks}</div>`:''}</div>
    <button class="dclose" onclick="closeDrawer()">✕</button>`;
  const total=c.people_count||1;
  const senBars=SEN_C.map((s,i)=>{const cnt=c.seniority_map[s]||0;if(!cnt)return'';return`<div class="cc-bar-seg" style="width:${Math.round(cnt/total*100)}%;background:${SEN_COLS[i]};height:8px"></div>`;}).join('');
  const senBreakdown=SEN_C.map((s,i)=>{const cnt=c.seniority_map[s]||0;if(!cnt)return'';return`<div class="ibox"><div class="ilbl">${s}</div><div class="ival" style="color:${SEN_COLS[i]}">${cnt}</div></div>`;}).join('');
  const pplHtml=c.people.map((p,pi)=>{
    const pidx=D.people.indexOf(p);
    return`<div class="dpitem" onclick="closeDrawer();setTimeout(()=>openPersonDrawer(${pidx}),200)">
      ${av(p.name,'',32,'pcav')}
      <div style="flex:1"><div style="font-size:13px;font-weight:500">${esc(p.name)}</div><div style="font-size:11px;color:var(--muted)">${esc(p.title||p.headline)}</div></div>
      <div style="display:flex;gap:4px">
        ${p.dm?.toLowerCase()==='yes'?'<span class="b bdm">🎯</span>':''}
        ${p.seniority?`<span class="b ${SCLS(p.seniority)}">${esc(SLBL(p.seniority))}</span>`:''}
      </div>
    </div>`;
  }).join('');
  document.getElementById('dbody').innerHTML=`
    <div class="dsec">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
        <div class="cc-stat" style="background:rgba(0,229,160,.06);border:1px solid rgba(0,229,160,.15);border-radius:12px;padding:16px">
          <div class="cc-stat-val vg">${c.people_count}</div><div class="cc-stat-lbl">People</div>
        </div>
        <div class="cc-stat" style="background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.15);border-radius:12px;padding:16px">
          <div class="cc-stat-val va">${c.dm_count}</div><div class="cc-stat-lbl">Decision Makers</div>
        </div>
      </div>
      <div class="dslbl">Seniority Distribution</div>
      <div class="cc-bar" style="height:8px;border-radius:4px;margin-bottom:12px">${senBars}</div>
      <div class="igrid">${senBreakdown}</div>
    </div>
    ${c.posts_engaged?`<div class="dsec"><div class="dslbl">Post Engagement</div>
      <div class="ibox"><div class="ilbl">Posts Engaged With</div><div class="ival" style="color:var(--green);font-size:20px">${c.posts_engaged}</div></div>
    </div>`:''}
    <div class="dsec"><div class="dslbl">People from this Company (${c.people_count})</div>
      <div class="dplist">${pplHtml}</div>
    </div>`;
  openDrawer();
}
function openCompanyDrawerByName(name){
  const idx=D.companies.findIndex(c=>c.name===name);
  if(idx>=0)openCompanyDrawer(idx);
}


/* ── POST MODAL ── */
function openPostModal(pi){
  const p=_filteredPosts[pi];if(!p)return;
  const eng=p.fe&&p.fe.length?p.fe:p.engagers;
  const csN=eng.filter(e=>e.seniority?.includes('C-Level')||e.seniority?.includes('Founder')).length;
  const ori=D.posts.indexOf(p);
  document.getElementById('modal-ptop').innerHTML=
    '<div class="pdot"></div>'+
    '<div class="pauth">'+esc(p.author||EMP_FULL)+'</div>'+
    '<div class="pdate">'+esc(p.date)+'</div>'+
    (p.url?'<a href="'+esc(p.url)+'" target="_blank" class="post-view-btn" onclick="event.stopPropagation()">View Post ↗</a>':'');
  document.getElementById('modal-snip').textContent=p.snippet||'';
  document.getElementById('modal-metrics').innerHTML=
    '<div class="mpill mp-g">'+eng.length+' Engager'+(eng.length!==1?'s':'')+'</div>'+
    (csN?'<div class="mpill mp-p">'+csN+' C-Suite</div>':'');
  document.getElementById('modal-elist').innerHTML=eng.map((e,ei)=>{
    const oi=p.engagers.indexOf(e);
    const _t=(e.title&&!/^[\d.,\s]+$/.test(e.title))?e.title:'';
    const subLine=[_t,e.company].filter(Boolean).join(' · ')||e.headline||'';
    return'<div class="eitem" onclick="openEngDrawer('+ori+','+oi+')" style="animation-delay:'+(ei*.025)+'s">'+
      av(e.name,e.pic,36,'eavtr')+
      '<div><div class="ename">'+esc(e.name)+'</div>'+
      '<div class="esub">'+esc(subLine)+'</div>'+
      (e.location?'<div class="esub" style="margin-top:1px;font-size:10px;opacity:0.65">📍 '+esc(e.location)+'</div>':'')+
      (e.headline&&e.headline!==subLine&&e.headline!==_t?'<div class="esub" style="margin-top:1px;font-size:10px;opacity:0.55;font-style:italic">'+esc(e.headline)+'</div>':'')+
      '</div>'+
      '<div class="ebadges">'+
        (e.commented?'<span class="b blike" title="Commented">💬</span>':'')+
        (e.seniority?'<span class="b '+SCLS(e.seniority)+'">'+esc(SLBL(e.seniority))+'</span>':'')+
        (e.reaction?'<span class="b '+(RCLS[e.reaction]||'bic')+'">'+(RICO[e.reaction]||'')+' '+e.reaction.charAt(0)+e.reaction.slice(1).toLowerCase()+'</span>':'')+
        (e.degree?'<span class="b bdeg">'+DLBL(e.degree)+'</span>':'')+
      '</div></div>';
  }).join('');
  document.getElementById('post-modal-bg').classList.add('open');
  document.body.style.overflow='hidden';
}
function closePostModal(){
  document.getElementById('post-modal-bg').classList.remove('open');
  document.body.style.overflow='';
}
/* ── INIT ── */
switchTab('overview');
loadLinkedInData(false);

