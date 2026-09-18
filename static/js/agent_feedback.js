/* Shared thumbs up/down control for Strategic Agents' generated reports.
   One script, wired into every report-rendering function across six very
   differently-shaped agents (LinkedIn Strategy Researcher, Social Media
   Intelligence, Event & Conference Intelligence, 42 North Dental Slot
   Checker, Contact Finder's assistant, Thought Leader Intelligence), rather
   than six copies of the same handful of DOM calls -- POST /api/agent-
   feedback (app.py) is the one backend contract all six talk to.

   A thumbs-down submits IMMEDIATELY (never blocked on typing a reason first,
   so a vote is never lost to someone closing a drawer) and only afterwards
   offers an optional reason box; that follow-up PATCHes the SAME row via
   /api/agent-feedback/<id>/reason rather than inserting a second one, which
   is why the returned id is kept on the widget's own DOM node instead of
   being thrown away. */
(function(){
  'use strict';

  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
      return {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;'}[c];
    });
  }

  /* Call this to get the HTML for one feedback control. Every agent's own
     template drops the result into its report markup at whatever it
     considers one "section" -- a report-drawer tab/pane, a chart-and-cards
     block, a single chat reply, a weekly briefing. `opts`:
       agentSlug    - one of tracker/agent_feedback.py's AGENT_LABELS keys
       runId        - that agent's own run/report identifier, any type that
                      stringifies sensibly (int run id, a cache stamp, or
                      omitted entirely for CPI's un-persisted chat replies)
       sectionKey   - stable machine key for this section within the report
                      (e.g. 'aienrichment', 'recommend:top_five') -- this is
                      what feedback rows get grouped by on the admin page, so
                      it must not embed anything that changes between runs
                      (a name, a count) or every run mints a brand-new,
                      never-repeated section.
       sectionLabel - what a human reading the admin page should see instead
                      of the raw key. */
  function html(opts){
    var agentSlug = opts.agentSlug || '';
    var runId = opts.runId == null ? '' : String(opts.runId);
    var sectionKey = opts.sectionKey || '';
    var sectionLabel = opts.sectionLabel || '';
    return '<div class="agent-fb" data-agent="' + esc(agentSlug) + '" data-run="' + esc(runId) +
      '" data-section="' + esc(sectionKey) + '" data-label="' + esc(sectionLabel) + '">' +
      '<span class="agent-fb-q">Was this useful?</span>' +
      '<button type="button" class="agent-fb-btn agent-fb-up" data-rating="up" aria-label="Thumbs up, this was useful" title="Thumbs up">👍</button>' +
      '<button type="button" class="agent-fb-btn agent-fb-down" data-rating="down" aria-label="Thumbs down, this was not useful" title="Thumbs down">👎</button>' +
      '<span class="agent-fb-ack" hidden></span>' +
      '<div class="agent-fb-reason" hidden>' +
        '<textarea class="agent-fb-reason-input" maxlength="2000" placeholder="Optional — what was wrong with this?"></textarea>' +
        '<button type="button" class="agent-fb-reason-send">Send</button>' +
        '<button type="button" class="agent-fb-reason-skip">Skip</button>' +
      '</div>' +
    '</div>';
  }

  function post(url, body){
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {})
    }).then(function(r){ return r.json(); }).catch(function(){ return null; });
  }

  function submit(root, rating){
    root.querySelectorAll('.agent-fb-btn').forEach(function(b){
      b.classList.toggle('active', b.getAttribute('data-rating') === rating);
    });
    var ack = root.querySelector('.agent-fb-ack');
    ack.hidden = false;
    ack.textContent = 'Thanks for the feedback';
    post('/api/agent-feedback', {
      agent_slug: root.getAttribute('data-agent'),
      run_id: root.getAttribute('data-run') || null,
      section_key: root.getAttribute('data-section'),
      section_label: root.getAttribute('data-label'),
      rating: rating,
      reason: ''
    }).then(function(d){
      if(d && d.saved && d.id){ root.setAttribute('data-fb-id', d.id); }
    });
  }

  function sendReason(root){
    var ta = root.querySelector('.agent-fb-reason-input');
    var reason = (ta && ta.value || '').trim();
    var box = root.querySelector('.agent-fb-reason');
    if(!reason){ box.hidden = true; return; }
    var fbId = root.getAttribute('data-fb-id');
    if(fbId){
      post('/api/agent-feedback/' + fbId + '/reason', {reason: reason});
    }
    box.hidden = true;
    var ack = root.querySelector('.agent-fb-ack');
    ack.textContent = 'Thanks — reason recorded';
  }

  /* Delegated on document, once, since every one of these panes/tabs/cards
     is built as an HTML string and dropped in with innerHTML well after
     page load -- a listener attached at render time would have to be rewired
     on every re-render across five different rendering functions. */
  document.addEventListener('click', function(e){
    var t = e.target;

    var btn = t.closest && t.closest('.agent-fb-btn');
    if(btn){
      var root = btn.closest('.agent-fb');
      if(!root) return;
      var rating = btn.getAttribute('data-rating');
      submit(root, rating);
      var reasonBox = root.querySelector('.agent-fb-reason');
      if(rating === 'down'){
        reasonBox.hidden = false;
        var ta = reasonBox.querySelector('.agent-fb-reason-input');
        if(ta){ ta.value = ''; ta.focus(); }
      } else {
        reasonBox.hidden = true;
      }
      return;
    }

    var send = t.closest && t.closest('.agent-fb-reason-send');
    if(send){
      var root2 = send.closest('.agent-fb');
      if(root2) sendReason(root2);
      return;
    }

    var skip = t.closest && t.closest('.agent-fb-reason-skip');
    if(skip){
      var box2 = skip.closest('.agent-fb-reason');
      if(box2) box2.hidden = true;
      return;
    }
  });

  window.agentFeedbackHtml = html;
})();
