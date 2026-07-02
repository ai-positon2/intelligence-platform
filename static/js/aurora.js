/* AURORA — micro-interactions for the internal app.
   Cursor-follow card spotlight + magnetic primary buttons.
   Fully optional, degrades gracefully, respects reduced-motion. */
(function(){
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  if (reduce || coarse) return;

  // 1 · Spotlight — feed --mx/--my to cards so the glow tracks the cursor
  var CARD = ".hub-card,.dash-card,.ds-card--hover,[data-spotlight]";
  document.addEventListener("pointermove", function(e){
    var card = e.target.closest && e.target.closest(CARD);
    if(!card) return;
    var r = card.getBoundingClientRect();
    card.style.setProperty("--mx", ((e.clientX - r.left) / r.width * 100) + "%");
    card.style.setProperty("--my", ((e.clientY - r.top) / r.height * 100) + "%");
  }, {passive:true});

  // 2 · Magnetic — primary buttons lean toward the cursor
  var MAG = ".ds-btn--primary,.btn-primary,[data-magnetic]";
  var mags = [];
  function collect(){ mags = Array.prototype.slice.call(document.querySelectorAll(MAG)); }
  collect();
  document.addEventListener("pointermove", function(e){
    for(var i=0;i<mags.length;i++){
      var b = mags[i], r = b.getBoundingClientRect();
      var cx = r.left + r.width/2, cy = r.top + r.height/2;
      var dx = e.clientX - cx, dy = e.clientY - cy;
      var dist = Math.hypot(dx, dy), reach = 90;
      if(dist < reach){
        b.style.transform = "translate(" + (dx*0.18) + "px," + (dy*0.22) + "px)";
      } else if(b.style.transform){
        b.style.transform = "";
      }
    }
  }, {passive:true});
  document.addEventListener("pointerleave", function(){
    for(var i=0;i<mags.length;i++) mags[i].style.transform = "";
  }, true);

  // keep magnet list fresh if DOM changes
  if(window.MutationObserver){
    var mo = new MutationObserver(function(){ collect(); });
    mo.observe(document.body, {childList:true, subtree:true});
  }
})();
