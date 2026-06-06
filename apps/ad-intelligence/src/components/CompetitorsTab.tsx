import { useState } from 'react';
import {
  ExternalLink, Globe, Target, DollarSign, Users, Tag,
  Lightbulb, TrendingUp, ArrowRight, Search, Zap, Clock,
} from 'lucide-react';
import type { Ad, NavFn } from '../lib/types';
import type { Competitor } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { getAdsByDomain, countByField, getKeywords, formatDate } from '../lib/utils';
import { AdCard } from './AdCard';
import { AdModal } from './AdModal';

interface CompetitorsTabProps {
  ads: Ad[];
  activeCompetitor: string;
  setActiveCompetitor: (d: string) => void;
  onNav: NavFn;
}

/* ─── InfoBlock ──────────────────────────────────────────── */
function InfoBlock({ label, icon, content }: {
  label: string; icon: React.ReactNode; content: string;
}) {
  if (!content?.trim()) return null;
  return (
    <div className="mb-4">
      <h5 className="flex items-center gap-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
        <span className="text-slate-300">{icon}</span>{label}
      </h5>
      <p className="text-sm text-slate-700 leading-relaxed">{content}</p>
    </div>
  );
}

/* ─── Insight strip (per-competitor) ────────────────────── */
function CompInsightStrip({ compAds, allAds, comp }: {
  compAds: Ad[]; allAds: Ad[]; comp: Competitor;
}) {
  if (compAds.length === 0) return null;

  const total       = compAds.length;
  const totalAll    = allAds.length;
  const share       = Math.round((total / totalAll) * 100);
  const activeCount = compAds.filter(a => a.Status === 'active').length;
  const activePct   = Math.round((activeCount / total) * 100);

  const ctaCounts: Record<string, number> = {};
  for (const ad of compAds)
    if (ad.CTA && ad.CTA.length < 40)
      ctaCounts[ad.CTA] = (ctaCounts[ad.CTA] || 0) + 1;
  const [topCTA, secondCTA] = Object.entries(ctaCounts).sort((a, b) => b[1] - a[1]);

  const fmtCounts: Record<string, number> = {};
  for (const ad of compAds) {
    const f = (ad.Format || 'text').toLowerCase();
    fmtCounts[f] = (fmtCounts[f] || 0) + 1;
  }
  const topFmt = Object.entries(fmtCounts).sort((a, b) => b[1] - a[1])[0];

  const dates      = compAds.map(a => a['Last Shown']).filter(Boolean).sort();
  const newestDate = dates[dates.length - 1];
  let freshLabel   = '';
  if (newestDate) {
    const days = Math.round((Date.now() - new Date(newestDate).getTime()) / 86_400_000);
    freshLabel = days === 0 ? 'Today'
               : days < 7  ? `${days}d ago`
               : days < 30 ? `${Math.round(days / 7)}w ago`
               : `${Math.round(days / 30)}mo ago`;
  }

  const tile = 'bg-white border border-slate-100 rounded-2xl p-4 shadow-sm';

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">

      {/* Ad Volume */}
      <div className={tile}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg flex items-center justify-center"
               style={{ background: `${comp.color}18` }}>
            <TrendingUp size={11} style={{ color: comp.color }}/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Ad Volume</span>
        </div>
        <div className="flex items-baseline gap-1 mb-2">
          <span className="text-2xl font-black" style={{ color: comp.color }}>{total}</span>
          <span className="text-xs text-slate-400">ads total</span>
        </div>
        <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden mb-1.5">
          <div className="h-full rounded-full" style={{ width: `${Math.max(share, 2)}%`, background: comp.color }}/>
        </div>
        <p className="text-[10px] text-slate-400">{share}% of all {totalAll} tracked ads</p>
      </div>

      {/* Active Rate */}
      <div className={tile}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg bg-emerald-50 flex items-center justify-center">
            <Zap size={11} className="text-emerald-600"/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Active Now</span>
        </div>
        <div className="flex items-baseline gap-1 mb-2">
          <span className="text-2xl font-black text-emerald-600">{activeCount}</span>
          <span className="text-xs text-slate-400">/ {total} live</span>
        </div>
        <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden mb-1.5">
          <div className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-emerald-500"
               style={{ width: `${Math.max(activePct, 2)}%` }}/>
        </div>
        <p className="text-[10px] text-slate-400">{activePct}% running · {total - activeCount} paused</p>
      </div>

      {/* Top CTA */}
      <div className={tile}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg bg-violet-50 flex items-center justify-center">
            <Target size={11} className="text-violet-600"/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Top CTA</span>
        </div>
        {topCTA ? (
          <>
            <p className="text-sm font-black text-slate-800 leading-tight mb-1">"{topCTA[0]}"</p>
            <p className="text-[11px] text-slate-400 mb-1">
              Used in <strong className="text-slate-700">{topCTA[1]}</strong> ads
            </p>
            {secondCTA && (
              <p className="text-[10px] text-slate-300">
                2nd: <span className="text-slate-500 font-medium">"{secondCTA[0]}"</span>
              </p>
            )}
          </>
        ) : <p className="text-sm text-slate-400">No CTA data</p>}
      </div>

      {/* Latest Intel */}
      <div className={tile}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg bg-sky-50 flex items-center justify-center">
            <Clock size={11} className="text-sky-600"/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Latest Intel</span>
        </div>
        {newestDate ? (
          <>
            <p className="text-sm font-black text-slate-800 mb-0.5">Last seen {freshLabel}</p>
            <p className="text-[11px] text-slate-400 mb-2">{formatDate(newestDate)}</p>
            {topFmt && (
              <div className="flex items-center gap-1 flex-wrap">
                <span className="text-[10px] text-slate-400">Primary format:</span>
                <span className="text-[10px] font-bold text-slate-700 capitalize">{topFmt[0]}</span>
                <span className="text-[10px] text-slate-300">
                  ({Math.round((topFmt[1] / total) * 100)}%)
                </span>
              </div>
            )}
          </>
        ) : <p className="text-sm text-slate-400">No date data</p>}
      </div>
    </div>
  );
}

/* ─── Competitive Landscape ──────────────────────────────── */
function CompetitiveLandscape({ allAds, activeComp, onSelect }: {
  allAds: Ad[]; activeComp: string; onSelect: (d: string) => void;
}) {
  const maxCount = Math.max(
    ...COMPETITORS.map(c => allAds.filter(a => a.Domain === c.domain).length), 1,
  );
  const totalAll = allAds.length;

  return (
    <div className="bg-white border border-slate-100 rounded-2xl p-5 shadow-sm mb-5">
      <div className="flex items-center justify-between mb-4">
        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
          Competitive Landscape
        </p>
        <p className="text-[10px] text-slate-300">Click to switch competitor</p>
      </div>
      <div className="space-y-4">
        {COMPETITORS.map(comp => {
          const count      = allAds.filter(a => a.Domain === comp.domain).length;
          const active     = allAds.filter(a => a.Domain === comp.domain && a.Status === 'active').length;
          const sharePct   = Math.round((count / totalAll) * 100);
          const barPct     = (count / maxCount) * 100;
          const isSelected = activeComp !== 'all' && comp.domain === activeComp;

          return (
            <button key={comp.domain} onClick={() => onSelect(comp.domain)}
                    className="w-full text-left group">
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <div className="w-6 h-6 rounded-lg flex items-center justify-center text-[10px] font-black text-white flex-shrink-0 transition-all"
                       style={{ background: isSelected ? comp.color : `${comp.color}55` }}>
                    {comp.name[0]}
                  </div>
                  <span className={`text-sm font-bold transition-colors ${
                    isSelected ? '' : 'text-slate-500 group-hover:text-slate-700'}`}
                        style={isSelected ? { color: comp.color } : {}}>
                    {comp.name}
                  </span>
                  {isSelected && (
                    <span className="text-[9px] font-bold text-white px-1.5 py-0.5 rounded-full"
                          style={{ background: comp.color }}>
                      selected
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  {active > 0 && (
                    <span className="flex items-center gap-1 text-emerald-600 font-semibold text-[10px]">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block"/>
                      {active} live
                    </span>
                  )}
                  <span className="text-sm font-black text-slate-800">{count}</span>
                  <span className="text-[10px] text-slate-300">{sharePct}%</span>
                </div>
              </div>
              <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                <div className="h-full rounded-full transition-all duration-500"
                     style={{
                       width: `${barPct}%`,
                       background: isSelected
                         ? `linear-gradient(90deg,${comp.color},${comp.color}90)`
                         : `${comp.color}45`,
                     }}/>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Pattern chips ──────────────────────────────────────── */
function CompPatternChips({ compAds, comp }: { compAds: Ad[]; comp: Competitor }) {
  if (compAds.length === 0) return null;
  const chips: { text: string; color: string }[] = [];

  const active = compAds.filter(a => a.Status === 'active').length;
  if (active > 0) chips.push({ text: `${active} ads currently live`, color: '#10b981' });

  const imgPct = Math.round(
    (compAds.filter(a => a['Image URLs']).length / compAds.length) * 100,
  );
  if (imgPct >= 40) chips.push({ text: `${imgPct}% image creative`, color: comp.color });

  const kwCounts: Record<string, number> = {};
  for (const ad of compAds)
    for (const kw of getKeywords(ad)) {
      const k = kw.toLowerCase().trim();
      if (k.length > 2) kwCounts[k] = (kwCounts[k] || 0) + 1;
    }
  const topKW = Object.entries(kwCounts).sort((a, b) => b[1] - a[1])[0];
  if (topKW && topKW[1] > 1)
    chips.push({ text: `Top keyword: "${topKW[0]}"`, color: '#f59e0b' });

  const ctaSet = new Set(
    compAds.map(a => a.CTA?.trim()).filter((c): c is string => !!c && c.length < 40),
  );
  if (ctaSet.size > 1) chips.push({ text: `${ctaSet.size} distinct CTAs`, color: '#0ea5e9' });

  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 mb-5">
      {chips.map((c, i) => (
        <span key={i}
              className="inline-flex items-center gap-1.5 text-[10px] font-semibold px-2.5 py-1 rounded-full border"
              style={{ color: c.color, background: `${c.color}10`, borderColor: `${c.color}28` }}>
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: c.color }}/>
          {c.text}
        </span>
      ))}
    </div>
  );
}

/* ─── Main ───────────────────────────────────────────────── */
export function CompetitorsTab({
  ads, activeCompetitor, setActiveCompetitor, onNav,
}: CompetitorsTabProps) {
  const [selectedAd, setSelectedAd] = useState<Ad | null>(null);

  const byDomain   = getAdsByDomain(ads);
  const isAll      = activeCompetitor === 'all';
  const comp       = COMPETITORS.find(c => c.domain === activeCompetitor);
  const compAds    = isAll ? [] : (byDomain[activeCompetitor] || []);
  const enriched   = compAds.find(a => a['Website Summary'] || a.Services || a['Messaging Angle']);
  const allKW      = [...new Set(compAds.flatMap(a => getKeywords(a)).map(k => k.toLowerCase()))];
  const fmtCounts  = countByField(compAds, 'Format');
  const ctaList    = [...new Set(
    compAds.map(a => a.CTA).filter((c): c is string => !!c && c.length < 45),
  )];
  const msgAngles  = enriched?.['Messaging Angle']?.split(';').map(s => s.trim()).filter(Boolean) ?? [];
  const recentAds  = [...compAds]
    .filter(a => a['Last Shown'])
    .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))
    .slice(0, 8);
  const statusAct  = compAds.filter(a => a.Status === 'active').length;
  const totalActive = ads.filter(a => a.Status === 'active').length;

  return (
    <div>

      {/* ── Competitor selector ── */}
      <div className="flex flex-wrap gap-3 mb-6">

        {/* All Competitors button */}
        <button onClick={() => setActiveCompetitor('all')}
                className="flex items-center gap-3 px-4 py-3 rounded-2xl border-2 transition-all duration-200 card-lift"
                style={isAll
                  ? { background: 'linear-gradient(135deg,#6366f1,#8b5cf6)', borderColor: '#6366f1', color: 'white', boxShadow: '0 8px 25px -5px #6366f155' }
                  : { background: 'white', borderColor: '#6366f130', color: '#6366f1' }}>
          <div className="w-8 h-8 rounded-xl flex items-center justify-center text-[10px] font-black flex-shrink-0"
               style={isAll ? { background: 'rgba(255,255,255,0.25)', color: 'white' } : { background: '#6366f118', color: '#6366f1' }}>
            All
          </div>
          <div className="text-left">
            <p className="text-sm font-bold leading-none">All Competitors</p>
            <p className="text-[10px] mt-0.5 opacity-70">{ads.length} total ads</p>
          </div>
          <div className="ml-2 text-right flex-shrink-0">
            <span className="block text-xs font-black px-2 py-0.5 rounded-full"
                  style={isAll ? { background: 'rgba(255,255,255,0.2)', color: 'white' } : { background: '#6366f120', color: '#6366f1' }}>
              {ads.length}
            </span>
            {totalActive > 0 && (
              <span className="block text-[9px] font-semibold mt-0.5 opacity-80">{totalActive} live</span>
            )}
          </div>
        </button>

        {COMPETITORS.map(c => {
          const cnt   = (byDomain[c.domain] || []).length;
          const act   = (byDomain[c.domain] || []).filter(a => a.Status === 'active').length;
          const isSel = activeCompetitor === c.domain;
          return (
            <button key={c.domain} onClick={() => setActiveCompetitor(c.domain)}
                    className="flex items-center gap-3 px-4 py-3 rounded-2xl border-2 transition-all duration-200 card-lift"
                    style={isSel
                      ? { background: `linear-gradient(135deg,${c.color},${c.color}cc)`,
                          borderColor: c.color, color: 'white',
                          boxShadow: `0 8px 25px -5px ${c.color}55` }
                      : { background: 'white', borderColor: `${c.color}30`, color: c.color }}>
              <div className="w-8 h-8 rounded-xl flex items-center justify-center text-xs font-black flex-shrink-0"
                   style={isSel
                     ? { background: 'rgba(255,255,255,0.25)', color: 'white' }
                     : { background: `${c.color}18`, color: c.color }}>
                {c.name[0]}
              </div>
              <div className="text-left">
                <p className="text-sm font-bold leading-none">{c.name}</p>
                <p className="text-[10px] mt-0.5 opacity-70">{c.domain}</p>
              </div>
              <div className="ml-2 text-right flex-shrink-0">
                <span className="block text-xs font-black px-2 py-0.5 rounded-full"
                      style={isSel
                        ? { background: 'rgba(255,255,255,0.2)', color: 'white' }
                        : { background: `${c.color}20`, color: c.color }}>
                  {cnt}
                </span>
                {act > 0 && (
                  <span className="block text-[9px] font-semibold mt-0.5 opacity-80">
                    {act} live
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* ── All Competitors view ── */}
      {isAll && (
        <>
          <CompetitiveLandscape allAds={ads} activeComp="all" onSelect={setActiveCompetitor}/>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-2">
            {COMPETITORS.map((c, ci) => {
              const cAds      = byDomain[c.domain] || [];
              const fmts      = countByField(cAds, 'Format');
              const active    = cAds.filter(a => a.Status === 'active').length;
              const lastShown = [...cAds].filter(a => a['Last Shown']).sort((a,b) => b['Last Shown'].localeCompare(a['Last Shown']))[0]?.['Last Shown'];
              return (
                <div key={c.domain}
                     onClick={() => setActiveCompetitor(c.domain)}
                     className={`anim-pop-in delay-${ci+1} bg-white rounded-2xl border border-slate-100 shadow-sm p-4 card-lift cursor-pointer group active:scale-[0.98] transition-all`}>
                  <div className="h-1 rounded-full mb-4" style={{ background: `linear-gradient(90deg,${c.color},${c.color}50)` }}/>
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-black text-base shadow-md"
                         style={{ background: `linear-gradient(135deg,${c.color},${c.color}aa)` }}>
                      {c.name[0]}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-bold text-slate-800 text-sm leading-none group-hover:text-indigo-700 transition-colors">{c.name}</p>
                      <p className="text-xs text-slate-400 mt-0.5 truncate">{c.domain}</p>
                    </div>
                    <ArrowRight size={15} className="text-slate-200 group-hover:text-indigo-400 transition-colors flex-shrink-0"/>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-center mb-3">
                    {([['Total', cAds.length, '#f1f5f9'], ['Active', active, c.color], ['Formats', Object.keys(fmts).length, '#f1f5f9']] as [string, number, string][]).map(([l,v,col]) => (
                      <div key={l} className="rounded-xl py-2.5" style={{ background: `${c.color}0a` }}>
                        <p className="text-xl font-black" style={{ color: col }}>{v}</p>
                        <p className="text-[10px] text-slate-400">{l}</p>
                      </div>
                    ))}
                  </div>
                  {lastShown && (
                    <p className="text-center text-[10px] text-slate-400 mb-3">
                      Last active: <strong className="text-slate-600">{new Date(lastShown).toLocaleDateString()}</strong>
                    </p>
                  )}
                  <div className="flex gap-1.5 flex-wrap justify-center">
                    {Object.entries(fmts).map(([fmt]) => (
                      <button key={fmt}
                              onClick={e => { e.stopPropagation(); onNav({ tab: 'gallery', domain: c.domain, format: fmt }); }}
                              className="text-[10px] font-semibold px-2 py-0.5 rounded-full capitalize hover:opacity-80 transition-opacity"
                              style={{ background: `${c.color}18`, color: c.color }}>
                        {fmt}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ── Single-competitor view ── */}
      {!isAll && comp && (
        <>
          <CompInsightStrip compAds={compAds} allAds={ads} comp={comp}/>
          <CompetitiveLandscape allAds={ads} activeComp={activeCompetitor} onSelect={setActiveCompetitor}/>
          <CompPatternChips compAds={compAds} comp={comp}/>
        </>
      )}

      {/* ── Main 3-col grid (single competitor only) ── */}
      {!isAll && comp && <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

        {/* LEFT: intel */}
        <div className="space-y-4">

          {/* Profile card with gradient header + overlapping avatar */}
          <div className="bg-white rounded-2xl border border-slate-100 shadow-sm overflow-hidden">
            <div className="h-14 relative"
                 style={{ background: `linear-gradient(135deg,${comp.color}28,${comp.color}08)` }}>
              <div className="absolute inset-0"
                   style={{ background: `linear-gradient(to right,${comp.color}20,transparent)` }}/>
              <div className="absolute -bottom-6 left-5">
                <div className="w-12 h-12 rounded-2xl flex items-center justify-center text-white font-black text-lg ring-4 ring-white"
                     style={{
                       background: `linear-gradient(135deg,${comp.color},${comp.color}aa)`,
                       boxShadow: `0 8px 20px -4px ${comp.color}55`,
                     }}>
                  {comp.name[0]}
                </div>
              </div>
            </div>
            <div className="p-5 pt-9">
              <h3 className="font-black text-slate-900 text-base leading-none mb-1">{comp.name}</h3>
              <a href={`https://${comp.domain}`} target="_blank" rel="noopener noreferrer"
                 className="flex items-center gap-1 text-xs mb-4 hover:underline"
                 style={{ color: comp.color }}>
                <Globe size={10}/>{comp.domain}<ExternalLink size={9}/>
              </a>

              <div className="grid grid-cols-3 gap-2 text-center mb-4">
                {([
                  ['Total Ads', compAds.length,                  '#f1f5f9' ],
                  ['Active',    statusAct,                        comp.color],
                  ['Formats',   Object.keys(fmtCounts).length,   '#f1f5f9' ],
                ] as [string, number, string][]).map(([l, v, col]) => (
                  <div key={l} className="rounded-xl py-3 px-1"
                       style={{ background: `${comp.color}0a` }}>
                    <p className="text-2xl font-black" style={{ color: col }}>{v}</p>
                    <p className="text-[10px] text-slate-400 font-medium">{l}</p>
                  </div>
                ))}
              </div>

              <div className="flex gap-2 flex-wrap mb-3">
                {Object.entries(fmtCounts).map(([fmt, cnt]) => (
                  <button key={fmt}
                          onClick={() => onNav({ tab: 'gallery', domain: comp.domain, format: fmt, search: '' })}
                          className="text-xs font-semibold px-2.5 py-1 rounded-full capitalize hover:opacity-80 transition-opacity flex items-center gap-1"
                          style={{ background: `${comp.color}15`, color: comp.color }}>
                    {fmt} · {cnt} <ArrowRight size={9}/>
                  </button>
                ))}
              </div>

              <button onClick={() => onNav({ tab: 'gallery', domain: comp.domain, format: 'all', search: '' })}
                      className="w-full flex items-center justify-center gap-1.5 py-2 rounded-xl text-xs font-semibold transition-all hover:opacity-90"
                      style={{ background: `${comp.color}15`, color: comp.color }}>
                View all {compAds.length} ads <ArrowRight size={11}/>
              </button>
            </div>
          </div>

          {/* Intel */}
          {enriched && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <h4 className="flex items-center gap-2 font-bold text-slate-800 text-sm mb-4">
                <TrendingUp size={14} style={{ color: comp.color }}/> Competitor Intelligence
              </h4>
              <InfoBlock label="About"             icon={<Globe       size={11}/>} content={(enriched['Website Summary']   || '').slice(0, 300)}/>
              <InfoBlock label="Value Proposition" icon={<Target      size={11}/>} content={(enriched['Value Proposition'] || '').slice(0, 240)}/>
              <InfoBlock label="Services"          icon={<Tag         size={11}/>} content={(enriched.Services             || '').slice(0, 240)}/>
              <InfoBlock label="Pricing Model"     icon={<DollarSign  size={11}/>} content={(enriched['Pricing Model']     || '').slice(0, 200)}/>
              <InfoBlock label="Target Audience"   icon={<Users       size={11}/>} content={(enriched['Audience Type']     || '').slice(0, 200)}/>
            </div>
          )}

          {/* Messaging angles */}
          {msgAngles.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <h4 className="flex items-center gap-2 font-bold text-slate-800 text-sm mb-3">
                <Lightbulb size={14} style={{ color: comp.color }}/> Messaging Angles
              </h4>
              <ul className="space-y-2.5">
                {msgAngles.map((angle, i) => (
                  <li key={i}
                      onClick={() => onNav({
                        tab: 'gallery', domain: comp.domain,
                        search: angle.split(' ').slice(0, 3).join(' '),
                      })}
                      className="flex items-start gap-2 text-sm text-slate-700 cursor-pointer hover:text-indigo-700 transition-colors group">
                    <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0 mt-0.5 text-white"
                          style={{ background: comp.color }}>{i + 1}</span>
                    <span className="flex-1">{angle}</span>
                    <ArrowRight size={10} className="mt-1 flex-shrink-0 text-slate-200 group-hover:text-indigo-400 transition-colors"/>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* CTAs */}
          {ctaList.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <div className="flex items-center justify-between mb-3">
                <h4 className="font-bold text-slate-800 text-sm">CTAs Used</h4>
                <span className="text-[10px] text-slate-400">Click to find in gallery</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {ctaList.map((cta, i) => (
                  <button key={i}
                          onClick={() => onNav({ tab: 'gallery', search: cta, domain: 'all', format: 'all' })}
                          className="flex items-center gap-1 text-sm font-semibold px-3 py-1.5 rounded-xl hover:opacity-80 transition-opacity group"
                          style={{ background: `${comp.color}15`, color: comp.color }}>
                    {cta}
                    <Search size={10} className="opacity-0 group-hover:opacity-60 transition-opacity"/>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Keywords */}
          {allKW.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <div className="flex items-center justify-between mb-3">
                <h4 className="font-bold text-slate-800 text-sm">
                  Keywords
                  <span className="text-slate-400 font-normal ml-1">({allKW.length})</span>
                </h4>
                <span className="text-[10px] text-slate-400">Click to search gallery</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {allKW.slice(0, 40).map((kw, i) => (
                  <button key={i}
                          onClick={() => onNav({ tab: 'gallery', search: kw, domain: comp.domain, format: 'all' })}
                          className="text-xs px-2.5 py-1 rounded-full font-medium transition-all"
                          style={{ background: `${comp.color}10`, color: comp.color }}
                          onMouseEnter={e => {
                            (e.target as HTMLElement).style.background = comp.color;
                            (e.target as HTMLElement).style.color      = 'white';
                          }}
                          onMouseLeave={e => {
                            (e.target as HTMLElement).style.background = `${comp.color}10`;
                            (e.target as HTMLElement).style.color      = comp.color;
                          }}>
                    {kw}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT: recent ads */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
            <div className="flex items-center justify-between mb-4">
              <h4 className="font-bold text-slate-800 text-sm">
                Recent Ads
                <span className="text-slate-400 font-normal text-xs ml-1.5">
                  ({recentAds.length} shown)
                </span>
              </h4>
              <button onClick={() => onNav({ tab: 'gallery', domain: comp.domain, format: 'all', search: '' })}
                      className="flex items-center gap-1 text-xs font-semibold hover:opacity-70 transition-opacity"
                      style={{ color: comp.color }}>
                View all <ArrowRight size={11}/>
              </button>
            </div>

            {recentAds.length === 0 ? (
              <div className="text-center py-12 text-slate-400">
                <div className="w-12 h-12 rounded-xl bg-slate-100 flex items-center justify-center mx-auto mb-3">
                  <Tag size={20} className="text-slate-300"/>
                </div>
                <p className="font-medium">No ads found</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {recentAds.map((ad, i) => (
                  <div key={i} className="anim-fade-up" style={{ animationDelay: `${i * 0.04}s` }}>
                    <AdCard
                      ad={ad}
                      onClick={() => setSelectedAd(ad)}
                      onDomainClick={d => onNav({ tab: 'gallery', domain: d })}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

      </div>}

      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)}/>}
    </div>
  );
}
