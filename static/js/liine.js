/* Liine Lead Analysis (Paid Marketing Agents).
 *
 * What happens to the new patient leads Liine tracks. The server reads the folder Claude saves the Liine data in and does every sum
 * (tracker/paid_liine_math.py); this file only draws. A figure Liine hid arrives as null and is drawn as a dash, never as 0.
 */
(function(){
"use strict";

var $ = function(s,r){return (r||document).querySelector(s);};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});};
var S = {data:null, pid:"", busy:false};

function n(v){return v==null?"–":Number(v).toLocaleString("en-US");}
function pct(v,d){return v==null?"–":(v*100).toFixed(d==null?0:d)+"%";}
function money(v){return v==null?"–":"$"+Math.round(v).toLocaleString("en-US");}
function pts(v){return v==null?"–":(v>0?"+":"")+v.toFixed(0)+" pts";}
function cls(v,goodWhenUp){return v==null||Math.abs(v)<1?"":((v>0)===goodWhenUp?"ln-good":"ln-bad");}
function ago(h){
  if(h==null) return "";
  if(h<1) return "less than an hour ago";
  if(h<48) return Math.round(h)+" hours ago";
  return Math.round(h/24)+" days ago";
}

function kpi(k,v,d,c){return '<div class="ps-kpi ps-kpi-static" style="--kc:'+(c||"#818cf8")+'"><div class="ps-kpi-h"><span class="k">'+esc(k)+'</span></div><span class="v">'+v+'</span><span class="ps-kd">'+d+'</span><div class="ps-kpi-f"></div></div>';}

function sel(){
  var d=S.data, p=S.pid&&d.practices.filter(function(x){return x.id===S.pid;})[0];
  return p||null;
}

function drawKpis(){
  var d=S.data, p=sel(), h=d.headline;
  var leads=p?p.leads:h.leads, booked=p?p.booked:h.booked, rate=p?p.rate:h.rate;
  var prevRate=p?p.prev.rate:h.prevRate, prevLeads=p?p.prev.leads:h.prevLeads;
  var nc=p?p.notCalled:h.notCalled, ncs=p?p.notCalledShare:h.notCalledShare;
  var spend=p?p.spend:h.spend, cpb=p?p.costPerBooked:h.costPerBooked;
  var mr=p?p.missedRate:h.missedRate;
  var chg=rate!=null&&prevRate!=null?(rate-prevRate)*100:null;
  $("#kpis").innerHTML=
    kpi("New leads",n(leads),"Before: "+n(prevLeads))+
    kpi("Booked",n(booked),"Booking rate "+pct(rate,1)+' <b class="'+cls(chg,true)+'">'+pts(chg)+'</b>',"#34d399")+
    kpi("Never called",pct(ncs),n(nc)+" form leads marked Not Called","#ef4444")+
    kpi("Paid spend",money(spend),(cpb!=null?money(cpb)+" per booked paid lead":"Cost per booked paid lead: –"),"#f59e0b")+
    kpi("Calls to voicemail",pct(mr,1),"Inbound calls in business hours","#38bdf8");
  $("#kpiHint").textContent=(p?p.name+". ":"All practices. ")+"Last 90 days ("+d.asOf.last90[0]+" to "+d.asOf.last90[1]+"), counted by "+(d.asOf.dateBasis==="lead_creation"?"when the lead came in":"when the lead's outcome was decided")+".";
}

function drawFindings(){
  var d=S.data, list=d.findings.filter(function(f){return !S.pid||f.practiceId===S.pid;});
  $("#findHint").textContent=list.length+(list.length===1?" thing":" things")+" found. High means the most patients are being lost.";
  if(!list.length){ $("#findings").innerHTML='<div class="pm-panel ln-none">Nothing stands out for this view. The rules are shown under How this data gets here.</div>'; return; }
  $("#findings").innerHTML=list.map(function(f){
    return '<section class="pm-panel ln-find"><span class="ln-sev '+esc(f.severity)+'">'+esc(f.severity)+'</span><h3>'+esc(f.title)+'</h3><p>'+esc(f.why)+'</p><ul>'+
      f.evidence.map(function(e){return "<li>"+esc(e)+"</li>";}).join("")+'</ul><div class="ln-act"><b>What to do:</b> '+esc(f.action)+'</div></section>';
  }).join("");
}

function drawPractices(){
  var rows=S.data.practices;
  $("#pracTable").innerHTML='<div class="ps-tscroll"><table class="ps-table sa-table"><thead><tr><th>Practice</th><th class="num">Leads</th><th class="num">Booked</th><th class="num">Booking rate</th><th class="num">Change</th>'+
    '<th class="num">Never called</th><th class="num">Calls to voicemail</th><th class="num">Paid spend</th><th class="num">Per booked</th></tr></thead><tbody>'+
    rows.map(function(p){
      return '<tr class="ln-row'+(S.pid===p.id?' on':'')+'" data-pid="'+esc(p.id)+'"><td>'+esc(p.name)+'</td><td class="num">'+n(p.leads)+'</td><td class="num">'+n(p.booked)+'</td><td class="num">'+pct(p.rate,1)+'</td>'+
        '<td class="num '+cls(p.ratePtsChange,true)+'">'+pts(p.ratePtsChange)+'</td><td class="num '+(p.notCalledShare>=0.25?"ln-bad":"")+'">'+pct(p.notCalledShare)+'</td>'+
        '<td class="num">'+pct(p.missedRate,1)+'</td><td class="num">'+money(p.spend)+'</td><td class="num">'+money(p.costPerBooked)+'</td></tr>';
    }).join("")+'</tbody></table></div>';
  Array.prototype.forEach.call(document.querySelectorAll("#pracTable .ln-row"),function(tr){
    tr.addEventListener("click",function(){ setPractice(S.pid===tr.getAttribute("data-pid")?"":tr.getAttribute("data-pid")); });
  });
}

function drawMonths(){
  var d=S.data, pts_=S.pid?d.months.byPractice[S.pid]:d.months.all;
  var max=Math.max.apply(null,pts_.map(function(x){return x.leads||0;}).concat([1]));
  $("#months").innerHTML='<div class="ln-bars">'+pts_.map(function(x){
    var h=x.leads==null?0:Math.max(2,Math.round(x.leads/max*130));
    return '<div class="ln-bar'+(x.partial?" partial":"")+'" title="'+esc(x.month)+': '+n(x.leads)+' leads, '+n(x.booked)+' booked'+(x.partial?" (month still running)":"")+(x.hidden?" ("+x.hidden+" practice(s) hidden by Liine, so this is not the full total)":"")+'">'+
      '<span class="r">'+pct(x.rate)+'</span><i style="height:'+h+'px"></i><span class="m">'+esc(x.month)+(x.partial||x.hidden?"*":"")+'</span><span class="n">'+n(x.leads)+'</span></div>';
  }).join("")+'</div>';
  $("#monthHint").textContent="Bar height is new leads, the figure on top is the share that booked. * means the month is still running or Liine hid a practice's figure for it. A lead can still book after the month ends, so recent months fill in later.";
}

function drawReasons(){
  var d=S.data, src=S.pid?d.detail[S.pid]:d, rows=src.reasons.filter(function(r){return r.group!=="booked";});
  var prev={}; (S.pid?d.detail[S.pid].reasonsPrev:d.reasonsPrev).forEach(function(r){prev[r.reason]=r.leads;});
  var total=0; rows.forEach(function(r){total+=r.leads;});
  var max=rows.length?rows[0].leads:1;
  var label={not_called:"Never called",handling:"Lost in how the call was handled",fit:"Not a fit",nurture:"Still deciding or needs something",other:"Other"};
  $("#reasons").innerHTML='<div class="ln-legend">'+["not_called","handling","fit","nurture","other"].map(function(g){return '<span><b class="g-'+g+'" style="display:inline-block;width:10px;height:10px;border-radius:3px;background:currentColor"></b> '+label[g]+'</span>';}).join("")+'</div>'+
    '<div class="ln-rs"><b>Reason</b><span></span><b>Leads</b><b>Before</b>'+rows.slice(0,14).map(function(r){
      return '<span>'+esc(r.reason)+'</span><span class="t g-'+r.group+'"><i style="width:'+Math.max(2,r.leads/max*100)+'%"></i></span><span>'+n(r.leads)+'</span><span>'+n(prev[r.reason])+'</span>';
    }).join("")+'</div>';
  $("#reasonHint").textContent="Leads that did not book, by the reason Liine recorded, in the last 90 days"+(src.reasonsHidden?". Liine hides a count under 10 for privacy, so "+src.reasonsHidden+" smaller reason(s) are not shown":"")+". The first group is the one the practice can fix fastest.";
}

function drawChannels(){
  var d=S.data, rows=S.pid?d.detail[S.pid].channels:d.channels, all=0;
  rows.forEach(function(r){all+=r.leads;});
  $("#chanTable").innerHTML='<div class="ps-tscroll"><table class="ps-table sa-table"><thead><tr><th>Channel</th><th class="num">Leads</th><th class="num">Share</th><th class="num">Booked</th><th class="num">Booking rate</th></tr></thead><tbody>'+
    rows.map(function(r){
      return '<tr><td>'+esc(r.channel)+(r.hidden?' <span class="ps-hint">(some counts hidden)</span>':"")+'</td><td class="num">'+n(r.leads)+'</td><td class="num">'+pct(all?r.leads/all:null)+'</td><td class="num">'+n(r.rateBooked||null)+'</td><td class="num">'+pct(r.rate,1)+'</td></tr>';
    }).join("")+'</tbody></table></div>';
  $("#chanHint").textContent="First channel Liine credits for the lead. Liine hides a count under 10, so the table can add up to a little less than the practice's total. Not attributed means Liine could not tell where the lead came from.";
}

function drawCampaigns(){
  var d=S.data, rows=S.pid?d.detail[S.pid].campaigns:d.campaigns;
  $("#campTable").innerHTML=rows.length?'<div class="ps-tscroll"><table class="ps-table sa-table"><thead><tr><th>Campaign</th>'+(S.pid?"":"<th>Practice</th>")+'<th class="num">Spend</th><th class="num">Clicks</th><th class="num">Leads</th><th class="num">Booked</th><th class="num">Per lead</th><th class="num">Per booked</th></tr></thead><tbody>'+
    rows.map(function(r){
      var bad=r.spend>=500&&!r.booked;
      return '<tr><td>'+esc(r.campaign)+'</td>'+(S.pid?"":"<td>"+esc(r.practice)+"</td>")+'<td class="num">'+money(r.spend)+'</td><td class="num">'+n(r.clicks)+'</td><td class="num">'+n(r.leads)+'</td><td class="num '+(bad?"ln-bad":"")+'">'+n(r.booked)+'</td><td class="num">'+money(r.costPerLead)+'</td><td class="num">'+money(r.costPerBooked)+'</td></tr>';
    }).join("")+'</tbody></table></div>':'<div class="ln-none">No paid campaign rows for this view.</div>';
  $("#campHint").textContent="Last 90 days, biggest spend first. Red means $500 or more spent and no booked patient. Cost is what Liine imports from Google Ads (and Microsoft Ads or ChatGPT Ads where linked). Leads are counted by the first ad click that brought them.";
}

function drawHow(){
  var d=S.data, r=d.run||{}, a=d.asOf;
  $("#how").innerHTML=
    '<p>Liine has no key this platform can use: its connector signs in as a person, and only Claude can use it. So <b>Claude pulls the data from Liine and saves it here</b>, as Google Sheets in one Drive folder shared with the platform. This page shows the newest pull.</p>'+
    '<p>Showing the pull of <code>'+esc(r.stamp||"?")+'</code> (UTC), read from Liine as <code>'+esc(a.liineUser||"?")+'</code>'+(a.ageHours!=null?", "+esc(ago(a.ageHours)):"")+'. To refresh it, ask Claude: <code>Pull the Liine data again</code> in a Claude session that has the Liine connector.</p>'+
    '<p><b>The rules.</b> Never called: at least '+Math.round(d.thresholds.notCalledShare*100)+'% of a practice’s leads. Booking rate down at least '+d.thresholds.rateDropPts+' points on the 90 days before. A paid campaign that spent $'+d.thresholds.wasteSpendMin+' or more and booked nobody. Voicemail: '+Math.round(d.thresholds.missedRate*100)+'% or more of inbound calls. A channel with at least 100 leads booking at under half the practice’s rate. Every figure is a plain sum of Liine’s numbers; where Liine hid a small count the cell is left empty and is never counted as zero.</p>';
}

function drawNotes(){
  var d=S.data;
  $("#notice").innerHTML=(d.notes||[]).map(function(x){return '<div class="pm-panel ln-none">'+esc(x.text)+'</div>';}).join("");
  var a=d.asOf;
  $("#liveText").textContent="Liine data pulled "+ago(a.ageHours);
  $("#rangeNote").textContent="Last 90 days: "+a.last90[0]+" to "+a.last90[1]+". Before that: "+a.prev90[0]+" to "+a.prev90[1]+".";
}

function drawAll(){ drawKpis(); drawFindings(); drawPractices(); drawMonths(); drawReasons(); drawChannels(); drawCampaigns(); drawHow(); drawNotes(); }

function setPractice(pid){
  S.pid=pid; $("#pracSel").value=pid;
  try{ history.replaceState(null,"",pid?"#p="+encodeURIComponent(pid):location.pathname+location.search); }catch(e){}
  drawAll();
}

function fail(msg){
  $("#dash").hidden=true;
  $("#notice").innerHTML='<div class="pm-panel ln-none">'+esc(msg)+'</div>';
  $("#liveText").textContent="No Liine data";
  $("#live").classList.add("off");
}

function load(force){
  if(S.busy) return; S.busy=true;
  $("#liveText").textContent="Reading the Liine data…";
  fetch("/p2/paid-marketing/api/liine/report"+(force?"?refresh=1":""),{credentials:"same-origin"})
    .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
    .then(function(x){
      S.busy=false;
      if(!x.ok){ fail(x.j&&x.j.error||"The Liine data could not be read."); return; }
      S.data=x.j; $("#live").classList.remove("off");
      var sel_=$("#pracSel");
      sel_.innerHTML='<option value="">All practices</option>'+x.j.practices.map(function(p){return '<option value="'+esc(p.id)+'">'+esc(p.name)+'</option>';}).join("");
      var m=/#p=([0-9]+)/.exec(location.hash||"");
      S.pid=m&&x.j.practices.some(function(p){return p.id===m[1];})?m[1]:"";
      sel_.value=S.pid;
      $("#dash").hidden=false; drawAll();
    })
    .catch(function(){ S.busy=false; fail("The Liine data could not be read. Check your connection and try again."); });
}

document.addEventListener("DOMContentLoaded",function(){
  $("#pracSel").addEventListener("change",function(e){ setPractice(e.target.value); });
  $("#refreshBtn").addEventListener("click",function(){ load(true); });
  load(false);
});
})();
