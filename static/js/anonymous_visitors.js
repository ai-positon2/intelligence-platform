/* Anonymous Visitors — page logic (extracted) */
/* STATE */
  var _allPeople=[], _allCompanies=[], _activeTab='people';
  var _filteredPeople=[], _filteredCompanies=[];

  /* HELPERS */
  function initials(n){return(n||'?').split(' ').map(function(w){return w[0]||'';}).slice(0,2).join('').toUpperCase();}
  function pagesClass(p){var n=parseInt(p)||0;return n>=30?'pages-hi':n>=15?'pages-mid':'pages-lo';}
  function engLabel(p){var n=parseInt(p)||0;return n>=30?'High':n>=15?'Medium':'Low';}
  function engColor(p){var n=parseInt(p)||0;return n>=30?'#34d399':n>=15?'#818cf8':'#64748b';}
  function engPct(p){var n=parseInt(p)||0;return Math.min(100,Math.round(n/60*100));}
  function getSeniority(title){
    var t=(title||'').toLowerCase();
    if(/\b(ceo|cto|cfo|coo|cmo|ciso|cpo|cro|chief)\b/.test(t))return 'C-Suite';
    if(/\b(president|founder|co-founder|owner|chairman)\b/.test(t))return 'Founder/President';
    if(/\bvice president\b|^vp\b|\bsvp\b|\bevp\b|\bavp\b/.test(t)||/ vp,/.test(t)||/ vp /.test(t))return 'VP';
    if(/\bdirector\b/.test(t))return 'Director';
    if(/\bmanager\b/.test(t))return 'Manager';
    return 'Other';
  }
  function senClass(s){return{'C-Suite':'sen-c','Founder/President':'sen-fp','VP':'sen-vp','Director':'sen-dir','Manager':'sen-mgr','Other':'sen-oth'}[s]||'sen-oth';}
  function fmtRevenue(r){if(!r||r==='Unavailable')return'—';var m=r.match(/\$([\d,]+)/);if(!m)return r;var n=parseInt(m[1].replace(/,/g,''));if(n>=1000000000)return'$'+(n/1e9).toFixed(1)+'B';if(n>=1000000)return'$'+(n/1e6).toFixed(0)+'M';return'$'+(n/1000).toFixed(0)+'K';}
  function fmtEmp(e){if(!e||e==='Unavailable')return'—';var n=parseInt(e);if(isNaN(n))return e;return n>=1000?(n/1000).toFixed(1)+'K':n+'';}
  function empSize(e){var n=parseInt(e)||0;return n>=1000?'enterprise':n>=200?'mid':'smb';}
  function empSizeLabel(e){return{enterprise:'Enterprise (1000+)',mid:'Mid-Market (200–999)',smb:'SMB (<200)'}[empSize(e)]||'-';}
  function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
  function normDomain(u){return(u||'').toLowerCase().replace(/^https?:\/\//,'').replace(/\/$/,'').replace(/^www\./,'');}
  function liUrl(n){return'https://www.linkedin.com/search/results/people/?keywords='+encodeURIComponent(n||'');}

  /* DRAWER */
  function openDrawer(){document.getElementById('drawer').classList.add('open');document.getElementById('drawerBackdrop').classList.add('open');document.body.style.overflow='hidden';}
  function closeDrawer(){document.getElementById('drawer').classList.remove('open');document.getElementById('drawerBackdrop').classList.remove('open');document.body.style.overflow='';}
  document.addEventListener('keydown',function(e){if(e.key==='Escape')closeDrawer();});

  /* PERSON DRAWER */
  function openPersonDrawer(idx, fromAll){
    var src=fromAll?_allPeople:_filteredPeople;
    var p=src[idx]; if(!p)return;
    (function(){var comp=(p.website||'').replace(/^www\./,'').split('.')[0];comp=comp?comp.charAt(0).toUpperCase()+comp.slice(1):(p.industry||'this company');var tdisp=(p.time_raw||'').replace('T',' ').substring(11,16);window._kePerson={name:p.name,title:p.title||'',company:comp,domain:p.website||'',email:p.email||'',source:'anonymous',act:{pages:parseInt(p.pages)||0,when:[p.date,tdisp].filter(Boolean).join(' ')}};})();
    if(window.KairoEngage&&window._kePerson){window.KairoEngage.open(window._kePerson);return;}
    var sen=getSeniority(p.title);
    var col=engColor(p.pages);
    var pct=engPct(p.pages);
    var pages=parseInt(p.pages)||0;
    var colleagues=[];
    if(p.website){_allPeople.forEach(function(q,qi){if(normDomain(q.website)===normDomain(p.website)&&q.name!==p.name)colleagues.push({person:q,idx:qi});});}
    document.getElementById('drwHead').innerHTML=
      '<div class="drw-avatar-wrap">'+
        '<div class="drw-av-lg drw-av-person">'+esc(initials(p.name))+'</div>'+
        '<div class="drw-title-block">'+
          '<div class="drw-name">'+esc(p.name)+'</div>'+
          '<div class="drw-sub">'+esc(p.title||'No title available')+'</div>'+
        '</div>'+
      '</div>'+
      '<button class="drw-close" onclick="closeDrawer()">✕</button>';
    var h='';
    h+='<div class="ke-drw-banner" onclick="keOpen()"><div class="ke-drw-spark">✦</div><div class="ke-drw-tx"><div class="ke-drw-h">Kairo found a CRM match</div><div class="ke-drw-s">In your HubSpot · flagged as prospective client · tap to engage</div></div><div class="ke-drw-go">Engage →</div></div>';
    h+='<div class="drw-section"><div class="drw-chips">';
    h+='<span class="seniority-badge '+senClass(sen)+'">'+esc(sen)+'</span>';
    if(p.industry)h+='<span class="drw-chip purple">'+esc(p.industry)+'</span>';
    h+='</div></div>';
    h+='<div class="drw-section"><div class="drw-section-label">Contact</div><div class="drw-grid">';
    h+='<div class="drw-info-box"><div class="drw-info-label">Location</div><div class="drw-info-value'+(p.location?'':' muted')+'">'+esc(p.location||'Unknown')+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">Email</div><div class="drw-info-value'+(p.email?'':' muted')+'">'+esc(p.email||'Not available')+'</div></div>';
    h+='</div></div>';
    var timeDisp=(p.time_raw||'').replace('T',' ').substring(11,16);
    h+='<div class="drw-section"><div class="drw-section-label">Visit Details</div><div class="drw-grid">';
    h+='<div class="drw-info-box"><div class="drw-info-label">Visit Date</div><div class="drw-info-value">'+esc(p.date||'-')+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">Visit Time</div><div class="drw-info-value">'+(timeDisp?esc(timeDisp):'<span class=\"muted\">—</span>')+'</div></div>';
    h+='</div></div>';
    h+='<div class="drw-section"><div class="drw-section-label">Company</div>';
    h+='<div class="drw-info-box full"><div style="display:flex;align-items:center;gap:12px">';
    h+='<div class="company-av">'+esc(initials(p.website||'?'))+'</div>';
    h+='<div style="flex:1">';
    if(p.website)h+='<div class="drw-info-value"><a href="https://'+esc(p.website)+'" target="_blank" style="color:#a5b4fc;text-decoration:none">'+esc(p.website)+' ↗</a></div>';
    else h+='<div class="drw-info-value muted">Unknown company</div>';
    if(p.industry)h+='<div class="drw-info-label" style="margin-bottom:0;margin-top:3px">'+esc(p.industry)+'</div>';
    h+='</div>';
    if(p.website)h+='<button class="drw-btn drw-btn-ghost" style="margin-left:auto;white-space:nowrap" onclick="filterByCompany(\''+esc(p.website)+'\')">All visitors →</button>';
    h+='</div></div></div>';
    if(colleagues.length){
      h+='<div class="drw-section"><div class="drw-section-label">Others from this company ('+colleagues.length+')</div><div class="drw-person-list">';
      colleagues.slice(0,5).forEach(function(c){
        var cs=getSeniority(c.person.title);
        h+='<div class="drw-person-item" onclick="openPersonDrawer('+c.idx+',true)">';
        h+='<div class="drw-person-av-sm">'+esc(initials(c.person.name))+'</div>';
        h+='<div style="flex:1;min-width:0"><div class="drw-person-name-sm">'+esc(c.person.name)+'</div><div class="drw-person-title-sm">'+esc(c.person.title||'-')+'</div></div>';
        h+='<div class="drw-person-meta"><span class="seniority-badge '+senClass(cs)+'" style="font-size:9px">'+esc(cs)+'</span><span style="font-size:10px;color:#64748b">'+esc(c.person.date||'')+'</span></div>';
        h+='</div>';
      });
      if(colleagues.length>5)h+='<div class="drw-empty" style="padding:10px">+'+(colleagues.length-5)+' more</div>';
      h+='</div></div>';
    }
    h+='<div class="drw-section"><div class="drw-section-label">Quick Actions</div><div class="drw-actions">';
    h+='<button class="drw-btn ke-drw-btn" onclick="keOpen()">✦ Engage with Kairo</button>';
    h+='<a class="drw-btn drw-btn-secondary" href="'+liUrl(p.name)+'" target="_blank">🔗 Search LinkedIn</a>';
    if(p.email)h+='<a class="drw-btn drw-btn-secondary" href="mailto:'+esc(p.email)+'">✉ Send Email</a>';
    if(p.website)h+='<a class="drw-btn drw-btn-ghost" href="https://'+esc(p.website)+'" target="_blank">🌐 Website</a>';
    h+='</div></div>';
    document.getElementById('drwBody').innerHTML=h;
    openDrawer();
  }

  /* COMPANY DRAWER */
  function openCompanyDrawer(idx, fromAll){
    var src=fromAll?_allCompanies:_filteredCompanies;
    var c=src[idx]; if(!c)return;
    var visitors=[];
    _allPeople.forEach(function(p,pi){if(p.website&&c.website&&normDomain(p.website)===normDomain(c.website))visitors.push({person:p,idx:pi});});
    var loc=[c.city,c.state].filter(Boolean).join(', ')||c.country||'-';
    document.getElementById('drwHead').innerHTML=
      '<div class="drw-avatar-wrap">'+
        '<div class="drw-av-lg drw-av-company">'+esc(initials(c.name))+'</div>'+
        '<div class="drw-title-block">'+
          '<div class="drw-name">'+esc(c.name)+'</div>'+
          '<div class="drw-sub">'+esc(c.website||'No website on record')+'</div>'+
        '</div>'+
      '</div>'+
      '<button class="drw-close" onclick="closeDrawer()">✕</button>';
    var h='';
    h+='<div class="drw-section"><div class="drw-chips">';
    if(c.industry)h+='<span class="drw-chip purple">'+esc(c.industry)+'</span>';
    if(c.employees&&c.employees!=='Unavailable')h+='<span class="drw-chip amber">'+empSizeLabel(c.employees)+'</span>';
    h+='</div></div>';
    h+='<div class="drw-section"><div class="drw-section-label">Company Overview</div><div class="drw-grid">';
    h+='<div class="drw-info-box"><div class="drw-info-label">Industry</div><div class="drw-info-value'+(c.industry?'':' muted')+'">'+esc(c.industry||'Unknown')+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">Location</div><div class="drw-info-value'+(loc!=='—'?'':' muted')+'">'+esc(loc)+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">Employees</div><div class="drw-info-value">'+(c.employees&&c.employees!=='Unavailable'?fmtEmp(c.employees):'<span class="muted">—</span>')+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">Revenue</div><div class="drw-info-value">'+esc(fmtRevenue(c.revenue))+'</div></div>';
    if(c.country&&c.country!==c.state)h+='<div class="drw-info-box"><div class="drw-info-label">Country</div><div class="drw-info-value">'+esc(c.country)+'</div></div>';
    if(c.website)h+='<div class="drw-info-box"><div class="drw-info-label">Website</div><div class="drw-info-value"><a href="https://'+esc(c.website)+'" target="_blank" style="color:#a5b4fc;text-decoration:none">'+esc(c.website)+' ↗</a></div></div>';
    h+='</div></div>';

    h+='<div class="drw-section"><div class="drw-section-label">Identified Visitors'+(visitors.length?' ('+visitors.length+')':'')+'</div>';
    if(visitors.length){
      h+='<div class="drw-person-list">';
      visitors.forEach(function(v){
        var vs=getSeniority(v.person.title);
        h+='<div class="drw-person-item" onclick="openPersonDrawer('+v.idx+',true)">';
        h+='<div class="drw-person-av-sm">'+esc(initials(v.person.name))+'</div>';
        h+='<div style="flex:1;min-width:0"><div class="drw-person-name-sm">'+esc(v.person.name)+'</div><div class="drw-person-title-sm">'+esc(v.person.title||'-')+'</div></div>';
        h+='<div class="drw-person-meta"><span class="seniority-badge '+senClass(vs)+'" style="font-size:9px">'+esc(vs)+'</span><span class="pages-pill '+pagesClass(v.person.pages)+'" style="font-size:9px">'+esc(v.person.pages||'0')+'p</span></div>';
        h+='</div>';
      });
      h+='</div>';
    }else{h+='<div class="drw-empty">No individual visitors matched yet.</div>';}
    h+='</div>';
    h+='<div class="drw-section"><div class="drw-section-label">Quick Actions</div><div class="drw-actions">';
    if(c.website)h+='<a class="drw-btn drw-btn-primary" href="https://'+esc(c.website)+'" target="_blank">🌐 Website</a>';
    h+='<a class="drw-btn drw-btn-secondary" href="https://www.linkedin.com/company/'+encodeURIComponent(c.name||'')+'" target="_blank">🔗 LinkedIn</a>';
    if(c.industry)h+='<button class="drw-btn drw-btn-ghost" onclick="filterByIndustry(\''+esc(c.industry)+'\')">🏭 Filter by Industry</button>';
    h+='</div></div>';
    document.getElementById('drwBody').innerHTML=h;
    openDrawer();
  }

  /* INDUSTRY DRAWER */
  function openIndustryDrawer(industry){
    var cos=_allCompanies.filter(function(c){return c.industry===industry;});
    var ppl=_allPeople.filter(function(p){return p.industry===industry;});
    document.getElementById('drwHead').innerHTML=
      '<div class="drw-avatar-wrap">'+
        '<div class="drw-av-lg drw-av-industry">🏭</div>'+
        '<div class="drw-title-block">'+
          '<div class="drw-name">'+esc(industry)+'</div>'+
          '<div class="drw-sub">Industry details</div>'+
        '</div>'+
      '</div>'+
      '<button class="drw-close" onclick="closeDrawer()">✕</button>';
    var h='';
    var totalPg=ppl.reduce(function(s,p){return s+(parseInt(p.pages)||0);},0);
    h+='<div class="drw-section"><div class="drw-section-label">Industry Stats</div><div class="drw-grid">';
    h+='<div class="drw-info-box"><div class="drw-info-label">Companies</div><div class="drw-info-value" style="color:#a5b4fc">'+cos.length+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">People</div><div class="drw-info-value" style="color:#6ee7b7">'+ppl.length+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">Total Pages</div><div class="drw-info-value">'+totalPg+'</div></div>';
    h+='<div class="drw-info-box"><div class="drw-info-label">Avg Pages</div><div class="drw-info-value">'+(ppl.length?Math.round(totalPg/ppl.length):0)+'</div></div>';
    h+='</div></div>';
    var senCounts={};
    ppl.forEach(function(p){var s=getSeniority(p.title);senCounts[s]=(senCounts[s]||0)+1;});
    if(Object.keys(senCounts).length){
      h+='<div class="drw-section"><div class="drw-section-label">Seniority Mix</div><div class="drw-chips">';
      Object.keys(senCounts).sort(function(a,b){return senCounts[b]-senCounts[a];}).forEach(function(s){
        h+='<span class="seniority-badge '+senClass(s)+'">'+esc(s)+' ('+senCounts[s]+')</span> ';
      });
      h+='</div></div>';
    }
    if(cos.length){
      h+='<div class="drw-section"><div class="drw-section-label">Companies ('+cos.length+')</div><div class="drw-company-list">';
      cos.slice(0,10).forEach(function(c){
        var fidx=_allCompanies.indexOf(c);
        h+='<div class="drw-company-item" onclick="openCompanyDrawer('+fidx+',true)">';
        h+='<div class="drw-company-av-sm">'+esc(initials(c.name))+'</div>';
        h+='<div style="flex:1;min-width:0"><div class="drw-person-name-sm">'+esc(c.name)+'</div><div class="drw-person-title-sm">'+esc(c.website||c.country||'')+'</div></div>';
        if(c.employees&&c.employees!=='Unavailable')h+='<span class="drw-chip amber" style="font-size:10px">'+esc(fmtEmp(c.employees))+'</span>';
        h+='</div>';
      });
      if(cos.length>10)h+='<div class="drw-empty" style="padding:10px">+'+(cos.length-10)+' more</div>';
      h+='</div></div>';
    }
    h+='<div class="drw-section"><div class="drw-actions">';
    h+='<button class="drw-btn drw-btn-primary" onclick="filterByIndustry(\''+esc(industry)+'\')">Show all in table →</button>';
    h+='</div></div>';
    document.getElementById('drwBody').innerHTML=h;
    openDrawer();
  }

  /* FILTER SHORTCUTS */
  function filterByIndustry(ind){
    closeDrawer();
    document.getElementById('industryFilter').value=ind.toLowerCase();
    applyFilters();
    document.getElementById('c-main').scrollIntoView({behavior:'smooth'});
  }
  function filterByCompany(website){
    closeDrawer();
    switchTab('people');
    document.getElementById('searchInput').value=website;
    applyFilters();
    document.getElementById('c-main').scrollIntoView({behavior:'smooth'});
  }
  function onStatClick(type){
    if(type==='people'){switchTab('people');document.getElementById('c-main').scrollIntoView({behavior:'smooth'});}
    else if(type==='companies'){switchTab('companies');document.getElementById('c-main').scrollIntoView({behavior:'smooth'});}
    else if(type==='top-industry'){var t=document.getElementById('sv-industry').textContent;if(t&&t!=='—')openIndustryDrawer(t);}
  }

  /* RENDER — INDUSTRY BARS */
  function renderIndustries(industries){
    if(!industries||!industries.length)return'<div class="empty">No data</div>';
    var max=industries[0][1];
    return'<div class="bar-list">'+industries.map(function(i){
      var pct=Math.round(i[1]/max*100);
      var iEsc=i[0].replace(/\\/g,'\\\\').replace(/'/g,"\\'");
      return'<div class="bar-row" onclick="openIndustryDrawer(\''+iEsc+'\')" title="Explore '+esc(i[0])+'">'+
        '<span class="bar-name" title="'+esc(i[0])+'">'+esc(i[0])+'</span>'+
        '<div class="bar-track"><div class="bar-fill" data-w="'+pct+'" style="width:0%"></div></div>'+
        '<span class="bar-num">'+i[1]+'</span></div>';
    }).join('')+'</div>';
  }

  /* RENDER — RECENT VISITORS */
  function renderRecent(people){
    if(!people||!people.length)return'<div class="empty">No data</div>';
    return people.slice(0,5).map(function(p){
      var sen=getSeniority(p.title);
      var ri=_allPeople.indexOf(p);
      return'<div class="recent-row" onclick="openPersonDrawer('+ri+',true)" title="Click to view profile">'+
        '<div class="person-av">'+esc(initials(p.name))+'</div>'+
        '<div style="flex:1;min-width:0">'+
          '<div style="font-size:12px;font-weight:600;color:#f1f5f9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+esc(p.name)+'</div>'+
          '<div style="font-size:11px;color:#64748b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+esc(p.title)+'</div>'+
        '</div>'+
        '<span class="seniority-badge '+senClass(sen)+'">'+esc(sen)+'</span>'+
        '<div style="font-size:10px;color:#64748b;margin-left:8px;white-space:nowrap">'+esc(p.date)+'</div>'+
        '<div style="font-size:14px;color:#334155;margin-left:6px">›</div>'+
      '</div>';
    }).join('');
  }

  /* RENDER — PEOPLE TABLE */
  function renderPeople(rows){
    if(!rows||!rows.length)return'<div class="empty">No matching visitors found.</div>';
    var cols='<colgroup>'+
      '<col class="c-person"><col class="c-sen"><col class="c-company">'+
      '<col class="c-location"><col class="c-industry"><col class="c-date">'+
      '</colgroup>';
    var h='<thead><tr><th>Person</th><th>Seniority</th><th>Company</th><th>Location</th><th>Industry</th><th>Date</th></tr></thead>';
    var b='<tbody>'+rows.map(function(p,i){
      var sen=getSeniority(p.title);
      return'<tr style="animation:row-in .22s ease '+(Math.min(i,40)*9)+'ms both" onclick="openPersonDrawer('+i+')" title="Click to view full profile">'+
        '<td style="overflow:hidden"><div class="person-cell">'+
          '<div class="person-av" style="flex-shrink:0">'+esc(initials(p.name))+'</div>'+
          '<div style="min-width:0;overflow:hidden">'+
            '<div class="person-name" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(p.name)+'</div>'+
            '<div class="person-title" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(p.title)+'</div>'+
          '</div></div></td>'+
        '<td><span class="seniority-badge '+senClass(sen)+'">'+esc(sen)+'</span></td>'+
        '<td>'+(p.website?'<span class="domain-pill">'+esc(p.website)+'</span>':'—')+'</td>'+
        '<td style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(p.location)+'</td>'+
        '<td style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#cbd5e1">'+esc(p.industry)+'</td>'+
        
        '<td style="color:#64748b;white-space:nowrap">'+esc(p.date)+'</td>'+
      '</tr>';
    }).join('')+'</tbody>';
    return'<div class="tbl-wrap"><table>'+cols+h+b+'</table></div>';
  }

  /* RENDER — COMPANIES TABLE */
  function renderCompanies(rows){
    if(!rows||!rows.length)return'<div class="empty">No matching companies found.</div>';
    var cols='<colgroup><col style="width:260px"><col style="width:220px"><col style="width:200px"><col style="width:100px"><col style="width:110px"></colgroup>';
    var h='<thead><tr><th>Company</th><th>Industry</th><th>Location</th><th>Employees</th><th>Revenue</th></tr></thead>';
    var b='<tbody>'+rows.map(function(c,i){
      var loc=[c.city,c.state].filter(Boolean).join(', ')||c.country||'-';
      return'<tr style="animation:row-in .22s ease '+(Math.min(i,40)*9)+'ms both" onclick="openCompanyDrawer('+i+')" title="Click to view company">'+
        '<td><div class="company-cell">'+
          '<div class="company-av">'+esc(initials(c.name))+'</div>'+
          '<div style="min-width:0;overflow:hidden"><div class="company-name" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(c.name)+'</div><div class="company-site" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(c.website)+'</div></div>'+
        '</div></td>'+
        '<td style="color:#cbd5e1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(c.industry)+'</td>'+
        '<td style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(loc)+'</td>'+
        '<td style="color:#94a3b8">'+fmtEmp(c.employees)+'</td>'+
        '<td><span class="revenue-badge">'+fmtRevenue(c.revenue)+'</span></td>'+
      '</tr>';
    }).join('')+'</tbody>';
    return'<div class="tbl-wrap"><table>'+cols+h+b+'</table></div>';
  }

  /* FILTERS */
  function clearFilters(){
    document.getElementById('searchInput').value='';
    ['dateFrom','dateTo'].forEach(function(id){document.getElementById(id).value='';});
    ['industryFilter','seniorityFilter','engagementFilter','locationFilter','sizeFilter'].forEach(function(id){document.getElementById(id).value='';});
    applyFilters();
  }
  function applyFilters(){
    var q=(document.getElementById('searchInput').value||'').toLowerCase();
    var ind=document.getElementById('industryFilter').value.toLowerCase();
    var sen=document.getElementById('seniorityFilter').value;
    var eng=document.getElementById('engagementFilter').value;
    var loc=document.getElementById('locationFilter').value.toLowerCase();
    var sz=document.getElementById('sizeFilter').value;
    var df=document.getElementById('dateFrom').value;
    var dt=document.getElementById('dateTo').value;
    if(_activeTab==='people'){
      _filteredPeople=_allPeople.filter(function(p){
        if(q&&(p.name+p.title+p.website+p.location+p.industry).toLowerCase().indexOf(q)===-1)return false;
        if(ind&&p.industry.toLowerCase()!==ind)return false;
        if(sen&&getSeniority(p.title)!==sen)return false;
        if(eng){var n=parseInt(p.pages)||0;if(eng==='high'&&n<30)return false;if(eng==='mid'&&(n<15||n>=30))return false;if(eng==='low'&&n>=15)return false;}
        if(loc&&p.location.toLowerCase().indexOf(loc)===-1)return false;
        if(df&&p.date&&p.date<df)return false;
        if(dt&&p.date&&p.date>dt)return false;
        return true;
      });
      document.getElementById('pane-people').innerHTML=renderPeople(_filteredPeople);
      document.getElementById('resultCount').textContent=_filteredPeople.length+' of '+_allPeople.length;
    }else{
      _filteredCompanies=_allCompanies.filter(function(c){
        if(q&&(c.name+c.industry+c.city+c.state).toLowerCase().indexOf(q)===-1)return false;
        if(ind&&c.industry.toLowerCase()!==ind)return false;
        if(loc&&(c.city+' '+c.state+' '+c.country).toLowerCase().indexOf(loc)===-1)return false;
        if(sz&&empSize(c.employees)!==sz)return false;
        return true;
      });
      document.getElementById('pane-companies').innerHTML=renderCompanies(_filteredCompanies);
      document.getElementById('resultCount').textContent=_filteredCompanies.length+' of '+_allCompanies.length;
    }
  }

  /* TABS */
  function switchTab(name){
    _activeTab=name;
    ['people','companies'].forEach(function(n){
      document.getElementById('pane-'+n).classList.toggle('active',n===name);
      document.getElementById('btn-'+n).classList.toggle('active',n===name);
    });
    applyFilters();
  }

  /* DROPDOWN */
  function toggleMenu(){var p=document.getElementById('userPill'),d=document.getElementById('userDropdown');p.classList.toggle('open',!d.classList.contains('open'));d.classList.toggle('open');}
  document.addEventListener('click',function(e){var m=document.getElementById('userMenu');if(m&&!m.contains(e.target)){document.getElementById('userPill').classList.remove('open');document.getElementById('userDropdown').classList.remove('open');}});

  /* HELPERS */
  function populateFilters(industries,people){
    var sel=document.getElementById('industryFilter');
    while(sel.options.length>1)sel.remove(1);
    industries.forEach(function(i){var o=document.createElement('option');o.value=i[0].toLowerCase();o.textContent=i[0]+' ('+i[1]+')';sel.appendChild(o);});
    var locs={};
    people.forEach(function(p){if(p.location){var c=p.location.split(',').pop().trim();if(c)locs[c]=1;}});
    var locSel=document.getElementById('locationFilter');
    while(locSel.options.length>1)locSel.remove(1);
    Object.keys(locs).sort().forEach(function(l){var o=document.createElement('option');o.value=l.toLowerCase();o.textContent=l;locSel.appendChild(o);});
  }
  function animateBars(){setTimeout(function(){document.querySelectorAll('.bar-fill[data-w]').forEach(function(b){b.style.width=b.dataset.w+'%';});},80);}
  function revealCards(){document.querySelectorAll('.stat-card,.card').forEach(function(c,i){setTimeout(function(){c.classList.add('visible');},i*55);});}
  function animateCount(el,target){var start=0,dur=800,step=14;var t=setInterval(function(){start+=Math.ceil(target/(dur/step));if(start>=target){el.textContent=target;clearInterval(t);}else el.textContent=start;},step);}

  /* LOAD DATA */
  function loadData(){
    var icon=document.getElementById('refreshIcon');
    icon.style.transition='transform .6s ease';icon.style.transform='rotate(360deg)';
    setTimeout(function(){icon.style.transition='none';icon.style.transform='rotate(0)';},700);
    fetch('/ppc/anonymous-visitors/data')
      .then(function(r){return r.json();})
      .then(function(d){
        _allPeople=d.people_table||[];
        _allCompanies=d.company_table||[];
        _filteredPeople=[].concat(_allPeople);
        _filteredCompanies=[].concat(_allCompanies);
        animateCount(document.getElementById('sv-people'),d.total_people);
        animateCount(document.getElementById('sv-companies'),d.unique_companies);
        var topInd=d.top_industries&&d.top_industries[0]?d.top_industries[0][0]:'—';
        document.getElementById('sv-industry').textContent=topInd;
        document.getElementById('industry-body').innerHTML=renderIndustries(d.top_industries);
        document.getElementById('recent-body').innerHTML=renderRecent(_allPeople);
        document.getElementById('btn-people').textContent='People ('+_allPeople.length+')';
        document.getElementById('btn-companies').textContent='Companies ('+_allCompanies.length+')';
        populateFilters(d.top_industries||[],_allPeople);
        applyFilters();
        animateBars();
        revealCards();
        var ov=document.getElementById('overlay');
        ov.classList.add('fade-out');
        setTimeout(function(){ov.style.display='none';},400);
      })
      .catch(function(){
        document.getElementById('pane-people').innerHTML='<div class="empty" style="color:#f87171">Failed to load. <a href="" style="color:#34d399">Retry</a></div>';
        document.getElementById('overlay').classList.add('fade-out');
      });
  }
  loadData();
