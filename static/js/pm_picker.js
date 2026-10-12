/* Paid Marketing: the searchable dropdown shared by Pacing Desk and the
 * Client Feedback Agent (same look as Search Query Analysis' account
 * picker, styles in static/css/paid_marketing.css: .sq-dd*, .pd-dd*).
 *
 *   var p = PMPicker(hostEl, {label: "Client", search: true, onChange: fn});
 *   p.set([{value, label, sub, badge, avatar | icon, hue, group}], value, disabled);
 */
(function(){
"use strict";
var esc = function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
/* ── A searchable dropdown (same look as Search Query Analysis') ──
   items: [{value, label, sub, badge(html), avatar, hue, group}] */
window.PMPicker = function(host, opts){
  var wrap=document.createElement("div"); wrap.className="sq-dd pd-dd";
  wrap.innerHTML='<span class="pd-dd-k">'+esc(opts.label)+'</span>'+
    '<button type="button" class="sq-dd-btn" aria-haspopup="listbox" aria-expanded="false" aria-label="'+esc(opts.label)+'"></button>'+
    '<div class="sq-dd-menu" hidden>'+(opts.search?'<input class="sq-dd-search" placeholder="Filter '+esc(opts.label.toLowerCase())+'s…" autocomplete="off">':'')+
    '<div role="listbox" class="pd-dd-list"></div></div>';
  host.appendChild(wrap);
  var btn=wrap.querySelector(".sq-dd-btn"), menu=wrap.querySelector(".sq-dd-menu"),
      list=wrap.querySelector(".pd-dd-list"), q=wrap.querySelector(".sq-dd-search");
  var items=[], value="", hl=0, shown=[], disabled=false;
  function av(it){
    if(it.icon) return '<span class="sq-av pd-av-ic" style="--h:'+(it.hue||40)+'">'+it.icon+'</span>';
    return '<span class="sq-av" style="--h:'+(it.hue==null?40:it.hue)+'">'+esc(it.avatar||"·")+'</span>';
  }
  function cur(){ return items.filter(function(i){return i.value===value})[0]||items[0]; }
  function paintBtn(){
    var it=cur(); if(!it){btn.innerHTML="";return;}
    btn.innerHTML=av(it)+'<span class="sq-dd-txt"><span class="nm">'+esc(it.label)+'</span><span class="ds">'+esc(it.sub||"")+'</span></span>'+
      (it.badge||"")+'<span class="sq-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span>';
    btn.disabled=disabled;
  }
  function paintList(){
    var term=q?q.value.trim().toLowerCase():"";
    shown=items.filter(function(i){return !term||(i.label+" "+(i.sub||"")+" "+(i.group||"")).toLowerCase().indexOf(term)>=0});
    if(hl>=shown.length) hl=Math.max(0,shown.length-1);
    var html="", grp=null;
    shown.forEach(function(it,i){
      if(it.group&&it.group!==grp){grp=it.group; html+='<div class="pd-grp">'+esc(grp)+'</div>';}
      html+='<button type="button" role="option" class="sq-opt'+(i===hl?' hl':'')+'" data-i="'+i+'" aria-selected="'+(it.value===value)+
        '" style="animation-delay:'+Math.min(i*18,300)+'ms">'+av(it)+'<span class="sq-dd-txt"><span class="nm" style="font-size:13.5px">'+esc(it.label)+
        '</span><span class="ds">'+esc(it.sub||"")+'</span></span>'+(it.badge||"")+'<span class="tick">✓</span></button>';
    });
    list.innerHTML=html||'<div class="sq-none">Nothing matches “'+esc(term)+'”.</div>';
    var h=list.querySelector(".hl"); if(h) h.scrollIntoView({block:"nearest"});
  }
  function open(){
    if(disabled) return;
    document.querySelectorAll(".pd-dd.open").forEach(function(o){if(o!==wrap&&o.__close)o.__close()});
    wrap.classList.add("open"); menu.hidden=false; btn.setAttribute("aria-expanded","true");
    if(q) q.value="";
    hl=Math.max(0,items.map(function(i){return i.value}).indexOf(value)); paintList();
    setTimeout(function(){(q||btn).focus()},20);
  }
  function close(focus){ wrap.classList.remove("open"); menu.hidden=true; btn.setAttribute("aria-expanded","false"); if(focus) btn.focus(); }
  wrap.__close=close;
  function choose(it){
    close(true);
    if(it.value===value) return;
    value=it.value; paintBtn();
    btn.animate([{transform:"scale(.97)"},{transform:"scale(1.01)"},{transform:"none"}],{duration:300,easing:"ease-out"});
    opts.onChange(value);
  }
  btn.addEventListener("click",function(){menu.hidden?open():close()});
  btn.addEventListener("keydown",function(e){if(e.key==="ArrowDown"&&menu.hidden){e.preventDefault();open();}});
  list.addEventListener("click",function(e){var o=e.target.closest(".sq-opt"); if(o) choose(shown[+o.dataset.i]);});
  (q||menu).addEventListener("keydown",function(e){
    if(e.key==="ArrowDown"){e.preventDefault();hl=Math.min(shown.length-1,hl+1);paintList();}
    else if(e.key==="ArrowUp"){e.preventDefault();hl=Math.max(0,hl-1);paintList();}
    else if(e.key==="Enter"){e.preventDefault();if(shown[hl])choose(shown[hl]);}
    else if(e.key==="Escape"){e.preventDefault();close(true);}
  });
  if(q) q.addEventListener("input",function(){hl=0;paintList();});
  document.addEventListener("click",function(e){if(!wrap.contains(e.target)&&!menu.hidden)close();});
  return {
    set:function(newItems,newValue,isDisabled){items=newItems;value=newValue;disabled=!!isDisabled;paintBtn();if(!menu.hidden)paintList();},
    value:function(){return value;}
  };
};
})();
