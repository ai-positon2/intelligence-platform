/* Creative Analysis (Paid Marketing Agents).
 *
 * Pick a Google Ads or Meta ad account. The server sends 90 days of daily rows and each creative (tracker/paid_creative_roas*.py); this page judges them for the dates and
 * filters picked, with the rules in creative_analysis_data.js (CAData): scale, keep running, cut or too early against the account's own average. Built first as Soteri
 * Skin's stand-alone dashboard; the tables, charts and wording are that page's.
 */
(function(){
"use strict";

var CA = window.CAData, API = "/p2/paid-marketing/api/creative-analysis/data";
var $ = function(s){ return document.querySelector(s); };
var esc = function(s){ return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){ return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); };
function store(k, v){ try { if (v === undefined) return localStorage.getItem(k) || ""; localStorage.setItem(k, v); } catch (e) { return ""; } }

var VERDICTS = CA.VERDICTS, VLABEL = CA.VLABEL;
var D = null, G = false, PNAME = "Meta", L = {}, money, money0, moneyK;
var S = {account:"", seq:0, tab:"creatives", verdict:"all", q:"", sort:{creatives:["spend",-1], hooks:["hook",-1], copy:["spend",-1], formats:["spend",-1], seg:["spend",-1]},
         segDelim:"_", segBy:null, minSpendTouched:false, goal:""};
function vcolor(v){ return getComputedStyle($("#ca")).getPropertyValue({Scale:"--scale",Keep:"--keep",Cut:"--cut",Early:"--early"}[v]).trim(); }

/* the words that differ between the platforms and the goals */
function words(){
  var lead = S.goal === "cpl";
  L = G ? {purch: lead ? "Conv." : "Conv.", purchases:"conversions", Purchases:"Conversions", value:"Conv. value", hook:"View rate", hookT:"Video views ÷ impressions",
           v3:"Video views", v3T:"YouTube video views", hold:"Watched to end", holdT:"Share of views watched to the end", cp3:"Cost / view",
           ctrT:"Clicks ÷ impressions", adset:"ad group", adsets:"ad groups", tool:"Google Ads", opening:"First headline", search:"Search name, campaign, headline…",
           cpr: lead ? "Cost / conv." : "CPA"}
        : {purch: lead ? "Leads" : "Purch.", purchases: lead ? "leads" : "purchases", Purchases: lead ? "Leads" : "Purchases", value:"Purchase value",
           hook:"Hook rate", hookT:"3-second video plays ÷ impressions (videos only)", v3:"3-sec views", v3T:"Video plays of at least 3 seconds", hold:"Hold rate",
           holdT:"ThruPlays ÷ 3-second plays: how many keep watching", cp3:"Cost / 3-sec view", ctrT:"Link clicks ÷ impressions", adset:"ad set", adsets:"ad sets",
           tool:"Ads Manager", opening:"Opening line", search:"Search name, campaign, hook…", cpr: lead ? "CPL" : "CPA"};
  L.metric = S.goal === "cpl" ? (G ? "Cost per conversion" : "Cost per lead") : "ROAS";
}

/* ── Loading ─────────────────────────────────────────────── */
function show(which){ $("#empty").hidden = which !== "empty"; $("#skel").hidden = which !== "skel"; $("#app").hidden = which !== "app"; }
function banner(html, kind){ $("#banners").innerHTML = html ? '<div class="ca-banner ' + (kind || "err") + '">' + html + "</div>" : ""; }
function getJson(url){
  return fetch(url, {credentials:"same-origin"}).then(function(r){ return r.json().catch(function(){ return {}; }).then(function(j){ return {ok:r.ok, status:r.status, j:j}; }); });
}

function load(refresh, polls){
  if (!S.account){ show("empty"); banner(""); return; }
  var seq = ++S.seq;
  try { history.replaceState(null, "", location.pathname + "?account=" + encodeURIComponent(S.account)); } catch (e) {}
  if (!D || D.account.id !== S.account) show("skel");
  if (!polls) $("#loadingNote").textContent = "Reading the last 90 days of every ad, then what each one shows. A large account read for the first time takes a few minutes; after that it opens at once.";
  getJson(API + "?account=" + encodeURIComponent(S.account) + (refresh ? "&refresh=1" : "")).then(function(r){
    if (seq !== S.seq) return;
    if (r.ok && r.j.loading){                                         // still reading in the background: ask again
      var mins = r.j.started ? Math.max(0, Math.round((Date.now() - new Date(r.j.started)) / 60000)) : 0;
      $("#loadingNote").textContent = "Still reading " + (r.j.name || "the account") + (mins ? " (started " + mins + " min ago)" : "") + ". This page keeps checking by itself.";
      if (!D || D.account.id !== S.account) show("skel");
      setTimeout(function(){ if (seq === S.seq) load(false, (polls || 0) + 1); }, 6000);
      return;
    }
    refreshDone();
    if (!r.ok){
      show(D && D.account.id === S.account ? "app" : "empty");
      if (r.j && r.j.kind === "cooldown"){ banner(esc(r.j.error), "note"); return; }
      var hint = r.j && r.j.kind === "quota" ? " The platform limits how fast this app can read, so this clears on its own." : "";
      banner(esc((r.j && r.j.error) || ("The data could not be read (HTTP " + r.status + ").")) + hint);
      return;
    }
    take(r.j);
  }).catch(function(){
    if (seq !== S.seq) return;
    refreshDone(); show(D ? "app" : "empty"); banner("The server could not be reached. Try again.");
  });
}

function take(d){
  var fresh = !D || D.account.id !== d.account.id;
  D = d; D._liveCamps = null;
  G = D.platform === "google"; PNAME = G ? "Google Ads" : "Meta";
  if (fresh){ S.goal = CA.defaultGoal(D); S.minSpendTouched = false; S.segBy = null; S.verdict = "all"; S.q = ""; $("#campaign").value = ""; $("#kind").value = ""; }
  words();
  var cur = D.account.currency || "USD";
  money = new Intl.NumberFormat(undefined, {style:"currency", currency:cur, maximumFractionDigits:2});
  money0 = new Intl.NumberFormat(undefined, {style:"currency", currency:cur, maximumFractionDigits:0});
  moneyK = new Intl.NumberFormat(undefined, {style:"currency", currency:cur, notation:"compact", maximumSignificantDigits:3});
  var hasRev = D.rows.some(function(r){ return r[CA.F.rev] > 0; }), hasLeads = D.rows.some(function(r){ return r[CA.F.leads] > 0; });
  $("#goalWrap").style.display = hasRev && hasLeads && !G ? "" : "none";
  $("#goal").value = S.goal;
  $("#goal").options[1].textContent = G ? "Conversions (cost per conversion)" : "Leads (cost per lead)";
  $("#partner").style.display = G ? "none" : "";
  $("#group").options[1].textContent = "Ad name (same creative across " + L.adsets + ")";
  document.querySelectorAll("#ca .mWord").forEach(function(el){ el.textContent = S.goal === "cpl" ? "results per $" : "ROAS"; });
  $("#purchWord").textContent = L.purchases;
  var w = [];
  if (D.warnings && D.warnings.length) w.push(esc(D.warnings.join(" · ")));
  if (D.stale) w.push("The platform is not answering, so this is the copy saved " + esc(new Date(D.stale).toLocaleString()) + ".");
  banner(w.join("<br>"), "note");
  fillCampaigns();
  var kinds = []; D.ads.forEach(function(a){ var k = CA.kindOf(a, G); if (kinds.indexOf(k) < 0) kinds.push(k); });
  var order = function(k){ return G ? 0 : ({Image:0, Video:1}[k] != null ? {Image:0, Video:1}[k] : 2); };
  kinds.sort(function(a, b){ return order(a) - order(b) || a.localeCompare(b); });
  var klabel = G ? {} : {Image:"Image ads (single + flexible)", Video:"Video ads (single + flexible)"};
  var kcur = $("#kind").value;
  $("#kind").innerHTML = '<option value="">All formats</option>' + kinds.map(function(k){ return '<option value="' + esc(k) + '"' + (k === kcur ? " selected" : "") + ">" + esc(klabel[k] || k) + "</option>"; }).join("");
  var last = CA.lastDay(D);
  if (fresh || !$("#until").value){ $("#until").value = last; $("#since").value = CA.shift(last, -29); }
  $("#since").min = $("#until").min = D.since; $("#since").max = $("#until").max = D.until;
  S.segDelim = CA.nameDelim(active().map(function(a){ return a.name; }));
  $("#meta").innerHTML = '<span class="live"></span><b>' + esc(D.account.name) + "</b> · " + PNAME + " · " + active().length + " running " + (G ? "ads and asset groups" : "ads") +
    " · read " + esc(D.readAt ? new Date(D.readAt).toLocaleString() : "just now");
  $("#foot").textContent = "Source: " + (G ? "Google Ads: every ad and Performance Max asset group that cost money in the last " + D.lookbackDays + " days, by day; conversions and conversion value are Google's Conversions and Conv. value columns."
    : "Meta: every ad that spent in the last " + D.lookbackDays + " days, by day; purchases and their value as Ads Manager counts them, or the first lead action for an account that sells nothing online.") +
    " \"Last N days\" ends yesterday in the account's time zone, as in " + L.tool + ". Saved so the same account opens instantly; Refresh reads it again. Nothing is changed in an ad account.";
  show("app");
  try { render(); } catch (e){ banner("The data was read but could not be drawn: " + esc(String(e && e.message || e))); }
}

function active(){ return D.ads.filter(function(a){ return a.status === "ACTIVE"; }); }
/* every campaign, paused ones last and marked, keeping the chosen one when it is still there */
function fillCampaigns(){
  var cur = $("#campaign").value, on = {}, seen = {}, camps = [];
  D.ads.forEach(function(a){ if (!a.campaign) return; if (CA.campaignActive(D, a)) on[a.campaign] = 1; if (!seen[a.campaign]){ seen[a.campaign] = 1; camps.push(a.campaign); } });
  camps.sort(function(a, b){ return (on[b] ? 1 : 0) - (on[a] ? 1 : 0) || a.localeCompare(b); });
  $("#campaign").innerHTML = '<option value="">All campaigns</option>' + camps.map(function(c){
    return '<option value="' + esc(c) + '"' + (c === cur ? " selected" : "") + ">" + esc(c) + (on[c] ? "" : " (paused)") + "</option>"; }).join("");
}

/* ── Judging (CAData) ─────────────────────────────────────── */
function range(){
  var v = $("#range").value;
  if (v === "custom") return [$("#since").value || D.since, $("#until").value || CA.lastDay(D)];
  return CA.presetRange(D, +v);
}
function compute(){
  var r = range();
  var C = CA.compute(D, {since:r[0], until:r[1], campaign:$("#campaign").value, kind:$("#kind").value, partner:G ? "" : $("#partner").value, goal:S.goal,
                         scaleX:$("#scaleX").value, keepX:$("#keepX").value, minRes:$("#minPurch").value, minSpend:S.minSpendTouched ? $("#minSpend").value : null});
  var cpr = C.total.all.cpr;
  if (!S.minSpendTouched) $("#minSpend").value = C.rules.autoMin;
  $("#minSpendNote").textContent = S.minSpendTouched ? "(1.5 × account " + L.cpr + " is " + cost(C.rules.autoMin) + ")" : "(1.5 × the " + cost(cpr) + " account " + L.cpr + ")";
  return C;
}
function creatives(C){ return CA.creatives(C, $("#group").value, function(x){ return money0.format(x); }); }
function judge(t, R){ return CA.judge(t, R, function(x){ return money0.format(x); }); }
function groupBy(list, keyFn, labelFn){ return CA.groupBy(list, keyFn, labelFn, S.goal); }
function adFilterLabel(){
  var k = $("#kind").value, p = G ? "" : $("#partner").value;
  return [p === "1" ? "partnership" : p === "0" ? "brand" : "", k ? k.toLowerCase() : ""].filter(Boolean).join(" ") + " ads";
}

/* ── Formatting ──────────────────────────────────────────── */
function num(x){ return x == null || !x ? "—" : Math.round(x).toLocaleString(); }
function cnt(x){ return Math.round(x || 0).toLocaleString(); }
function big(x){ return Math.abs(x) >= 1e5 ? moneyK.format(x) : Math.abs(x) >= 1000 || !x ? money0.format(x) : money.format(x); }
function cost(x){ return x == null ? "—" : Math.abs(x) >= 100 ? money0.format(x) : money.format(x); }
function pct(x, d){ return x == null ? "—" : (x * 100).toFixed(d == null ? 1 : d) + "%"; }
/* the goal's figure: ROAS, or the cost per result */
function mtext(t){ return S.goal === "cpl" ? cost(t.cpr) : (t.spend ? t.roas.toFixed(2) : "—"); }
function vsAvg(t, avg){ if (!avg || !t.spend) return "—"; var d = t.score / avg - 1; return '<span class="' + (d >= 0 ? "up" : "down") + '">' + (d >= 0 ? "+" : "") + (d * 100).toFixed(0) + "%</span>"; }
function pill(v){ return '<span class="pill v-' + v + '">' + VLABEL[v] + "</span>"; }
function thumb(a){ return a.thumb ? '<img class="thumb" loading="lazy" referrerpolicy="no-referrer" src="' + esc(a.thumb) + '" alt="">' : '<div class="thumb">' + esc(a.format || "") + "</div>"; }
function links(a){
  var out = [];
  if (G){
    if (a.post_url) out.push('<a href="' + esc(a.post_url) + '" target="_blank" rel="noopener">Video</a>');
    if (a.landing) out.push('<a href="' + esc(a.landing) + '" target="_blank" rel="noopener">Landing page</a>');
    return out.join(" · ");
  }
  var act = String(D.account.id || "").replace(/\D/g, "");
  if (act && !/^pmax-/.test(a.id)) out.push('<a href="https://adsmanager.facebook.com/adsmanager/manage/ads?act=' + act + "&selected_ad_ids=" + esc(a.id) + '" target="_blank" rel="noopener">Ads Manager</a>');
  if (a.post_url) out.push('<a href="' + esc(a.post_url) + '" target="_blank" rel="noopener">Post</a>');
  return out.join(" · ");
}

function table(id, cols, rows, key){
  var sk = S.sort[key][0], dir = S.sort[key][1];
  var col = cols.filter(function(c){ return c.k === sk; })[0] || cols[0];
  var val = function(r){ return col.v ? col.v(r) : r[col.k]; };
  rows = rows.slice().sort(function(a, b){ var x = val(a), y = val(b); return ((x == null) - (y == null)) || (x < y ? -1 : x > y ? 1 : 0) * dir; });
  if (!rows.length) return '<div class="empty">Nothing in this view.</div>';
  return '<div class="tablewrap"><table id="' + id + '" data-key="' + key + '"><thead><tr>' + cols.map(function(c){
    return '<th data-k="' + c.k + '" class="' + (c.num ? "num" : "") + " " + (c.k === sk ? "sorted" + (dir > 0 ? " asc" : "") : "") + '"' + (c.title ? ' title="' + esc(c.title) + '"' : "") + ">" + c.h + "</th>"; }).join("") +
    "</tr></thead><tbody>" + rows.map(function(r){ return "<tr>" + cols.map(function(c){ return '<td class="' + (c.num ? "num" : "") + '">' + c.f(r) + "</td>"; }).join("") + "</tr>"; }).join("") +
    "</tbody></table></div>";
}

/* ── Scatter (spend × the goal's figure, or hook rate × it) ─── */
function scatter(points, o){
  var W = 820, H = 300, Lm = 46, Rm = 14, T = 12, B = 34;
  if (!points.length) return '<div class="empty">Nothing to plot.</div>';
  var xs = points.map(function(p){ return p.x; }), ys = points.map(function(p){ return p.y; });
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
  if (o.xlog){ x0 = Math.max(x0, 1); x1 = Math.max(x1, x0 * 10); }
  if (x0 === x1) x1 = x0 + 1;
  var ymax = Math.max.apply(null, ys.concat(o.lines.map(function(l){ return l.y; }))) * 1.08 || 1;
  var fx = function(x){ return Lm + (W - Lm - Rm) * (o.xlog ? (Math.log(Math.max(x, x0)) - Math.log(x0)) / (Math.log(x1) - Math.log(x0)) : (x - x0) / (x1 - x0)); };
  var fy = function(y){ return T + (H - T - B) * (1 - Math.min(y, ymax) / ymax); };
  var s = '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" aria-label="' + esc(o.label) + '">', i;
  for (i = 0; i <= 4; i++){ var yv = ymax * i / 4; s += '<line x1="' + Lm + '" x2="' + (W - Rm) + '" y1="' + fy(yv) + '" y2="' + fy(yv) + '" stroke="var(--line)"/><text x="' + (Lm - 6) + '" y="' + (fy(yv) + 4) + '" text-anchor="end">' + o.yfmt(yv) + "</text>"; }
  var ticks = [];
  if (o.xlog){ for (var p = Math.pow(10, Math.floor(Math.log10(x0))); p <= x1 * 1.01; p *= 10) [1, 3].forEach(function(m){ var v = p * m; if (v >= x0 && v <= x1) ticks.push(v); }); }
  else for (i = 0; i <= 4; i++) ticks.push(x0 + (x1 - x0) * i / 4);
  ticks.forEach(function(v){ s += '<text x="' + fx(v) + '" y="' + (H - B + 16) + '" text-anchor="middle">' + o.xfmt(v) + "</text>"; });
  s += '<text x="' + (W + Lm) / 2 + '" y="' + (H - 2) + '" text-anchor="middle">' + esc(o.xlabel) + "</text>";
  if (o.vline != null) s += '<line x1="' + fx(o.vline) + '" x2="' + fx(o.vline) + '" y1="' + T + '" y2="' + (H - B) + '" stroke="var(--avg)" stroke-dasharray="4 4"/>';
  o.lines.forEach(function(l, k){ s += '<line x1="' + Lm + '" x2="' + (W - Rm) + '" y1="' + fy(l.y) + '" y2="' + fy(l.y) + '" stroke="' + l.c + '" stroke-dasharray="' + (l.dash == null ? "4 4" : l.dash) + '" stroke-width="' + (l.w || 1) + '"/><text x="' + (W - Rm - k * 150) + '" y="' + (fy(l.y) - 4) + '" text-anchor="end" style="fill:' + l.c + '">' + esc(l.t) + "</text>"; });
  points.forEach(function(p){ s += '<circle cx="' + fx(p.x) + '" cy="' + fy(p.y) + '" r="' + (p.r || 5) + '" fill="' + p.c + '" fill-opacity=".8" stroke="var(--bg)" data-tip="' + esc(p.tip) + '"/>'; });
  return s + "</svg>";
}
/* the chart's y value and threshold lines: ROAS, or the cost per result (lower is better, so the lines swap) */
function yOf(t){ return S.goal === "cpl" ? (t.cpr || 0) : t.roas; }
function lines(R){
  if (S.goal !== "cpl") return [{y:R.avg * R.scaleX, c:"var(--scale)", t:"Scale ≥ " + (R.avg * R.scaleX).toFixed(2)}, {y:R.avg, c:"var(--avg)", t:"Average " + R.avg.toFixed(2), dash:"", w:1.5},
                                {y:R.avg * R.keepX, c:"var(--cut)", t:"Cut < " + (R.avg * R.keepX).toFixed(2)}];
  if (!R.avg) return [];
  var a = 1 / R.avg;
  return [{y:a / R.scaleX, c:"var(--scale)", t:"Scale ≤ " + cost(a / R.scaleX)}, {y:a, c:"var(--avg)", t:"Average " + cost(a), dash:"", w:1.5}, {y:a / R.keepX, c:"var(--cut)", t:"Cut > " + cost(a / R.keepX)}];
}

/* ── Tabs ────────────────────────────────────────────────── */
function render(){
  if (!D) return;
  var C = compute(), list = creatives(C), R = C.rules, T = C.total, P = T.paused, A = T.all, F = T.filtered, fl = F ? adFilterLabel() : "";
  var by = function(v){ return list.filter(function(t){ return t.verdict === v; }); };
  var kv = function(cls, l, v, s, full){ return '<div class="kpi ' + cls + '"><div class="l">' + l + '</div><div class="v"' + (full ? ' title="' + esc(full) + '"' : "") + ">" + v + '</div><div class="s">' + (s || "") + "</div></div>"; };
  var vk = function(v){ var a = by(v), sp = a.reduce(function(s, t){ return s + t.spend; }, 0); return kv(v.toLowerCase(), VLABEL[v], cnt(a.length), big(sp) + " · " + (T.spend ? (sp / T.spend * 100).toFixed(0) : 0) + "% of spend"); };
  var lead = S.goal === "cpl";
  $("#kpis").innerHTML = kv("", F ? "Spend · " + esc(fl) : "Spend · all campaigns", big(T.spend),
      C.since + " → " + C.until + (F && A.spend ? " · " + (T.spend / A.spend * 100).toFixed(0) + "% of all spend" : "") + (P.spend >= 1 ? "<br>incl. " + big(P.spend) + " from paused campaigns and ads" : ""), money.format(T.spend))
    + (lead ? kv("", L.Purchases, cnt(T.res), (P.res ? cnt(P.res) + " from paused campaigns and ads" : ""))
            : kv("", L.value, big(T.rev), cnt(T.purch) + " " + L.purchases + (P.purch ? " (" + cnt(P.purch) + " from paused campaigns and ads)" : ""), money.format(T.rev)))
    + (F ? kv("", L.metric + " · " + esc(fl), T.spend ? mtext(T) : "—", lead ? cnt(T.res) + " " + L.purchases : L.cpr + " " + cost(T.cpr)) : "")
    + kv("avg", (F ? "Average " + L.metric + " · all ads" : "Average " + L.metric) + " (benchmark)", A.spend ? mtext(A) : "—", lead ? cnt(A.res) + " " + L.purchases : L.cpr + " " + cost(A.cpr))
    + VERDICTS.map(vk).join("");
  renderCreatives(C, list); renderHooks(C, list); renderFormats(C, list);
}

function renderCreatives(C, list){
  var R = C.rules, counts = {};
  VERDICTS.forEach(function(v){ counts[v] = list.filter(function(t){ return t.verdict === v; }).length; });
  var rows = list.filter(function(t){ return S.verdict === "all" || t.verdict === S.verdict; });
  if (S.q){ var q = S.q.toLowerCase(); rows = rows.filter(function(t){ return (t.ad.name + " " + t.ad.campaign + " " + t.ad.adset + " " + (t.ad.hook || "")).toLowerCase().indexOf(q) >= 0; }); }
  var pts = list.filter(function(t){ return t.spend > 0 && (S.goal !== "cpl" || t.cpr); }).map(function(t){
    return {x:t.spend, y:yOf(t), c:vcolor(t.verdict), r:4 + Math.min(6, Math.sqrt(t.res)), tip:t.ad.name + " — " + VLABEL[t.verdict] + "\nSpend " + money0.format(t.spend) + " · " + L.metric + " " + mtext(t) + " · " + cnt(t.res) + " " + L.purchases}; });
  var multi = $("#group").value === "name";
  var cols = [
    {k:"thumb", h:"", f:function(t){ return thumb(t.ad); }, v:function(){ return 0; }},
    {k:"name", h:"Creative", v:function(t){ return t.ad.name.toLowerCase(); }, f:function(t){
      return '<div class="name">' + esc(t.ad.name) + '</div><div class="small">' + esc(t.ad.format) +
        (t.ad.partnership ? ' · <span style="color:var(--avg)">Partnership' + (t.ad.partnership_ids ? " (" + esc(t.ad.partnership_ids) + ")" : "") + "</span>" : "") +
        (t.ad.cta && !G ? " · " + esc(t.ad.cta) : "") + " · " + esc(t.ad.campaign) + (multi && t.ads.length > 1 ? " · in " + t.ads.length + " " + L.adsets : " · " + esc(t.ad.adset)) + "</div>" +
        (G && t.ad.hook ? '<div class="small hook">“' + esc(t.ad.hook) + "”</div>" : "") + '<div class="small">' + links(t.ad) + "</div>"; }},
    {k:"verdict", h:"Verdict", v:function(t){ return VERDICTS.indexOf(t.verdict); }, f:function(t){ return pill(t.verdict) + '<div class="small" style="max-width:220px">' + esc(t.why) + "</div>"; }},
    {k:"spend", h:"Spend", num:1, f:function(t){ return money0.format(t.spend); }},
  ].concat(S.goal === "cpl" ? [
    {k:"res", h:L.purch, num:1, f:function(t){ return cnt(t.res); }},
    {k:"cpr", h:L.cpr, num:1, v:function(t){ return t.cpr == null ? null : -t.cpr; }, f:function(t){ return cost(t.cpr); }},
    {k:"ratio", h:"vs avg", num:1, f:function(t){ return vsAvg(t, R.avg); }},
  ] : [
    {k:"rev", h:L.value, num:1, f:function(t){ return money0.format(t.rev); }},
    {k:"roas", h:"ROAS", num:1, f:function(t){ return t.roas.toFixed(2); }},
    {k:"ratio", h:"vs avg", num:1, f:function(t){ return vsAvg(t, R.avg); }},
    {k:"purch", h:L.purch, num:1, f:function(t){ return cnt(t.purch); }},
    {k:"cpr", h:L.cpr, num:1, f:function(t){ return cost(t.cpr); }},
  ]).concat([
    {k:"ctr", h:"CTR", num:1, title:L.ctrT, f:function(t){ return pct(t.ctr, 2); }},
    {k:"hook", h:L.hook, num:1, title:L.hookT, f:function(t){ return pct(t.hook); }},
    {k:"v3", h:L.v3, num:1, title:L.v3T, f:function(t){ return num(t.v3); }},
  ]);
  if (!cols.some(function(c){ return c.k === S.sort.creatives[0]; })) S.sort.creatives = ["spend", -1];
  $("#tab-creatives").innerHTML =
    '<div class="card"><h2>Spend vs ' + L.metric + '</h2><p class="sub">Every ' + (G ? "ad and Performance Max asset group" : "creative") + " with spend in the range. " +
      (S.goal === "cpl" ? "Below the green line: scale. Above the red line: cut." : "Above the green line: scale. Below the red line: cut.") + " Bigger dots have more " + L.purchases + ".</p>" +
      scatter(pts, {xlog:true, label:"Spend vs " + L.metric, xlabel:"Spend (log scale)", xfmt:function(v){ return money0.format(v); }, yfmt:function(v){ return S.goal === "cpl" ? cost(v) : v.toFixed(1); }, lines:lines(R), vline:R.minSpend}) +
      '<div class="legend">' + VERDICTS.map(function(v){ return '<span><i style="background:' + vcolor(v) + '"></i>' + VLABEL[v] + "</span>"; }).join("") + "<span>Dashed vertical line: spend needed before judging</span></div></div>" +
    '<div class="card"><div class="chips"><button type="button" class="chip ' + (S.verdict === "all" ? "on" : "") + '" data-v="all">All ' + list.length + "</button>" +
      VERDICTS.map(function(v){ return '<button type="button" class="chip ' + (S.verdict === v ? "on" : "") + '" data-v="' + v + '">' + VLABEL[v] + " " + counts[v] + "</button>"; }).join("") +
      '<input type="search" id="q" placeholder="' + L.search + '" value="' + esc(S.q) + '" style="margin-left:auto;min-width:220px"></div>' + table("t-cre", cols, rows, "creatives") + "</div>";
}

function renderHooks(C, list){
  var R = C.rules, lead = S.goal === "cpl";
  var vids = list.filter(function(t){ return t.hook != null && t.impr >= 1000; });
  var tot = vids.reduce(function(a, t){ a.v3 += t.v3; a.impr += t.impr; a.thru += t.thru; a.spend += t.spend; return a; }, {v3:0, impr:0, thru:0, spend:0});
  var avgHook = tot.impr ? tot.v3 / tot.impr : 0, avgHold = tot.v3 ? tot.thru / tot.v3 : 0;
  var converts = function(t){ return t.score >= R.avg * R.keepX; };
  var quad = function(t){ return (t.hook >= avgHook ? "Strong hook" : "Weak hook") + ", " + (converts(t) ? "converts" : "doesn't convert"); };
  var advice = {"Strong hook, converts":"Winner: scale and make variations of this opening", "Strong hook, doesn't convert":"Stops the scroll, loses them after: fix the body, offer or landing page",
                "Weak hook, converts":"Sells once watched: test new first 3 seconds", "Weak hook, doesn't convert":"Neither works: cut"};
  var pts = vids.filter(function(t){ return !lead || t.cpr; }).map(function(t){ return {x:t.hook, y:yOf(t), c:vcolor(t.verdict), r:4 + Math.min(6, Math.sqrt(t.res)),
    tip:t.ad.name + "\n" + L.hook + " " + pct(t.hook) + " · " + L.hold + " " + pct(t.hold) + " · " + L.metric + " " + mtext(t) + "\n" + quad(t)}; });
  var vcols = [
    {k:"thumb", h:"", f:function(t){ return thumb(t.ad); }, v:function(){ return 0; }},
    {k:"name", h:"Video", v:function(t){ return t.ad.name.toLowerCase(); }, f:function(t){ return '<div class="name">' + esc(t.ad.name) + '</div><div class="small hook">' + esc(t.ad.hook ? "“" + t.ad.hook + "”" : "") + "</div>"; }},
    {k:"hook", h:L.hook, num:1, title:L.hookT, f:function(t){ return '<span class="' + (t.hook >= avgHook ? "up" : "down") + '">' + pct(t.hook) + "</span>"; }},
    {k:"hold", h:L.hold, num:1, title:L.holdT, f:function(t){ return '<span class="' + (t.hold >= avgHold ? "up" : "down") + '">' + pct(t.hold) + "</span>"; }},
    {k:"v3", h:L.v3, num:1, f:function(t){ return num(t.v3); }},
    {k:"cp3", h:L.cp3, num:1, f:function(t){ return t.cp3 ? money.format(t.cp3) : "—"; }},
    {k:"ctr", h:"CTR", num:1, f:function(t){ return pct(t.ctr, 2); }},
    {k:"score", h:L.metric, num:1, f:function(t){ return mtext(t); }},
    {k:"spend", h:"Spend", num:1, f:function(t){ return money0.format(t.spend); }},
    {k:"q", h:"Read", v:function(t){ return quad(t); }, f:function(t){ return "<b>" + quad(t) + '</b><div class="small" style="max-width:240px">' + advice[quad(t)] + "</div>"; }},
    {k:"verdict", h:"Verdict", v:function(t){ return VERDICTS.indexOf(t.verdict); }, f:function(t){ return pill(t.verdict); }},
  ];
  var copy = groupBy(C.perAd.filter(function(t){ return t.ad.hook; }), function(t){ return t.ad.hook.toLowerCase(); }, function(t){ return t.ad.hook; });
  copy.forEach(function(g){ judge(g, R); var f = []; g.ads.forEach(function(a){ if (f.indexOf(a.format) < 0) f.push(a.format); }); g.formats = f.join(", "); });
  var ccols = [
    {k:"label", h:G ? "First headline" : "Opening line of the primary text", v:function(g){ return g.label.toLowerCase(); }, f:function(g){ return '<div class="hook">“' + esc(g.label) + '”</div><div class="small">' + g.ads.length + " ad" + (g.ads.length > 1 ? "s" : "") + " · " + esc(g.formats) + "</div>"; }},
    {k:"spend", h:"Spend", num:1, f:function(g){ return money0.format(g.spend); }},
    {k:"score", h:L.metric, num:1, f:function(g){ return mtext(g); }},
    {k:"ratio", h:"vs avg", num:1, f:function(g){ return vsAvg(g, R.avg); }},
    {k:"res", h:L.purch, num:1, f:function(g){ return cnt(g.res); }},
    {k:"ctr", h:"CTR", num:1, f:function(g){ return pct(g.ctr, 2); }},
    {k:"hook", h:L.hook, num:1, f:function(g){ return pct(g.hook); }},
    {k:"verdict", h:"Verdict", v:function(g){ return VERDICTS.indexOf(g.verdict); }, f:function(g){ return pill(g.verdict); }},
  ];
  $("#tab-hooks").innerHTML =
    '<div class="card"><h2>' + (G ? "YouTube and Demand Gen videos: do people watch, and does it sell?" : "Video hooks: does the first 3 seconds stop the scroll, and does it sell?") + "</h2>" +
      '<p class="sub">Videos with at least 1,000 impressions: <b>' + num(tot.v3) + "</b> " + (G ? "video views" : "3-second views") + " at <b>" + (tot.v3 ? money.format(tot.spend / tot.v3) : "—") + "</b> each. Average " +
      L.hook.toLowerCase() + " <b>" + pct(avgHook) + "</b> (vertical line), average " + L.hold.toLowerCase() + " <b>" + pct(avgHold) + "</b>. \"Converts\" means " + L.metric + " better than the Cut line." +
      (G ? " Performance Max doesn't report video views, so its asset groups aren't here." : "") + "</p>" +
      scatter(pts, {xlog:false, label:L.hook + " vs " + L.metric, xlabel:L.hook + " (" + L.hookT.toLowerCase() + ")", xfmt:function(v){ return pct(v, 0); }, yfmt:function(v){ return lead ? cost(v) : v.toFixed(1); },
                    lines:lines(R).slice(1), vline:avgHook}) + table("t-vid", vcols, vids, "hooks") + "</div>" +
    '<div class="card"><h2>' + (G ? "Headlines: which first headline makes money" : "Copy hooks: which opening lines make money") + "</h2>" +
      '<p class="sub">' + (G ? "Ads and asset groups grouped by the headline that shows first (the one pinned to position 1, else the first written), judged with the same rules. Google mixes headlines in its own order, so this is a lead, not proof."
        : "Ads grouped by the first line of their primary text (what shows before \"See more\"), judged with the same rules.") + "</p>" + table("t-copy", ccols, copy, "copy") + "</div>";
}

function renderFormats(C, list){
  var R = C.rules, T = C.total, lead = S.goal === "cpl";
  function breakdown(id, label, keyOf){
    var gs = groupBy(C.perAd, function(t){ return keyOf(t.ad); }).filter(function(g){ return g.spend >= 1; });
    gs.forEach(function(g){ judge(g, R); g.n = g.ads.length; g.verdicts = VERDICTS.map(function(v){ return [v, list.filter(function(t){ return t.ads.some(function(a){ return keyOf(a) === g.key; }) && t.verdict === v; }).length]; }); });
    var maxS = Math.max.apply(null, gs.map(function(g){ return g.score; }).concat([R.avg])) || 1;
    var cols = [
      {k:"key", h:label, f:function(g){ return "<b>" + esc(g.key) + '</b><div class="small">' + g.n + " ad" + (g.n > 1 ? "s" : "") + "</div>"; }},
      {k:"spend", h:"Spend", num:1, f:function(g){ return money0.format(g.spend) + '<div class="small">' + (T.spend ? (g.spend / T.spend * 100).toFixed(0) : 0) + "% of total</div>"; }},
      {k:"score", h:L.metric, f:function(g){ return '<div class="barcell"><div style="display:flex;justify-content:space-between"><b>' + mtext(g) + "</b>" + vsAvg(g, R.avg) + "</div>" +
        '<div style="position:relative"><div class="b" style="width:' + (g.score / maxS * 100) + "%;background:" + vcolor(g.verdict) + '"></div>' +
        '<div style="position:absolute;top:-3px;left:' + (R.avg / maxS * 100) + '%;width:2px;height:14px;background:var(--avg)" title="Average"></div></div></div>'; }},
      {k:"cpr", h:L.cpr, num:1, f:function(g){ return cost(g.cpr); }},
      {k:"ctr", h:"CTR", num:1, f:function(g){ return pct(g.ctr, 2); }},
      {k:"cvr", h:"Conv. rate", num:1, title:G ? "Conversions ÷ clicks" : (lead ? "Leads" : "Purchases") + " ÷ link clicks", f:function(g){ return pct(g.cvr, 2); }},
      {k:"v3", h:L.v3, num:1, f:function(g){ return num(g.v3); }},
      {k:"hook", h:L.hook, num:1, f:function(g){ return pct(g.hook); }},
      {k:"mix", h:"Creatives by verdict", v:function(g){ return g.verdicts[0][1]; }, f:function(g){ return g.verdicts.filter(function(x){ return x[1]; }).map(function(x){ return '<span class="pill v-' + x[0] + '" style="margin:1px">' + x[1] + " " + VLABEL[x[0]].toLowerCase() + "</span>"; }).join(" "); }},
    ];
    if (!S.sort[id]) S.sort[id] = ["spend", -1];
    return table("t-" + id, cols, gs, id);
  }
  var unknownPartner = C.perAd.filter(function(t){ return t.ad.partnership == null; }).length;
  var ugcNotPartner = C.perAd.filter(function(t){ return CA.isUgc(t.ad.name) && t.ad.partnership === false; });
  var act = active();
  var maxSeg = Math.max.apply(null, [1].concat(act.map(function(a){ return a.name.split(S.segDelim).length; })));
  var tagCount = {};
  act.forEach(function(a){ Object.keys(CA.tagsOf(a.name)).forEach(function(k){ tagCount[k] = (tagCount[k] || 0) + 1; }); });
  var tagKeys = Object.keys(tagCount).sort(function(a, b){ return tagCount[b] - tagCount[a]; });
  if (S.segBy == null){
    var tagged = C.perAd.filter(function(t){ return CA.tagsOf(t.ad.name).C; }).reduce(function(s, t){ return s + t.spend; }, 0);
    S.segBy = tagKeys.indexOf("C") >= 0 && tagged >= C.total.spend / 2 ? "tag:C" : maxSeg > 2 ? "pos:2" : "pos:0";
  }
  if (S.segBy.indexOf("pos:") === 0 && +S.segBy.slice(4) >= maxSeg) S.segBy = "pos:0";
  var byTag = S.segBy.indexOf("tag:") === 0, tag = S.segBy.slice(4), pos = +S.segBy.slice(4);
  var partOf = function(a){ if (byTag) return CA.tagsOf(a.name)[tag] || null; var p = a.name.split(S.segDelim); return p.length > pos ? p[pos].trim() || null : null; };
  var segOf = function(t){ return partOf(t.ad); };
  var ex = []; act.map(partOf).forEach(function(x){ if (x && ex.indexOf(x) < 0 && ex.length < 4) ex.push(x); });
  var untagged = C.perAd.filter(function(t){ return !segOf(t); });
  var seg = groupBy(C.perAd, segOf).filter(function(g){ return g.spend >= 1; });
  seg.forEach(function(g){ judge(g, R); g.n = g.ads.length; });
  var scols = [
    {k:"key", h:"Name part", f:function(g){ return "<b>" + esc(g.key) + '</b><div class="small">' + g.n + " ad" + (g.n > 1 ? "s" : "") + "</div>"; }},
    {k:"spend", h:"Spend", num:1, f:function(g){ return money0.format(g.spend); }},
    {k:"score", h:L.metric, num:1, f:function(g){ return mtext(g); }},
    {k:"ratio", h:"vs avg", num:1, f:function(g){ return vsAvg(g, R.avg); }},
    {k:"res", h:L.purch, num:1, f:function(g){ return cnt(g.res); }},
    {k:"ctr", h:"CTR", num:1, f:function(g){ return pct(g.ctr, 2); }},
    {k:"hook", h:L.hook, num:1, f:function(g){ return pct(g.hook); }},
    {k:"verdict", h:"Verdict", v:function(g){ return VERDICTS.indexOf(g.verdict); }, f:function(g){ return pill(g.verdict); }},
  ];
  var delims = [["_","underscore _"],["|","pipe |"],[" - ","spaced dash  - "],["-","dash -"]];
  $("#tab-formats").innerHTML =
    '<div class="card"><h2>Ad formats</h2><p class="sub">Spend and return by format. The purple tick is the account average (' + mtext(T.all) + ").</p>" + breakdown("formats", "Format", function(a){ return a.format || "Other"; }) + "</div>" +
    (G ? "" : '<div class="card"><h2>Partnership ads vs brand ads</h2><p class="sub">Partnership ads run from the brand together with a creator\'s Facebook or Instagram identity (Meta\'s branded-content setting on the creative). ' +
        (ugcNotPartner.length ? '<b style="color:var(--early)">' + ugcNotPartner.length + " running ad" + (ugcNotPartner.length > 1 ? "s" : "") + " named UGC " + (ugcNotPartner.length > 1 ? "are" : "is") + " not set up as a partnership ad.</b> " : "") +
        (unknownPartner ? "Meta's setting wasn't read for " + unknownPartner + " ad" + (unknownPartner > 1 ? "s" : "") + " (shown as \"Not known\"): only the " + D.extrasMax + " biggest creatives are read one by one." : "") + "</p>" +
        breakdown("partner", "Ad type", CA.partLabel) + "</div>" +
      '<div class="card"><h2>Call-to-action buttons</h2><p class="sub">Which button the ad shows. Flexible ads with several buttons are counted under the first one.</p>' + breakdown("cta", "Button", function(a){ return a.cta || "Not known"; }) + "</div>") +
    '<div class="card"><h2>Angles, hooks and creators from ad names</h2><p class="sub">Compare ads by a tag in their name (<code>C:UGC</code>, <code>X:Before-After</code>…), or by the part in a fixed position for names without tags.</p>' +
      '<div class="chips"><label class="small">Compare by <select id="segBy">' +
        (tagKeys.length ? '<optgroup label="Name tags">' + tagKeys.map(function(k){ return '<option value="tag:' + esc(k) + '"' + (S.segBy === "tag:" + k ? " selected" : "") + ">" + esc(k) + ": " + esc(CA.TAGS[k] || "tag") + " (" + tagCount[k] + " ads)</option>"; }).join("") + "</optgroup>" : "") +
        '<optgroup label="Part of the name by position">' + Array.apply(null, {length:maxSeg}).map(function(_, i){ return '<option value="pos:' + i + '"' + (S.segBy === "pos:" + i ? " selected" : "") + ">Part #" + (i + 1) + "</option>"; }).join("") + "</optgroup></select></label>" +
        (byTag ? "" : '<label class="small">split on <select id="segDelim">' + delims.map(function(d){ return '<option value="' + esc(d[0]) + '"' + (d[0] === S.segDelim ? " selected" : "") + ">" + esc(d[1]) + "</option>"; }).join("") + "</select></label>") +
        '<span class="small">e.g. ' + esc(ex.join(", ")) + "</span>" +
        (untagged.length ? '<span class="small">· ' + untagged.length + " ad" + (untagged.length > 1 ? "s" : "") + " with spend have no " + (byTag ? esc(tag) + ": tag" : "part #" + (pos + 1)) + " and are left out (" + money0.format(untagged.reduce(function(s, t){ return s + t.spend; }, 0)) + ")</span>" : "") +
      "</div>" + table("t-seg", scols, seg, "seg") + "</div>";
}

/* ── CSV of the creatives view ───────────────────────────── */
function csv(){
  if (!D) return;
  var C = compute(), list = creatives(C);
  var head = ["Ad name","Ad ID","Status","Format","Campaign",G ? "Ad group" : "Ad set",L.opening,"Verdict","Why","Spend",L.value,"ROAS",L.Purchases,L.cpr,L.metric + " vs avg","CTR",L.hook,L.hold,L.v3,L.cp3].concat(G ? ["Landing page"] : ["Partnership ad","CTA button"]);
  var lines = [head].concat(list.sort(function(a, b){ return b.spend - a.spend; }).map(function(t){
    return [t.ad.name, t.ads.map(function(a){ return a.id; }).join(" "), t.ad.status, t.ad.format, t.ad.campaign, t.ad.adset, t.ad.hook || "", VLABEL[t.verdict], t.why,
      +t.spend.toFixed(2), +t.rev.toFixed(2), +t.roas.toFixed(2), Math.round(t.res), t.cpr ? +t.cpr.toFixed(2) : "", +(t.ratio || 0).toFixed(2), (t.ctr * 100).toFixed(2) + "%",
      t.hook != null ? (t.hook * 100).toFixed(1) + "%" : "", t.hold != null ? (t.hold * 100).toFixed(1) + "%" : "", Math.round(t.v3), t.cp3 ? +t.cp3.toFixed(3) : ""]
      .concat(G ? [t.ad.landing || ""] : [CA.partLabel(t.ad), t.ad.cta || ""]); }));
  var blob = new Blob(["﻿" + lines.map(function(l){ return l.map(CA.csvCell).join(","); }).join("\r\n")], {type:"text/csv;charset=utf-8"});
  var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "creative-analysis-" + D.account.name.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "") + "-" + C.since + "_" + C.until + ".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

/* ── Events ──────────────────────────────────────────────── */
["#range","#campaign","#kind","#partner","#group","#since","#until","#scaleX","#keepX","#minPurch"].forEach(function(s){ $(s).addEventListener("change", function(){
  $("#customDates").style.display = $("#range").value === "custom" ? "" : "none"; render(); }); });
["#since","#until"].forEach(function(s){
  var el = $(s);
  el.addEventListener("click", function(){ try { el.showPicker(); } catch (e) {} });
  el.addEventListener("keydown", function(e){ if (e.key !== "Tab") e.preventDefault(); });
  el.addEventListener("change", function(){ $("#until").min = $("#since").value || D.since; $("#since").max = $("#until").value || D.until; });
});
$("#range").addEventListener("change", function(){ if ($("#range").value === "custom") setTimeout(function(){ try { $("#since").showPicker(); } catch (e) {} }, 0); });
$("#minSpend").addEventListener("change", function(){ S.minSpendTouched = true; render(); });
$("#goal").addEventListener("change", function(){ S.goal = $("#goal").value; S.minSpendTouched = false; words(); take(D); });
$("#tabs").addEventListener("click", function(e){ var b = e.target.closest("button"); if (!b) return; S.tab = b.dataset.tab;
  document.querySelectorAll("#tabs button").forEach(function(x){ x.classList.toggle("on", x === b); });
  ["creatives","hooks","formats"].forEach(function(t){ $("#tab-" + t).style.display = t === S.tab ? "" : "none"; }); });
$("#app").addEventListener("click", function(e){
  var chip = e.target.closest(".chip[data-v]"); if (chip){ S.verdict = chip.dataset.v; render(); return; }
  var th = e.target.closest("th[data-k]"); if (th){ var key = th.closest("table").dataset.key, k = th.dataset.k; S.sort[key] = [k, S.sort[key][0] === k ? -S.sort[key][1] : -1]; render(); }
});
$("#app").addEventListener("change", function(e){
  if (e.target.id === "segDelim"){ S.segDelim = e.target.value; S.segBy = "pos:0"; render(); }
  if (e.target.id === "segBy"){ S.segBy = e.target.value; render(); }
});
$("#app").addEventListener("input", function(e){ if (e.target.id === "q"){ S.q = e.target.value; var p = e.target.selectionStart; render(); var q = $("#q"); q.focus(); q.setSelectionRange(p, p); } });
var tip = $("#tip");
document.addEventListener("mousemove", function(e){ var t = e.target.closest && e.target.closest("#ca [data-tip]");
  if (!t){ tip.style.display = "none"; return; }
  tip.style.display = "block"; tip.textContent = t.dataset.tip;
  tip.style.left = Math.min(e.clientX + 12, innerWidth - 330) + "px"; tip.style.top = (e.clientY + 12) + "px"; });
$("#csv").addEventListener("click", csv);
function refreshDone(){ var b = $("#refresh"); b.disabled = false; b.querySelector("span").textContent = "Refresh"; }
$("#refresh").addEventListener("click", function(){ var b = $("#refresh"); b.disabled = true; b.querySelector("span").textContent = "Refreshing…"; load(true); });

/* ── Start ───────────────────────────────────────────────── */
(function start(){
  var want = new URLSearchParams(location.search).get("account") || store("ca_account") || "";
  window.PMPlatform.accounts($("#acctPick"), {url:"/p2/paid-marketing/api/gads/accounts", allow:["google","meta"], platform:/^act_/.test(want) ? "meta" : "google", account:want,
    onChange:function(acct){ S.account = acct ? acct.id : ""; if (S.account) store("ca_account", S.account); D = null; load(); },
    onError:function(e){ banner(esc((e && e.message) || "The account list could not be read.")); }});
})();
})();
