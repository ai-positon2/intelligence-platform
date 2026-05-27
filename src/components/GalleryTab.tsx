import { useState } from 'react';
import { Search, SlidersHorizontal } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { AdCard } from './AdCard';
import { AdModal } from './AdModal';

interface GalleryTabProps {
  ads: Ad[];
}

export function GalleryTab({ ads }: GalleryTabProps) {
  const [selectedAd, setSelectedAd] = useState<Ad | null>(null);
  const [search, setSearch] = useState('');
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
      {/* Filters bar */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-5 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 text-slate-400">
          <SlidersHorizontal size={16} />
          <span className="text-sm font-medium text-slate-600">Filter:</span>
        </div>

        {/* Competitor filter */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setFilterDomain('all')}
            className={`text-xs font-medium px-3 py-1.5 rounded-full transition-all ${
              filterDomain === 'all'
                ? 'bg-slate-800 text-white'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            All Competitors
          </button>
          {COMPETITORS.map(c => (
            <button
              key={c.domain}
              onClick={() => setFilterDomain(c.domain)}
              className="text-xs font-semibold px-3 py-1.5 rounded-full transition-all"
              style={
                filterDomain === c.domain
                  ? { backgroundColor: c.color, color: 'white' }
                  : { backgroundColor: `${c.color}15`, color: c.color }
              }
            >
              {c.name}
            </button>
          ))}
        </div>

        {/* Divider */}
        <div className="h-6 w-px bg-slate-200 mx-1" />

        {/* Format filter */}
        <div className="flex gap-2">
          {['all', 'image', 'text', 'video'].map(fmt => (
            <button
              key={fmt}
              onClick={() => setFilterFormat(fmt)}
              className={`text-xs font-medium px-3 py-1.5 rounded-full capitalize transition-all ${
                filterFormat === fmt
                  ? 'bg-slate-800 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {fmt === 'all' ? 'All Formats' : fmt}
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="flex-1 min-w-48 relative ml-auto">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search headlines, CTAs, keywords…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full text-sm pl-8 pr-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          />
        </div>
      </div>

      {/* Results count */}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-slate-500">
          Showing <strong className="text-slate-700">{filtered.length}</strong> of {ads.length} ads
        </p>
      </div>

      {/* Grid */}
      {filtered.length === 0 ? (
        <div className="text-center py-16 text-slate-400">
          <Search size={40} className="mx-auto mb-3 opacity-30" />
          <p className="font-medium">No ads match your filters</p>
          <p className="text-sm mt-1">Try adjusting your search or filters</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((ad, i) => (
            <AdCard
              key={`${ad['Creative ID']}-${i}`}
              ad={ad}
              onClick={() => setSelectedAd(ad)}
            />
          ))}
        </div>
      )}

      {/* Modal */}
      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)} />}
    </div>
  );
}
