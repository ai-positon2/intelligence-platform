/* Market Radar client report (templates/market_radar_report.html).

   Renders the latest report from /api/clients/<id>/report: the brief the
   writer produced (tracker/market_radar_report.py), every statement next to
   the evidence it cites, and the evidence's own sources. Everything shown
   comes from other companies' websites, news headlines or a model, so every
   string goes through esc() and every link through safeUrl().

   The PDF is this page: the rendered report is cloned with every folded
   part open and every control removed, and sent to the server, which lays
   it out (tracker/event_intel_pdf.py). */
(function () {
  'use strict';

  var API = '/p2/admin/market-radar/api';
  var root = document.getElementById('rr');
  var body = document.getElementById('rrBody');
  var clientId = root ? root.getAttribute('data-client') : null;
  var state = { view: null, refs: {} };

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function safeUrl(u) {
    if (!u) return null;
    try {
      var x = new URL(String(u), window.location.origin);
      return x.protocol === 'https:' || x.protocol === 'http:' ? x.href : null;
    } catch (e) { return null; }
  }

  function link(url, text) {
    var href = safeUrl(url);
    return href ? '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(text) + '</a>' : esc(text);
  }

  // A page change's summary can be a whole filter list ("Leggings (9)Coats
  // + Jackets (4)..."): the first lines say enough.
  function clip(t, n) {
    t = String(t || '');
    return t.length > n ? t.slice(0, n).replace(/\s+\S*$/, '') + '…' : t;
  }

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function day(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return m ? (+m[3]) + ' ' + MONTHS[+m[2] - 1] + ' ' + m[1] : '';
  }

  var COUNTRY = { US: 'United States', GB: 'United Kingdom', DE: 'Germany', IN: 'India', AU: 'Australia',
    CA: 'Canada', IE: 'Ireland', FR: 'France', ES: 'Spain', IT: 'Italy', NL: 'Netherlands', BR: 'Brazil' };
  var ARCHETYPE = { local_single: 'Local business, one site', multi_location: 'Multi-location chain',
    ecommerce: 'Online shop / D2C brand', b2b_services: 'B2B services', b2b_product: 'B2B product',
    manufacturer: 'Manufacturer', other: 'Other' };
  var KIND = { direct: 'Direct', indirect: 'Indirect', local: 'Local', aspirational: 'Aspirational' };
  var SEV = { HIGH: ['bad', 'High'], MEDIUM: ['warn', 'Medium'], LOW: ['', 'Low'] };
  var STATUS = { planned: 'Planned', announced: 'Announced', rumored: 'Rumoured', opened: 'Opened',
    completed: 'Done', closed: 'Closed' };
  var CONF = { high: ['ok', 'High confidence'], medium: ['warn', 'Medium confidence'], low: ['', 'Low confidence'] };
  var DETECTOR = { news: 'News', newsroom: 'Its own site', locations: 'Its location pages', catalog: 'Its shop',
    jobs: 'Its jobs board', promotions: 'Its homepage', pages: 'Its website', reviews: 'Review counts' };

  // == evidence ==========================================================================

  function sourceList(sources) {
    if (!sources || !sources.length) return '';
    return '<ul class="rr-src">' + sources.map(function (s) {
      var who = s.publisher || DETECTOR[s.detector] || s.detector || 'Source';
      return '<li>' + link(s.url, s.headline || who) + (s.headline ? ' <span class="rr-q">' + esc(who) +
        (s.date ? ', ' + esc(day(s.date)) : '') + '</span>' : '') + '</li>';
    }).join('') + '</ul>';
  }

  function evidence(ref) {
    var it = state.refs[ref];
    if (!it) return '';
    var k = ref.charAt(0), head;
    if (k === 'M') {
      head = '<b>' + esc(it.company) + '</b>: ' + esc(it.title) + ' <span class="rr-q">' +
        esc([it.label, STATUS[it.status], day(it.date), it.articles > 1 ? it.articles + ' articles' : ''].filter(Boolean).join(' · ')) + '</span>';
      return '<li>' + head + sourceList(it.sources) + '</li>';
    }
    if (k === 'T') {
      return '<li><b>' + esc(it.title) + '</b> <span class="rr-q">industry news, ' + esc(COUNTRY[it.country] || it.country) + '</span>' +
        (it.summary ? '<div class="rr-ev-sum">' + esc(it.summary) + '</div>' : '') +
        '<ul class="rr-src">' + (it.articles || []).map(function (a) {
          return '<li>' + link(a.link, a.title) + ' <span class="rr-q">' + esc([a.publisher, day(a.date)].filter(Boolean).join(', ')) + '</span></li>';
        }).join('') + '</ul></li>';
    }
    if (k === 'N') {
      return '<li><b>' + link(it.website, it.name) + '</b> <span class="rr-q">' +
        esc([String(it.category || '').replace(/_/g, ' '), it.distance_km != null ? it.distance_km + ' km away' : '',
          it.certain === false ? 'possibly new' : STATUS[it.status]].filter(Boolean).join(' · ')) + '</span>' +
        '<ul class="rr-src">' + (it.evidence || []).map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul></li>';
    }
    if (k === 'B') {
      return '<li><b>' + link(it.website, it.name) + '</b> <span class="rr-q">new brand</span>' +
        '<ul class="rr-src">' + (it.evidence || []).map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul></li>';
    }
    if (k === 'R') {
      return '<li>' + link(it.link, it.type + ': ' + it.title) + ' <span class="rr-q">' + esc([it.agency, day(it.date)].filter(Boolean).join(', ')) + '</span></li>';
    }
    if (k === 'H') {
      return '<li><b>' + esc(it.company) + '</b>: ' + esc(it.open) + ' open roles in ' + esc(it.places) + ' places' +
        (it.read ? ' <span class="rr-q">read ' + esc(day(it.read)) + '</span>' : '') + '</li>';
    }
    if (k === 'C') return '<li><b>' + esc(it.name) + '</b> <span class="rr-q">' + esc(it.domain) + '</span></li>';
    return '';
  }

  function based(cites, open) {
    var refs = (cites || []).filter(function (r) { return state.refs[r]; });
    if (!refs.length) return '';
    return '<details class="rr-ev"' + (open ? ' open' : '') + '><summary>Evidence (' + refs.length + ')</summary><ul class="rr-evl">' +
      refs.map(evidence).join('') + '</ul></details>';
  }

  // == feedback ===========================================================================

  function thumbs(eventIds, current) {
    if (!eventIds.length) return '';
    var ids = esc(eventIds.join(','));
    return '<span class="rr-fb" data-noprint>' +
      '<button type="button" class="rr-thumb' + (current === 'up' ? ' on' : '') + '" data-fb="up" data-events="' + ids + '" aria-pressed="' + (current === 'up') + '" title="Useful">&#128077;</button>' +
      '<button type="button" class="rr-thumb' + (current === 'down' ? ' on' : '') + '" data-fb="down" data-events="' + ids + '" aria-pressed="' + (current === 'down') + '" title="Not useful">&#128078;</button></span>';
  }

  function movesOf(cites) {
    return (cites || []).filter(function (r) { return r.charAt(0) === 'M' && state.refs[r]; })
      .map(function (r) { return state.refs[r]; });
  }

  function commonFeedback(moves) {
    if (!moves.length) return null;
    var f = moves[0].feedback || null;
    return moves.every(function (m) { return (m.feedback || null) === f; }) ? f : null;
  }

  // == sections ===========================================================================

  function section(id, title, intro, inner) {
    if (!inner) return '';
    return '<section class="rr-sec" id="' + id + '"><h2>' + esc(title) + '</h2>' +
      (intro ? '<p class="rr-intro">' + esc(intro) + '</p>' : '') + inner + '</section>';
  }

  function renderTop(top) {
    if (!top || !top.length) return '';
    return '<div class="rr-top">' + top.map(function (t, i) {
      var conf = CONF[t.confidence] || CONF.low;
      var moves = movesOf(t.cites);
      return '<article class="rr-topi"><div class="rr-n" data-noprint>' + (i + 1) + '</div><div class="rr-topb">' +
        '<div class="rr-toph"><h3><span class="rr-pn">' + (i + 1) + '. </span>' + esc(t.title) + '</h3><span class="mr-chip ' + conf[0] + '">' + conf[1] + '</span>' +
        thumbs(moves.map(function (m) { return m.event_id; }), commonFeedback(moves)) + '</div>' +
        '<p class="rr-what">' + esc(t.what_happened) + '</p>' +
        '<div class="rr-pair"><div class="rr-so"><div class="rr-lab">So what for you</div><p>' + esc(t.so_what) + '</p></div>' +
        '<div class="rr-act"><div class="rr-lab">Suggested action</div><p>' + esc(t.action) + '</p></div></div>' +
        based(t.cites) + '</div></article>';
    }).join('') + '</div>';
  }

  function renderCompany(pack) {
    var c = pack.client || {};
    var rows = [
      ['Business', c.one_liner], ['Type', ARCHETYPE[c.archetype] || c.archetype], ['Industry', c.industry],
      ['Based in', [c.city, COUNTRY[c.country] || c.country].filter(Boolean).join(', ')],
      ['Locations', c.locations > 0 ? c.locations : ''], ['Sells', (c.offerings || []).join(', ')],
      ['Competitors tracked', (pack.competitors || []).length]
    ].filter(function (r) { return r[1] !== '' && r[1] != null; });
    return '<dl class="rr-dl">' + rows.map(function (r) {
      return '<div><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>';
    }).join('') + '</dl>';
  }

  function moveRow(m) {
    var sev = SEV[m.severity] || SEV.LOW;
    var first = (m.sources || [])[0] || {};
    return '<li class="rr-mv" id="ev-' + esc(m.ref) + '"><span class="rr-dot ' + sev[0] + '" title="' + esc(sev[1]) + ' for this client"></span>' +
      '<div class="rr-mvd">' + esc(day(m.date)) + '</div><div class="rr-mvb"><div class="rr-mvt">' +
      '<span class="mr-chip">' + esc(m.label) + '</span>' + (STATUS[m.status] && m.status !== 'unknown' ? '<span class="mr-chip">' + esc(STATUS[m.status]) + '</span>' : '') +
      link(first.url, m.title) + thumbs([m.event_id], m.feedback || null) + '</div>' +
      (m.summary ? '<div class="rr-q">' + esc(clip(m.summary, 220)) + '</div>' : '') +
      '<div class="rr-q">' + esc([m.place, (m.articles > 1 ? m.articles + ' articles' : (first.publisher || DETECTOR[first.detector] || '')),
        m.evidence > 1 ? m.evidence + ' independent sources' : '', sev[1] + ' for you'].filter(Boolean).join(' · ')) + '</div></div></li>';
  }

  function renderCompetitors(brief, pack) {
    var byRef = {};
    (pack.competitors || []).forEach(function (c) { byRef[c.ref] = c; });
    var movesBy = {};
    (pack.moves || []).forEach(function (m) { (movesBy[m.company_ref] = movesBy[m.company_ref] || []).push(m); });
    Object.keys(movesBy).forEach(function (k) { movesBy[k].sort(function (a, b) { return String(b.date).localeCompare(String(a.date)); }); });
    var written = {};
    var cards = (brief.competitors || []).map(function (b) {
      var c = byRef[b.company_ref];
      if (!c) return '';
      written[b.company_ref] = true;
      var ms = movesBy[b.company_ref] || [];
      return '<article class="rr-comp"><header><h3>' + esc(c.name) + '</h3><span class="rr-q">' + esc(c.domain) + '</span>' +
        '<span class="mr-chip">' + esc(KIND[c.kind] || c.kind) + '</span></header><p>' + esc(b.summary) + '</p>' +
        (ms.length ? '<details class="rr-ev" open><summary>Moves (' + ms.length + ')</summary><ul class="rr-mvs">' + ms.map(moveRow).join('') + '</ul></details>' : '') +
        '</article>';
    }).join('');
    var rest = (pack.competitors || []).filter(function (c) { return !written[c.ref] && movesBy[c.ref]; });
    if (rest.length) {
      cards += '<details class="rr-more"><summary>Other competitors with moves (' + rest.length + ')</summary>' +
        rest.map(function (c) {
          return '<div class="rr-comp rr-comp-s"><header><h3>' + esc(c.name) + '</h3><span class="rr-q">' + esc(c.domain) + '</span></header>' +
            '<ul class="rr-mvs">' + movesBy[c.ref].map(moveRow).join('') + '</ul></div>';
        }).join('') + '</details>';
    }
    return cards;
  }

  // A map of the client and the new businesses near it: an equirectangular
  // projection is exact enough over a few kilometres.
  function nearbyMap(pack) {
    var c = pack.client || {}, p = c.point;
    var pts = (pack.nearby || []).filter(function (n) { return n.lat != null && n.lon != null; });
    if (!p || !pts.length) return '';
    var k = Math.cos(p.lat * Math.PI / 180);
    var xy = pts.map(function (n) {
      return { n: n, x: (n.lon - p.lon) * 111.32 * k, y: (n.lat - p.lat) * 110.57 };
    });
    var reach = Math.max(c.radius_km || 0, Math.max.apply(null, xy.map(function (q) { return Math.sqrt(q.x * q.x + q.y * q.y); }))) * 1.12 || 1;
    var S = 340, C = S / 2, scale = (C - 18) / reach;
    var rings = [1, 2, 3].map(function (i) {
      var r = (C - 18) * i / 3, km = reach * i / 3;
      return '<circle cx="' + C + '" cy="' + C + '" r="' + r.toFixed(1) + '" class="rr-ring"/>' +
        '<text x="' + (C + 4) + '" y="' + (C - r + 12).toFixed(1) + '" class="rr-ringt">' + (km < 10 ? km.toFixed(1) : Math.round(km)) + ' km</text>';
    }).join('');
    var dots = xy.map(function (q) {
      var x = C + q.x * scale, y = C - q.y * scale;
      return '<g><circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="9" class="rr-pin' + (q.n.certain === false ? ' weak' : '') + '"/>' +
        '<text x="' + x.toFixed(1) + '" y="' + (y + 3.5).toFixed(1) + '" class="rr-pint">' + esc(q.n.ref.slice(1)) + '</text>' +
        '<title>' + esc(q.n.name) + ', ' + esc(q.n.distance_km) + ' km</title></g>';
    }).join('');
    return '<figure class="rr-map" data-noprint><svg viewBox="0 0 ' + S + ' ' + S + '" role="img" aria-label="Map of new businesses near ' + esc(c.name) + '">' +
      rings + '<circle cx="' + C + '" cy="' + C + '" r="7" class="rr-you"/><text x="' + C + '" y="' + (C + 22) + '" class="rr-yout">You</text>' + dots +
      '</svg><figcaption>North is up. Numbers match the list.</figcaption></figure>';
  }

  function renderNearby(brief, pack) {
    var local = (brief.local || []).map(function (l) { return '<p class="rr-lead2">' + esc(l.text) + '</p>' + based(l.cites); }).join('');
    var list = (pack.nearby || []).length ? '<ol class="rr-near">' + pack.nearby.map(function (n) {
      return '<li value="' + esc(n.ref.slice(1)) + '"><b>' + link(n.website, n.name) + '</b> <span class="rr-q">' +
        esc([String(n.category || '').replace(/_/g, ' '), n.distance_km != null ? n.distance_km + ' km' : '',
          n.certain === false ? 'possibly new' : (STATUS[n.status] || '')].filter(Boolean).join(' · ')) + '</span>' +
        '<div class="rr-q">' + esc((n.evidence || []).join('; ')) + '</div></li>';
    }).join('') + '</ol>' : '';
    var entrants = (pack.entrants || []).length ? '<h3 class="rr-h3">New brands in your category</h3><ul class="rr-near">' +
      pack.entrants.map(function (b) {
        return '<li><b>' + link(b.website, b.name) + '</b><div class="rr-q">' + esc((b.evidence || []).join('; ')) + '</div></li>';
      }).join('') + '</ul>' : '';
    if (!local && !list && !entrants) return '';
    return local + (list ? '<div class="rr-nearw">' + nearbyMap(pack) + list + '</div>' : '') + entrants;
  }

  function renderIndustry(brief, pack) {
    var items = (brief.industry || []).map(function (x) {
      return '<li><h3>' + esc(x.title) + '</h3><p>' + esc(x.implication) + '</p>' + based(x.cites) + '</li>';
    }).join('');
    var rules = (pack.rules || []).length ? '<h3 class="rr-h3">US federal rules naming this industry</h3><ul class="rr-evl">' +
      pack.rules.map(function (r) { return evidence(r.ref); }).join('') + '</ul>' : '';
    return items || rules ? (items ? '<ul class="rr-ind">' + items + '</ul>' : '') + rules : '';
  }

  function renderHiring(pack) {
    var rows = (pack.hiring || []).slice().sort(function (a, b) { return (b.open || 0) - (a.open || 0); });
    if (!rows.length) return '';
    return '<div class="rr-tablew"><table class="rr-table"><thead><tr><th>Competitor</th><th>Open roles</th><th>Places</th><th>Mostly</th><th>Senior roles open</th></tr></thead><tbody>' +
      rows.map(function (h) {
        return '<tr><td>' + esc(h.company) + '</td><td class="num">' + esc(h.open) + '</td><td class="num">' + esc(h.places) + '</td><td>' +
          esc((h.functions || []).map(function (f) { return f[0] + ' ' + f[1]; }).join(', ')) + '</td><td>' + esc((h.senior || []).join('; ')) + '</td></tr>';
      }).join('') + '</tbody></table></div><p class="rr-q">From each competitor\'s public jobs board, as last read. Competitors with no public board are not listed.</p>';
  }

  function listOf(items) {
    return items && items.length ? '<ul class="rr-bul">' + items.map(function (x) {
      return '<li>' + esc(x.text) + based(x.cites) + '</li>';
    }).join('') + '</ul>' : '';
  }

  function renderOT(brief) {
    var o = listOf(brief.opportunities), t = listOf(brief.threats);
    if (!o && !t) return '';
    return '<div class="rr-two"><div><h3 class="rr-h3 ok">Opportunities</h3>' + (o || '<p class="rr-q">None stood out.</p>') +
      '</div><div><h3 class="rr-h3 bad">Threats</h3>' + (t || '<p class="rr-q">None stood out.</p>') + '</div></div>';
  }

  function money(v) { return v == null ? '' : '$' + Number(v).toFixed(Number(v) < 0.1 ? 3 : 2); }

  function renderCoverage(v) {
    var pack = v.pack || {};
    var out = '<ul class="rr-bul">' + (pack.coverage || []).filter(Boolean).map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('') + '</ul>';
    var removed = v.removed || [];
    out += '<h3 class="rr-h3">Statements removed by the citation check</h3>' + (removed.length
      ? '<p class="rr-q">Every statement was checked against the evidence it cites. These were not supported and were removed, not reworded.</p><ul class="rr-bul">' +
        removed.map(function (r) { return '<li>' + esc(r.text) + ' <span class="rr-q">(' + esc(r.why) + ')</span></li>'; }).join('') + '</ul>'
      : '<p class="rr-q">' + (v.check_note ? esc(v.check_note) : 'None: every statement was supported by the evidence it cites.') + '</p>');
    if (removed.length && v.check_note) out += '<p class="rr-q">' + esc(v.check_note) + '</p>';
    if ((v.dropped || []).length) {
      out += '<p class="rr-q">' + esc(v.dropped.length) + ' statements were left out before the check because they cited no evidence.</p>';
    }
    var cost = v.cost;
    out += '<p class="rr-q">Written ' + esc(day(v.created_at)) + ' by ' + esc((v.models || {}).writer || 'the writer') + ', checked by ' + esc((v.models || {}).check || 'the checker') +
      (cost ? '. This collection and report cost ' + esc(money(cost.total_usd)) + (cost.partial ? ' (some calls counted at their reserved amount)' : '') + '.' : '.') + '</p>';
    if (cost && cost.by_stage) {
      out += '<details class="rr-more" data-noprint><summary>Cost by step</summary><ul class="rr-bul">' + Object.keys(cost.by_stage).map(function (k) {
        return '<li>' + esc(k.replace(/_/g, ' ')) + ': ' + esc(money(cost.by_stage[k])) + '</li>';
      }).join('') + '</ul></details>';
    }
    return out;
  }

  // == the page ===========================================================================

  function render(v) {
    state.view = v;
    state.refs = {};
    var pack = v.pack || {};
    ['competitors', 'moves', 'nearby', 'entrants', 'themes', 'rules', 'hiring'].forEach(function (k) {
      (pack[k] || []).forEach(function (it) { state.refs[it.ref] = it; });
    });
    var back = '<a class="mr-btn ghost" href="/p2/admin/market-radar#company-' + esc(v.client_id) + '">Edit page</a>';
    if (v.status === 'none' || !v.brief) {
      var why = v.status === 'none' ? 'No report has been written for this company yet. A report is written at the end of every collection: open the edit page and press Collect.'
        : (v.note || 'The report could not be written.');
      body.innerHTML = '<header class="rr-head"><div class="eyebrow">Market Radar report</div><h1>' + esc(v.name || v.domain) + '</h1></header>' +
        '<div class="mr-callout' + (v.status === 'failed' ? ' bad' : '') + '">' + esc(why) + '</div><p>' + back + '</p>';
      return;
    }
    var b = v.brief, c = pack.client || {};
    var meta = [c.domain, c.industry, [c.city, COUNTRY[c.country] || c.country].filter(Boolean).join(', ')].filter(Boolean).join(' · ');
    var html = '<div id="rrDoc"><header class="rr-head"><div class="eyebrow">Market Radar report</div>' +
      '<h1>' + esc(c.name || v.name) + '</h1><p class="rr-meta">' + esc(meta) + '</p>' +
      '<p class="rr-meta">Written ' + esc(day(v.created_at)) + (pack.collected_at ? ' from the collection of ' + esc(day(pack.collected_at)) : '') +
      '. ' + esc((pack.competitors || []).length) + ' competitors tracked.</p>' +
      '<div class="rr-actions" data-noprint><button type="button" class="mr-btn primary" id="rrPdf">Download PDF</button>' + back + '</div></header>';
    var la = v.latest_attempt;
    if (la) {
      html += '<div class="mr-callout' + (la.status === 'failed' ? ' bad' : '') + '" data-noprint>A newer collection on ' + esc(day(la.created_at)) +
        ' produced no report: ' + esc(la.note || 'no reason was given') + ' This is the last report that was written.</div>';
    }
    if (b.summary) html += '<div class="rr-summary"><p>' + esc(b.summary.text) + '</p>' + based(b.summary.cites) + '</div>';
    html += section('rr-top', 'What matters most', 'The developments that matter most to this business, most important first.', renderTop(b.top));
    html += section('rr-you', 'Your company, as we read it', 'From its own website. Corrections made on the edit page are used.', renderCompany(pack));
    html += section('rr-comp', 'Competitor moves', 'What each competitor did, from its website, shop, jobs board and the news. A dot shows how much a move matters to you.', renderCompetitors(b, pack));
    html += section('rr-near', 'Nearby and new', null, renderNearby(b, pack));
    html += section('rr-ind', 'Industry pulse', 'This month\'s industry news, grouped into themes, and what each could mean for you.', renderIndustry(b, pack));
    html += section('rr-hire', 'Hiring', null, renderHiring(pack));
    html += section('rr-ot', 'Opportunities and threats', null, renderOT(b));
    html += section('rr-watch', 'What to watch next', null, listOf(b.watch));
    html += section('rr-cov', 'Coverage and confidence', 'What was checked, what could not be, and how this report was checked.', renderCoverage(v));
    body.innerHTML = html + '</div>';
  }

  function load() {
    fetch(API + '/clients/' + encodeURIComponent(clientId) + '/report', { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) throw new Error(x.j.error || 'The report could not be loaded.');
        render(x.j);
      })
      .catch(function (e) { body.innerHTML = '<div class="mr-callout bad">' + esc(e.message) + '</div>'; });
  }

  function sendFeedback(btn) {
    var want = btn.getAttribute('data-fb');
    var ids = btn.getAttribute('data-events').split(',').filter(Boolean);
    var value = btn.classList.contains('on') ? null : want;
    var group = btn.parentNode;
    group.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    Promise.all(ids.map(function (id) {
      return fetch(API + '/clients/' + encodeURIComponent(clientId) + '/events/' + encodeURIComponent(id) + '/feedback', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedback: value })
      }).then(function (r) { if (!r.ok) throw new Error('feedback ' + r.status); });
    })).then(function () {
      (state.view.pack.moves || []).forEach(function (m) { if (ids.indexOf(String(m.event_id)) >= 0) m.feedback = value; });
      group.querySelectorAll('button').forEach(function (b) {
        var on = value && b.getAttribute('data-fb') === value;
        b.classList.toggle('on', !!on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }).catch(function () {
      group.setAttribute('title', 'Could not save; try again.');
    }).then(function () {
      group.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
    });
  }

  // The report as printed: every folded part open, no controls, no map,
  // and the company's facts as "Label: value" lines rather than a grid.
  function printable() {
    var doc = document.getElementById('rrDoc').cloneNode(true);
    doc.querySelectorAll('[data-noprint]').forEach(function (n) { n.parentNode.removeChild(n); });
    doc.querySelectorAll('details').forEach(function (d) { d.setAttribute('open', ''); });
    doc.querySelectorAll('.rr-dl').forEach(function (dl) {
      var out = document.createElement('div');
      dl.querySelectorAll('div').forEach(function (row) {
        var p = document.createElement('p'), b = document.createElement('b');
        b.textContent = row.querySelector('dt').textContent + ': ';
        p.appendChild(b);
        p.appendChild(document.createTextNode(row.querySelector('dd').textContent));
        out.appendChild(p);
      });
      dl.parentNode.replaceChild(out, dl);
    });
    var h1 = doc.querySelector('h1');
    if (h1) h1.parentNode.removeChild(h1);
    return doc.innerHTML;
  }

  function downloadPdf(btn) {
    var v = state.view, c = (v.pack || {}).client || {};
    btn.disabled = true;
    btn.textContent = 'Building the PDF…';
    fetch(API + '/clients/' + encodeURIComponent(clientId) + '/report/pdf', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ html: printable(), title: 'Market Radar: ' + (c.name || v.name), subtitle: 'Written ' + day(v.created_at) })
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'The PDF could not be built.'); });
      return r.blob();
    }).then(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'market-radar-' + String(c.name || v.name || 'report').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '.pdf';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    }).catch(function (e) {
      window.alert(e.message);
    }).then(function () {
      btn.disabled = false;
      btn.textContent = 'Download PDF';
    });
  }

  if (body) {
    body.addEventListener('click', function (e) {
      var t = e.target.closest('button');
      if (!t) return;
      if (t.classList.contains('rr-thumb')) sendFeedback(t);
      else if (t.id === 'rrPdf') downloadPdf(t);
    });
    if (clientId) load();
  }

  window.MRR = { render: render, printable: printable, nearbyMap: nearbyMap, esc: esc, state: state };
})();
