import { useState } from 'react';
import { ExternalLink, Globe, Target, DollarSign, Users, Tag, Lightbulb } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { getAdsByDomain, countByField, getKeywords } from '../lib/utils';
import { AdCard } from './AdCard';
import { AdModal } from './AdModal';

interface CompetitorsTabProps {
  ads: Ad[];
}

function InfoBlock({ label, icon, content }: { label: string; icon: React.ReactNode; content: string }) {
  if (!content) return null;
  return (
    <div className="mb-4">
      <h5 className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5 mb-1.5">
        {icon} {label}
      </h5>
      <p className="text-sm text-slate-700 leading-relaxed">{content}</p>
    </div>
  );
}

function KeywordCloud({ keywords, color }: { keywords: string[]; color: string }) {
  if (!keywords.length) return <p className="text-sm text-slate-400">No keyword data</p>;
  const unique = [...new Set(keywords.slice(0, 40))];
  return (
    <div className="flex flex-wrap gap-1.5">
      {unique.map((kw, i) => (
        <span
          key={i}
          className="text-xs px-2 py-0.5 rounded-full"
          style={{ backgroundColor: `${color}15`, color }}
        >
          {kw}
        </span>
      ))}
    </div>
  );
}

export function CompetitorsTab({ ads }: CompetitorsTabProps) {
  const [selectedAd, setSelectedAd] = useState<Ad | null>(null);
  const [activeTab, setActiveTab] = useState(COMPETITORS[0].domain);

  const byDomain = getAdsByDomain(ads);

  const comp = COMPETITORS.find(c => c.domain === activeTab)!;
  const compAds = byDomain[activeTab] || [];

  // Aggregate enriched data (from first enriched row)
  const enriched = compAds.find(a => a['Website Summary'] || a.Services || a['Messaging Angle']);
  const allKeywords = compAds.flatMap(a => getKeywords(a));
  const uniqueKeywords = [...new Set(allKeywords.map(k => k.toLowerCase()))];

  const formatCounts = countByField(compAds, 'Format');
  const ctaList = [...new Set(compAds.map(a => a.CTA).filter(c => c && c.length < 40))];

  // Messaging angles (unique)
  const messagingAngles = enriched?.['Messaging Angle']
    ? enriched['Messaging Angle'].split(';').map(s => s.trim()).filter(Boolean)
    : [];

  // Social profiles
  const socialProfiles = enriched?.['Social Profiles'] || '';

  const recentAds = [...compAds]
    .filter(a => a['Last Shown'])
    .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))
    .slice(0, 8);

  return (
    <div>
      {/* Competitor tabs */}
      <div className="flex gap-3 mb-6">
        {COMPETITORS.map(c => {
          const count = (byDomain[c.domain] || []).length;
          return (
            <button
              key={c.domain}
              onClick={() => setActiveTab(c.domain)}
              className="flex items-center gap-2 px-4 py-3 rounded-xl border transition-all text-sm font-semibold"
              style={
                activeTab === c.domain
                  ? { backgroundColor: c.color, color: 'white', borderColor: c.color }
                  : { backgroundColor: 'white', color: c.color, borderColor: `${c.color}40` }
              }
            >
              <div
                className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                style={{ backgroundColor: activeTab === c.domain ? 'rgba(255,255,255,0.3)' : c.color }}
              >
                {c.name[0]}
              </div>
              <span>{c.name}</span>
              <span
                className="text-xs px-1.5 py-0.5 rounded-full font-medium"
                style={{
                  backgroundColor: activeTab === c.domain ? 'rgba(255,255,255,0.25)' : `${c.color}20`,
                  color: activeTab === c.domain ? 'white' : c.color
                }}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Competitor detail */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Left: intel panel */}
        <div className="lg:col-span-1 space-y-4">
          {/* Header card */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
            <div className="flex items-start gap-3 mb-4">
              <div
                className="w-12 h-12 rounded-xl flex items-center justify-center text-white font-bold text-xl flex-shrink-0"
                style={{ backgroundColor: comp.color }}
              >
                {comp.name[0]}
              </div>
              <div>
                <h3 className="font-bold text-slate-800">{comp.name}</h3>
                <a
                  href={`https://${comp.domain}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm flex items-center gap-1 hover:text-indigo-600 transition-colors"
                  style={{ color: comp.color }}
                >
                  <Globe size={12} /> {comp.domain} <ExternalLink size={11} />
                </a>
              </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-2 text-center mb-4">
              <div className="bg-slate-50 rounded-lg p-2.5">
                <p className="text-xl font-bold text-slate-800">{compAds.length}</p>
                <p className="text-xs text-slate-400">Total Ads</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-2.5">
                <p className="text-xl font-bold" style={{ color: comp.color }}>
                  {compAds.filter(a => a.Status === 'active').length}
                </p>
                <p className="text-xs text-slate-400">Active</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-2.5">
                <p className="text-xl font-bold text-slate-800">{Object.keys(formatCounts).length}</p>
                <p className="text-xs text-slate-400">Formats</p>
              </div>
            </div>

            {/* Format breakdown */}
            <div className="flex gap-2 flex-wrap">
              {Object.entries(formatCounts).map(([fmt, cnt]) => (
                <span key={fmt} className="text-xs font-medium px-2 py-1 rounded-lg bg-slate-100 text-slate-600 capitalize">
                  {fmt}: {cnt}
                </span>
              ))}
            </div>
          </div>

          {/* Intelligence */}
          {enriched && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
              <h4 className="font-semibold text-slate-800 mb-4">Competitor Intelligence</h4>

              {enriched['Website Summary'] && (
                <InfoBlock
                  label="About"
                  icon={<Globe size={12} />}
                  content={enriched['Website Summary'].slice(0, 400)}
                />
              )}

              {enriched['Value Proposition'] && (
                <InfoBlock
                  label="Value Proposition"
                  icon={<Target size={12} />}
                  content={enriched['Value Proposition'].slice(0, 300)}
                />
              )}

              {enriched.Services && (
                <InfoBlock
                  label="Services"
                  icon={<Tag size={12} />}
                  content={enriched.Services.slice(0, 300)}
                />
              )}

              {enriched['Pricing Model'] && (
                <InfoBlock
                  label="Pricing Model"
                  icon={<DollarSign size={12} />}
                  content={enriched['Pricing Model'].slice(0, 250)}
                />
              )}

              {enriched['Audience Type'] && (
                <InfoBlock
                  label="Target Audience"
                  icon={<Users size={12} />}
                  content={enriched['Audience Type'].slice(0, 250)}
                />
              )}
            </div>
          )}

          {/* Messaging Angles */}
          {messagingAngles.length > 0 && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
              <h4 className="font-semibold text-slate-800 mb-3 flex items-center gap-2">
                <Lightbulb size={16} style={{ color: comp.color }} /> Messaging Angles
              </h4>
              <ul className="space-y-2">
                {messagingAngles.map((angle, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                    <span
                      className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0"
                      style={{ backgroundColor: comp.color }}
                    />
                    {angle}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* CTAs */}
          {ctaList.length > 0 && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
              <h4 className="font-semibold text-slate-800 mb-3">CTAs Used</h4>
              <div className="flex flex-wrap gap-2">
                {ctaList.map((cta, i) => (
                  <span
                    key={i}
                    className="text-sm font-medium px-3 py-1 rounded-lg"
                    style={{ backgroundColor: `${comp.color}15`, color: comp.color }}
                  >
                    {cta}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Keywords */}
          {uniqueKeywords.length > 0 && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
              <h4 className="font-semibold text-slate-800 mb-3">Keywords ({uniqueKeywords.length})</h4>
              <KeywordCloud keywords={uniqueKeywords} color={comp.color} />
            </div>
          )}

          {/* Social links */}
          {socialProfiles && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
              <h4 className="font-semibold text-slate-800 mb-3">Social & Web Presence</h4>
              <div className="space-y-1.5">
                {socialProfiles.split('|').map((profile, i) => {
                  const urlMatch = profile.match(/https?:\/\/[^\s]+/);
                  const label = profile.replace(urlMatch?.[0] || '', '').replace(/^[\s:]+/, '').trim();
                  if (!urlMatch) return null;
                  return (
                    <a
                      key={i}
                      href={urlMatch[0]}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-2 text-sm text-slate-600 hover:text-indigo-600 transition-colors"
                    >
                      <ExternalLink size={12} style={{ color: comp.color }} />
                      {label || urlMatch[0]}
                    </a>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Right: recent ads */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
            <h4 className="font-semibold text-slate-800 mb-4">Recent Ads</h4>
            {recentAds.length === 0 ? (
              <p className="text-slate-400 text-sm">No ads found for this competitor.</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {recentAds.map((ad, i) => (
                  <AdCard key={i} ad={ad} onClick={() => setSelectedAd(ad)} />
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
