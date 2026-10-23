/* The Meta views of Ad Strength Analysis ("Ad quality") and Asset Coverage Audit ("Creative coverage"): the HTML for what tracker/paid_meta_creative_analyse.py worked out.
 *
 *   MetaCreative.quality(report)   -> html      the ads that need work, with Meta's own rankings and what to try
 *   MetaCreative.coverage(report)  -> html      the ad sets held to the team defaults, the next steps, the ads Meta stopped
 *
 * These only draw. Which ad is flagged, how badly and what to try is decided on the server; nothing here works out a rating or a gap. Plain functions, so tests/test_meta_creative_js.py
 * runs them in Node.
 */
(function(root){
"use strict";

function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
function int(v){return v==null?"Na":Math.round(v).toLocaleString("en-US");}
function money(v,cur){
  if(v==null) return "Na";
  try{return new Intl.NumberFormat("en-US",{style:"currency",currency:cur||"USD",maximumFractionDigits:0}).format(v);}catch(e){return Math.round(v).toLocaleString("en-US");}
}
function pct(v){return v==null?"Na":Math.round(v*100)+"%";}
function ago(iso){
  var t=new Date(iso), ms=Date.now()-t.getTime();
  if(!iso||isNaN(t.getTime())) return "";
  var m=Math.max(0,Math.round(ms/60000));
  return m<1?"just now":m<60?m+" min ago":m<1440?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
}
var SEV={high:"High",medium:"Medium",low:"Low"};
var RK={0:"good",1:"avg",2:"low",3:"med",4:"bad"};
var FORMAT={video:"Video",image:"Image",carousel:"Carousel",other:"Other"};

function kpi(label,value,sub,cls){
  return '<div class="pd-kpi '+(cls||"")+'"><div class="k">'+esc(label)+'</div><div class="v">'+esc(value)+'</div>'+(sub?'<div class="s">'+esc(sub)+'</div>':"")+'</div>';
}
function head(r,what){
  var a=r.account||{}, w=r.window||{};
  return '<div class="mc-head"><p class="pd-fresh"><span class="live"></span><b>'+esc(a.name||"")+'</b> · Meta Ads · '+esc(what)+(r.readAt?' · read '+esc(ago(r.readAt)):"")+
    (w.from?' · the last '+esc(w.days||30)+' days':"")+(r.stale?' <span class="sg-mcc">Meta is not answering, so this is the copy saved '+esc(ago(r.stale))+'</span>':"")+'</p></div>'+
    (r.warnings&&r.warnings.length?'<p class="pm-note mc-warn">'+r.warnings.map(esc).join(" · ")+'</p>':"");
}
function sevPill(s){return s?'<span class="mc-sev '+esc(s)+'">'+esc(SEV[s]||s)+'</span>':"";}
/* What is wrong, one line each; with `withNext` each line carries what to try for that very problem (an ad can have two). */
function flagList(flags,withNext){
  return (flags||[]).length?'<ul class="mc-flags">'+flags.map(function(f){
    return '<li class="'+esc(f.severity)+'">'+esc(f.text)+(withNext&&f.next?'<span class="mc-try"><b>What to try:</b> '+esc(f.next)+'</span>':"")+'</li>';}).join("")+'</ul>':"";
}
function notes(r){
  return (r.notes||[]).length?'<p class="pd-foot mc-notes">'+r.notes.map(esc).join(" ")+'</p>':"";
}

/* ── Ad quality ───────────────────────────────────────────── */
function rankPills(ranks){
  return '<span class="mc-ranks">'+[["quality","Quality"],["engagement","Engagement"],["conversion","Conversion"]].map(function(k){
    var x=(ranks||{})[k[0]]||{rank:null,label:"Not ranked"};
    return '<span class="mc-rk '+(x.rank==null?"unr":RK[x.rank])+'" title="Meta\'s '+esc(k[1].toLowerCase())+' ranking">'+esc(k[1])+': '+esc(x.label)+'</span>';
  }).join("")+'</span>';
}
function adRow(it,cur,full){
  return '<article class="mc-row">'+
    '<div class="mc-top">'+sevPill(it.severity)+'<span class="mc-name"><b>'+esc(it.name||"(unnamed)")+'</b><span class="mc-sub">'+esc(it.campaign)+(it.adset?' · '+esc(it.adset):"")+' · '+esc(FORMAT[it.format]||"Other")+'</span></span>'+
      '<span class="mc-num"><b>'+esc(money(it.spend,cur))+'</b> spent<span>'+int(it.impressions)+' impressions · seen '+(it.frequency?it.frequency.toFixed(1):"0")+' times each'+(it.results?' · '+int(it.results)+' results':"")+'</span></span></div>'+
    rankPills(it.ranks)+flagList(it.flags,full)+'</article>';
}
function quality(r){
  var s=r.summary||{}, cur=(r.account||{}).currency;
  var out=head(r,"ad quality")+'<p class="mc-headline">'+esc(r.headline||"")+'</p>';
  out+='<div class="pd-strip mc-kpis">'+
    kpi("Live ads",int(s.ads),s.idle?int(s.idle)+" with no delivery":"all delivering")+
    kpi("Ranked by Meta",int(s.ranked),s.notRanked?int(s.notRanked)+" under 500 impressions":"")+
    kpi("Below average",int(s.belowAverage),s.ranked?pct(s.shareBelow)+" of the spend":"",s.belowAverage?"warn":"good")+
    kpi("Worn out",int(s.fatigued),"seen 4 or more times by the same people",s.fatigued?"warn":"good")+
    kpi("Spent, no result",int(s.noResults),"in "+((r.window||{}).days||30)+" days",s.noResults?"warn":"good")+'</div>';
  var fix=r.fixFirst||[];
  out+='<section class="pm-panel"><div class="pm-panel-h"><span class="pm-panel-t">Ads to work on</span><span class="pm-panel-s">worst first, then the dearest</span></div>'+
    (fix.length?fix.map(function(i){return adRow(i,cur,true);}).join(""):'<p class="pm-empty">'+(r.insightsRead===false?"Meta's numbers could not be read, so no ad can be judged.":"Nothing here needs work.")+'</p>')+'</section>';
  var all=(r.items||[]).filter(function(i){return i.impressions>0;}), shown=all.slice(0,60);
  out+='<section class="pm-panel"><div class="pm-panel-h"><span class="pm-panel-t">Every delivering ad</span><span class="pm-panel-s">'+int(all.length)+' ads'+(all.length>shown.length?', the first '+shown.length:"")+'</span></div>'+
    (shown.length?'<div class="mc-table-wrap"><table class="mc-table"><thead><tr><th>Ad</th><th class="r">Spend</th><th class="r">Impressions</th><th class="r">Frequency</th><th class="r">Results</th><th>Meta\'s rankings</th></tr></thead><tbody>'+
      shown.map(function(i){return '<tr><td><b>'+esc(i.name)+'</b><span class="mc-sub">'+esc(i.campaign)+(i.adset?' · '+esc(i.adset):"")+'</span></td><td class="r">'+esc(money(i.spend,cur))+'</td><td class="r">'+int(i.impressions)+'</td><td class="r">'+(i.frequency?i.frequency.toFixed(1):"0")+'</td><td class="r">'+int(i.results)+'</td><td>'+rankPills(i.ranks)+'</td></tr>';}).join("")+'</tbody></table></div>':'<p class="pm-empty">No ad delivered in this window.</p>')+'</section>';
  return out+notes(r);
}

/* ── Creative coverage ────────────────────────────────────── */
function formatChips(list){return (list||[]).map(function(f){return '<span class="mc-fmt">'+esc(FORMAT[f]||f)+'</span>';}).join("");}
function coverage(r){
  var s=r.summary||{}, cur=(r.account||{}).currency, t=r.targets||{ads:3};
  var out=head(r,"creative coverage")+'<p class="mc-headline">'+esc(r.headline||"")+'</p>';
  out+='<div class="pd-strip mc-kpis">'+
    kpi("Live ad sets",int(s.adsets),int(s.ads)+" live ads")+
    kpi("With "+t.ads+" or more ads",int(s.enough),s.adsets?pct(s.shareEnough)+" of ad sets":"",s.adsets&&s.enough<s.adsets?"warn":"good")+
    kpi("On a single ad",int(s.singleAd),"nothing to compare",s.singleAd?"warn":"good")+
    kpi("With a video",int(s.withVideo),"of "+int(s.adsets)+" ad sets")+
    kpi("Stopped by Meta",int(s.stopped),"disapproved or limited",s.stopped?"warn":"good")+'</div>';
  var steps=r.nextSteps||[];
  out+='<section class="pm-panel"><div class="pm-panel-h"><span class="pm-panel-t">Do this first</span><span class="pm-panel-s">most serious first, then the most spend</span></div>'+
    (steps.length?steps.map(function(x){
      return '<article class="mc-row"><div class="mc-top">'+sevPill(x.severity)+'<span class="mc-name"><b>'+esc(x.where)+'</b><span class="mc-sub">'+esc(x.campaign)+'</span></span>'+
        (x.spend?'<span class="mc-num"><b>'+esc(money(x.spend,cur))+'</b> spent</span>':"")+'</div><ul class="mc-flags"><li class="'+esc(x.severity)+'">'+esc(x.text)+'</li></ul><p class="mc-next"><b>What to do:</b> '+esc(x.next)+'</p></article>';}).join("")
      :'<p class="pm-empty">Every ad set has enough ads and nothing is stopped.</p>')+'</section>';
  var sets=r.adsets||[];
  out+='<section class="pm-panel"><div class="pm-panel-h"><span class="pm-panel-t">Ad sets</span><span class="pm-panel-s">'+int(sets.length)+' live</span></div>'+
    (sets.length?'<div class="mc-table-wrap"><table class="mc-table"><thead><tr><th>Ad set</th><th class="r">Live ads</th><th>Formats</th><th class="r">Spend</th><th>What the rules see</th></tr></thead><tbody>'+
      sets.map(function(a){return '<tr><td><b>'+esc(a.name)+'</b><span class="mc-sub">'+esc(a.campaign)+'</span></td><td class="r'+(a.ads<t.ads?" short":"")+'">'+int(a.ads)+'</td><td>'+formatChips(a.formatList)+(a.dynamic?'<span class="mc-fmt dyn">Flexible</span>':"")+'</td><td class="r">'+esc(money(a.spend,cur))+'</td><td>'+(a.flags.length?flagList(a.flags):'<span class="mc-ok">Nothing stands out</span>')+'</td></tr>';}).join("")+'</tbody></table></div>':'<p class="pm-empty">No live ad set.</p>')+'</section>';
  var camps=r.campaigns||[];
  if(camps.length) out+='<section class="pm-panel"><div class="pm-panel-h"><span class="pm-panel-t">Campaigns</span><span class="pm-panel-s">'+int(camps.length)+' live</span></div><div class="mc-table-wrap"><table class="mc-table"><thead><tr><th>Campaign</th><th class="r">Ad sets</th><th class="r">Live ads</th><th>Formats</th><th class="r">Spend</th><th>What the rules see</th></tr></thead><tbody>'+
    camps.map(function(c){return '<tr><td><b>'+esc(c.name)+'</b></td><td class="r">'+int(c.adsets)+'</td><td class="r">'+int(c.ads)+'</td><td>'+formatChips(c.formats)+'</td><td class="r">'+esc(money(c.spend,cur))+'</td><td>'+(c.flags.length?flagList(c.flags):'<span class="mc-ok">Nothing stands out</span>')+'</td></tr>';}).join("")+'</tbody></table></div></section>';
  var st=r.stopped||[];
  if(st.length) out+='<section class="pm-panel"><div class="pm-panel-h"><span class="pm-panel-t">Stopped or limited by Meta</span><span class="pm-panel-s">'+int(st.length)+' ads</span></div>'+
    st.map(function(a){return '<article class="mc-row"><div class="mc-top">'+sevPill(a.severity)+'<span class="mc-name"><b>'+esc(a.name)+'</b><span class="mc-sub">'+esc(a.campaign)+(a.adset?' · '+esc(a.adset):"")+'</span></span><span class="mc-num"><b>'+esc(a.label)+'</b></span></div><p class="mc-next">'+esc(a.next)+'</p></article>';}).join("")+'</section>';
  return out+notes(r);
}

var api={quality:quality,coverage:coverage,esc:esc,money:money};
if(typeof module!=="undefined"&&module.exports) module.exports=api; else root.MetaCreative=api;
})(typeof window!=="undefined"?window:globalThis);
