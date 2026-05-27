import { useState } from 'react';
import { Search, SlidersHorizontal, ImageIcon, FileText, Video, X, ArrowRight } from 'lucide-react';
import type { Ad, NavFn } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { AdCard } from './AdCard';
import { AdModal } from './AdModal';

interface GalleryTabProps {
  ads: Ad[];
  domain: string;      setDomain: (v: string) => void;
  format: string;      setFormat: (v: string) => void;
  search: string;      setSearch: (v: string) => void;
  onNav: NavFn;
}

const FORMAT_OPTS = [
  { id: 'all',   label: 'All',   icon: <SlidersHorizontal size={12}/> },
  { id: 'image', label: 'Image', icon: <ImageIcon  size={12}/> },
  { id: 'text',  label: 'Text',  icon: <FileText   size={12}/> },
  { id: 'video', label: 'Video', icon: <Video      size={12}/> },
];

export function GalleryTab({ ads, domain, setDomain, format, setFormat, search, setSearch, onNav }: GalleryTabProps) {
  const [selectedAd, setSelectedAd] = useState<Ad | null>(null);

  const hasFilters = domain !== 'all' || format !== 'all' || search !== '';

  const filtered = ads.filter(ad => {
    const matchDomain = domain === 'all' || ad.Domain === domain;
    const matchFormat = format === 'all' || ad.Format?.toLowerCase() === format;
    const q = search.toLowerCase();
    const matchSearch = !q ||
      ad.Headline?.toLowerCase().includes(q) ||
      ad.Description?.toLowerCase().includes(q) ||
      ad['Full Ad Text']?.toLowerCase().includes(q) ||
      ad.CTA?.toLowerCase().includes(q) ||
      ad.Keywords?.toLowerCase().includes(q);
    return matchDomain && matchFormat && matchSearch;
  });

  const clearAll = () => { setDomain('all'); setFormat('all'); setSearch(''); };

  return (
    <div>
      {/* Active filter breadcrumb */}
      {hasFilters && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <span className="text-xs text-slate-500 font-medium">Filtered by:</span>
          {domain !== 'all' && (
            <span className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-100">
              {COMPETITORS.find(c => c.domain === domain)?.name ?? domain}
              <button onClick={() => setDomain('all')}><X size={10}/></button>
            </span>
          )}
          {format !== 'all' && (
            <span className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 capitalize">
              {format} ads
              <button onClick={() => setFormat('all')}><X size={10}/></button>
            </span>
          )}
          {search && (
            <span className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-100">
              "{search}"
              <button onClick={() => setSearch('')}><X size={10}/></button>
            </span>
          )}
          <button onClick={clearAll} className="text-xs text-slate-400 hover:text-slate-600 underline ml-1">Clear all</button>
        </div>
      )}

      {/* Filter bar */}
      <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-5">
        <div className="flex flex-wrap items-center gap-3">
          {/* Competitor pills */}
          <div className="flex flex-wrap gap-1.5">
            <button onClick={() => setDomain('all')}
                    className={`text-xs font-semibold px-3 py-1.5 rounded-full transition-all ${domain === 'all' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
              All
            </button>
            {COMPETITORS.map(c => (
              <button key={c.domain} onClick={() => setDomain(c.domain)}
                      className="text-xs font-semibold px-3 py-1.5 rounded-full transition-all"
                      style={domain === c.domain ? { background: c.color, color: 'white' } : { background: `${c.color}15`, color: c.color }}>
                {c.name}
              </button>
            ))}
          </div>

          <div className="h-5 w-px bg-slate-200 hidden sm:block"/>

          {/* Format pills */}
          <div className="flex gap-1.5">
            {FORMAT_OPTS.map(f => (
              <button key={f.id} onClick={() => setFormat(f.id)}
                      className={`flex items-center gap-1 text-xs font-semibold px-3 py-1.5 rounded-full capitalize transition-all ${
                        format === f.id ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                {f.icon} {f.label}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="flex-1 min-w-48 relative ml-auto">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"/>
            <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                   placeholder="Search headlines, CTAs, keywords…"
                   className="w-full text-sm pl-9 pr-8 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 transition-all"/>
            {search && (
              <button onClick={() => setSearch('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                <X size={13}/>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Result count + cross-link */}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-slate-500">
          Showing <strong className="text-slate-800 font-bold">{filtered.length}</strong>{' '}
          <span className="text-slate-400">of {ads.length} ads</span>
        </p>
        <div className="flex items-center gap-3">
          {domain !== 'all' && (
            <button onClick={() => onNav({ tab: 'competitors', competitor: domain })}
                    className="flex items-center gap-1 text-xs font-semibold text-indigo-500 hover:text-indigo-700 transition-colors">
              View competitor profile <ArrowRight size={11}/>
            </button>
          )}
          {hasFilters && (
            <button onClick={clearAll} className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 transition-colors">
              <X size={11}/> Clear
            </button>
          )}
        </div>
      </div>

      {/* Grid */}
      {filtered.length === 0 ? (
        <div className="text-center py-20 bg-white rounded-2xl border border-slate-100">
          <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mx-auto mb-4">
            <Search size={28} className="text-slate-300"/>
          </div>
          <p className="font-bold text-slate-600 text-lg">No ads match your filters</p>
          <p className="text-sm text-slate-400 mt-1.5">Try adjusting your search or clearing filters</p>
          <button onClick={clearAll} className="mt-4 text-sm text-indigo-600 hover:text-indigo-800 font-semibold">
            Clear all filters →
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((ad, i) => (
            <div key={`${ad['Creative ID']}-${i}`} className="anim-fade-up" style={{ animationDelay: `${Math.min(i*0.03, 0.3)}s` }}>
              <AdCard ad={ad} onClick={() => setSelectedAd(ad)}
                      onDomainClick={d => { setDomain(d); onNav({ tab: 'gallery', domain: d }); }} />
            </div>
          ))}
        </div>
      )}

      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)}/>}
    </div>
  );
}
