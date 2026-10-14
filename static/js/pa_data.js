/* Product Analysis: the numbers, with no page code.
 *
 * Everything the page works out from the products the server sends lives here
 * so it can be tested on its own (tests/test_product_analysis_js.py runs this
 * file in Node): a product's rates, which of five groups it belongs to, the
 * 80/20 curve, roll-ups by brand and product type, what moved against the
 * comparison period, the filters behind each recommendation, and the lines
 * that say what stands out.
 *
 * A product's numbers are NP values, in the order the server sends them:
 * [impressions, clicks, cost, conversions, value, all conversions, all
 * conversion value, orders, units sold, cost of goods, gross profit].
 * Rates are worked out with PSData's metric catalog, so a rate here can never
 * disagree with the same rate on the Performance Summarizer.
 */
(function(root){
"use strict";

var P = root.PSData || (typeof require!=="undefined"?require("./ps_data.js"):null);

var IMPR=0, CLK=1, COST=2, CONV=3, VAL=4, ALLC=5, ALLV=6, ORD=7, UNITS=8, COGS=9, PROFIT=10, NP=11;

/* The five groups every product falls into, in the order they are checked. */
var SEGS = [
  {key:"star",   label:"Stars",           tone:"good", hint:"Earning clearly more than the target"},
  {key:"steady", label:"Steady",          tone:"info", hint:"Meeting the target"},
  {key:"drag",   label:"Below target",    tone:"warn", hint:"Selling, but not earning enough"},
  {key:"waste",  label:"Spend, no sales", tone:"bad",  hint:"Real spend and clicks, nothing sold"},
  {key:"thin",   label:"Not enough data", tone:"mute", hint:"Too few clicks to judge yet"}
];
var SEG = {}; SEGS.forEach(function(s){SEG[s.key]=s;});
var DEFAULTS = {target:null, minClicks:25, minSpend:null};

function zero(){var a=[];for(var i=0;i<NP;i++)a.push(0);return a;}
function addTo(a,b){for(var i=0;i<NP;i++)a[i]+=b[i]||0;return a;}
/* A product's numbers as a Performance Summarizer total, so PSData.mval gives every rate. */
function asTotal(n){var t=P.zero();for(var i=0;i<7;i++)t[i]=n[i]||0;return t;}
function rate(key,n){return P.mval(key,asTotal(n));}
function ratio(a,b){return b?a/b:null;}

/* ── Reading what the server sent ── */
/* One object per product, plus the folded "all other products" row when the feed was trimmed. */
function items(data){
  if(!data) return [];
  var d=data.dims||[""], out=data.products.map(function(r){
    var n=r.slice(6,6+NP); while(n.length<NP) n.push(0);
    var t=[d[r[3]],d[r[4]],d[r[5]]].filter(Boolean);
    return {id:r[0],title:r[1],brand:d[r[2]]||"",type:t.join(" > "),t1:d[r[3]]||"",t2:d[r[4]]||"",n:n,other:false};
  });
  var o=data.other;
  if(o&&o.count>0&&o.n&&o.n.some(function(x){return x;}))
    out.push({id:"",title:"All other products ("+o.count.toLocaleString("en-US")+")",brand:"",type:"",t1:"",t2:"",n:o.n.slice(),other:true,count:o.count});
  return out;
}
function totals(data){return data&&data.total?data.total.slice():zero();}

/* ── Which group each product belongs to ── */
/* "Earning enough" is ROAS against a target when the account reports conversion value, otherwise cost per conversion
   against the account's own: either way e is 1.0 at the target and higher is better. The auto target is the account's
   own ROAS, so by default half the money is above it and half below. */
function context(its,total,opts){
  var o={target:opts&&opts.target>0?+opts.target:null, minClicks:opts&&opts.minClicks>=0?+opts.minClicks:DEFAULTS.minClicks,
         minSpend:opts&&opts.minSpend>=0?+opts.minSpend:null};
  var hasValue=total[VAL]>0, acctRoas=ratio(total[VAL],total[COST]), acctCpa=ratio(total[COST],total[CONV]);
  var target=hasValue?(o.target||acctRoas):null;
  var minSpend=o.minSpend!=null?o.minSpend:(acctCpa||0);     // "more than a typical sale costs, and sold nothing"
  var imprs=its.filter(function(i){return !i.other&&i.n[IMPR]>0;}).map(function(i){return i.n[IMPR];}).sort(function(a,b){return a-b;});
  return {hasValue:hasValue, hasConv:total[CONV]>0, target:target, autoTarget:!o.target, acctRoas:acctRoas, acctCpa:acctCpa,
    acctCvr:ratio(total[CONV],total[CLK]), acctCtr:ratio(total[CLK],total[IMPR]), minClicks:o.minClicks, minSpend:minSpend,
    imprP75:imprs.length?imprs[Math.floor(imprs.length*0.75)]:0, total:total, basis:hasValue?"value":"conv"};
}
/* How far above (>1) or below (<1) the target a product is, or null when it cannot be told. */
function efficiency(n,ctx){
  if(ctx.hasValue){var r=ratio(n[VAL],n[COST]); return r==null||!ctx.target?null:r/ctx.target;}
  if(ctx.hasConv&&ctx.acctCpa){var c=ratio(n[COST],n[CONV]); return c?ctx.acctCpa/c:null;}
  return null;
}
function segOf(it,ctx){
  var n=it.n;
  if(n[CLK]<ctx.minClicks&&n[CONV]<=0) return "thin";
  if(n[CONV]<=0) return n[COST]>=ctx.minSpend&&n[COST]>0?"waste":"thin";
  var e=efficiency(n,ctx);
  if(e==null) return "steady";
  return e>=1.25?"star":e>=1?"steady":"drag";
}
/* Group every product. The folded "all other" row is left out: it is many products, not one. */
function classify(its,total,opts){
  var ctx=context(its,total,opts), by={};
  SEGS.forEach(function(s){by[s.key]=[];});
  var shareBase=ctx.hasValue?total[VAL]:total[CONV], costBase=total[COST];
  its.forEach(function(it){
    if(it.other){it.seg="";it.gem=false;return;}
    it.seg=segOf(it,ctx);
    var share=shareBase?(ctx.hasValue?it.n[VAL]:it.n[CONV])/shareBase:0, cshare=costBase?it.n[COST]/costBase:0;
    it.gem=it.seg==="star"&&share>0&&cshare<share*0.5;       // earns twice its share of the money it is given
    by[it.seg].push(it);
  });
  return {ctx:ctx,by:by};
}
function segTotals(its){
  var out={};
  SEGS.forEach(function(s){out[s.key]={count:0,n:zero()};});
  its.forEach(function(it){if(it.seg&&out[it.seg]){out[it.seg].count++;addTo(out[it.seg].n,it.n);}});
  return out;
}

/* ── The 80/20 curve ── */
/* Products biggest earner first, and how much of the total the first k of them make. */
function pareto(its,ctx){
  var idx=ctx.hasValue?VAL:CONV;
  var list=its.filter(function(i){return !i.other&&i.n[idx]>0;}).sort(function(a,b){return b.n[idx]-a.n[idx];});
  var sum=list.reduce(function(s,i){return s+i.n[idx];},0);
  if(!sum) return {basis:ctx.basis,count:0,points:[],at80:0,pct80:null,top5:null,all:its.filter(function(i){return !i.other;}).length};
  var cum=0, pts=[{k:0,share:0}], at80=0;
  list.forEach(function(i,k){
    cum+=i.n[idx];
    if(!at80&&cum/sum>=0.8) at80=k+1;
    pts.push({k:k+1,share:cum/sum});
  });
  var step=Math.max(1,Math.ceil(pts.length/160)), thin=pts.filter(function(p,j){return j%step===0||j===pts.length-1;});
  var top5=pts[Math.min(5,list.length)].share;
  var all=its.filter(function(i){return !i.other;}).length;
  return {basis:ctx.basis,count:list.length,points:thin,at80:at80,pct80:all?at80/all:null,top5:top5,all:all};
}

/* ── Roll-ups ── */
var BY = {brand:"Brand",type1:"Product type",type2:"Product type, 2nd level"};
function keyOf(it,by){return (by==="brand"?it.brand:by==="type1"?it.t1:it.t2)||"";}
/* Totals for each brand or product type, biggest spend first, the small ones folded into "Everything else". */
function rollup(its,by,top){
  top=top||12;
  var acc={}, none=null;
  its.forEach(function(it){
    if(it.other) return;
    var k=keyOf(it,by), a=acc[k]||(acc[k]={key:k,n:zero(),count:0});
    addTo(a.n,it.n); a.count++;
  });
  var list=Object.keys(acc).map(function(k){return acc[k];});
  list.forEach(function(a){if(!a.key){a.key="(not set)";a.unset=true;}});
  list.sort(function(a,b){return b.n[COST]-a.n[COST]||b.n[VAL]-a.n[VAL]||(a.key<b.key?-1:1);});
  if(list.length<=top) return list;
  var head=list.slice(0,top), rest=list.slice(top), o={key:"Everything else ("+rest.length+")",n:zero(),count:0,rest:true};
  rest.forEach(function(a){addTo(o.n,a.n);o.count+=a.count;});
  head.push(o);
  return head;
}

/* ── What moved against the comparison period ── */
function movers(cur,prev,ctx,n){
  n=n||8;
  if(!prev||!prev.length) return null;
  var idx=ctx.hasValue?VAL:CONV, pm={};
  prev.forEach(function(p){if(!p.other)pm[p.id]=p;});
  var rows=[], seen={};
  cur.forEach(function(c){
    if(c.other) return;
    var p=pm[c.id]; seen[c.id]=1;
    rows.push({id:c.id,title:c.title,cur:c.n,prev:p?p.n:zero(),delta:c.n[idx]-(p?p.n[idx]:0),isNew:!p});
  });
  var gone=0;
  prev.forEach(function(p){
    if(p.other||seen[p.id]) return;
    if(p.n[idx]>0||p.n[COST]>0){gone++; rows.push({id:p.id,title:p.title,cur:zero(),prev:p.n,delta:-p.n[idx],gone:true});}
  });
  var up=rows.filter(function(r){return r.delta>0;}).sort(function(a,b){return b.delta-a.delta;}).slice(0,n);
  var down=rows.filter(function(r){return r.delta<0;}).sort(function(a,b){return a.delta-b.delta;}).slice(0,n);
  return {up:up,down:down,basis:ctx.basis,newCount:rows.filter(function(r){return r.isNew&&r.cur[COST]>0;}).length,goneCount:gone};
}

/* ── The filters behind the groups and the recommendations ── */
/* Each is a test on one product; the table and the recommendations use the same ones, so a count never disagrees
   with the rows it opens. `oos` is a Set of out-of-stock ids (or null when that could not be read). */
var FILTERS = {
  all:    function(){return true;},
  star:   function(it){return it.seg==="star";},
  steady: function(it){return it.seg==="steady";},
  drag:   function(it){return it.seg==="drag";},
  waste:  function(it){return it.seg==="waste";},
  thin:   function(it){return it.seg==="thin";},
  gem:    function(it){return !!it.gem;},
  /* People click and do not buy: plenty of clicks, a conversion rate under half the account's, and not already a no-sale product */
  leak:   function(it,ctx){return !it.other&&it.seg!=="waste"&&it.seg!=="thin"&&it.n[CLK]>=ctx.minClicks*2&&ctx.acctCvr!=null&&
            (rate("cvr",it.n)||0)<ctx.acctCvr*0.5;},
  /* Shown a lot, clicked far less than the account average: the title or image is not earning the click */
  window: function(it,ctx){return !it.other&&it.n[IMPR]>=Math.max(ctx.imprP75,500)&&ctx.acctCtr!=null&&
            (rate("ctr",it.n)||0)<ctx.acctCtr*0.5;},
  oos:    function(it,ctx,oos){return !it.other&&!!oos&&oos.has(it.id)&&it.n[COST]>0;}
};
function filterItems(its,key,ctx,oos){
  var f=FILTERS[key]||FILTERS.all;
  return its.filter(function(it){return f(it,ctx,oos);});
}

/* The recommendations, most money first. Each says what to do, how many products and how much it is about. */
function actions(its,cls,oosIds){
  var ctx=cls.ctx, oos=oosIds?new Set(oosIds):null, out=[];
  function pack(key,title,tone,amountKind,how){
    var list=filterItems(its,key,ctx,oos);
    if(!list.length) return;
    var cost=list.reduce(function(s,i){return s+i.n[COST];},0), val=list.reduce(function(s,i){return s+i.n[VAL];},0);
    out.push({key:key,title:title,tone:tone,count:list.length,cost:cost,value:val,amountKind:amountKind,how:how});
  }
  pack("waste","Stop paying for products that do not sell","bad","cost",
    "Each spent more than a typical sale costs on this account and sold nothing. Exclude them, cut their bids, or check the feed and the page.");
  pack("oos","Out of stock, still being advertised","bad","cost",
    "Merchant Center says these are out of stock, yet they still got spend. Pause them or fix availability.");
  pack("gem","Give your best earners more room","good","value",
    "These earn at least 1.25x the target and take less than half the budget their sales justify. Raise their bids or move them to their own campaign.");
  pack("leak","Fix the page or the price","warn","cost",
    "People click these but rarely buy, converting at under half the account rate. Look at price, shipping, reviews and the landing page.");
  pack("window","Make the listing worth a click","info","cost",
    "Shown often but clicked at under half the usual rate. Improve the title, the main image and the price shown.");
  if(cls.by.drag.length) pack("drag","Trim what sells below target","warn","cost",
    "These sell, but earn less than the target. Lower their bids a little, or raise the price if the margin allows.");
  out.sort(function(a,b){
    var order={bad:0,good:1,warn:2,info:3};
    return (order[a.tone]-order[b.tone])||(b.cost-a.cost);
  });
  return out;
}

/* ── The headline numbers ── */
function kpis(total,prev,cls){
  var by=cls.by, waste=by.waste.reduce(function(s,i){return s+i.n[COST];},0);
  var selling=by.star.length+by.steady.length+by.drag.length;
  return {spend:total[COST],revenue:total[VAL],roas:ratio(total[VAL],total[COST]),conv:total[CONV],cpa:ratio(total[COST],total[CONV]),
    waste:waste,wastePct:total[COST]?waste/total[COST]:null,selling:selling,products:selling+by.waste.length+by.thin.length,prev:prev||null};
}

/* ── How each product is performing ── */
/* The measures the page ranks and draws products by. `additive` ones add up across products, so each product has a
   share of the account's total; the rest are rates. */
var PERF = {
  value:{label:"Revenue",additive:true}, cost:{label:"Spend",additive:true}, conversions:{label:"Conversions",additive:true},
  clicks:{label:"Clicks",additive:true}, impressions:{label:"Impressions",additive:true},
  roas:{label:"ROAS",additive:false,needsValue:true}, ctr:{label:"CTR",additive:false}, cpc:{label:"Avg. CPC",additive:false}
};
function metricValue(n,key){return key==="value"?n[VAL]:P.mval(key,asTotal(n));}
/* One value per day for a product, or null when its days were not read (only the top spenders' days are). */
function seriesValues(series,id,key){
  var rows=series&&series.rows&&series.rows[String(id).toLowerCase()];
  return rows?rows.map(function(n){return metricValue(n,key);}):null;
}
/* The products ranked by `key`, best first: each with its value, its share of the account (adding measures), what it was in
   the comparison period and how that changed. A product with nothing to measure (no spend for a rate, a zero for an
   adding measure) is left out. */
function leaderboard(its,cmp,key,n){
  var pm={}, total=zero(), meta=PERF[key]||PERF.cost;
  (cmp||[]).forEach(function(p){if(!p.other)pm[p.id]=p;});
  its.forEach(function(i){addTo(total,i.n);});
  var all=metricValue(total,key), rows=[];
  its.forEach(function(it){
    if(it.other) return;
    var v=metricValue(it.n,key);
    if(v==null||!isFinite(v)||(meta.additive?v<=0:!it.n[COST])) return;
    var p=pm[it.id], pv=p?metricValue(p.n,key):null;
    rows.push({it:it,v:v,prev:pv,change:pv!=null&&isFinite(pv)?P.change(key,v,pv):null,
               share:meta.additive&&all>0?v/all:null});
  });
  rows.sort(function(a,b){return b.v-a.v||b.it.n[COST]-a.it.n[COST]||(a.it.id<b.it.id?-1:1);});
  rows=rows.slice(0,n||20);
  rows.forEach(function(r,i){r.rank=i+1;});
  return {rows:rows,total:all,additive:meta.additive};
}

/* ── Table: search, filter, sort ── */
var COLS = [
  {key:"title",label:"Product",kind:"text"},{key:"cost",label:"Spend",kind:"money"},{key:"value",label:"Revenue",kind:"money"},
  {key:"roas",label:"ROAS",kind:"x"},{key:"conversions",label:"Conv.",kind:"num"},{key:"cpa",label:"Cost / conv.",kind:"money2"},
  {key:"clicks",label:"Clicks",kind:"int"},{key:"impressions",label:"Impr.",kind:"int"},{key:"ctr",label:"CTR",kind:"pct"},
  {key:"cvr",label:"Conv. rate",kind:"pct"},{key:"cpc",label:"Avg. CPC",kind:"money2"}
];
function colValue(it,key){
  if(key==="title") return (it.title||"").toLowerCase();
  if(key==="share") return it.n[COST];
  return P.METRICS[key]?P.mval(key,asTotal(it.n)):null;
}
function sortItems(list,key,dir){
  var d=dir<0?-1:1;
  return list.slice().sort(function(a,b){
    var x=colValue(a,key), y=colValue(b,key);
    if(x==null&&y==null) return a.title<b.title?-1:1;
    if(x==null) return 1;                       // nothing sorts last whichever way you sort
    if(y==null) return -1;
    return x<y?-d:x>y?d:(a.title<b.title?-1:a.title>b.title?1:0);
  });
}
function search(list,q){
  q=String(q||"").trim().toLowerCase();
  if(!q) return list;
  var words=q.split(/\s+/);
  return list.filter(function(it){
    var hay=(it.title+" "+it.id+" "+it.brand+" "+it.type).toLowerCase();
    return words.every(function(w){return hay.indexOf(w)>=0;});
  });
}

/* ── Words ── */
function money(v,cur,small){return P.fmt("money",v,cur,small);}
function pct(v,d){return v==null?"–":(v*100).toFixed(d==null?0:d)+"%";}
function plural(n,one,many){return n+" "+(n===1?one:many||one+"s");}
/* The lines under "What stands out". Plain statements of what the numbers say. */
function insights(its,cls,pa,mv,oosIds,currency){
  var ctx=cls.ctx, out=[], tot=ctx.total, byKey=cls.by;
  if(!tot[COST]&&!tot[IMPR]) return out;
  var unit=ctx.hasValue?"revenue":"conversions";
  if(pa&&pa.at80){
    out.push({tone:pa.pct80!=null&&pa.pct80<0.2?"info":"flat",
      text:plural(pa.at80,"product")+" ("+pct(pa.pct80)+" of your "+pa.all.toLocaleString("en-US")+") make 80% of your "+unit+". The top five make "+pct(pa.top5)+"."});
    if(pa.top5>=0.6&&pa.count>=6) out.push({tone:"warn",text:"Your "+unit+" leans hard on five products. If one runs out of stock or loses its ranking, it shows."});
  }
  var w=byKey.waste;
  if(w.length){
    var wc=w.reduce(function(s,i){return s+i.n[COST];},0);
    out.push({tone:"bad",text:plural(w.length,"product")+" took "+money(wc,currency)+" ("+pct(tot[COST]?wc/tot[COST]:null)+" of spend) and sold nothing."});
  }
  if(byKey.star.length&&ctx.hasValue){
    var sv=byKey.star.reduce(function(s,i){return s+i.n[VAL];},0), sc=byKey.star.reduce(function(s,i){return s+i.n[COST];},0);
    out.push({tone:"good",text:plural(byKey.star.length,"star")+" returned "+money(sv,currency)+" on "+money(sc,currency)+", "+(sc?(sv/sc).toFixed(1):"0")+"x. That is "+
      pct(tot[VAL]?sv/tot[VAL]:null)+" of revenue from "+pct(tot[COST]?sc/tot[COST]:null)+" of spend."});
  }
  var thin=byKey.thin.length;
  if(thin&&(thin>=0.5*(cls.by.star.length+cls.by.steady.length+cls.by.drag.length+cls.by.waste.length+thin)))
    out.push({tone:"mute",text:pct(thin/(cls.by.star.length+cls.by.steady.length+cls.by.drag.length+cls.by.waste.length+thin))+" of products have under "+ctx.minClicks+" clicks, too few to judge. A longer date range tells you more."});
  if(oosIds&&oosIds.length){
    var oos=new Set(oosIds), spent=0, cnt=0;
    its.forEach(function(i){if(!i.other&&oos.has(i.id)&&i.n[COST]>0){spent+=i.n[COST];cnt++;}});
    if(cnt) out.push({tone:"bad",text:plural(cnt,"out-of-stock product")+" still got "+money(spent,currency)+" of spend."});
  }
  if(mv&&mv.up.length&&mv.up[0].delta>0){
    var u=mv.up[0];
    out.push({tone:"good",text:"Biggest gain: "+u.title+", "+(ctx.hasValue?"+"+money(u.delta,currency):"+"+Math.round(u.delta)+" conversions")+" against the comparison period."});
  }
  if(mv&&mv.down.length&&mv.down[0].delta<0){
    var d=mv.down[0];
    out.push({tone:"warn",text:"Biggest drop: "+d.title+", "+(ctx.hasValue?"−"+money(-d.delta,currency):"−"+Math.round(-d.delta)+" conversions")+" against the comparison period."});
  }
  if(!ctx.hasValue&&ctx.hasConv) out.push({tone:"info",text:"This account does not report conversion value, so products are judged on cost per conversion, not ROAS."});
  if(!ctx.hasConv&&tot[CLK]>0) out.push({tone:"warn",text:"No conversions were recorded in this range. Check conversion tracking before trusting any product ranking."});
  return out;
}

function csv(list,currency,ctx){
  var rows=[["Item id","Product","Brand","Product type","Group","Spend","Revenue","ROAS","Conversions","Cost per conversion","Clicks","Impressions","CTR","Conversion rate","Avg CPC"]];
  list.forEach(function(it){
    var t=asTotal(it.n), r=function(k){var v=P.mval(k,t);return v==null?"":Math.round(v*10000)/10000;};
    rows.push([it.id,it.title,it.brand,it.type,it.seg&&SEG[it.seg]?SEG[it.seg].label:"",Math.round(it.n[COST]*100)/100,Math.round(it.n[VAL]*100)/100,
      r("roas"),Math.round(it.n[CONV]*100)/100,r("cpa"),it.n[CLK],it.n[IMPR],r("ctr"),r("cvr"),r("cpc")]);
  });
  return P.toCsv(rows);
}

var api = {
  IMPR:IMPR,CLK:CLK,COST:COST,CONV:CONV,VAL:VAL,ALLC:ALLC,ALLV:ALLV,ORD:ORD,UNITS:UNITS,COGS:COGS,PROFIT:PROFIT,NP:NP,
  SEGS:SEGS,SEG:SEG,DEFAULTS:DEFAULTS,BY:BY,COLS:COLS,FILTERS:FILTERS,
  zero:zero,addTo:addTo,asTotal:asTotal,rate:rate,items:items,totals:totals,context:context,efficiency:efficiency,segOf:segOf,classify:classify,
  segTotals:segTotals,pareto:pareto,rollup:rollup,movers:movers,filterItems:filterItems,actions:actions,kpis:kpis,
  PERF:PERF,metricValue:metricValue,seriesValues:seriesValues,leaderboard:leaderboard,
  colValue:colValue,sortItems:sortItems,search:search,insights:insights,csv:csv
};
root.PAData = api;
if(typeof module!=="undefined"&&module.exports) module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
