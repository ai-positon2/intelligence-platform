/* Position2 Intelligence - light/dark theme toggle (internal app) */
(function(){
  function el(id){return document.getElementById(id);}
  function apply(t){
    document.documentElement.setAttribute('data-theme', t);
    var lbl=el('p2ThemeLabel'), ic=el('p2ThemeIcon');
    if(lbl) lbl.textContent = (t==='light' ? 'Dark mode' : 'Light mode');
    if(ic)  ic.textContent  = (t==='light' ? '\u263D' : '\u2600');
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
