/* Asset Coverage Audit: how one asset is drawn (Paid Marketing Agents).
 *
 * The server sends each asset as a small object (tracker/paid_assets_detail.py content()): t type, x main text, d lines under it,
 * v values, u link, img picture, w h b size, vid YouTube id, s e dates, o offers, off promotion terms, code, occ, cta, bn, cc, n name;
 * and st (live | pending | disapproved | expired | paused), why, lv (where it is linked), i k (last 30 days), auto.
 * This turns one into a card (an image shows the image, a sitelink its text and lines, a video its thumbnail), or a short chip for the
 * strip under a table row. It draws what it is given: it never judges. Pictures load only from Google's own hosts.
 */
(function(root){
"use strict";

var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var num = function(v){return Math.round(Number(v)||0).toLocaleString("en-US");};

function safeHttp(u){return /^https?:\/\/[^\s]+$/i.test(u||"")?u:"";}
function safeImg(u){
  var m=/^https:\/\/([A-Za-z0-9.-]+)(?:[\/?#]|$)/.exec(u||""); if(!m||/\s/.test(u)) return "";
  return /(^|\.)(googlesyndication\.com|googleusercontent\.com|ggpht\.com)$/i.test(m[1])?u:"";
}
function safeVideo(id){return /^[A-Za-z0-9_-]{6,20}$/.test(id||"")?id:"";}
function shortUrl(u){var s=String(u||"").replace(/^https?:\/\//i,"").split(/[?#]/)[0].replace(/\/$/,""); return s.length>64?s.slice(0,61)+"…":s;}
function bytes(b){b=Number(b)||0; return !b?"":b<1024?b+" B":b<1048576?Math.round(b/1024)+" KB":(b/1048576).toFixed(1)+" MB";}

var STATE = {live:["Live","live"], pending:["Under review","pending"], disapproved:["Disapproved","bad"], expired:["Past end date","bad"], paused:["Paused","muted"]};
var LEVEL = {campaign:"This campaign", account:"Account-wide", "asset group":"This asset group", ad:"This ad"};
var TYPE = {IMAGE:"Image", SITELINK:"Sitelink", CALLOUT:"Callout", STRUCTURED_SNIPPET:"Structured snippet", CALL:"Call", PROMOTION:"Promotion", PRICE:"Price",
  LEAD_FORM:"Lead form", TEXT:"Text", YOUTUBE_VIDEO:"Video", CALL_TO_ACTION:"Call to action", SET:"Location"};

function dates(t){
  if(t.s&&t.e) return "Runs "+t.s+" to "+t.e;
  if(t.e) return "Ends "+t.e;
  if(t.s) return "Starts "+t.s;
  return "";
}
function chips(list){return list.filter(Boolean).map(function(c){return '<span class="ac-t'+(c[1]?" "+c[1]:"")+'">'+esc(c[0])+'</span>';}).join(" ");}

/* the picture, video thumbnail or first words of an asset */
function media(t){
  if(t.t==="IMAGE"){
    var src=safeImg(t.img);
    return src?'<button type="button" class="ac-img" data-zoom="'+esc(src)+'" data-cap="'+esc(t.n||"Image")+'" aria-label="Enlarge '+esc(t.n||"image")+'"><img src="'+esc(src)+'" alt="'+esc(t.n||"Image asset")+'" loading="lazy" referrerpolicy="no-referrer"></button>'
      :'<div class="ac-img none">No preview from Google</div>';
  }
  if(t.t==="YOUTUBE_VIDEO"){
    var id=safeVideo(t.vid);
    return id?'<a class="ac-img vid" href="https://www.youtube.com/watch?v='+esc(id)+'" target="_blank" rel="noopener noreferrer" aria-label="Open the video on YouTube"><img src="https://i.ytimg.com/vi/'+esc(id)+'/hqdefault.jpg" alt="'+esc(t.x||"Video")+'" loading="lazy" referrerpolicy="no-referrer"><i aria-hidden="true"></i></a>'
      :'<div class="ac-img none">No video id</div>';
  }
  return "";
}

/* the words and facts of an asset */
function body(t){
  var h="", T=t.t;
  if(T==="IMAGE"){
    h+='<b class="ac-tt">'+esc(t.n||"Image")+'</b>';
    h+='<div class="ac-tm">'+chips([t.w&&t.h?[t.w+" × "+t.h+" px"]:null, t.b?[bytes(t.b)]:null])+'</div>';
  } else if(T==="YOUTUBE_VIDEO"){
    h+='<b class="ac-tt">'+esc(t.x||"YouTube video")+'</b>'+(t.vid?'<div class="ac-tm">'+chips([["YouTube "+t.vid]])+'</div>':"");
  } else if(T==="SITELINK"){
    h+='<b class="ac-tt">'+esc(t.x||"(no text)")+'</b>'+(t.d||[]).map(function(l){return '<p class="ac-td">'+esc(l)+'</p>';}).join("");
    if(safeHttp(t.u)) h+='<a class="ac-url" href="'+esc(t.u)+'" target="_blank" rel="noopener noreferrer" title="'+esc(t.u)+'">'+esc(shortUrl(t.u))+'</a>';
  } else if(T==="STRUCTURED_SNIPPET"){
    h+='<b class="ac-tt">'+esc(t.x||"Snippet")+'</b><div class="ac-tm">'+(t.v||[]).map(function(v){return '<span class="ac-t">'+esc(v)+'</span>';}).join(" ")+'</div>';
  } else if(T==="CALL"){
    h+='<b class="ac-tt">'+esc(t.x||"Phone number")+'</b>'+(t.cc?'<div class="ac-tm">'+chips([[t.cc]])+'</div>':"");
  } else if(T==="PROMOTION"){
    h+='<b class="ac-tt">'+esc(t.x||"Promotion")+'</b>'+(t.off?'<p class="ac-td">'+esc(t.off)+'</p>':"")+(t.code?'<p class="ac-td">Code '+esc(t.code)+'</p>':"")+
      (t.occ?'<div class="ac-tm">'+chips([[t.occ]])+'</div>':"");
    if(safeHttp(t.u)) h+='<a class="ac-url" href="'+esc(t.u)+'" target="_blank" rel="noopener noreferrer" title="'+esc(t.u)+'">'+esc(shortUrl(t.u))+'</a>';
  } else if(T==="PRICE"){
    h+='<b class="ac-tt">'+esc(t.x||"Price")+'</b><ul class="ac-offers">'+(t.o||[]).map(function(o){
      return '<li><b>'+esc(o.h)+'</b>'+(o.p?' <span>'+esc(o.p)+'</span>':"")+(o.d?'<em>'+esc(o.d)+'</em>':"")+'</li>';}).join("")+'</ul>';
  } else if(T==="LEAD_FORM"){
    h+='<b class="ac-tt">'+esc(t.x||"Lead form")+'</b>'+(t.d||[]).map(function(l){return '<p class="ac-td">'+esc(l)+'</p>';}).join("")+
      '<div class="ac-tm">'+chips([t.bn?[t.bn]:null, t.cta?[t.cta]:null])+'</div>';
  } else if(T==="TEXT"){
    h+='<b class="ac-tt">'+esc(t.x||"(empty)")+'</b>'+(t.x?'<div class="ac-tm">'+chips([[t.x.length+" characters"]])+'</div>':"");
  } else if(T==="CALL_TO_ACTION"){
    h+='<b class="ac-tt">'+esc(t.cta||"Call to action")+'</b>';
  } else if(T==="SET"){
    h+='<b class="ac-tt">'+esc(t.x||"Location set")+'</b>'+(t.n?'<div class="ac-tm">'+chips([[t.n]])+'</div>':"");
  } else {
    h+='<b class="ac-tt">'+esc(t.x||t.n||TYPE[T]||"Asset")+'</b>';
  }
  return h;
}

/* where it is linked, how it did, and what is wrong with it */
function foot(t, library){
  var st=STATE[t.st]||STATE.live, bits=[];
  if(t.st!=="live") bits.push([st[0], st[1]]);
  if(t.lv&&!library) bits.push([LEVEL[t.lv]||t.lv]);
  if(t.auto) bits.push(["Added by Google"]);
  var d=dates(t); if(d) bits.push([d, t.st==="expired"?"bad":""]);
  var h='<div class="ac-tm">'+chips(bits)+'</div>';
  if(t.i) h+='<p class="ac-perf"><b>'+num(t.i)+'</b> impressions'+(t.k?' · <b>'+num(t.k)+'</b> clicks':"")+' <span>last 30 days</span></p>';
  if(t.why&&t.why.length) h+='<p class="ac-why">'+t.why.map(esc).join(" · ")+'</p>';
  if(library) h+=usage(t);
  return h;
}
function usage(t){
  var bits=[];
  if(t.account) bits.push(esc(t.account));
  if(t.copies>1) bits.push(num(t.copies)+" identical copies");
  if(t.nc) bits.push(num(t.nc)+" campaign"+(t.nc===1?"":"s"));
  if(t.ng) bits.push(num(t.ng)+" asset group"+(t.ng===1?"":"s"));
  if(t.acct) bits.push("account-wide");
  if(!bits.length) return "";
  var names=(t.camps||[]).map(function(c){return '<button type="button" class="ac-link" data-camp="'+esc(c.key)+'">'+esc(c.name||c.key)+'</button>';}).join("");
  var more=t.nc>(t.camps||[]).length?'<span class="ac-more">and '+num(t.nc-(t.camps||[]).length)+' more</span>':"";
  return '<p class="ac-use">Used by '+bits.join(" · ")+'</p>'+(names?'<div class="ac-uses">'+names+more+'</div>':"");
}

/* one asset as a card */
function tile(t, opts){
  opts=opts||{};
  var st=STATE[t.st]||STATE.live, m=media(t);
  return '<article class="ac-tile '+st[1]+' t-'+esc(String(t.t||"").toLowerCase())+(m?" has-m":"")+'" data-id="'+esc(t.id||"")+'">'+m+'<div class="ac-tbody">'+body(t)+foot(t, opts.library)+'</div></article>';
}
function tiles(list, opts){return list.length?'<div class="ac-tgrid">'+list.map(function(t){return tile(t, opts);}).join("")+'</div>':"";}

/* a short chip for the strip under a table row: the first words of an asset, a picture thumbnail */
function chip(t){
  var st=STATE[t.st]||STATE.live, cls="ac-chip "+st[1], T=t.t, label="";
  if(T==="IMAGE"){
    var src=safeImg(t.img);
    return '<span class="'+cls+' img" title="'+esc((t.n||"Image")+(t.st!=="live"?" ("+st[0].toLowerCase()+")":""))+'">'+(src?'<img src="'+esc(src)+'" alt="" loading="lazy" referrerpolicy="no-referrer">':"?")+'</span>';
  }
  if(T==="YOUTUBE_VIDEO") label="Video: "+(t.x||t.vid||"");
  else if(T==="STRUCTURED_SNIPPET") label=(t.x?t.x+": ":"")+(t.v||[]).slice(0,3).join(", ")+((t.v||[]).length>3?" +"+((t.v||[]).length-3):"");
  else if(T==="PROMOTION") label=[t.x,t.off].filter(Boolean).join(" · ");
  else if(T==="PRICE") label=(t.x||"Price")+" ("+(t.o||[]).length+")";
  else if(T==="CALL_TO_ACTION") label=t.cta||"Call to action";
  else label=t.x||t.n||TYPE[T]||"Asset";
  label=String(label); if(label.length>46) label=label.slice(0,45)+"…";
  return '<span class="'+cls+'" title="'+esc(label+(t.st!=="live"?" ("+st[0].toLowerCase()+")":""))+'">'+esc(label)+'</span>';
}
function preview(list, max){
  max=max||8; var shown=list.slice(0,max);
  return shown.map(chip).join("")+(list.length>max?'<span class="ac-chip more">+'+(list.length-max)+' more</span>':"");
}

root.ACTiles={tile:tile, tiles:tiles, chip:chip, preview:preview, safeImg:safeImg, safeHttp:safeHttp, safeVideo:safeVideo, esc:esc, STATE:STATE};
})(typeof window!=="undefined"?window:this);
