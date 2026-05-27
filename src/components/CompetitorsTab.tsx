import { useState } from 'react';
import { ExternalLink, Globe, Target, DollarSign, Users, Tag, Lightbulb, TrendingUp } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { getAdsByDomain, countByField, getKeywords } from '../lib/utils';
import { AdCard } from './AdCard';
import { AdModal } from './AdModal';

interface CompetitorsTabProps { ads: Ad[]; }

function InfoBlock({ label, icon, content }: { label: string; icon: React.ReactNode; content: string }) {
  if (!content?.trim()) return null;
  return (
    <div className="mb-5">
      <h5 className="flex items-center gap-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
        <span className="text-slate-300">{icon}</span>{label}
      </h5>
      <p className="text-sm text-slate-700 leading-relaxed">{content}</p>
    </div>
  );
}

export function CompetitorsTab({ ads }: CompetitorsTabProps) {
  const [activeComp, setActiveComp] = useState(COMPETITORS[0].domain);
  const [selectedAd, setSelectedAd] = useState<Ad | null>(null);

  const byDomain = getAdsByDomain(ads);
  const comp     = COMPETITORS.find(c => c.domain === activeComp)!;
  const compAds  = byDomain[activeComp] || [];
  const enriched = compAds.find(a => a['Website Summary'] || a.Services || a['Messaging Angle']);
  const allKW    = [...new Set(compAds.flatMap(a => getKeywords(a)).map(k => k.toLowerCase()))];
  const fmtCounts = countByField(compAds, 'Format');
  const ctaList   = [...new Set(compAds.map(a => a.CTA).filter(c => c && c.length < 45))];
  const msgAngles = enriched?.['Messaging Angle']?.split(';').map(s => s.trim()).filter(Boolean) ?? [];
  const recentAds = [...compAds].filter(a => a['Last Shown']).sort((a,b) => b['Last Shown'].localeCompare(a['Last Shown'])).slice(0, 8);
  const statusActive = compAds.filter(a => a.Status === 'active').length;

  return (
    <div>
      {/* Competitor selector tabs */}
      <div className="flex flex-wrap gap-3 mb-6">
        {COMPETITORS.map(c => {
          const cnt  = (byDomain[c.domain] || []).length;
          const isActive = activeComp === c.domain;
          return (
            <button key={c.domain} onClick={() => setActiveComp(c.domain)}
                    className="flex items-center gap-3 px-4 py-3 rounded-2xl border-2 transition-all duration-200 card-lift"
                    style={isActive
                      ? { background: `linear-gradient(135deg, ${c.color}, ${c.color}cc)`, borderColor: c.color, color: 'white', boxShadow: `0 8px 25px -5px ${c.color}55` }
                      : { background: 'white', borderColor: `${c.color}30`, color: c.color }}>
              <div className="w-8 h-8 rounded-xl flex items-center justify-center text-xs font-black flex-shrink-0"
                   style={isActive ? { background: 'rgba(255,255,255,0.25)', color: 'white' } : { background: `${c.color}18`, color: c.color }}>
                {c.name[0]}
              </div>
              <div className="text-left">
                <p className="text-sm font-bold leading-none">{c.name}</p>
                <p className="text-[10px] mt-0.5 opacity-70">{c.domain}</p>
              </div>
              <span className="text-xs font-black px-2 py-1 rounded-full ml-1"
                    style={isActive ? { background: 'rgba(255,255,255,0.2)', color: 'white' } : { background: `${c.color}20`, color: c.color }}>
                {cnt}
              </span>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

        {/* Left column: intelligence */}
        <div className="space-y-4">

          {/* Profile card */}
          <div className="bg-white rounded-2xl border border-slate-100 shadow-sm overflow-hidden">
            <div className="h-2" style={{ background: `linear-gradient(90deg, ${comp.color}, ${comp.color}60)` }} />
            <div className="p-5">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-12 h-12 rounded-2xl flex items-center justify-center text-white font-black text-lg shadow-lg"
                     style={{ background: `linear-gradient(135deg, ${comp.color}, ${comp.color}aa)`, boxShadow: `0 8px 20px -4px ${comp.color}55` }}>
                  {comp.name[0]}
                </div>
                <div>
                  <h3 className="font-black text-slate-900 text-base leading-none">{comp.name}</h3>
                  <a href={`https://${comp.domain}`} target="_blank" rel="noopener noreferrer"
                     className="flex items-center gap-1 text-xs mt-1 hover:underline" style={{ color: comp.color }}>
                    <Globe size={10} /> {comp.domain} <ExternalLink size={9} />
                  </a>
                </div>
              </div>
              {/* Stats */}
              <div className="grid grid-cols-3 gap-2 text-center">
                {[['Total Ads', compAds.length], ['Active', statusActive], ['Formats', Object.keys(fmtCounts).length]].map(([l,v], i) => (
                  <div key={i} className="rounded-xl py-3 px-1" style={{ background: `${comp.color}0a` }}>
                    <p className="text-2xl font-black" style={{ color: i===1 ? comp.color : '#0f172a' }}>{v}</p>
                    <p className="text-[10px] text-slate-400 font-medium">{l}</p>
                  </div>
                ))}
              </div>
              {/* Format pills */}
              <div className="flex gap-2 flex-wrap mt-3">
                {Object.entries(fmtCounts).map(([fmt, cnt]) => (
                  <span key={fmt} className="text-xs font-semibold px-2.5 py-1 rounded-full capitalize"
                        style={{ background: `${comp.color}15`, color: comp.color }}>
                    {fmt} · {cnt}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Intelligence */}
          {enriched && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <h4 className="flex items-center gap-2 font-bold text-slate-800 text-sm mb-4">
                <TrendingUp size={15} style={{ color: comp.color }} /> Competitor Intelligence
              </h4>
              <InfoBlock label="About" icon={<Globe size={11} />} content={(enriched['Website Summary'] || '').slice(0, 350)} />
              <InfoBlock label="Value Proposition" icon={<Target size={11} />} content={(enriched['Value Proposition'] || '').slice(0, 280)} />
              <InfoBlock label="Services" icon={<Tag size={11} />} content={(enriched.Services || '').slice(0, 280)} />
              <InfoBlock label="Pricing Model" icon={<DollarSign size={11} />} content={(enriched['Pricing Model'] || '').slice(0, 230)} />
              <InfoBlock label="Target Audience" icon={<Users size={11} />} content={(enriched['Audience Type'] || '').slice(0, 230)} />
            </div>
          )}

          {/* Messaging angles */}
          {msgAngles.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <h4 className="flex items-center gap-2 font-bold text-slate-800 text-sm mb-3">
                <Lightbulb size={14} style={{ color: comp.color }} /> Messaging Angles
              </h4>
              <ul className="space-y-2">
                {msgAngles.map((a, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                    <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0 mt-0.5 text-white"
                          style={{ background: comp.color }}>{i+1}</span>
                    {a}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* CTAs */}
          {ctaList.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <h4 className="font-bold text-slate-800 text-sm mb-3">CTAs Used</h4>
              <div className="flex flex-wrap gap-2">
                {ctaList.map((cta, i) => (
                  <span key={i} className="text-sm font-semibold px-3 py-1.5 rounded-xl"
                        style={{ background: `${comp.color}15`, color: comp.color }}>{cta}</span>
                ))}
              </div>
            </div>
          )}

          {/* Keywords */}
          {allKW.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
              <h4 className="font-bold text-slate-800 text-sm mb-3">Keywords <span className="text-slate-400 font-normal">({allKW.length})</span></h4>
              <div className="flex flex-wrap gap-1.5">
                {allKW.slice(0, 40).map((kw, i) => (
                  <span key={i} className="text-xs px-2.5 py-1 rounded-full font-medium"
                        style={{ background: `${comp.color}10`, color: comp.color }}>{kw}</span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right: recent ads */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
            <h4 className="font-bold text-slate-800 text-sm mb-4">
              Recent Ads
              <span className="ml-2 text-slate-400 font-normal text-xs">({recentAds.length} shown)</span>
            </h4>
            {recentAds.length === 0 ? (
              <div className="text-center py-12 text-slate-400">
                <div className="w-12 h-12 rounded-xl bg-slate-100 flex items-center justify-center mx-auto mb-3">
                  <Tag size={20} className="text-slate-300" />
                </div>
                <p className="font-medium">No ads found</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {recentAds.map((ad, i) => (
                  <div key={i} className="anim-fade-up" style={{ animationDelay: `${i * 0.04}s` }}>
                    <AdCard ad={ad} onClick={() => setSelectedAd(ad)} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)} />}
    </div>
  );
}
