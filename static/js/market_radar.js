/* Market Radar edit page (templates/market_radar.html).
 *
 * The render* functions are pure: data in, HTML string out, every value
 * escaped (names, reasons and quotes come from other companies' websites and
 * from a model, so none of it is trusted markup). They are exported as
 * window.MR so tests can run them under Node without a browser. The wiring at
 * the bottom only runs on the page itself.
 */
(function (root) {
  'use strict';

  var API = '/p2/admin/market-radar/api';

  var ARCHETYPES = [
    ['local_single', 'Local business, one site'], ['multi_location', 'Chain with many locations'],
    ['ecommerce', 'Online shop'], ['b2b_services', 'Business services'],
    ['b2b_product', 'Business software or products'], ['manufacturer', 'Manufacturer'], ['other', 'Other']];
  var CUSTOMERS = [['B2C', 'Consumers'], ['B2B', 'Businesses'], ['both', 'Both']];
  var PRICES = [['budget', 'Budget'], ['mid', 'Mid-market'], ['premium', 'Premium'], ['luxury', 'Luxury'],
    ['unknown', 'Not known']];
  var KINDS = [['direct', 'Direct'], ['local', 'Nearby'], ['indirect', 'Indirect'], ['aspirational', 'Bigger player']];
  var KIND_HELP = {
    direct: 'Sells the same thing to the same customers in the same market',
    local: 'Same kind of business near this one',
    indirect: 'A substitute customers compare it with',
    aspirational: 'A much larger player in the same market'
  };
  var STATUS = { proposed: 'Suggested', confirmed: 'Confirmed', removed: 'Removed' };
  var STEPS = ['Reading the website', 'Planning searches', 'Searching', 'Checking sites', 'Ranking', 'Saving'];
  var STAGE_STEP = { queued: 0, profile: 0, rivals_plan: 1, rivals_search: 2, rivals_verify: 3,
    rivals_rank: 4, save: 5, done: 6 };

  // field, label, type, options, wide, hint
  var FIELDS = [
    ['name', 'Name', 'text'],
    ['archetype', 'Type of business', 'select', ARCHETYPES],
    ['one_liner', 'What they do', 'textarea', null, true],
    ['industry.plain_label', 'Industry', 'text'],
    ['customer_type', 'Customers', 'select', CUSTOMERS],
    ['price_positioning', 'Price level', 'select', PRICES],
    ['offerings', 'What they sell', 'list', null, true, 'Separate items with commas.'],
    ['industry.keywords', 'Industry keywords', 'list', null, true, 'Used to find news and competitors. Separate with commas.'],
    ['location_count', 'Number of locations', 'number', null, false, '0 for online only. Leave empty if not known.'],
    ['markets', 'Countries served', 'list', null, false, 'Two-letter codes, such as US, GB.'],
    ['service_area', 'Service area', 'text', null, true],
    ['hq.street', 'Street address', 'text', null, false, 'Where nearby competitors are searched from.'],
    ['hq.postal_code', 'Postcode', 'text'],
    ['hq.city', 'City', 'text'],
    ['hq.region', 'Region or state', 'text'],
    ['hq.country_code', 'Country', 'text', null, false, 'Two-letter code, such as US.']
  ];

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function safeUrl(u) {
    return /^https?:\/\/[^\s"'<>]+$/i.test(String(u || '')) ? String(u) : null;
  }
  function siteUrl(domain) {
    return /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(String(domain || '')) ? 'https://' + domain + '/' : null;
  }
  function label(pairs, value) {
    for (var i = 0; i < pairs.length; i++) if (pairs[i][0] === value) return pairs[i][1];
    return value == null || value === '' ? '' : String(value);
  }
  function money(v) {
    if (v == null) return 'not measured';
    return '$' + Number(v).toFixed(Number(v) < 1 ? 3 : 2);
  }
  function duration(s) {
    if (s == null) return '';
    s = Math.round(Number(s));
    return s < 60 ? s + ' s' : Math.floor(s / 60) + ' min ' + (s % 60) + ' s';
  }
  function when(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function shown(field, value) {
    var def = fieldDef(field);
    if (value == null || value === '' || (Array.isArray(value) && !value.length)) return null;
    if (field === 'location_count') {
      if (value === -1) return null;
      return value === 0 ? 'Online only' : String(value);
    }
    if (Array.isArray(value)) return value.join(', ');
    if (def && def[2] === 'select') return label(def[3], value);
    return String(value);
  }
  function fieldDef(field) {
    for (var i = 0; i < FIELDS.length; i++) if (FIELDS[i][0] === field) return FIELDS[i];
    return null;
  }
  function kindChip(kind) {
    return '<span class="mr-chip k-' + esc(kind) + '" title="' + esc(KIND_HELP[kind] || '') + '">' +
      esc(label(KINDS, kind)) + '</span>';
  }

  // == the rail ======================================================================

  function renderClientList(clients, selectedId) {
    if (!clients || !clients.length) {
      return '<div class="mr-empty"><b>No companies yet</b>Add a website above to start.</div>';
    }
    return clients.map(function (c) {
      var run = c.last_run || {};
      var state = run.status === 'running' ? '<span class="mr-chip acc">Running</span>'
        : run.status === 'failed' ? '<span class="mr-chip bad">Last run failed</span>' : '';
      return '<button type="button" class="mr-co' + (c.client_id === selectedId ? ' on' : '') +
        '" data-act="select" data-id="' + esc(c.client_id) + '">' +
        '<b>' + esc(c.name || c.domain) + '</b><small>' + esc(c.domain) + '</small>' +
        '<span class="mr-co-meta">' +
        (c.confirmed ? '<span class="mr-chip ok">' + esc(c.confirmed) + ' confirmed</span>' : '') +
        (c.proposed ? '<span class="mr-chip">' + esc(c.proposed) + ' to review</span>' : '') +
        state + '</span></button>';
    }).join('');
  }

  // == progress ======================================================================

  function renderProgress(run) {
    var step = STAGE_STEP[run.stage] != null ? STAGE_STEP[run.stage] : 0;
    var steps = STEPS.map(function (name, i) {
      return '<div class="mr-step ' + (i < step ? 'done' : i === step ? 'now' : '') + '">' + esc(name) + '</div>';
    }).join('');
    var note = run.stale ? '<div class="mr-callout bad" style="margin-top:12px">' + esc(run.stale) + '</div>' : '';
    return '<div class="card mr-progress"><div class="mr-progress-line"><span><b>Finding competitors…</b> ' +
      esc(STEPS[Math.min(step, STEPS.length - 1)]) + '</span><span>' +
      (run.cost_usd != null ? 'Spent so far ' + esc(money(run.cost_usd)) : '') + '</span></div>' +
      '<div class="mr-steps">' + steps + '</div>' + note + '</div>';
  }

  // == the profile ===================================================================

  function renderProfile(view, editing, errors) {
    var p = view.profile || {};
    var f = p.fields || {};
    var edited = p.edited || [];
    errors = errors || {};
    if (!p.has_reading && !edited.length) {
      return '<div class="card mr-card"><div class="mr-card-h"><div><h3>Profile</h3></div></div>' +
        '<div class="mr-empty"><b>Not read yet</b>The website has not been read for this company. Run it to build a profile.</div></div>';
    }
    var head = '<div class="mr-card-h"><div><h3>Profile</h3><p>' +
      (p.read_at ? 'Read from the website ' + esc(when(p.read_at)) : 'Read from the website') +
      (edited.length ? '; ' + esc(edited.length) + ' field' + (edited.length > 1 ? 's' : '') + ' corrected by you' : '') +
      '</p></div>' + (editing ? '' : '<button type="button" class="mr-btn small" data-act="edit">Edit profile</button>') + '</div>';

    var checks = (p.checks || []).map(function (c) {
      return '<div class="mr-callout">' + esc(c) + '</div>';
    }).join('');

    var body;
    if (editing) {
      body = '<form class="mr-form" id="mrProfileForm" novalidate><div class="mr-fields">' + FIELDS.map(function (d) {
        var name = d[0], v = f[name], input;
        var id = 'mrf-' + name.replace(/\./g, '-');
        if (d[2] === 'select') {
          input = '<select id="' + id + '" name="' + esc(name) + '">' + d[3].map(function (o) {
            return '<option value="' + esc(o[0]) + '"' + (o[0] === v ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
          }).join('') + '</select>';
        } else if (d[2] === 'textarea') {
          input = '<textarea id="' + id + '" name="' + esc(name) + '" rows="2">' + esc(v || '') + '</textarea>';
        } else {
          var val = Array.isArray(v) ? v.join(', ') : (name === 'location_count' && v === -1 ? '' : (v == null ? '' : v));
          input = '<input type="' + (d[2] === 'number' ? 'number' : 'text') + '" id="' + id + '" name="' + esc(name) +
            '" value="' + esc(val) + '"' + (d[2] === 'number' ? ' min="0" step="1"' : '') + ' />';
        }
        return '<div class="mr-field' + (d[4] ? ' wide' : '') + '"><label class="mr-k" for="' + id + '">' + esc(d[1]) + '</label>' +
          input + (d[5] ? '<div class="mr-hint">' + esc(d[5]) + '</div>' : '') +
          (errors[name] ? '<div class="mr-err">' + esc(errors[name]) + '</div>' : '') + '</div>';
      }).join('') + '</div><div class="mr-form-foot"><span class="mr-hint">Changing the address moves where nearby competitors are searched.</span>' +
        '<button type="button" class="mr-btn ghost" data-act="cancel-edit">Cancel</button>' +
        '<button type="submit" class="mr-btn primary">Save changes</button></div></form>';
    } else {
      body = '<div class="mr-fields">' + FIELDS.map(function (d) {
        var name = d[0], text = shown(name, f[name]), isEdited = edited.indexOf(name) >= 0;
        var was = isEdited ? shown(name, (p.site_reading || {})[name]) : null;
        return '<div class="mr-field' + (d[4] ? ' wide' : '') + '"><div class="mr-k">' + esc(d[1]) +
          (isEdited ? ' <span class="mr-edited">Your correction</span>' : '') + '</div>' +
          '<div class="mr-v' + (text ? '' : ' muted') + '">' + esc(text || 'Not stated') + '</div>' +
          (isEdited ? '<div class="mr-was">The website said: ' + esc(was || 'nothing') +
            ' · <button type="button" class="mr-link" data-act="reset" data-field="' + esc(name) + '">Use the website\'s</button></div>' : '') +
          '</div>';
      }).join('') + '</div>';
    }

    return '<div class="card mr-card">' + head + '<div class="mr-card-b">' + checks + body +
      renderLocation(view) + (editing ? '' : renderEvidence(p)) + '</div></div>';
  }

  function renderLocation(view) {
    var p = view.profile || {}, f = p.fields || {}, pt = p.hq_point;
    var local = f.archetype === 'local_single' || f.archetype === 'multi_location';
    var where = pt ? 'Located ' + ({ exact: 'exactly, from the website\'s own map data', street: 'by street address',
      postcode: 'by postcode', city: 'only by city: nearby competitors may be missed' }[pt.precision] || 'approximately') +
      (pt.label ? ' (' + pt.label + ')' : '') : 'Not located on the map yet';
    var radius = view.client && view.client.radius_km;
    var precisionWarn = pt && pt.precision === 'city' && f.archetype === 'local_single';
    return '<div class="mr-more" style="border-top:1px solid var(--mr-line)"><div class="mr-k">Map location</div>' +
      '<div class="mr-v">' + esc(where) + '</div>' +
      (precisionWarn ? '<div class="mr-callout" style="margin-top:8px">Add the street address so the map search looks in the right neighbourhood.</div>' : '') +
      (local ? '<form class="mr-addrow" id="mrRadiusForm" style="border:0;background:none;padding:10px 0 0">' +
        '<label class="mr-k" for="mrRadius" style="align-self:center">Search radius</label>' +
        '<input type="number" id="mrRadius" min="0.5" max="25" step="0.5" style="flex:0 0 110px" value="' + esc(radius == null ? '' : radius) +
        '" placeholder="Automatic" /> <span class="mr-hint" style="align-self:center">km</span>' +
        '<button type="submit" class="mr-btn small">Save radius</button>' +
        (radius != null ? '<button type="button" class="mr-link" data-act="radius-auto">Back to automatic</button>' : '') +
        '</form>' : '') + '</div>';
  }

  function renderEvidence(p) {
    var quotes = (p.evidence || []).map(function (e) {
      var href = safeUrl(e.url);
      return '<li><span class="mr-k" style="display:inline">' + esc(String(e.field || '').replace(/_/g, ' ')) + '</span><br>' +
        '<q>' + esc(e.quote) + '</q>' + (href ? '<br><a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(href) + '</a>' : '') + '</li>';
    }).join('');
    var unknowns = (p.unknowns || []).map(function (u) { return '<li>' + esc(u) + '</li>'; }).join('');
    var why = p.archetype_reason ? '<p class="mr-hint" style="font-size:13px">Why this type: ' + esc(p.archetype_reason) + '</p>' : '';
    if (!quotes && !unknowns && !why) return '';
    return '<details class="mr-more"><summary>What the website says</summary>' + why +
      (quotes ? '<ul class="mr-quotes">' + quotes + '</ul>' : '') +
      (unknowns ? '<div class="mr-k" style="margin-top:14px">Not stated on the website</div><ul class="mr-list">' + unknowns + '</ul>' : '') +
      '</details>';
  }

  // == competitors ===================================================================

  function counts(rows) {
    var c = { proposed: 0, confirmed: 0, removed: 0 };
    (rows || []).forEach(function (r) { c[r.status] = (c[r.status] || 0) + 1; });
    return c;
  }

  function renderCompetitors(rows, filter) {
    rows = rows || [];
    var c = counts(rows);
    filter = filter || (c.proposed ? 'proposed' : 'confirmed');
    var tabs = [['proposed', 'To review'], ['confirmed', 'Confirmed'], ['removed', 'Removed']].map(function (t) {
      return '<button type="button" class="' + (filter === t[0] ? 'on' : '') + '" data-act="filter" data-filter="' + t[0] + '">' +
        esc(t[1]) + ' ' + esc(c[t[0]] || 0) + '</button>';
    }).join('');
    var list = rows.filter(function (r) { return r.status === filter; });
    var body = list.length ? list.map(renderRow).join('') : '<div class="mr-empty">' + esc({
      proposed: c.confirmed ? 'Nothing left to review.' : 'No suggestions yet. Run the search, or add a competitor below.',
      confirmed: 'None confirmed yet. Confirm suggestions you agree with.',
      removed: 'Nothing removed.'
    }[filter]) + '</div>';
    var bulk = filter === 'proposed' && list.length > 1
      ? '<button type="button" class="mr-btn small ok" data-act="confirm-all">Confirm all ' + esc(list.length) + '</button>' : '';
    return '<div class="card mr-card"><div class="mr-card-h"><div><h3>Competitors</h3>' +
      '<p>Suggested by the agent, checked against each company\'s own website. Confirmed ones will be tracked.</p></div>' +
      '<div class="mr-actions"><div class="mr-seg" role="tablist">' + tabs + '</div>' + bulk + '</div></div>' +
      '<div class="mr-rows">' + body + '</div>' +
      '<form class="mr-addrow" id="mrAddCompetitor" autocomplete="off"><label class="mr-sr" for="mrCompUrl">Competitor website</label>' +
      '<input type="text" id="mrCompUrl" placeholder="Add a competitor by website, such as rival.com" />' +
      '<select id="mrCompKind" aria-label="Kind of competitor">' + KINDS.map(function (k) {
        return '<option value="' + k[0] + '">' + esc(k[1]) + '</option>';
      }).join('') + '</select><button type="submit" class="mr-btn small">Add</button></form></div>';
  }

  function renderRow(r) {
    var href = siteUrl(r.domain);
    var score = r.score != null ? r.score : (r.confidence != null ? Math.round(r.confidence * 100) : null);
    var meta = [];
    if (score != null) meta.push('<span class="mr-score" title="How closely to watch it, out of 100"><i style="--w:' +
      esc(Math.max(0, Math.min(100, score))) + '%"></i>' + esc(score) + '</span>');
    if (r.distance_km != null) meta.push(esc(Number(r.distance_km).toFixed(1)) + ' km away' +
      (r.branches_nearby > 1 ? ' (' + esc(r.branches_nearby) + ' branches nearby)' : ''));
    if (r.location) meta.push(esc(r.location));
    if (r.added_by_user) meta.push('Added by you, not checked');
    else if (r.checked_on) meta.push('Checked on ' + esc(r.checked_on));
    var found = (r.found || []).filter(function (x) { return x !== 'added by you'; });
    if (found.length) meta.push('Found: ' + esc(found.slice(0, 3).join('; ')));
    var act;
    if (r.status === 'removed') {
      act = '<button type="button" class="mr-btn small" data-act="status" data-status="proposed" data-id="' + esc(r.entity_id) + '">Restore</button>';
    } else {
      act = '<select data-act="kind" data-id="' + esc(r.entity_id) + '" aria-label="Kind of competitor">' + KINDS.map(function (k) {
        return '<option value="' + k[0] + '"' + (k[0] === r.kind ? ' selected' : '') + '>' + esc(k[1]) + '</option>';
      }).join('') + '</select>' +
        (r.status === 'proposed' ? '<button type="button" class="mr-btn small ok" data-act="status" data-status="confirmed" data-id="' + esc(r.entity_id) + '">Confirm</button>'
          : '<span class="mr-chip ok">Confirmed</span>') +
        '<button type="button" class="mr-btn small bad" data-act="status" data-status="removed" data-id="' + esc(r.entity_id) + '" aria-label="Remove ' + esc(r.name) + '">Remove</button>';
    }
    return '<div class="mr-row ' + esc(r.status) + '"><div class="mr-row-main"><div class="mr-row-top"><b>' + esc(r.name || r.domain) + '</b>' +
      (href ? '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(r.domain) + '</a>' : esc(r.domain)) +
      kindChip(r.kind) + '</div>' +
      (r.reason ? '<div class="mr-reason">' + esc(r.reason) + '</div>' : (r.sells ? '<div class="mr-reason">' + esc(r.sells) + '</div>' : '')) +
      (meta.length ? '<div class="mr-row-meta">' + meta.join('<span aria-hidden="true">·</span>') + '</div>' : '') +
      '</div><div class="mr-row-act">' + act + '</div></div>';
  }

  // == the last run ==================================================================

  function renderRun(run) {
    if (!run) return '';
    var failed = run.status === 'failed';
    var stats = '<div class="mr-stats">' +
      '<div class="mr-stat"><b>' + esc(failed ? 'Failed' : run.status === 'complete' ? 'Finished' : run.status) + '</b><span>' + esc(when(run.finished_at || run.created_at)) + '</span></div>' +
      (run.seconds != null ? '<div class="mr-stat"><b>' + esc(duration(run.seconds)) + '</b><span>Time taken</span></div>' : '') +
      '<div class="mr-stat"><b>' + esc(money(run.cost_usd)) + '</b><span>Cost' + (run.cost_partial ? ' (at most)' : '') + '</span></div>' +
      (run.checked && run.checked.candidates != null ? '<div class="mr-stat"><b>' + esc(run.checked.judged || 0) + ' of ' + esc(run.checked.candidates) + '</b><span>Candidates checked</span></div>' : '') +
      '</div>';
    var err = failed && run.error ? '<div class="mr-callout bad">' + esc(run.error) + '</div>' : '';
    var srcRows = Object.keys(run.sources || {}).map(function (k) {
      var s = run.sources[k], text;
      if (s.status === 'failed') text = 'Failed: ' + (s.error || 'no reason given');
      else if (s.status === 'skipped' || s.status === 'not_run') text = 'Not used: ' + (s.note || '');
      else if (k === 'places') text = (s.found != null ? s.found + ' places of the same kind within ' + s.radius_km + ' km' : 'Used') + (s.note ? '. ' + s.note : '');
      else if (k === 'search') text = (s.results != null ? s.results + ' results' : 'Used') + (s.status === 'partial' ? ' (some searches failed: ' + (s.error || '') + ')' : '');
      else text = (s.found || 0) + ' found';
      return '<tr><td>' + esc(s.label) + '</td><td>' + esc(text) + '</td></tr>';
    }).join('');
    var unread = (run.unread || []).map(function (u) {
      return '<li><b>' + esc(u.domain) + '</b>: ' + esc(u.why) + '</li>';
    }).join('');
    var gaps = (run.gaps || []).map(function (g) { return '<li>' + esc(g) + '</li>'; }).join('');
    var left = (run.left_out || []).map(function (l) { return '<li><b>' + esc(l.domain) + '</b>: ' + esc(l.why) + '</li>'; }).join('');
    return '<div class="card mr-card"><div class="mr-card-h"><div><h3>Last search</h3><p>Where the suggestions came from, and what could not be checked.</p></div></div>' +
      '<div class="mr-card-b">' + err + stats +
      (srcRows ? '<table class="mr-src"><tbody>' + srcRows + '</tbody></table>' : '') +
      (unread ? '<details class="mr-more" open><summary>Websites that could not be read (' + esc(run.unread.length) + ')</summary>' +
        '<p class="mr-hint">Suggested competitors whose sites refused our reader. They are not on the list because they could not be checked; add any you know are competitors.</p><ul class="mr-list">' + unread + '</ul></details>' : '') +
      (gaps ? '<details class="mr-more"><summary>What this list may miss</summary><ul class="mr-list">' + gaps + '</ul></details>' : '') +
      (left ? '<details class="mr-more"><summary>Considered and left out (' + esc(run.left_out.length) + ')</summary><ul class="mr-list">' + left + '</ul></details>' : '') +
      '</div></div>';
  }

  function renderHead(view, running) {
    var c = view.client || {}, p = view.profile || {}, f = p.fields || {};
    var href = siteUrl(c.domain);
    return '<div class="mr-head"><div><h2>' + esc(f.name || c.name || c.domain) + '</h2><div class="mr-head-sub">' +
      (href ? '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(c.domain) + '</a>' : esc(c.domain)) +
      (f.archetype ? '<span class="mr-chip">' + esc(label(ARCHETYPES, f.archetype)) + '</span>' : '') +
      (f['hq.city'] ? '<span>' + esc([f['hq.city'], f['hq.country_code']].filter(Boolean).join(', ')) + '</span>' : '') +
      '</div></div><div class="mr-actions"><button type="button" class="mr-btn" data-act="run"' + (running ? ' disabled' : '') + '>' +
      (running ? 'Running…' : 'Search again') + '</button></div></div>';
  }

  function renderDetail(state) {
    var v = state.view;
    if (!v) return '<div class="card"><div class="mr-empty"><b>Pick a company</b>Or add one by its website above.</div></div>';
    var running = state.run && state.run.status === 'running';
    return renderHead(v, running) + (running ? renderProgress(state.run) : '') +
      renderProfile(v, state.editing, state.errors) + renderCompetitors(v.competitors, state.filter) +
      (running ? '' : renderRun(v.last_run));
  }

  /** The edits a submitted form makes: only fields whose value changed. */
  function formChanges(values, current) {
    var out = {};
    FIELDS.forEach(function (d) {
      var name = d[0];
      if (!(name in values)) return;
      var v = values[name], cur = current[name];
      if (d[2] === 'list') {
        var list = String(v).split(/[,\n]/).map(function (x) { return x.trim(); }).filter(Boolean);
        if (JSON.stringify(list) !== JSON.stringify(cur || [])) out[name] = list;
      } else if (d[2] === 'number') {
        var n = String(v).trim() === '' ? -1 : Number(v);
        if (n !== (cur == null ? -1 : cur)) out[name] = String(v).trim() === '' ? '' : n;
      } else if (String(v).trim() !== String(cur == null ? '' : cur)) {
        out[name] = String(v).trim();
      }
    });
    return out;
  }

  var MR = { esc: esc, safeUrl: safeUrl, siteUrl: siteUrl, renderClientList: renderClientList,
    renderProgress: renderProgress, renderProfile: renderProfile, renderCompetitors: renderCompetitors,
    renderRow: renderRow, renderRun: renderRun, renderDetail: renderDetail, formChanges: formChanges,
    FIELDS: FIELDS, STAGE_STEP: STAGE_STEP };
  root.MR = MR;

  // == the page ======================================================================

  if (typeof document === 'undefined' || !document.getElementById('mr')) return;

  var state = { clients: [], selected: null, view: null, filter: null, editing: false, errors: null,
    run: null, poll: null };
  var $ = function (id) { return document.getElementById(id); };

  function api(path, body) {
    var opts = body === undefined ? { credentials: 'same-origin' }
      : { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    return fetch(API + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || ('Request failed (' + r.status + ')')); e.data = j; e.status = r.status; throw e; }
        return j;
      });
    });
  }
  function toast(text) {
    var t = document.createElement('div');
    t.className = 'mr-toast'; t.setAttribute('role', 'status'); t.textContent = text;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 4200);
  }
  function ask(title, text, optText) {
    return new Promise(function (resolve) {
      $('mrModalTitle').textContent = title; $('mrModalText').textContent = text;
      $('mrModalOptWrap').style.display = optText ? '' : 'none';
      $('mrModalOpt').checked = false; $('mrModalOptText').textContent = optText || '';
      var bg = $('mrModal'); bg.classList.add('open'); $('mrModalYes').focus();
      function done(ok) {
        bg.classList.remove('open');
        $('mrModalYes').onclick = $('mrModalNo').onclick = null; document.removeEventListener('keydown', onKey);
        resolve(ok ? { option: $('mrModalOpt').checked } : null);
      }
      function onKey(e) { if (e.key === 'Escape') done(false); }
      document.addEventListener('keydown', onKey);
      $('mrModalYes').onclick = function () { done(true); };
      $('mrModalNo').onclick = function () { done(false); };
    });
  }

  function paintRail() { $('mrClients').innerHTML = renderClientList(state.clients, state.selected); }
  function paint() { $('mrDetail').innerHTML = renderDetail(state); }

  function loadClients() {
    return api('/clients').then(function (j) { state.clients = j.clients || []; paintRail(); })
      .catch(function (e) { $('mrClients').innerHTML = '<div class="mr-empty">' + esc(e.message) + '</div>'; });
  }
  function select(id) {
    state.selected = id; state.filter = null; state.editing = false; state.errors = null;
    try { history.replaceState(null, '', '#company-' + id); } catch (e) { /* ignore */ }
    paintRail();
    $('mrDetail').innerHTML = '<div class="card"><div class="mr-empty">Loading…</div></div>';
    return loadView();
  }
  function loadView() {
    var id = state.selected;
    return api('/clients/' + id).then(function (v) {
      if (state.selected !== id) return;
      state.view = v;
      var last = v.last_run;
      if (last && last.status === 'running') watch(last.id); else { state.run = null; paint(); }
    }).catch(function (e) {
      $('mrDetail').innerHTML = '<div class="card"><div class="mr-empty">' + esc(e.message) + '</div></div>';
    });
  }
  function watch(runId) {
    clearTimeout(state.poll);
    var forClient = state.selected;
    function tick() {
      api('/runs/' + runId).then(function (r) {
        if (state.selected !== forClient) return;
        state.run = r;
        if (r.status === 'running' && !r.stale) { paint(); state.poll = setTimeout(tick, 4000); return; }
        state.run = null;
        toast(r.status === 'complete' ? 'Search finished.' : 'The search did not finish: ' + (r.error || r.stale || r.status));
        loadClients(); loadView();
      }).catch(function () { state.poll = setTimeout(tick, 8000); });
    }
    tick();
  }
  function startRun(body, title, text, optText) {
    return ask(title, text, optText).then(function (ok) {
      if (!ok) return;
      body.confirm_spend = true;
      if (optText) body.refresh_profile = ok.option;
      return api('/runs', body).then(function (j) {
        return loadClients().then(function () {
          if (j.client_id) {
            state.selected = j.client_id; paintRail();
            state.view = state.view && state.view.client && state.view.client.id === j.client_id ? state.view : null;
            return api('/clients/' + j.client_id).then(function (v) { state.view = v; watch(j.run_id); });
          }
        });
      }).catch(function (e) {
        if (e.status === 409 && e.data && e.data.run_id) { toast(e.message); watch(e.data.run_id); return; }
        toast(e.message);
      });
    });
  }

  $('mrAddForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var url = $('mrAddUrl').value.trim();
    if (!url) { $('mrAddUrl').focus(); return; }
    startRun({ url: url }, 'Find competitors for ' + url + '?',
      'This reads the website and searches for competitors: about $0.12 and 2 to 4 minutes. If the company is already on your list, its stored profile is reused.')
      .then(function () { $('mrAddUrl').value = ''; });
  });

  function setStatus(entityId, status, kind) {
    var body = {}; if (status) body.status = status; if (kind) body.kind = kind;
    return api('/clients/' + state.selected + '/competitors/' + entityId, body).then(function (j) {
      state.view.competitors = j.competitors; paint(); loadClients();
    }).catch(function (e) { toast(e.message); });
  }
  function saveProfile(body) {
    return api('/clients/' + state.selected + '/profile', body).then(function (j) {
      state.view = j.view; state.editing = false; state.errors = null; paint(); loadClients();
      toast((j.notes || []).join(' ') || 'Saved.');
    }).catch(function (e) {
      if (e.data && e.data.fields) { state.errors = e.data.fields; paint(); }
      toast(e.message);
    });
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-act]');
    if (!el || !$('mr').parentNode.contains(el)) return;
    var act = el.getAttribute('data-act');
    if (act === 'select') select(Number(el.getAttribute('data-id')));
    else if (act === 'filter') { state.filter = el.getAttribute('data-filter'); paint(); }
    else if (act === 'status') setStatus(el.getAttribute('data-id'), el.getAttribute('data-status'));
    else if (act === 'edit') { state.editing = true; state.errors = null; paint(); var first = document.querySelector('#mrProfileForm input'); if (first) first.focus(); }
    else if (act === 'cancel-edit') { state.editing = false; state.errors = null; paint(); }
    else if (act === 'reset') saveProfile({ reset: [el.getAttribute('data-field')] });
    else if (act === 'radius-auto') saveProfile({ radius_km: null });
    else if (act === 'confirm-all') {
      var ids = state.view.competitors.filter(function (r) { return r.status === 'proposed'; }).map(function (r) { return r.entity_id; });
      ids.reduce(function (p, id) { return p.then(function () { return api('/clients/' + state.selected + '/competitors/' + id, { status: 'confirmed' }); }); }, Promise.resolve())
        .then(function () { toast('Confirmed ' + ids.length + '.'); state.filter = 'confirmed'; return loadView(); })
        .then(loadClients).catch(function (err) { toast(err.message); loadView(); });
    } else if (act === 'run') {
      var name = (state.view.profile.fields || {}).name || state.view.client.domain;
      startRun({ client_id: state.selected }, 'Search again for ' + name + '?',
        'Searches for competitors again using the profile with your corrections: about $0.07 and 2 to 4 minutes. Competitors you confirmed or removed keep your decision.',
        'Also re-read the website (about $0.05 more). Your corrections still apply.');
    }
  });
  document.addEventListener('change', function (e) {
    var el = e.target;
    if (el.getAttribute && el.getAttribute('data-act') === 'kind') setStatus(el.getAttribute('data-id'), null, el.value);
  });
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form.id === 'mrProfileForm') {
      e.preventDefault();
      var values = {};
      Array.prototype.forEach.call(form.elements, function (el) { if (el.name) values[el.name] = el.value; });
      var changes = formChanges(values, state.view.profile.fields || {});
      if (!Object.keys(changes).length) { state.editing = false; paint(); return; }
      saveProfile({ changes: changes });
    } else if (form.id === 'mrRadiusForm') {
      e.preventDefault();
      var v = $('mrRadius').value.trim();
      saveProfile({ radius_km: v === '' ? null : Number(v) });
    } else if (form.id === 'mrAddCompetitor') {
      e.preventDefault();
      var url = $('mrCompUrl').value.trim();
      if (!url) return;
      api('/clients/' + state.selected + '/competitors', { url: url, kind: $('mrCompKind').value }).then(function (j) {
        state.view.competitors = j.competitors; state.filter = 'confirmed'; paint(); loadClients(); toast('Added.');
      }).catch(function (err) { toast(err.message); });
    }
  });

  loadClients().then(function () {
    var m = /#company-(\d+)/.exec(location.hash);
    var id = m ? Number(m[1]) : (state.clients[0] && state.clients[0].client_id);
    if (id && state.clients.some(function (c) { return c.client_id === id; })) select(id);
    else paint();
  });
})(typeof window !== 'undefined' ? window : globalThis);
