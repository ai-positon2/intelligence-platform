import { useState, useMemo } from 'react';
import {
  Search, ImageIcon, FileText, Video, X, ArrowRight,
  LayoutGrid, AlignJustify, ChevronDown, Zap, Clock,
  Target, ExternalLink,
} from 'lucide-react';
import type { Ad, NavFn } from '../lib/types';
import { COMPETITORS, COMPETITOR_COLORS } from '../lib/types';
import { AdCard } from './AdCard';
import { AdModal } from './AdModal';
import { formatDate, getImageUrls, getAdPreviewText, getKeywords } from '../lib/utils';

/* ─── types ─────────────────────────────────────────────── */
interface GalleryTabProps {
  ads: Ad[];
  domain: string;  setDomain: (v: string) => void;
  format: string;  setFormat: (v: string) => void;
  search: string;  setSearch: (v: string) => void;
  onNav: NavFn;
}
type SortKey  = 'newest' | 'oldest' | 'active' | 'alpha';
type ViewMode = 'grid'   | 'list';

const FORMAT_OPTS = [
  { id: 'all',   label: 'All'   },
  { id: 'image', label: 'Image' },
  { id: 'text',  label: 'Text'  },
  { id: 'video', label: 'Video' },
];
const FMT_COLORS: Record<string, string> = {
  image: '#10b981', text: '#6366f1', video: '#f59e0b',
};
const SORT_LABELS: Record<SortKey, string> = {
  newest: 'Newest first',
  oldest: 'Oldest first',
  active: 'Active first',
  alpha:  'A → Z',
};

/* ─── Insight strip ──────────────────────────────────────── */
function InsightStrip({ ads }: { ads: Ad[] }) {
  if (ads.length === 0) return null;

  const total       = ads.length;
  const activeCount = ads.filter(a => a.Status === 'active').length;
  const activePct   = Math.round((activeCount / total) * 100);

  /* format breakdown */
  const fmtCounts: Record<string, number> = {};
  for (const ad of ads) {
    const f = (ad.Format || 'text').toLowerCase();
    fmtCounts[f] = (fmtCounts[f] || 0) + 1;
  }
  const fmtEntries = Object.entries(fmtCounts).sort((a, b) => b[1] - a[1]);

  /* top CTA */
  const ctaCounts: Record<string, number> = {};
  for (const ad of ads) {
    if (ad.CTA && ad.CTA.length < 40)
      ctaCounts[ad.CTA] = (ctaCounts[ad.CTA] || 0) + 1;
  }
  const [topCTA, secondCTA] = Object.entries(ctaCounts).sort((a, b) => b[1] - a[1]);

  /* timeline */
  const dates      = ads.map(a => a['Last Shown']).filter(Boolean).sort();
  const newestDate = dates[dates.length - 1];
  const oldestDate = dates[0];
  let freshLabel   = '';
  if (newestDate) {
    const days = Math.round((Date.now() - new Date(newestDate).getTime()) / 86_400_000);
    freshLabel = days === 0 ? 'Today'
               : days < 7  ? `${days}d ago`
               : days < 30 ? `${Math.round(days / 7)}w ago`
               : `${Math.round(days / 30)}mo ago`;
  }

  const tile = 'card-glow bg-white border border-slate-100 rounded-2xl p-4 shadow-sm anim-pop-in';

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">

      {/* 1 · Activity */}
      <div className={`${tile} delay-1`}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg bg-emerald-50 flex items-center justify-center">
            <Zap size={11} className="text-emerald-600"/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Activity</span>
        </div>
        <div className="flex items-baseline gap-1 mb-2">
          <span className="text-2xl font-black text-emerald-600">{activeCount}</span>
          <span className="text-xs text-slate-400 font-medium">/ {total} live</span>
        </div>
        <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden mb-1.5">
          <div className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-emerald-500"
               style={{ width: `${Math.max(activePct, 2)}%` }}/>
        </div>
        <p className="text-[10px] text-slate-400">
          {total - activeCount} paused · {activePct}% running
        </p>
      </div>

      {/* 2 · Creative Mix */}
      <div className={`${tile} delay-2`}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg bg-indigo-50 flex items-center justify-center">
            <ImageIcon size={11} className="text-indigo-600"/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Creative Mix</span>
        </div>
        <div className="space-y-2">
          {fmtEntries.map(([fmt, cnt]) => {
            const pct = Math.round((cnt / total) * 100);
            const col = FMT_COLORS[fmt] || '#94a3b8';
            return (
              <div key={fmt}>
                <div className="flex justify-between text-[10px] mb-0.5">
                  <span className="font-medium capitalize text-slate-600">{fmt}</span>
                  <span className="font-bold" style={{ color: col }}>{pct}%</span>
                </div>
                <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                  <div className="h-full rounded-full"
                       style={{ width: `${pct}%`, background: col }}/>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 3 · Top CTA */}
      <div className={`${tile} delay-3`}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg bg-violet-50 flex items-center justify-center">
            <Target size={11} className="text-violet-600"/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Top CTA</span>
        </div>
        {topCTA ? (
          <>
            <p className="text-sm font-black text-slate-800 leading-tight mb-1">"{topCTA[0]}"</p>
            <p className="text-[11px] text-slate-400 mb-2">
              Used in <strong className="text-slate-700">{topCTA[1]}</strong> ads
            </p>
            {secondCTA && (
              <p className="text-[10px] text-slate-300">
                2nd: <span className="text-slate-500 font-medium">"{secondCTA[0]}"</span>{' '}
                <span className="text-slate-300">({secondCTA[1]})</span>
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-slate-400">No CTA data</p>
        )}
      </div>

      {/* 4 · Timeline */}
      <div className={`${tile} delay-4`}>
        <div className="flex items-center gap-1.5 mb-3">
          <div className="w-6 h-6 rounded-lg bg-sky-50 flex items-center justify-center">
            <Clock size={11} className="text-sky-600"/>
          </div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Timeline</span>
        </div>
        {newestDate ? (
          <>
            <p className="text-sm font-black text-slate-800 mb-0.5">Latest {freshLabel}</p>
            <p className="text-[10px] text-slate-400 mb-3">{formatDate(newestDate)}</p>
            <div className="flex items-center gap-2 text-[9px] text-slate-400">
              <span className="flex-shrink-0 font-medium">Oldest</span>
              <div className="relative flex-1 h-0.5 bg-gradient-to-r from-slate-200 to-sky-400 rounded-full">
                <span className="absolute left-0 -top-[3px] w-1.5 h-1.5 rounded-full bg-slate-300 border border-white"/>
                <span className="absolute right-0 -top-[3px] w-1.5 h-1.5 rounded-full bg-sky-500 border border-white"/>
              </div>
              <span className="flex-shrink-0 font-medium">Now</span>
            </div>
            <p className="text-[9px] text-slate-300 mt-1.5 truncate">
              {formatDate(oldestDate)} — {formatDate(newestDate)}
            </p>
          </>
        ) : (
          <p className="text-sm text-slate-400">No date data</p>
        )}
      </div>
    </div>
  );
}

/* ─── Competitor share bar ────────────────────────────────── */
function CompetitorShareBar({ ads }: { ads: Ad[] }) {
  if (ads.length === 0) return null;
  const total  = ads.length;
  const counts = COMPETITORS.map(c => ({
    comp:   c,
    count:  ads.filter(a => a.Domain === c.domain).length,
    active: ads.filter(a => a.Domain === c.domain && a.Status === 'active').length,
  })).filter(x => x.count > 0);
  if (counts.length < 2) return null;

  return (
    <div className="bg-white border border-slate-100 rounded-2xl px-5 py-4 mb-5 shadow-sm">
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-3">
        Competitor Ad Share
      </p>
      {/* Stacked bar */}
      <div className="flex gap-0.5 h-2.5 rounded-full overflow-hidden mb-3">
        {counts.map(({ comp, count }) => (
          <div key={comp.domain}
               className="h-full first:rounded-l-full last:rounded-r-full"
               style={{ width: `${(count / total) * 100}%`, background: comp.color }}
               title={`${comp.name}: ${count}`}/>
        ))}
      </div>
      {/* Legend */}
      <div className="flex flex-wrap gap-5">
        {counts.map(({ comp, count, active }) => (
          <div key={comp.domain} className="flex items-center gap-2 min-w-0">
            <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: comp.color }}/>
            <span className="text-xs font-semibold text-slate-700 truncate">{comp.name}</span>
            <span className="text-xs font-black" style={{ color: comp.color }}>{count}</span>
            {active > 0 && (
              <span className="text-[10px] text-emerald-500 font-medium flex-shrink-0">
                · {active} live
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Pattern chips ──────────────────────────────────────── */
function PatternChips({ ads }: { ads: Ad[] }) {
  if (ads.length === 0) return null;

  const chips: { text: string; color: string }[] = [];

  const active = ads.filter(a => a.Status === 'active').length;
  if (active > 0) chips.push({ text: `${active} ads currently live`, color: '#10b981' });

  const imgPct = Math.round(
    (ads.filter(a => a['Image URLs']).length / ads.length) * 100,
  );
  if (imgPct >= 40) chips.push({ text: `${imgPct}% image creative`, color: '#6366f1' });

  const kwCounts: Record<string, number> = {};
  for (const ad of ads) {
    for (const kw of getKeywords(ad)) {
      const k = kw.toLowerCase().trim();
      if (k.length > 2) kwCounts[k] = (kwCounts[k] || 0) + 1;
    }
  }
  const topKW = Object.entries(kwCounts).sort((a, b) => b[1] - a[1])[0];
  if (topKW && topKW[1] > 2)
    chips.push({ text: `Top keyword: "${topKW[0]}"`, color: '#f59e0b' });

  const ctaSet = new Set(
    ads.map(a => a.CTA?.trim()).filter((c): c is string => !!c && c.length < 40),
  );
  if (ctaSet.size > 1) chips.push({ text: `${ctaSet.size} distinct CTAs`, color: '#0ea5e9' });

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 mb-5">
      {chips.map((c, i) => (
        <span key={i}
              className="chip-pop inline-flex items-center gap-1.5 text-[10px] font-semibold px-2.5 py-1 rounded-full border hover:scale-105 transition-transform cursor-default"
              style={{ color: c.color, background: `${c.color}10`, borderColor: `${c.color}28`, animationDelay: `${i * 0.07}s` }}>
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: c.color }}/>
          {c.text}
        </span>
      ))}
    </div>
  );
}

/* ─── List row ───────────────────────────────────────────── */
function ListRow({
  ad, onClick, onDomainClick,
}: { ad: Ad; onClick: () => void; onDomainClick: (d: string) => void }) {
  const images   = getImageUrls(ad);
  const thumb    = images[0];
  const color    = COMPETITOR_COLORS[ad.Domain] || '#6366f1';
  const fmt      = (ad.Format || 'text').toLowerCase();
  const headline = getAdPreviewText(ad);
  const isActive = ad.Status === 'active';
  const FmtIcon  = fmt === 'video' ? Video : fmt === 'image' ? ImageIcon : FileText;

  return (
    <div
      onClick={onClick}
      className="group flex items-center gap-4 bg-white border border-slate-100 rounded-2xl px-4 py-3 cursor-pointer hover:shadow-md hover:border-indigo-100 transition-all"
    >
      {/* Thumbnail */}
      <div className="w-20 h-14 flex-shrink-0 rounded-xl overflow-hidden relative"
           style={{ background: `${color}10` }}>
        {thumb ? (
          <img src={thumb} alt=""
               className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
               onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}/>
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <FmtIcon size={20} style={{ color: `${color}55` }}/>
          </div>
        )}
        <div className="absolute bottom-0 left-0 right-0 flex justify-center py-0.5"
             style={{ background: `${color}cc` }}>
          <span className="text-[8px] font-bold capitalize text-white">{fmt}</span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <button
            onClick={e => { e.stopPropagation(); onDomainClick(ad.Domain); }}
            className="text-[10px] font-bold px-2 py-0.5 rounded-full flex-shrink-0 hover:opacity-80 transition-opacity"
            style={{ background: `${color}18`, color }}>
            {ad.Domain.split('.')[0]}
          </button>
          {ad['Last Shown'] && (
            <span className="text-[10px] text-slate-400 flex-shrink-0">
              {formatDate(ad['Last Shown'])}
            </span>
          )}
          {isActive && (
            <span className="flex items-center gap-1 text-[9px] font-bold text-emerald-600 flex-shrink-0">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block"/>
              Live
            </span>
          )}
        </div>
        <p className="text-sm font-semibold text-slate-800 truncate group-hover:text-indigo-700 transition-colors">
          {headline}
        </p>
        {ad.Description && (
          <p className="text-[11px] text-slate-400 truncate mt-0.5">{ad.Description}</p>
        )}
      </div>

      {/* Right */}
      <div className="flex items-center gap-2.5 flex-shrink-0">
        {ad.CTA && ad.CTA.length < 40 && (
          <span className="hidden md:inline-block text-[11px] font-bold text-white px-2.5 py-1 rounded-lg"
                style={{ background: color }}>
            {ad.CTA}
          </span>
        )}
        {ad['Destination URL'] && (
          <a href={
               ad['Destination URL'].startsWith('http')
                 ? ad['Destination URL']
                 : `https://${ad['Destination URL']}`
             }
             target="_blank" rel="noopener noreferrer"
             onClick={e => e.stopPropagation()}
             className="text-slate-300 hover:text-indigo-500 transition-colors">
            <ExternalLink size={13}/>
          </a>
        )}
        <ArrowRight size={14}
                    className="text-slate-200 group-hover:text-indigo-400 group-hover:translate-x-0.5 transition-all"/>
      </div>
    </div>
  );
}

/* ─── Main ───────────────────────────────────────────────── */
export function GalleryTab({
  ads, domain, setDomain, format, setFormat, search, setSearch, onNav,
}: GalleryTabProps) {
  const [selectedAd, setSelectedAd] = useState<Ad | null>(null);
  const [sortBy,     setSortBy]     = useState<SortKey>('newest');
  const [viewMode,   setViewMode]   = useState<ViewMode>('grid');
  const [sortOpen,   setSortOpen]   = useState(false);

  const hasFilters = domain !== 'all' || format !== 'all' || search !== '';

  /* filter */
  const filtered = useMemo(() => ads.filter(ad => {
    if (domain !== 'all' && ad.Domain !== domain) return false;
    if (format !== 'all' && ad.Format?.toLowerCase() !== format) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        ad.Headline?.toLowerCase().includes(q)       ||
        ad.Description?.toLowerCase().includes(q)    ||
        ad['Full Ad Text']?.toLowerCase().includes(q)||
        ad.CTA?.toLowerCase().includes(q)            ||
        ad.Keywords?.toLowerCase().includes(q)       ||
        false
      );
    }
    return true;
  }), [ads, domain, format, search]);

  /* sort */
  const sorted = useMemo(() => {
    const s = [...filtered];
    switch (sortBy) {
      case 'newest': return s.sort((a, b) =>
        (b['Last Shown'] || '').localeCompare(a['Last Shown'] || ''));
      case 'oldest': return s.sort((a, b) =>
        (a['Last Shown'] || '').localeCompare(b['Last Shown'] || ''));
      case 'active': return s.sort((a, b) => {
        if (a.Status === 'active' && b.Status !== 'active') return -1;
        if (b.Status === 'active' && a.Status !== 'active') return  1;
        return (b['Last Shown'] || '').localeCompare(a['Last Shown'] || '');
      });
      case 'alpha': return s.sort((a, b) =>
        (a.Headline || '').localeCompare(b.Headline || ''));
      default: return s;
    }
  }, [filtered, sortBy]);

  const clearAll = () => { setDomain('all'); setFormat('all'); setSearch(''); };

  return (
    <div>

      {/* Active filter breadcrumbs */}
      {hasFilters && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <span className="text-xs text-slate-400 font-medium">Filtered:</span>
          {domain !== 'all' && (
            <span className="chip-pop flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-100">
              {COMPETITORS.find(c => c.domain === domain)?.name ?? domain}
              <button onClick={() => setDomain('all')} className="hover:text-indigo-900 ml-0.5 hover:rotate-90 transition-transform">
                <X size={10}/>
              </button>
            </span>
          )}
          {format !== 'all' && (
            <span className="chip-pop flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 capitalize">
              {format} ads
              <button onClick={() => setFormat('all')} className="hover:text-emerald-900 ml-0.5 hover:rotate-90 transition-transform">
                <X size={10}/>
              </button>
            </span>
          )}
          {search && (
            <span className="chip-pop flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-100">
              "{search}"
              <button onClick={() => setSearch('')} className="hover:text-amber-900 ml-0.5 hover:rotate-90 transition-transform">
                <X size={10}/>
              </button>
            </span>
          )}
          <button onClick={clearAll}
                  className="text-xs text-slate-400 hover:text-slate-600 ml-1 underline underline-offset-2 transition-colors">
            Clear all
          </button>
        </div>
      )}

      {/* Filter bar */}
      <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-3.5 mb-5">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap gap-1.5">
            <button onClick={() => setDomain('all')}
                    className={`text-xs font-semibold px-3 py-1.5 rounded-full transition-all ${
                      domain === 'all'
                        ? 'bg-slate-900 text-white shadow-sm'
                        : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
              All
            </button>
            {COMPETITORS.map(c => (
              <button key={c.domain} onClick={() => setDomain(c.domain)}
                      className="text-xs font-semibold px-3 py-1.5 rounded-full transition-all"
                      style={domain === c.domain
                        ? { background: c.color, color: 'white',
                            boxShadow: `0 4px 12px -2px ${c.color}55` }
                        : { background: `${c.color}12`, color: c.color }}>
                {c.name}
              </button>
            ))}
          </div>

          <div className="h-5 w-px bg-slate-200 hidden sm:block"/>

          <div className="flex gap-1.5">
            {FORMAT_OPTS.map(f => (
              <button key={f.id} onClick={() => setFormat(f.id)}
                      className={`text-xs font-semibold px-3 py-1.5 rounded-full transition-all ${
                        format === f.id
                          ? 'bg-indigo-600 text-white shadow-sm'
                          : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                {f.label}
              </button>
            ))}
          </div>

          <div className="flex-1 min-w-48 relative ml-auto search-expand rounded-xl">
            <Search size={14}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none transition-transform group-focus-within:scale-110"/>
            <input
              type="text" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Search headlines, CTAs, keywords…"
              className="w-full text-sm pl-9 pr-8 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-400 transition-all"
            />
            {search && (
              <button onClick={() => setSearch('')}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                <X size={13}/>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Insight strip */}
      <InsightStrip ads={filtered}/>

      {/* Competitor share bar — only when no domain filter */}
      {domain === 'all' && <CompetitorShareBar ads={filtered}/>}

      {/* Pattern chips */}
      <PatternChips ads={filtered}/>

      {/* Controls row */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <p className="text-sm text-slate-500">
            <strong className="text-slate-800 font-bold">{sorted.length}</strong>
            <span className="text-slate-400"> of {ads.length} ads</span>
          </p>
          {domain !== 'all' && (
            <button
              onClick={() => onNav({ tab: 'competitors', competitor: domain })}
              className="flex items-center gap-1 text-xs font-semibold text-indigo-500 hover:text-indigo-700 transition-colors">
              View competitor profile <ArrowRight size={10}/>
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Sort dropdown */}
          <div className="relative">
            {sortOpen && (
              <div className="fixed inset-0 z-10" onClick={() => setSortOpen(false)}/>
            )}
            <button
              onClick={() => setSortOpen(v => !v)}
              className="relative z-20 flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-xl bg-white border border-slate-200 text-slate-600 hover:border-slate-300 shadow-sm transition-all">
              {SORT_LABELS[sortBy]}
              <ChevronDown size={12}
                           className={`transition-transform ${sortOpen ? 'rotate-180' : ''}`}/>
            </button>
            {sortOpen && (
              <div className="absolute right-0 top-full mt-1 w-40 bg-white rounded-xl shadow-xl border border-slate-100 z-30 overflow-hidden">
                {(Object.entries(SORT_LABELS) as [SortKey, string][]).map(([key, label]) => (
                  <button key={key}
                          onClick={() => { setSortBy(key); setSortOpen(false); }}
                          className={`w-full text-left text-xs px-3 py-2.5 font-medium transition-colors ${
                            sortBy === key
                              ? 'bg-indigo-50 text-indigo-700 font-semibold'
                              : 'text-slate-600 hover:bg-slate-50'}`}>
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* View toggle */}
          <div className="flex rounded-xl border border-slate-200 overflow-hidden shadow-sm bg-white">
            <button onClick={() => setViewMode('grid')}
                    title="Grid view"
                    className={`p-2 transition-colors ${
                      viewMode === 'grid' ? 'bg-slate-900 text-white' : 'text-slate-400 hover:text-slate-600'}`}>
              <LayoutGrid size={14}/>
            </button>
            <button onClick={() => setViewMode('list')}
                    title="List view"
                    className={`p-2 border-l border-slate-200 transition-colors ${
                      viewMode === 'list' ? 'bg-slate-900 text-white' : 'text-slate-400 hover:text-slate-600'}`}>
              <AlignJustify size={14}/>
            </button>
          </div>
        </div>
      </div>

      {/* Content */}
      {sorted.length === 0 ? (
        <div className="anim-pop-in text-center py-24 bg-white rounded-2xl border border-dashed border-slate-200">
          <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mx-auto mb-4 anim-float">
            <Search size={28} className="text-slate-300"/>
          </div>
          <p className="font-bold text-slate-600 text-lg">No ads match your filters</p>
          <p className="text-sm text-slate-400 mt-1.5 mb-5">
            Try adjusting your search or clearing some filters
          </p>
          <button onClick={clearAll}
                  className="text-sm font-semibold text-indigo-600 hover:text-indigo-800 transition-colors">
            Clear all filters →
          </button>
        </div>

      ) : viewMode === 'list' ? (
        <div className="space-y-2">
          {sorted.map((ad, i) => (
            <div key={`${ad['Creative ID']}-${i}`}
                 className="anim-fade-up"
                 style={{ animationDelay: `${Math.min(i * 0.02, 0.25)}s` }}>
              <ListRow
                ad={ad}
                onClick={() => setSelectedAd(ad)}
                onDomainClick={d => { setDomain(d); onNav({ tab: 'gallery', domain: d }); }}
              />
            </div>
          ))}
        </div>

      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5">
          {sorted.map((ad, i) => (
            <div key={`${ad['Creative ID']}-${i}`}
                 className="anim-fade-up"
                 style={{ animationDelay: `${Math.min(i * 0.025, 0.3)}s` }}>
              <AdCard
                ad={ad}
                onClick={() => setSelectedAd(ad)}
                onDomainClick={d => { setDomain(d); onNav({ tab: 'gallery', domain: d }); }}
              />
            </div>
          ))}
        </div>
      )}

      {selectedAd && (
        <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)}/>
      )}
    </div>
  );
}
