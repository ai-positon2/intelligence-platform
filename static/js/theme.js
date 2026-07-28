/* Position2 Intelligence - light/dark theme toggle (internal app) */
(function(){
  function el(id){return document.getElementById(id);}
  var SUN='<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var MOON='<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  function apply(t){
    document.documentElement.setAttribute('data-theme', t);
    var lbl=el('p2ThemeLabel'), ic=el('p2ThemeIcon');
    if(lbl) lbl.textContent = (t==='light' ? 'Dark mode' : 'Light mode');
    /* Show the icon of the mode you'd switch TO: moon while in light, sun while
       in dark. innerHTML (not textContent) because the icons are now inline SVG. */
    if(ic)  ic.innerHTML    = (t==='light' ? MOON : SUN);
  }
  function get(){ try{ return localStorage.getItem('p2-theme') || 'dark'; }catch(e){ return 'dark'; } }
  window.P2toggleTheme=function(ev){
    if(ev){ ev.preventDefault(); ev.stopPropagation(); }
    var t = (get()==='light' ? 'dark' : 'light');
    try{ localStorage.setItem('p2-theme', t); }catch(e){}
    apply(t);
  };
  apply(get());
  document.addEventListener('DOMContentLoaded', function(){ apply(get()); });
})();
