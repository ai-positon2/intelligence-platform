/* Paid Marketing: the platform switch (Google, Meta or Both) and the account pickers that go with it.
 *
 *   var c = PMPlatform.accounts(hostEl, {
 *     url: "/p2/paid-marketing/api/gads/accounts",   // the account list; asked with ?platform=both
 *     allow: ["google","meta","both"],               // which choices to offer
 *     platform: "google", account: "<id from the URL>",
 *     onChange: function(acct, platform){...},       // an account is (re)chosen; acct is null when none can be
 *     onLoaded: function(accounts, info){...},       // the account list arrived: info = {googleProblem, metaProblem, usage, ...}
 *     onError: function(err){...}
 *   });
 *
 * One account is {id, name, currency, tz, platform: "google" | "meta"}. In "Both" the reader picks a Google account and
 * a Meta account; with both picked the chosen account is a combined one: {id: "<google id>+<act_id>", platform: "both",
 * parts: [google, meta]} and the page asks for each part and merges the answers (mergeAccountResponses and friends below).
 * Everything that does not touch the page is a plain function so tests/test_platform_js.py runs it in Node.
 */
(function(root){
"use strict";

var KEY = "pm_platform";
var PLATFORMS = [["google","Google"],["meta","Meta"],["both","Both"]];
var LABEL = {google:"Google Ads", meta:"Meta Ads", both:"Google Ads and Meta Ads"};

function validPlatform(v){return v==="google"||v==="meta"||v==="both";}
function saved(){try{var v=root.localStorage.getItem(KEY); return validPlatform(v)?v:"";}catch(e){return "";}}
function save(v){try{root.localStorage.setItem(KEY,v);}catch(e){}}
function isMeta(id){return /^act_\d+$/.test(String(id||""));}
function isCombo(id){return /^\d{10}\+act_\d+$/.test(String(id||""));}
function comboId(g,m){return g+"+"+m;}
function platformOfId(id){return isCombo(id)?"both":isMeta(id)?"meta":"google";}
function partsOf(id){return isCombo(id)?String(id).split("+"):[String(id)];}
/* Every id the page can ask the server about: a Google customer id or a Meta act_ id (never a combined one). */
function singleId(id){return /^\d{10}$/.test(String(id||""))||isMeta(id);}

var norm = function(n){return String(n||"").toLowerCase().replace(/\(.*?\)/g,"").replace(/[^a-z0-9]/g,"");};
/* The one account on the other platform that is plainly the same client: its name is the same, or (when only one other
   account of that currency has a name that contains it) one name holds the other. null when there is none or it is unclear. */
function counterpart(acct,others){
  var n=norm(acct.name); if(n.length<3) return null;
  var pool=others.filter(function(o){return o.currency===acct.currency;});
  var exact=pool.filter(function(o){return norm(o.name)===n;});
  if(exact.length===1) return exact[0];
  if(exact.length>1) return null;
  var like=pool.filter(function(o){var a=norm(o.name); return a.length>=4&&n.length>=4&&(a.indexOf(n)>=0||n.indexOf(a)>=0);});
  return like.length===1?like[0]:null;
}

function initials(n){return String(n||"").replace(/\(.*?\)/g,"").split(/[\s&.,-]+/).filter(function(w){return /^[A-Za-z0-9]/.test(w);}).slice(0,2).map(function(w){return w[0];}).join("").toUpperCase()||"·";}
function hueOf(n){var h=0;n=String(n||"");for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))%360;return h;}
function fmtId(id){
  id=String(id||"");
  if(/^\d{10}$/.test(id)) return id.slice(0,3)+"-"+id.slice(3,6)+"-"+id.slice(6);
  return id;
}

/* A combined account from a Google and a Meta one (the name is both, the currency their shared one). */
function combine(g,m){
  return {id:comboId(g.id,m.id),name:g.name===m.name?g.name:g.name+" + "+m.name,currency:g.currency,tz:g.tz||m.tz||"",
    platform:"both",parts:[g,m]};
}

/* ── Merging what the server sends for the two halves of a combined account ── */
function maxOf(a,b){return a==null?b:b==null?a:Math.max(a,b);}
function oldest(a,b){return !a?b:!b?a:(new Date(a)<=new Date(b)?a:b);}
/* The page shape of two ranges (see tracker/paid_gads.py parse_campaign_days) as one: same days, both sets of
   campaigns, and a column is "missing" only when neither platform has it. */
function mergePerf(a,b){
  if(!a) return b; if(!b) return a;
  var off=a.campaigns.length, rows=a.rows.slice();
  b.rows.forEach(function(r){var c=r.slice(); c[0]+=off; rows.push(c);});
  var missing=(a.missing||[]).filter(function(i){return (b.missing||[]).indexOf(i)>=0;});
  var shares={}, k;
  for(k in (a.shares||{})) shares[k]=a.shares[k];
  for(k in (b.shares||{})) if(b.shares[k]!=null||!(k in shares)) shares[k]=b.shares[k];
  return {start:a.start,end:a.end,days:a.days,campaigns:a.campaigns.concat(b.campaigns),rows:rows,missing:missing,
    sis:a.sis||b.sis||null,shares:shares};
}
function mergePerfResponse(acct,g,m){
  return {account:acct,current:mergePerf(g.current,m.current),compare:(g.compare||m.compare)?mergePerf(g.compare,m.compare):null,
    stale:oldest(g.stale,m.stale),fetchedAt:oldest(g.fetchedAt,m.fetchedAt),blockedUntil:maxOf(g.blockedUntil,m.blockedUntil)};
}
function sumRows(rows,keyAt){
  var by={}, order=[];
  (rows||[]).forEach(function(r){
    var k=keyAt.map(function(i){return r[i];}).join("|"), n=r.slice(keyAt.length);
    if(!by[k]){by[k]={key:r.slice(0,keyAt.length),n:n.map(function(){return 0;})}; order.push(k);}
    n.forEach(function(v,i){by[k].n[i]+=v;});
  });
  return order.map(function(k){return by[k].key.concat(by[k].n.map(function(v){return Math.round(v*10000)/10000;}));});
}
function mergeExtras(g,m){
  if(!g) return m; if(!m) return g;
  var missing=(g.missing||[]).filter(function(i){return (m.missing||[]).indexOf(i)>=0;});
  return {device:sumRows((g.device||[]).concat(m.device||[]),[0]),network:sumRows((g.network||[]).concat(m.network||[]),[0]),
    dowHour:g.dowHour||[],hour:m.hour||[],missing:missing,stale:oldest(g.stale,m.stale),fetchedAt:oldest(g.fetchedAt,m.fetchedAt)};
}


/* ── Products (Product Analysis): two feeds as one, matched on the item id ── */
function addN(a,b){var o=a.slice(); b.forEach(function(v,i){o[i]=(o[i]||0)+v;}); return o;}
function r4(v){return Math.round(v*10000)/10000;}
function mergeProducts(a,b){
  if(!a) return b; if(!b) return a;
  var dims=[""], at={"":0}, di=function(x){x=x||""; if(!(x in at)){at[x]=dims.length; dims.push(x);} return at[x];};
  var by={}, order=[];
  [a,b].forEach(function(src,k){
    src.products.forEach(function(p){
      var id=p[0], n=p.slice(6), e=by[id];
      if(!e){e=by[id]={id:id,title:p[1],brand:src.dims[p[2]]||"",t:[src.dims[p[3]]||"",src.dims[p[4]]||"",src.dims[p[5]]||""],n:n.map(function(){return 0;}),spend:0}; order.push(id);}
      e.n=addN(e.n,n);
      if(k===0||e.title===id) {if(p[1]&&p[1]!==id) e.title=p[1]; if(src.dims[p[2]]) e.brand=src.dims[p[2]];
        if(src.dims[p[3]]){e.t=[src.dims[p[3]]||"",src.dims[p[4]]||"",src.dims[p[5]]||""];}}
    });
  });
  var rows=order.map(function(id){var e=by[id]; return [id,e.title,di(e.brand),di(e.t[0]),di(e.t[1]),di(e.t[2])].concat(e.n.map(r4));});
  rows.sort(function(x,y){return (y[8]-x[8])||(y[10]-x[10])||(y[7]-x[7])||(x[0]<y[0]?-1:1);});   // cost, value, clicks
  var sum=function(x,y){return x&&y?addN(x,y):(x||y);};
  return {start:a.start,end:a.end,dims:dims,products:rows,
    other:{count:(a.other?a.other.count:0)+(b.other?b.other.count:0),n:(a.other&&b.other)?addN(a.other.n,b.other.n).map(r4):(a.other||b.other||{n:[]}).n},
    total:sum(a.total,b.total).map(r4),missing:(a.missing||[]).filter(function(i){return (b.missing||[]).indexOf(i)>=0;}),
    daily:a.daily&&b.daily?{days:a.daily.days,rows:a.daily.rows.map(function(r,i){return addN(r,b.daily.rows[i]||[]).map(r4);})}:(a.daily||b.daily),
    outOfStock:a.outOfStock||b.outOfStock?Array.from(new Set((a.outOfStock||[]).concat(b.outOfStock||[]))).sort():(a.outOfStock===null&&b.outOfStock===null?null:[]),
    series:mergeSeries(a.series,b.series),mix:mergeMix(a,b),note:a.note||b.note||undefined};
}
/* Which campaigns each product spends in (Google's read, with Meta's spend in the last slot): two feeds as one. A feed that has products but
   no mix (the read failed) makes the whole mix unknown, because advice built on half of a product's spend would be wrong. */
function mergeMix(a,b){
  var xs=[a,b].filter(function(s){return s.products&&s.products.length;});
  if(xs.some(function(s){return !s.mix;})) return null;
  if(!xs.length) return a.mix||b.mix||null;
  var out={campaigns:[],by:{},top:{}};
  xs.forEach(function(s){
    var off=out.campaigns.length;
    out.campaigns=out.campaigns.concat(s.mix.campaigns||[]);
    Object.keys(s.mix.by||{}).forEach(function(id){out.by[id]=addN(out.by[id]||[0,0,0,0,0],s.mix.by[id]).map(r4);});
    Object.keys(s.mix.top||{}).forEach(function(id){if(!(id in out.top)) out.top[id]=s.mix.top[id]+off;});
  });
  return out;
}
function mergeSeries(a,b){
  if(!a||!b) return a||b||null;
  var ids=Array.from(new Set(a.ids.concat(b.ids))), rows={};
  ids.forEach(function(id){
    var x=a.rows[id], y=b.rows[id];
    rows[id]=x&&y?x.map(function(r,i){return addN(r,y[i]||[]).map(r4);}):(x||y);
  });
  return {days:a.days,ids:ids,rows:rows};
}
function mergeProductsResponse(acct,g,m){
  return {account:acct,current:mergeProducts(g.current,m.current),compare:(g.compare||m.compare)?mergeProducts(g.compare,m.compare):null,
    stale:oldest(g.stale,m.stale),fetchedAt:oldest(g.fetchedAt,m.fetchedAt),blockedUntil:maxOf(g.blockedUntil,m.blockedUntil)};
}

/* ── One request, or two for a combined account (Both) ── */
function plain(url){
  return root.fetch(url,{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      if(!r.ok){var e=new Error(j.error||("The server answered "+r.status)); e.status=r.status; e.config=j.configProblem; e.cooldown=!!j.cooldown; e.retryIn=j.retryIn; throw e;}
      return j;
    });
  });
}
/* fetchJson(url, refresh, {acct, merge(url, google, meta) -> response}): a request whose `account=` is a combined id is sent
   once per platform and the answers merged. When one platform cannot be read the other is returned, with `partial` saying
   what is missing; both failing throws the first error. */
function fetchJson(url,refresh,opt){
  opt=opt||{};
  var m=/[?&]account=([^&]+)/.exec(url), id=m?decodeURIComponent(m[1]):"";
  var u=refresh?url+(url.indexOf("?")<0?"?":"&")+"refresh=1":url;
  if(!id||!isCombo(id)) return plain(u);
  var parts=partsOf(id), who=["Google Ads","Meta Ads"];
  return Promise.all(parts.map(function(pid,i){
    return plain(u.replace(/([?&]account=)[^&]+/,"$1"+encodeURIComponent(pid))).then(function(j){return {j:j};},function(e){return {e:e,i:i};});
  })).then(function(r){
    var ok=r.filter(function(x){return x.j;}), bad=r.filter(function(x){return x.e;});
    if(!ok.length) throw bad[0].e;
    var out=ok.length===1?ok[0].j:opt.merge(url,r[0].j,r[1].j);
    if(ok.length===1&&opt.acct&&out.account) out=Object.assign({},out,{account:opt.acct});
    if(bad.length) out=Object.assign({},out,{partial:who[bad[0].i]+" could not be read, so these numbers leave it out: "+bad[0].e.message});
    return out;
  });
}

/* ── The control: the switch and the pickers ───────────────────────────────── */
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
var GLYPH = {
  google:'<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M12 12h8"/></svg>',
  meta:'<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 15c0-4 2-9 4.5-9S11 12 12 12s2-6 4.5-6S21 11 21 15c0 2-1.2 3-2.5 3C16 18 14 12 12 12s-4 6-6.5 6C4.2 18 3 17 3 15z"/></svg>',
  both:'<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/></svg>'
};

/* Just the Google | Meta | Both switch: opts {allow, value, onChange(platform)}; returns {set(platform), value()}. */
function switcher(host,o){
  var allow=(o.allow||["google","meta","both"]).filter(validPlatform), cur=allow.indexOf(o.value)>=0?o.value:allow[0];
  var el=document.createElement("div"); el.className="pm-plat-sw"; el.setAttribute("role","radiogroup"); el.setAttribute("aria-label","Platform");
  el.innerHTML='<span class="pd-dd-k">Platform</span><div class="pm-plat-seg">'+PLATFORMS.filter(function(p){return allow.indexOf(p[0])>=0;}).map(function(p){
    return '<button type="button" role="radio" data-pf="'+p[0]+'" aria-checked="false">'+GLYPH[p[0]]+'<span>'+p[1]+'</span></button>';}).join("")+'</div>';
  host.appendChild(el);
  function paint(){Array.prototype.forEach.call(el.querySelectorAll("button"),function(b){b.setAttribute("aria-checked",String(b.dataset.pf===cur));});}
  el.addEventListener("click",function(e){var b=e.target.closest("[data-pf]"); if(b&&b.dataset.pf!==cur){cur=b.dataset.pf; save(cur); paint(); o.onChange&&o.onChange(cur);}});
  paint();
  return {set:function(p){if(allow.indexOf(p)>=0){cur=p;paint();}},value:function(){return cur;},saved:saved,el:el};
}

/* The switch of an agent whose subject exists only on Google (impression share, search terms, keywords). It offers all three choices like every other agent. Google is the agent as it
   is. Meta has nothing to show, so it hides `o.body` (a selector of what sits under the control) and says in a card why and which agent to use for Meta instead. Both keeps the Google
   view and adds the same card so nobody thinks Meta was read. o: {name, why, closest: [{label, href}], body: "#selector", onChange(platform)}; returns {value(), set(p)}. */
function googleOnlyCard(p,o){
  var links=(o.closest||[]).map(function(c){return '<a href="'+esc(c.href)+'">'+esc(c.label)+'</a>';}).join(", ");
  return '<div class="pm-gonly-in"><b>'+esc(o.name)+(p==="meta"?" is a Google Ads agent":": Meta is not read here")+'.</b> '+esc(o.why)+
    (links?' For Meta use '+links+'.':"")+(p==="both"?" What follows is Google Ads only.":"")+'</div>';
}
function googleOnly(host,o){
  var note=document.createElement("div"); note.className="pm-gonly"; note.hidden=true;
  var anchor=(host.closest&&host.closest("section"))||host, cur=saved()||"google";
  function body(){return o.body?document.querySelector(o.body):null;}
  function apply(p){
    cur=p; var b=body(); if(b) b.hidden=p==="meta";
    note.hidden=p==="google"; note.innerHTML=p==="google"?"":googleOnlyCard(p,o);
    if(o.onChange) o.onChange(p);
  }
  var sw=switcher(host,{value:cur,onChange:apply});
  if(anchor.parentNode) anchor.parentNode.insertBefore(note,anchor.nextSibling);
  apply(cur);
  return {value:function(){return cur;},set:function(p){sw.set(p);apply(p);},note:note};
}

function accounts(host,o){
  var allow=(o.allow||["google","meta","both"]).filter(validPlatform);
  var st={platform:"google",all:[],info:{},gSel:"",mSel:"",ready:false};
  st.platform=allow.indexOf(o.platform)>=0?o.platform:(allow.indexOf(saved())>=0?saved():allow[0]);
  var wantId=o.account||"";
  var root_=document.createElement("div"); root_.className="pm-plat";
  var swc=switcher(root_,{allow:allow,value:st.platform,onChange:function(p){setPlatform(p,true);}});
  if(o.noSwitch) swc.el.hidden=true;
  var pickers=document.createElement("div"); pickers.className="pm-plat-pick";
  var hostG=document.createElement("div"), hostM=document.createElement("div");
  pickers.appendChild(hostG); pickers.appendChild(hostM);
  var note=document.createElement("p"); note.className="pm-plat-note"; note.hidden=true;
  root_.appendChild(pickers); root_.appendChild(note); host.appendChild(root_);

  var pg=root.PMPicker(hostG,{label:"Google Ads account",search:true,onChange:function(v){st.gSel=v; if(st.platform==="google") st.mSel=""; if(st.platform!=="both") settle(""); resolve(true);}});
  var pm=root.PMPicker(hostM,{label:"Meta ad account",search:true,onChange:function(v){st.mSel=v; if(st.platform==="meta") st.gSel=""; if(st.platform!=="both") settle(""); resolve(true);}});

  function list(p){return st.all.filter(function(a){return a.platform===p;});}
  function find(id){return st.all.filter(function(a){return a.id===id;})[0]||null;}
  function item(a){return {value:a.id,label:a.name,sub:fmtId(a.id)+" · "+a.currency,avatar:initials(a.name),hue:hueOf(a.name)};}
  var NONE={value:"",label:"None",sub:"Leave this platform out",avatar:"–",hue:220};

  function paintSwitch(){swc.set(st.platform);}
  function paintNote(){
    var t="", p=st.platform, i=st.info||{};
    if((p==="meta"||p==="both")&&i.metaProblem) t=(p==="meta"?"":"Showing Google only. ")+i.metaProblem;
    else if((p==="google"||p==="both")&&i.googleProblem) t=(p==="google"?"":"Showing Meta only. ")+i.googleProblem;
    note.hidden=!t; note.textContent=t;
  }
  function paintPickers(){
    var p=st.platform, g=list("google"), m=list("meta");
    hostG.hidden=p==="meta"; hostM.hidden=p==="google";
    var gi=g.map(item), mi=m.map(item);
    if(p==="both"){gi=[NONE].concat(gi); mi=[NONE].concat(mi);}
    pg.set(gi,st.gSel,!g.length); pm.set(mi,st.mSel,!m.length);
  }
  /* The account the selection means, or null when nothing is chosen. */
  function chosen(){
    var p=st.platform, g=find(st.gSel), m=find(st.mSel);
    if(p==="google") return g; if(p==="meta") return m;
    if(g&&m) return g.currency===m.currency?combine(g,m):g;       // two currencies cannot be added up: the Google account stands
    return g||m;
  }
  function visible(){
    var p=st.platform;
    return p==="both"?st.all:list(p);
  }
  function resolve(userMade){
    var a=chosen();
    if(userMade&&st.platform==="both"&&a&&a.platform==="google"&&find(st.mSel)&&find(st.mSel).currency!==a.currency)
      note.hidden=false, note.textContent="These two accounts bill in different currencies, so they cannot be added together. Showing the Google account.";
    else paintNote();
    o.onChange&&o.onChange(a,st.platform);
  }
  function isOf(id,p){var a=find(id); return !!a&&a.platform===p;}
  /* Choose sensible accounts for the platform: keep what is chosen, else what a link named, else the same client on the
     other platform when that is clear, else (for a single platform) the first account. In "Both" an unclear match is None. */
  function settle(prefer){
    var g=list("google"), m=list("meta"), p=st.platform;
    if(prefer){
      if(isCombo(prefer)){var ps=partsOf(prefer); if(isOf(ps[0],"google")) st.gSel=ps[0]; if(isOf(ps[1],"meta")) st.mSel=ps[1];}
      else if(find(prefer)){ if(find(prefer).platform==="google") st.gSel=prefer; else st.mSel=prefer; }
    }
    if(!isOf(st.gSel,"google")) st.gSel="";
    if(!isOf(st.mSel,"meta")) st.mSel="";
    if(p!=="meta"&&!st.gSel){var c=st.mSel?counterpart(find(st.mSel),g):null; st.gSel=c?c.id:(p==="google"&&g[0]?g[0].id:"");}
    if(p!=="google"&&!st.mSel){var c2=st.gSel?counterpart(find(st.gSel),m):null; st.mSel=c2?c2.id:(p==="meta"&&m[0]?m[0].id:"");}
  }
  function setPlatform(p){
    if(allow.indexOf(p)<0) return;
    st.platform=p; save(p);
    if(!st.ready) return;
    settle(""); paintPickers(); resolve(false);
  }
  paintSwitch();

  var url=o.url+(o.url.indexOf("?")<0?"?":"&")+"platform=both";
  root.fetch(url,{credentials:"same-origin"}).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      if(!r.ok){var e=new Error(j.error||("The server answered "+r.status)); e.status=r.status; e.config=j.configProblem; throw e;}
      return j;
    });
  }).then(function(j){
    st.all=j.accounts||[]; st.info=j; st.ready=true;
    var want=wantId;
    // a link that names an account decides the platform too
    if(want&&allow.indexOf(platformOfId(want))>=0&&!(o.platform&&validPlatform(o.platform))) st.platform=platformOfId(want);
    if(want&&isCombo(want)&&allow.indexOf("both")<0) want=partsOf(want)[0];
    settle(want); paintSwitch(); paintPickers();
    o.onLoaded&&o.onLoaded(st.all,j);
    resolve(false);
  }).catch(function(err){o.onError&&o.onError(err);});

  return {
    platform:function(){return st.platform;},
    accounts:function(){return visible();},
    all:function(){return st.all;},
    current:chosen,
    /* the account was changed from outside (a link, the compare list) */
    select:function(id){settle(id); paintPickers(); resolve(false);}
  };
}

var api = {KEY:KEY,PLATFORMS:PLATFORMS,LABEL:LABEL,validPlatform:validPlatform,isMeta:isMeta,isCombo:isCombo,comboId:comboId,
  platformOfId:platformOfId,partsOf:partsOf,singleId:singleId,counterpart:counterpart,combine:combine,norm:norm,fmtId:fmtId,
  mergePerf:mergePerf,mergePerfResponse:mergePerfResponse,mergeExtras:mergeExtras,sumRows:sumRows,oldest:oldest,
  mergeProducts:mergeProducts,mergeProductsResponse:mergeProductsResponse,fetchJson:fetchJson,plain:plain,
  accounts:accounts,switcher:switcher,googleOnly:googleOnly,googleOnlyCard:googleOnlyCard};
if(typeof module!=="undefined"&&module.exports) module.exports=api; else root.PMPlatform=api;
})(typeof window!=="undefined"?window:globalThis);
