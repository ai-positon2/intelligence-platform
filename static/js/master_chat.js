/* Master Agent chat (Paid Marketing Agents).
 *
 * Ask anything about the paid accounts, with files, and get an answer or a file back. The server runs each question as a background turn
 * (tracker/master_chat.py) and this page polls it, showing a quiet "Thinking" line and then the answer as it is written (the working steps are never shown). Every text from the server or the model is escaped before it is drawn;
 * the answer's markdown is rendered by a small, safe renderer here (no HTML from the model is ever inserted).
 */
(function(){
"use strict";

var API = "/p2/paid-marketing/api/master/chat";
var MAX_FILES = 40, MAX_BYTES = 20 * 1024 * 1024;
var $ = function(s,r){return (r||document).querySelector(s);};
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});};
var S = {convos:[], cid:"", turns:[], files:[], polling:null, running:null, sending:false};

var SUGGESTIONS = [
  "Which Google Ads campaigns wasted the most spend last month, across all clients?",
  "Compare Google and Meta cost per lead for one client over the last 90 days, month by month.",
  "Make an Excel of every search term that spent over $50 with no conversion in the last 30 days for one account.",
  "Why did conversions drop last week? Check spend, impression share, changes and tracking."
];

/* ── a small, safe markdown renderer ───────────────────────────────────── */
function inline(t){
  t = esc(t);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  t = t.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>");
  t = t.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/(?!\/))[^\s)]+)\)/g, function(_m, label, url){
    var ext = /^https?:/.test(url);
    return '<a href="' + url.replace(/"/g, "%22") + '"' + (ext ? ' target="_blank" rel="noopener noreferrer"' : "") + ">" + label + "</a>";
  });
  return t;
}
function cells(line){ return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function(c){ return c.trim(); }); }
function md(src){
  var lines = String(src || "").replace(/\r/g, "").split("\n"), out = [], i = 0;
  while (i < lines.length){
    var l = lines[i];
    if (/^```/.test(l)){
      var code = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])){ code.push(lines[i]); i++; }
      i++; out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>"); continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])){
      var head = cells(l), rows = []; i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])){ rows.push(cells(lines[i])); i++; }
      var num = head.map(function(_h, k){ return rows.length > 0 && rows.every(function(r){ return !r[k] || /^[-+‑−$€£₹(]*[\d,.]+%?\)?[A-Za-z]{0,3}$/.test(r[k].replace(/\s|\*\*/g, "")); }); });
      out.push('<div class="mx-tw"><table><thead><tr>' + head.map(function(h, k){ return "<th" + (num[k] ? ' class="n"' : "") + ">" + inline(h) + "</th>"; }).join("") +
        "</tr></thead><tbody>" + rows.map(function(r){ return "<tr>" + head.map(function(_h, k){ return "<td" + (num[k] ? ' class="n"' : "") + ">" + inline(r[k] || "") + "</td>"; }).join("") + "</tr>"; }).join("") +
        "</tbody></table></div>");
      continue;
    }
    var h = /^(#{1,4})\s+(.*)$/.exec(l);
    if (h){ var lv = Math.min(4, h[1].length + 2); out.push("<h" + lv + ">" + inline(h[2]) + "</h" + lv + ">"); i++; continue; }
    if (/^\s*([-*•])\s+/.test(l)){
      var items = [];
      while (i < lines.length && /^\s*([-*•])\s+/.test(lines[i])){ items.push("<li>" + inline(lines[i].replace(/^\s*[-*•]\s+/, "")) + "</li>"); i++; }
      out.push("<ul>" + items.join("") + "</ul>"); continue;
    }
    if (/^\s*\d+[.)]\s+/.test(l)){
      var oitems = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])){ oitems.push("<li>" + inline(lines[i].replace(/^\s*\d+[.)]\s+/, "")) + "</li>"); i++; }
      out.push("<ol>" + oitems.join("") + "</ol>"); continue;
    }
    if (/^\s*---+\s*$/.test(l)){ out.push("<hr>"); i++; continue; }
    if (!l.trim()){ i++; continue; }
    var para = [];
    while (i < lines.length && lines[i].trim() && !/^```|^#{1,4}\s|^\s*[-*•]\s|^\s*\d+[.)]\s|^\s*\|.*\|\s*$/.test(lines[i])){ para.push(inline(lines[i])); i++; }
    if (para.length) out.push("<p>" + para.join("<br>") + "</p>"); else { out.push("<p>" + inline(l) + "</p>"); i++; }
  }
  return out.join("");
}

/* ── helpers ───────────────────────────────────────────────────────────── */
function size(n){ return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : n >= 1024 ? Math.round(n / 1024) + " KB" : n + " B"; }
function api(path, opts){
  opts = opts || {};
  var init = {credentials: "same-origin", method: opts.method || "GET", headers: {}};
  if (opts.body !== undefined){ init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  return fetch(API + path, init).then(function(r){
    return r.json().catch(function(){ return {}; }).then(function(j){ if (!r.ok) throw new Error(j.error || ("The server answered " + r.status)); return j; });
  });
}
function fileUrl(f, inline){ return API + "/files/" + encodeURIComponent(f.id) + (inline ? "?inline=1" : ""); }
function isImage(name){ return /\.(png|jpe?g|gif|webp)$/i.test(name || ""); }
function ago(iso){
  if (!iso) return "";
  var s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + " min ago"; if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " d ago";
}
function toast(msg){
  var el = document.createElement("div"); el.className = "mx-toast"; el.textContent = msg; document.body.appendChild(el);
  setTimeout(function(){ el.remove(); }, 6000);
}

/* ── conversations ─────────────────────────────────────────────────────── */
function drawConvos(){
  var box = $("#convos");
  if (!S.convos.length){ box.innerHTML = '<p class="mx-none">No conversations yet.</p>'; return; }
  box.innerHTML = S.convos.map(function(c){
    return '<div class="mx-ci' + (c.id === S.cid ? " on" : "") + '" role="listitem"><button type="button" class="mx-cbtn" data-id="' + esc(c.id) + '"><span class="t">' + esc(c.title || "New chat") +
      '</span><span class="w">' + esc(ago(c.updatedAt)) + '</span></button><button type="button" class="mx-del" data-del="' + esc(c.id) + '" title="Delete this conversation" aria-label="Delete">&times;</button></div>';
  }).join("");
}
function loadConvos(){
  return api("/convos").then(function(j){
    S.convos = j.convos || []; drawConvos();
    var cur = S.convos.filter(function(c){ return c.id === S.cid; })[0];
    if (cur && cur.title){ $("#title").textContent = cur.title; $("#renameBtn").hidden = false; }
  }).catch(function(e){ toast(e.message); });
}
function openConvo(cid){
  stopPolling();
  S.cid = cid; S.turns = [];
  try { history.replaceState(null, "", cid ? "#c=" + cid : location.pathname); } catch (e) {}
  drawConvos();
  if (!cid){ drawThread(); return Promise.resolve(); }
  return api("/convos/" + cid).then(function(j){
    S.turns = j.turns || []; $("#title").textContent = (j.convo && j.convo.title) || "New chat"; $("#renameBtn").hidden = false;
    drawThread();
    var live = S.turns.filter(function(t){ return t.status === "running"; })[0];
    if (live) poll(live.id);
  }).catch(function(e){ toast(e.message); S.cid = ""; drawThread(); });
}

/* ── the thread ────────────────────────────────────────────────────────── */
function filesHtml(files, out){
  if (!files || !files.length) return "";
  return '<div class="mx-files">' + files.map(function(f){
    if (!f.id) return '<span class="mx-file">' + esc(f.name) + (f.note ? ' <i title="' + esc(f.note) + '">!</i>' : "") + "</span>";
    var img = out && isImage(f.name) ? '<a href="' + fileUrl(f) + '" class="mx-img"><img src="' + fileUrl(f, true) + '" alt="' + esc(f.name) + '" loading="lazy"></a>' : "";
    return img + '<a class="mx-file' + (out ? " out" : "") + '" href="' + fileUrl(f) + '" download><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>' +
      esc(f.name) + ' <span class="s">' + esc(size(f.size || 0)) + "</span></a>";
  }).join("") + "</div>";
}
function secs(t){ return Math.max(0, Math.round((Date.now() - Date.parse(t.createdAt || "")) / 1000)) || 0; }
function turnHtml(t){
  var u = '<div class="mx-u"><div class="mx-bub">' + (t.question ? md(t.question) : '<p class="dim">(files only)</p>') + filesHtml(t.filesIn, false) + "</div></div>";
  var running = t.status === "running", body = "";
  if (t.status === "done") body = '<div class="mx-ans">' + md(t.answer || "") + "</div>" + filesHtml(t.filesOut, true);
  else if (running && t.draft) body = '<div class="mx-ans mx-draft">' + md(t.draft) + '<span class="mx-caret" aria-hidden="true"></span></div>';
  else if (running) body = '<div class="mx-think" role="status"><span class="mx-orb" data-orb aria-hidden="true"></span><span class="mx-tl"><span class="mx-shim">Thinking</span><span class="sec" data-since="' + esc(t.createdAt || "") + '">' + secs(t) + " s</span></span></div>";
  else if (t.status === "failed") body = '<div class="mx-err">' + esc(t.error === "Stopped." ? "Stopped." : ("This did not finish: " + (t.error || "an error"))) + "</div>";
  var meta = "";
  if (t.status === "done"){
    var src = (t.sources && t.sources.length) ? '<details class="mx-src"><summary>' + t.sources.length + (t.sources.length === 1 ? " source" : " sources") + "</summary><ul>" + t.sources.map(function(s){
      return "<li>" + esc(s.agent) + (s.view ? " / " + esc(s.view) : "") + (s.account ? " for " + esc(s.account) : "") + (s.failed ? ' <b class="bad">could not be read</b>' : "") + "</li>";
    }).join("") + "</ul></details>" : "";
    meta = '<div class="mx-meta"><button type="button" class="mx-copy" data-copy="' + esc(t.id) + '">Copy</button>' + src +
      (t.finishedAt && t.createdAt ? "<span>" + Math.max(1, Math.round((Date.parse(t.finishedAt) - Date.parse(t.createdAt)) / 1000)) + " s</span>" : "") + "</div>";
  }
  return u + '<div class="mx-a' + (running && !t.draft ? " busy" : "") + '" id="t-' + esc(t.id) + '"><span class="mx-mark" aria-hidden="true"></span><div class="mx-body">' + body + meta + "</div></div>";
}
/* the dotted thinking orb: the platform's shared orb (static/js/thinking-orb.js), mounted into each [data-orb] slot; orbs whose slot has gone are stopped */
var ORBS = [], orbMod = null;
function mountOrbs(){
  ORBS = ORBS.filter(function(o){ if (o.el.isConnected) return true; try { o.c.destroy(); } catch (e) {} return false; });
  var slots = document.querySelectorAll("[data-orb]:not([data-orb-on])");
  if (!slots.length) return;
  var go = function(m){
    for (var k = 0; k < slots.length; k++){
      if (!slots[k].isConnected || slots[k].getAttribute("data-orb-on")) continue;
      slots[k].setAttribute("data-orb-on", "1");
      try { ORBS.push({el: slots[k], c: m.mountThinkingOrb(slots[k], {state: "breathing", size: 64, layout: "row"})}); } catch (e) {}
    }
  };
  if (orbMod) go(orbMod);
  else try { import("/static/js/thinking-orb.js").then(function(m){ orbMod = m; go(m); }).catch(function(){}); } catch (e) {}
}
function drawThread(){
  var th = $("#thread");
  if (!S.turns.length){
    th.innerHTML = EMPTY; drawSugs();
    $("#title").textContent = "New chat"; $("#renameBtn").hidden = !S.cid;
  } else {
    th.innerHTML = S.turns.map(turnHtml).join("");
  }
  th.scrollTop = th.scrollHeight; mountOrbs();
  var running = S.turns.some(function(t){ return t.status === "running"; });
  $("#stopBtn").hidden = !running; $("#sendBtn").hidden = running; $("#sendBtn").disabled = S.sending;
}
var EMPTY = "";
function drawSugs(){
  var box = $("#sugs"); if (!box) return;
  box.innerHTML = SUGGESTIONS.map(function(q){ return '<button type="button" class="mx-sug">' + esc(q) + "</button>"; }).join("");
}

/* ── asking ────────────────────────────────────────────────────────────── */
function readFile(f){
  return new Promise(function(res, rej){
    var r = new FileReader();
    r.onload = function(){ var s = String(r.result || ""); res({name: f.webkitRelativePath || f.name, mime: f.type || "", base64: s.slice(s.indexOf(",") + 1)}); };
    r.onerror = function(){ rej(new Error("Could not read " + f.name)); };
    r.readAsDataURL(f);
  });
}
function addFiles(list){
  var arr = Array.prototype.slice.call(list || []).filter(function(f){ return f.size > 0 && !/(^|\/)\./.test(f.webkitRelativePath || f.name); });
  var total = S.files.reduce(function(a, f){ return a + f.size; }, 0);
  arr.forEach(function(f){
    if (S.files.length >= MAX_FILES){ toast("The most at once is " + MAX_FILES + " files. Zip the rest."); return; }
    if (total + f.size > MAX_BYTES){ toast(f.name + " would take the files over 20 MB. Send it in another message."); return; }
    total += f.size; S.files.push(f);
  });
  drawChips();
}
function drawChips(){
  $("#chips").innerHTML = S.files.map(function(f, i){
    return '<span class="mx-chip">' + esc(f.webkitRelativePath || f.name) + ' <span class="s">' + esc(size(f.size)) + '</span><button type="button" data-rm="' + i + '" aria-label="Remove">&times;</button></span>';
  }).join("");
}
function ensureConvo(){
  if (S.cid) return Promise.resolve(S.cid);
  return api("/convos", {method: "POST", body: {}}).then(function(j){ S.cid = j.convo.id; S.convos.unshift(j.convo); drawConvos();
    try { history.replaceState(null, "", "#c=" + S.cid); } catch (e) {} return S.cid; });
}
function ask(){
  var q = $("#q").value.trim();
  if (S.sending || (!q && !S.files.length)) return;
  if (S.turns.some(function(t){ return t.status === "running"; })){ toast("Wait for the answer, or press Stop."); return; }
  S.sending = true; $("#sendBtn").disabled = true;
  var files = S.files.slice();
  Promise.all(files.map(readFile)).then(function(payload){
    return ensureConvo().then(function(cid){ return api("/convos/" + cid + "/ask", {method: "POST", body: {question: q, files: payload}}); });
  }).then(function(j){
    $("#q").value = ""; S.files = []; drawChips(); autosize();
    S.turns.push(j.turn); drawThread(); poll(j.turn.id); loadConvos();
  }).catch(function(e){ toast(e.message); })
    .then(function(){ S.sending = false; $("#sendBtn").disabled = false; });
}
function stopPolling(){ if (S.polling){ clearTimeout(S.polling); S.polling = null; } S.running = null; }
function poll(tid){
  stopPolling(); S.running = tid;
  var tick = function(){
    api("/turns/" + tid).then(function(j){
      if (S.running !== tid) return;
      var t = j.turn, at = -1;
      S.turns.forEach(function(x, k){ if (x.id === tid) at = k; });
      if (at >= 0){
        var old = S.turns[at]; S.turns[at] = t;
        var node = document.getElementById("t-" + tid);
        if (node && old.status === t.status && (old.draft || "") === (t.draft || "")) { /* nothing new to show */ }
        else if (node){ var wrap = document.createElement("div"); wrap.innerHTML = turnHtml(t); node.replaceWith(wrap.lastChild); $("#thread").scrollTop = $("#thread").scrollHeight; mountOrbs(); }
        else drawThread();
      }
      if (t.status === "running") S.polling = setTimeout(tick, 1000);
      else { stopPolling(); drawThread(); loadConvos(); }
    }).catch(function(){ if (S.running === tid) S.polling = setTimeout(tick, 4000); });
  };
  tick();
}
function stop(){
  var live = S.turns.filter(function(t){ return t.status === "running"; })[0];
  if (!live) return;
  api("/turns/" + live.id + "/cancel", {method: "POST", body: {}}).catch(function(e){ toast(e.message); });
}
function autosize(){ var t = $("#q"); t.style.height = "auto"; t.style.height = Math.min(220, t.scrollHeight) + "px"; }

/* ── wiring ────────────────────────────────────────────────────────────── */
/* the page fills the window under the top bar and never scrolls itself: only the thread and the list do */
function fit(){
  var m = document.querySelector(".main"); if (!m) return;
  m.style.height = Math.max(420, window.innerHeight - m.getBoundingClientRect().top) + "px";
}
function tickSeconds(){
  var els = document.querySelectorAll(".mx-think .sec[data-since]");
  for (var k = 0; k < els.length; k++){ var since = Date.parse(els[k].getAttribute("data-since")); if (since) els[k].textContent = Math.max(0, Math.round((Date.now() - since) / 1000)) + " s"; }
}
if (document.documentElement) document.documentElement.classList.add("mx-page");
if (document.body) document.body.classList.add("mx-page");

document.addEventListener("DOMContentLoaded", function(){
  document.body.classList.add("mx-page"); fit();
  window.addEventListener("resize", fit);
  setInterval(tickSeconds, 1000);
  EMPTY = $("#empty").outerHTML;
  drawSugs(); mountOrbs();
  $("#composer").addEventListener("submit", function(e){ e.preventDefault(); ask(); });
  $("#q").addEventListener("keydown", function(e){ if (e.key === "Enter" && !e.shiftKey && !e.isComposing){ e.preventDefault(); ask(); } });
  $("#q").addEventListener("input", autosize);
  $("#fileIn").addEventListener("change", function(e){ addFiles(e.target.files); e.target.value = ""; });
  $("#folderIn").addEventListener("change", function(e){ addFiles(e.target.files); e.target.value = ""; });
  $("#chips").addEventListener("click", function(e){ var b = e.target.closest("[data-rm]"); if (b){ S.files.splice(+b.getAttribute("data-rm"), 1); drawChips(); } });
  $("#stopBtn").addEventListener("click", stop);
  $("#newBtn").addEventListener("click", function(){ openConvo(""); $("#q").focus(); });
  $("#sideBtn").addEventListener("click", function(){ $("#side").classList.toggle("open"); });
  $("#convos").addEventListener("click", function(e){
    var del = e.target.closest("[data-del]");
    if (del){
      var id = del.getAttribute("data-del");
      if (!window.confirm("Delete this conversation? It cannot be opened again.")) return;
      api("/convos/" + id + "/delete", {method: "POST", body: {}}).then(function(){ if (id === S.cid) openConvo(""); loadConvos(); }).catch(function(er){ toast(er.message); });
      return;
    }
    var b = e.target.closest("[data-id]");
    if (b){ openConvo(b.getAttribute("data-id")); $("#side").classList.remove("open"); }
  });
  $("#renameBtn").addEventListener("click", function(){
    if (!S.cid) return;
    var t = window.prompt("Name this conversation", $("#title").textContent || "");
    if (!t || !t.trim()) return;
    api("/convos/" + S.cid + "/rename", {method: "POST", body: {title: t.trim()}}).then(function(j){ $("#title").textContent = j.title; loadConvos(); }).catch(function(er){ toast(er.message); });
  });
  $("#thread").addEventListener("click", function(e){
    var sug = e.target.closest(".mx-sug");
    if (sug){ $("#q").value = sug.textContent; autosize(); $("#q").focus(); return; }
    var cp = e.target.closest("[data-copy]");
    if (cp){
      var t = S.turns.filter(function(x){ return x.id === cp.getAttribute("data-copy"); })[0];
      if (t && navigator.clipboard) navigator.clipboard.writeText(t.answer || "").then(function(){ cp.textContent = "Copied"; setTimeout(function(){ cp.textContent = "Copy"; }, 1500); });
    }
  });
  var main = $(".mx-main"), depth = 0;
  main.addEventListener("dragenter", function(e){ e.preventDefault(); depth++; $("#drop").hidden = false; });
  main.addEventListener("dragover", function(e){ e.preventDefault(); });
  main.addEventListener("dragleave", function(){ depth = Math.max(0, depth - 1); if (!depth) $("#drop").hidden = true; });
  main.addEventListener("drop", function(e){ e.preventDefault(); depth = 0; $("#drop").hidden = true; addFiles(e.dataTransfer && e.dataTransfer.files); });
  var m = /#c=([0-9a-f]{32})/.exec(location.hash || "");
  loadConvos().then(function(){ if (m) openConvo(m[1]); });
});

window.MasterChat = {md: md, esc: esc, turnHtml: turnHtml};
})();
