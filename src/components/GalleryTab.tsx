import { useState } from 'react';
import { Search, SlidersHorizontal, ImageIcon, FileText, Video, X } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { AdCard } from './AdCard';
import { AdModal } from './AdModal';

interface GalleryTabProps { ads: Ad[]; }

const FORMAT_OPTS = [
  { id: 'all',   label: 'All',   icon: <SlidersHorizontal size={12} /> },
  { id: 'image', label: 'Image', icon: <ImageIcon size={12} /> },
  { id: 'text',  label: 'Text',  icon: <FileText  size={12} /> },
  { id: 'video', label: 'Video', icon: <Video     size={12} /> },
];

export function GalleryTab({ ads }: GalleryTabProps) {
  const [selectedAd, setSelectedAd]   = useState<Ad | null>(null);
  const [search, setSearch]           = useState('');
  const [filterDomain, setFilterDomain] = useState<string>('all');
  const [filterFormat, setFilterFormat] = useState<string>('all');

  const filtered = ads.filter(ad => {
    const matchDomain = filterDomain === 'all' || ad.Domain === filterDomain;
    const matchFormat = filterFormat === 'all' || ad.Format?.toLowerCase() === filterFormat;
    const q = search.toLowerCase();
    const matchSearch = !q ||
      ad.Headline?.toLowerCase().includes(q) ||
      ad.Description?.toLowerCase().includes(q) ||
      ad['Full Ad Text']?.toLowerCase().includes(q) ||
      ad.CTA?.toLowerCase().includes(q) ||
      ad.Keywords?.toLowerCase().includes(q);
    return matchDomain && matchFormat && matchSearch;
  });

  return (
    <div>
      {/* Filter bar */}
      <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-5">
        <div className="flex flex-wrap items-center gap-3">

          {/* Competitor pills */}
          <div className="flex flex-wrap gap-1.5">
            <button onClick={() => setFilterDomain('all')}
                    className={`text-xs font-semibold px-3 py-1.5 rounded-full transition-all ${filterDomain === 'all' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
              All
            </button>
            {COMPETITORS.map(c => (
              <button key={c.domain} onClick={() => setFilterDomain(c.domain)}
                      className="text-xs font-semibold px-3 py-1.5 rounded-full transition-all"
                      style={filterDomain === c.domain
                        ? { background: c.color, color: 'white' }
                        : { background: `${c.color}15`, color: c.color }}>
                {c.name}
              </button>
            ))}
          </div>

          <div className="h-5 w-px bg-slate-200 hidden sm:block" />

          {/* Format pills */}
          <div className="flex gap-1.5">
            {FORMAT_OPTS.map(f => (
              <button key={f.id} onClick={() => setFilterFormat(f.id)}
                      className={`flex items-center gap-1 text-xs font-semibold px-3 py-1.5 rounded-full capitalize transition-all ${
                        filterFormat === f.id ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                      }`}>
                {f.icon} {f.label}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="flex-1 min-w-48 relative ml-auto">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
            <input
              type="text" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Search headlines, CTAs, keywords…"
              className="w-full text-sm pl-9 pr-8 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 transition-all"
            />
            {search && (
              <button onClick={() => setSearch('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                <X size={13} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Result count */}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-slate-500">
          Showing <strong className="text-slate-800 font-bold">{filtered.length}</strong>{' '}
          <span className="text-slate-400">of {ads.length} ads</span>
        </p>
        {(filterDomain !== 'all' || filterFormat !== 'all' || search) && (
          <button onClick={() => { setFilterDomain('all'); setFilterFormat('all'); setSearch(''); }}
                  className="text-xs text-indigo-600 hover:text-indigo-800 font-medium flex items-center gap-1">
            <X size={11} /> Clear filters
          </button>
        )}
      </div>

      {/* Grid */}
      {filtered.length === 0 ? (
        <div className="text-center py-20 bg-white rounded-2xl border border-slate-100">
          <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mx-auto mb-4">
            <Search size={28} className="text-slate-300" />
          </div>
          <p className="font-bold text-slate-600 text-lg">No ads match your filters</p>
          <p className="text-sm text-slate-400 mt-1.5">Try adjusting your search or clearing filters</p>
          <button onClick={() => { setFilterDomain('all'); setFilterFormat('all'); setSearch(''); }}
                  className="mt-4 text-sm text-indigo-600 hover:text-indigo-800 font-semibold">
            Clear all filters →
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((ad, i) => (
            <div key={`${ad['Creative ID']}-${i}`} className="anim-fade-up" style={{ animationDelay: `${Math.min(i * 0.03, 0.3)}s` }}>
              <AdCard ad={ad} onClick={() => setSelectedAd(ad)} />
            </div>
          ))}
        </div>
      )}

      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)} />}
    </div>
  );
}
