/* Product Analysis: what to do about each group of products, and how.
 *
 * pa_data.js says which products are in trouble or underfunded; this says what to do about them, chosen from the campaigns they spend in. A bid change reaches a
 * product only on a Shopping campaign whose bids are set by hand or from clicks: Smart Bidding sets its own, and Performance Max has no bid for a single product. So
 * every plan is built from the kind of campaign the group's spend is on (the server's "mix", see tracker/paid_products.py) and says which steps reach which spend.
 * Where the mix could not be read the plan gives the steps for each kind instead of guessing.
 *
 * build(key, products, ctx, mix, o) -> {kind, lead, reach, steps, worth} or null. Pure functions, no page code: tests/test_product_analysis_plan_js.py runs this file in Node.
 */
(function(root){
"use strict";

var P = root.PSData || (typeof require!=="undefined"?require("./ps_data.js"):null);
var A = root.PAData || (typeof require!=="undefined"?require("./pa_data.js"):null);

var IMPR=A.IMPR, CLK=A.CLK, COST=A.COST, CONV=A.CONV, VAL=A.VAL;
var MAJORITY=0.6, REACH_MIN=0.1;
var DAMPEN=0.8, STEP=0.05, MAX_UP=0.40, MAX_DOWN=-0.50;          /* the bid rule Geo Performance and Segment Analysis use: most of the gap, in steps of 5%, +40% to -50% */
var KINDS = ["adjustable","smart","auto","meta"];
var AT = {adjustable:0, smart:1, auto:2, other:3, meta:4};
var LABEL = {
  adjustable:"On campaigns whose bids you set (Manual CPC or Maximize clicks): ",
  smart:"On Smart Bidding Shopping campaigns: ",
  auto:"On Performance Max: ",
  meta:"On Meta: "
};
var KEYS = ["waste","oos","gem","leak","window","drag"];

function money(v,cur,small){return P.fmt("money",v,cur,small);}
function pct(v){return Math.round(v*100)+"%";}
function plural(n,one,many){return n+" "+(n===1?one:many||one+"s");}
function products(n,verb){return plural(n,"product")+" "+(n===1?verb[0]:verb[1]);}      /* "1 product earns", "3 products earn" */
function sum(list,i){return list.reduce(function(s,it){return s+(it.n[i]||0);},0);}

/* The bid change a gap in results calls for: Geo Performance's rule, so a product is never told something the other agents would not say. */
function adjustmentFor(idx){
  var raw=Math.max(MAX_DOWN,Math.min(MAX_UP,(idx-1)*DAMPEN)), stepped=Math.round(raw/STEP)*STEP;
  return Math.abs(stepped)<STEP-1e-9?0:Math.round(stepped*100)/100;
}
function signed(v){return (v>0?"+":"")+Math.round(v*100)+"%";}
function median(xs){
  if(!xs.length) return 0;
  var s=xs.slice().sort(function(a,b){return a-b;}), m=Math.floor(s.length/2);
  return s.length%2?s[m]:(s[m-1]+s[m])/2;
}

/* ── Which campaigns these products spend in ── */
/* The spend of a group of products by kind of campaign, or null when the read is missing or none of them spent. */
function groupMix(rows,mix){
  if(!mix||!mix.by) return null;
  var t=[0,0,0,0,0];
  rows.forEach(function(it){var v=mix.by[it.id]; if(v) for(var i=0;i<5;i++) t[i]+=v[i]||0;});
  var total=t.reduce(function(s,v){return s+v;},0);
  if(total<=0) return null;
  var share={}; Object.keys(AT).forEach(function(k){share[k]=t[AT[k]]/total;});
  return {spend:t,total:total,share:share};
}
/* "adjustable", "smart", "auto" or "meta" when that kind holds 60% or more of the spend, "mixed" when none does, "unknown" when it could not be read. */
function kindOf(g){
  if(!g) return "unknown";
  for(var i=0;i<KINDS.length;i++) if(g.share[KINDS[i]]>=MAJORITY-1e-9) return KINDS[i];
  return "mixed";
}
/* The biggest campaigns of one kind these products spend in, by the spend of the products that spend most there. */
function campaignsOf(rows,mix,kind){
  if(!mix||!mix.campaigns||!mix.top) return [];
  var w={};
  rows.forEach(function(it){
    var ci=mix.top[it.id], c=ci==null?null:mix.campaigns[ci];
    if(!c||c[3]!==kind) return;
    var v=mix.by&&mix.by[it.id]; w[ci]=(w[ci]||0)+(v?v.reduce(function(s,x){return s+x;},0):0);
  });
  return Object.keys(w).sort(function(a,b){return w[b]-w[a]||a-b;}).slice(0,3).map(function(ci){return mix.campaigns[ci][0];});
}
function inCamp(rows,mix,kind){
  var names=campaignsOf(rows,mix,kind);
  return names.length?"in "+names.join(" or "):"in the campaign";
}

/* ── The estimates ── */
function accountAverage(ctx,cur){
  return ctx.hasValue&&ctx.acctRoas?"the account's "+ctx.acctRoas.toFixed(2)+"x":(ctx.acctCpa?"the account's "+money(ctx.acctCpa,cur,true)+" a conversion":"");
}
/* What `spend` would return at the account's average: revenue when the account reports it, otherwise conversions. */
function returnOf(spend,ctx,cur){
  if(ctx.hasValue&&ctx.acctRoas) return "about "+money(spend*ctx.acctRoas,cur)+" of revenue";
  if(ctx.acctCpa) return "about "+plural(Math.round(spend/ctx.acctCpa),"conversion");
  return "";
}
/* How far above (>1) or below (<1) the target each product is, as the group's median, from the products that have a result. */
function medianIndex(rows,ctx){
  var xs=[];
  rows.forEach(function(it){var e=A.efficiency(it.n,ctx); if(e!=null&&isFinite(e)) xs.push(e);});
  return xs.length?median(xs):null;
}

/* ── Steps shared by the kinds ── */
function excludeSteps(rows,mix,kind){
  var where=inCamp(rows,mix,kind);
  if(kind==="adjustable"||kind==="smart")
    return "Open "+where.replace(/^in /,"")+", then Ad groups, the ad group, then Product groups. Click the plus beside All products, subdivide by Item ID, and set each of these item ids to Excluded"+
      (kind==="adjustable"?" (or cut its bid to a few cents to keep it in rotation).":" (a lower bid does nothing under Smart Bidding, so an exclusion is the lever).");
  if(kind==="auto")
    return "Performance Max has no bid for a single product, so exclude them: open "+where.replace(/^in /,"")+", then Asset groups, the asset group, Listing groups, Edit, subdivide by Item ID and exclude each of these item ids.";
  return "In Commerce Manager open Catalog, then Sets, and make a set of the products that sell. In Ads Manager open the ad set, choose that set as its product set, and the ones that spend without selling drop out.";
}
var LABEL_STEP = "With more than a handful, give them one custom label in a supplemental feed (Merchant Center, Products, Data sources, add a supplemental feed, custom label 0) and exclude that label once, instead of item by item.";

/* Choose the steps for the kind the spend is on, or one block per kind when it is mixed or unknown. */
function byKind(rows,g,kind,mix,make){
  if(KINDS.indexOf(kind)>=0) return make(kind);
  function blocks(all){
    var out=[];
    KINDS.forEach(function(k){
      if(!all&&(!g||g.share[k]<REACH_MIN)) return;
      var s=make(k);
      if(s&&s.length) out=out.concat(s.map(function(x,i){return i===0?LABEL[k]+x:x;}));
    });
    return out;
  }
  var out=blocks(false);
  return out.length?out:blocks(true);          /* the mix is not known, or it is mostly "other" bidding so nothing is as big as a tenth: say every kind */
}
/* A line saying how much of the spend a bid change reaches, or "" when it reaches nearly all of it. */
function reachLine(g,kind){
  if(kind==="unknown") return "Which kind of campaign these products spend in could not be read, so the steps cover each kind. Check each campaign's Bid strategy column first: only manual and Maximize clicks bids can be changed by product.";
  if(!g||g.share[kind]>=0.95) return "";
  var parts=[];
  if(g.share.adjustable>=0.005) parts.push(pct(g.share.adjustable)+" on bids you set");
  if(g.share.smart>=0.005) parts.push(pct(g.share.smart)+" on Smart Bidding");
  if(g.share.auto>=0.005) parts.push(pct(g.share.auto)+" on Performance Max");
  if(g.share.meta>=0.005) parts.push(pct(g.share.meta)+" on Meta");
  if(g.share.other>=0.005) parts.push(pct(g.share.other)+" on other bidding");
  return "Where their spend sits: "+parts.join(", ")+". A bid change reaches only the first; the steps below say what works for the rest.";
}

/* ── The six plans ── */
var BUILD = {};

BUILD.waste = function(rows,ctx,env){
  var cost=sum(rows,COST), kind=env.kind, cur=env.cur;
  var steps=["Check each product before you exclude it: in Merchant Center open Products and search its item id. A product people click and never buy often has a feed or page fault (a wrong price, no stock in the size shown, a page that errors), so fix that first and give it about two weeks."];
  steps=steps.concat(byKind(rows,env.g,kind,env.mix,function(k){return [excludeSteps(rows,env.mix,k)];}));
  if(rows.length>10) steps.push(LABEL_STEP);
  var avg=accountAverage(ctx,cur), ret=returnOf(cost,ctx,cur);
  return {lead:products(rows.length,["spent","spent"])+" "+money(cost,cur)+" without a sale"+(ctx.minSpend?", each more than the "+money(ctx.minSpend,cur,true)+" a typical sale costs here":"")+".",
    steps:steps,
    worth:"Excluding them frees "+money(cost,cur)+" for the range"+(ret?"; at "+avg+" that money would return "+ret:"")+": an estimate, since the next dollars on other products usually earn less, and a product you fix instead of exclude may sell."};
};

BUILD.oos = function(rows,ctx,env){
  var cost=sum(rows,COST), cur=env.cur;
  var steps=["In Merchant Center open Products, then Diagnostics, and search each item id. Check the availability the feed says and when the feed was last fetched (Products, Data sources): spend on an out-of-stock item usually means the feed is behind the shop.",
    "If it is in stock, correct the availability in the feed (or the shop's feed settings) and refresh the data source, so Google stops treating it as out of stock.",
    "If it really is out of stock, pause it until it is back: "];
  steps[2]+=byKind(rows,env.g,env.kind,env.mix,function(k){return [excludeSteps(rows,env.mix,k)];}).join(" ");
  steps.push("Set the feed to refresh at least daily (Merchant Center, Products, Data sources, the feed, Settings), so stock changes reach Google the same day.");
  return {lead:products(rows.length,["still got","still got"])+" "+money(cost,cur)+" of spend while Merchant Center said it was out of stock.",steps:steps,
    worth:"That "+money(cost,cur)+" went to clicks that could not become sales, and stops once the feed and the shop agree."};
};

BUILD.gem = function(rows,ctx,env){
  var cost=sum(rows,COST), val=sum(rows,VAL), cur=env.cur, kind=env.kind;
  var idx=medianIndex(rows,ctx), adj=idx==null?0:adjustmentFor(idx), up=adj>0?adj:MAX_UP/2;
  var steps=byKind(rows,env.g,kind,env.mix,function(k){
    var where=inCamp(rows,env.mix,k).replace(/^in /,"");
    if(k==="adjustable") return ["Raise their bids by about "+signed(up)+" (a rise is capped at +40% at a time): open "+where+", Ad groups, the ad group, Product groups, subdivide by Item ID if they are not split out, and raise each one's Max. CPC."];
    if(k==="smart") return ["Smart Bidding sets the bid, so a higher bid does nothing. Give these products their own campaign: Campaigns, plus, New campaign, Sales, Shopping, include only these item ids (a product group by Item ID or one custom label), a budget of its own, and a Target ROAS a little below the main campaign's (start about 10% lower, then check after two weeks).",
      "If "+where+" shows Limited by budget in the Status column, raising its daily budget does the same job."];
    if(k==="auto") return ["Performance Max has no bid for a single product. Give these their own asset group or campaign: "+where+", Asset groups, plus, and in its Listing groups subdivide by Item ID to hold only these. Give it its own budget and a lower target ROAS than the rest."];
    return ["Make a product set of these (Commerce Manager, Catalog, Sets) and run it in its own ad set with its own budget. Raise that budget by no more than about 20% at a time, so the ad set stays out of the learning phase."];
  });
  var extra=cost*0.2, roas=cost?val/cost:0;
  var worth=ctx.hasValue
    ? "These return "+money(val,cur)+" on "+money(cost,cur)+" ("+roas.toFixed(1)+"x). Raising their spend 20% ("+money(extra,cur)+" more) at half that return would add about "+money(extra*roas*0.5,cur)+" of revenue: an estimate, since the next dollars earn less than the first."
    : "These earn "+plural(Math.round(sum(rows,CONV)),"conversion")+" on "+money(cost,cur)+". Raising their spend 20% at half that return would add about "+plural(Math.round(sum(rows,CONV)*0.2*0.5),"conversion")+": an estimate, since the next dollars earn less than the first.";
  return {lead:products(rows.length,["earns","earn"])+" at least 1.25x the target and take"+(rows.length===1?"s":"")+" under half the budget their sales justify.",steps:steps,worth:worth,reachApplies:true};
};

BUILD.leak = function(rows,ctx,env){
  var cur=env.cur, clicks=sum(rows,CLK), conv=sum(rows,CONV);
  var steps=["Open the product page as a shopper would. Check the price matches the ad, the size or colour shown is the one that opens, shipping and delivery are clear without scrolling, and the page has reviews.",
    "In Merchant Center open Growth, then Price competitiveness, and see whether its price sits above the benchmark: a price above it gets clicks and loses the sale.",
    "If the ad shows one variant and the page opens another, fix the item's link so it lands on that variant."];
  var idx=ctx.acctCvr?(conv/Math.max(clicks,1))/ctx.acctCvr:null, adj=idx==null?0:Math.min(0,adjustmentFor(idx));
  steps=steps.concat(byKind(rows,env.g,env.kind,env.mix,function(k){
    var where=inCamp(rows,env.mix,k).replace(/^in /,"");
    if(k==="adjustable") return ["While you fix it, pay for fewer of these clicks: "+where+", Product groups, subdivide by Item ID, and lower the Max. CPC by about "+signed(adj||MAX_DOWN)+"."];
    return ["A bid cannot be lowered for one product here, so fix the page and price first. If it still converts well under the account rate after two weeks, exclude it: "+excludeSteps(rows,env.mix,k)];
  }));
  var gap=rows.reduce(function(s,it){return s+Math.max(0,it.n[CLK]*(ctx.acctCvr||0)-it.n[CONV]);},0);
  var aov=conv>0&&ctx.hasValue?sum(rows,VAL)/conv:(ctx.hasValue&&ctx.acctRoas&&ctx.acctCpa?ctx.acctRoas*ctx.acctCpa:0);
  var sales=Math.round(gap);
  return {lead:products(rows.length,["got","got"])+" "+Math.round(clicks).toLocaleString("en-US")+" clicks but converted at under half the account's rate.",steps:steps,
    worth:sales>=1?"If they converted at the account's rate they would add about "+plural(sales,"sale")+(aov?" (about "+money(sales*aov,cur)+")":"")+" over this range: an estimate, since a price or page problem may stop them reaching it.":null,reachApplies:true};
};

BUILD.window = function(rows,ctx,env){
  var cur=env.cur, gapClicks=rows.reduce(function(s,it){return s+Math.max(0,it.n[IMPR]*(ctx.acctCtr||0)-it.n[CLK]);},0);
  var steps=["Open each one in Merchant Center (Products) and put the brand, the product type and the key attribute (size, colour, material) in the first 70 characters of the title, since that is what shows in the listing.",
    "Check the main image: a plain background, the product filling the frame, no logo, text or watermark. Add a lifestyle shot as an additional image.",
    "In Merchant Center open Growth, then Price competitiveness, and compare its price with the benchmark. A price above it loses clicks.",
    "To change many titles at once, add a feed rule or a supplemental feed (Merchant Center, Products, Data sources) instead of editing item by item. Give it a week, then compare click rate."];
  var cvr=ctx.acctCvr||0;
  return {lead:products(rows.length,["was","were"])+" shown often but clicked at under half the account's rate, so the listing is not earning the click. A bid change does not fix that.",steps:steps,
    worth:gapClicks>=5?"At the account's click rate they would get about "+Math.round(gapClicks).toLocaleString("en-US")+" more clicks"+(cvr&&Math.round(gapClicks*cvr)>=1?", roughly "+plural(Math.round(gapClicks*cvr),"conversion")+" at the account's rate":"")+" over this range: an estimate, since a click rate that low may also reflect the price."
      :null};
};

BUILD.drag = function(rows,ctx,env){
  var cost=sum(rows,COST), cur=env.cur, kind=env.kind;
  var idx=medianIndex(rows,ctx), adj=idx==null?0:Math.min(0,adjustmentFor(idx)), down=adj<0?adj:-0.10;
  var steps=byKind(rows,env.g,kind,env.mix,function(k){
    var where=inCamp(rows,env.mix,k).replace(/^in /,"");
    if(k==="adjustable") return ["Lower their bids by about "+signed(down)+": open "+where+", Ad groups, the ad group, Product groups, subdivide by Item ID if they are not split out, and lower each one's Max. CPC."];
    if(k==="smart") return ["Smart Bidding sets the bid, so a lower bid does nothing. Move these item ids into a campaign of their own with a higher Target ROAS than the main one (start about 10% higher), or exclude the worst: "+excludeSteps(rows,env.mix,k)];
    if(k==="auto") return ["Performance Max has no bid for a single product. Move these into an asset group or campaign with a higher target ROAS (open "+where+", Asset groups, and subdivide the Listing groups by Item ID), or exclude the worst of them."];
    return ["Move them to an ad set of their own with a smaller budget, so they stop taking money from the products that earn more."];
  });
  steps.push("If the margin allows it, a small price rise (or a free-shipping threshold) improves what each sale returns. Check the price in Merchant Center, Products, before you change the feed.");
  var reached=kind==="adjustable"?cost:kind==="mixed"&&env.g?cost*env.g.share.adjustable:0, saved=reached*Math.abs(down);
  return {lead:products(rows.length,["sells","sell"])+", but earn"+(rows.length===1?"s":"")+" less than the target.",steps:steps,
    worth:saved>0?"Trimming bids "+Math.round(Math.abs(down)*100)+"% on the "+money(reached,cur)+" of their spend that bids reach would save up to about "+money(saved,cur)+" for the range, with fewer sales from them too: an estimate, since clicks fall with the bid.":null,reachApplies:true};
};

/* ── Putting a plan together ── */
function build(key,products,ctx,mix,o){
  o=o||{};
  var rows=(products||[]).filter(function(i){return !i.other;});
  if(!rows.length||!BUILD[key]) return null;
  var g=groupMix(rows,mix), kind=kindOf(g), env={cur:o.currency||"USD",g:g,kind:kind,mix:mix};
  var plan=BUILD[key](rows,ctx,env);
  plan.kind=kind;
  plan.reach=plan.reachApplies||kind==="unknown"?reachLine(g,kind):"";
  delete plan.reachApplies;
  if(plan.worth==null) delete plan.worth;
  return plan;
}

var api = {KEYS:KEYS, build:build, groupMix:groupMix, kindOf:kindOf, adjustmentFor:adjustmentFor, campaignsOf:campaignsOf, medianIndex:medianIndex};
root.PAPlan = api;
if(typeof module!=="undefined"&&module.exports) module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
