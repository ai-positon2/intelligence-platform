import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell
} from 'recharts';
import type { Ad } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { getTopKeywords, getAdsByDomain, getMessagingPoints } from '../lib/utils';

interface CreativeTabProps {
  ads: Ad[];
}

const KEYWORD_COLORS = [
  '#6366f1', '#8b5cf6', '#0ea5e9', '#10b981', '#f59e0b',
  '#ef4444', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
];

function ChartCard({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-lg p-3 text-sm max-w-xs">
      <p className="font-semibold text-slate-700 mb-1">{label}</p>
      <p className="text-indigo-600">Count: <strong>{payload[0]?.value}</strong></p>
    </div>
  );
};

export function CreativeTab({ ads }: CreativeTabProps) {
  const byDomain = getAdsByDomain(ads);
  const topKeywords = getTopKeywords(ads, 20);

  // Per-competitor top keywords
  const compKeywords = COMPETITORS.map(comp => ({
    comp,
    keywords: getTopKeywords(byDomain[comp.domain] || [], 15),
  }));

  // Messaging angle word frequency across all ads
  const messagingWords: Record<string, number> = {};
  for (const ad of ads) {
    const points = getMessagingPoints(ad);
    for (const point of points) {
      const words = point.toLowerCase().split(/[\s,;:]+/).filter(w => w.length > 4);
      for (const w of words) {
        messagingWords[w] = (messagingWords[w] || 0) + 1;
      }
    }
  }
  const topMessagingWords = Object.entries(messagingWords)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([word, count]) => ({ word, count }));

  // Headline patterns — extract first word patterns
  const headlineStarters: Record<string, number> = {};
  for (const ad of ads) {
    if (!ad.Headline) continue;
    const firstWords = ad.Headline.split(' ').slice(0, 2).join(' ').toLowerCase();
    if (firstWords.length > 3) {
      headlineStarters[firstWords] = (headlineStarters[firstWords] || 0) + 1;
    }
  }
  const topStarters = Object.entries(headlineStarters)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([phrase, count]) => ({ phrase, count }));

  // Format-based content analysis
  const withImages = ads.filter(a => a['Image URLs']);
  const withVideo = ads.filter(a => a['Video URLs'] || a.Format?.toLowerCase() === 'video');
  const withCTA = ads.filter(a => a.CTA && a.CTA.length < 40);
  const withOffer = ads.filter(a => a.Offer);

  const contentStats = [
    { label: 'Have Image Creative', count: withImages.length, pct: Math.round((withImages.length / ads.length) * 100) },
    { label: 'Have Video Creative', count: withVideo.length, pct: Math.round((withVideo.length / ads.length) * 100) },
    { label: 'Have Clear CTA', count: withCTA.length, pct: Math.round((withCTA.length / ads.length) * 100) },
    { label: 'Have Special Offer', count: withOffer.length, pct: Math.round((withOffer.length / ads.length) * 100) },
  ];

  return (
    <div className="space-y-5">
      {/* Creative content stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {contentStats.map(({ label, count, pct }) => (
          <div key={label} className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 text-center">
            <p className="text-3xl font-bold text-slate-800">{pct}%</p>
            <p className="text-xs text-slate-500 mt-1">{label}</p>
            <p className="text-xs text-slate-400 mt-0.5">{count} of {ads.length} ads</p>
          </div>
        ))}
      </div>

      {/* Top keywords overall */}
      <ChartCard title="Top Keywords Across All Competitors" subtitle="Based on keyword data extracted from each ad">
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={topKeywords} layout="vertical" barSize={16} margin={{ left: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
            <YAxis
              dataKey="keyword"
              type="category"
              tick={{ fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={160}
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar dataKey="count" radius={[0, 4, 4, 0]}>
              {topKeywords.map((_, i) => (
                <Cell key={i} fill={KEYWORD_COLORS[i % KEYWORD_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* Per-competitor keyword comparison */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {compKeywords.map(({ comp, keywords }) => (
          <ChartCard key={comp.domain} title={`${comp.name} Keywords`} subtitle={`Top ${keywords.length} keywords`}>
            <div className="space-y-2">
              {keywords.slice(0, 12).map(({ keyword, count }, i) => {
                const max = keywords[0]?.count || 1;
                return (
                  <div key={i}>
                    <div className="flex justify-between text-xs mb-0.5">
                      <span className="text-slate-600 truncate mr-2 capitalize">{keyword}</span>
                      <span className="font-semibold text-slate-700 flex-shrink-0">{count}</span>
                    </div>
                    <div className="h-1.5 bg-slate-100 rounded-full">
                      <div
                        className="h-full rounded-full transition-all"
                        style={{ width: `${(count / max) * 100}%`, backgroundColor: comp.color }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </ChartCard>
        ))}
      </div>

      {/* Messaging word frequency */}
      {topMessagingWords.length > 0 && (
        <ChartCard title="Top Words in Messaging Angles" subtitle="Most frequently used words across all competitor messaging">
          <div className="flex flex-wrap gap-2 mt-2">
            {topMessagingWords.map(({ word, count }, i) => {
              const size = Math.max(11, Math.min(20, 11 + count * 1.5));
              return (
                <span
                  key={i}
                  className="px-3 py-1.5 rounded-full font-medium text-white transition-transform hover:scale-105"
                  style={{
                    fontSize: `${size}px`,
                    backgroundColor: KEYWORD_COLORS[i % KEYWORD_COLORS.length],
                    opacity: 0.85 + (i / topMessagingWords.length) * 0.15,
                  }}
                >
                  {word}
                </span>
              );
            })}
          </div>
        </ChartCard>
      )}

      {/* Headline patterns */}
      {topStarters.length > 0 && (
        <ChartCard title="Common Headline Openers" subtitle="First 2 words of headlines — reveals messaging patterns">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {topStarters.map(({ phrase, count }, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-3 rounded-lg"
                style={{ backgroundColor: `${KEYWORD_COLORS[i % KEYWORD_COLORS.length]}12` }}
              >
                <span
                  className="text-sm font-semibold capitalize"
                  style={{ color: KEYWORD_COLORS[i % KEYWORD_COLORS.length] }}
                >
                  "{phrase}"
                </span>
                <span
                  className="text-xs font-bold w-5 h-5 rounded-full flex items-center justify-center text-white ml-2 flex-shrink-0"
                  style={{ backgroundColor: KEYWORD_COLORS[i % KEYWORD_COLORS.length] }}
                >
                  {count}
                </span>
              </div>
            ))}
          </div>
        </ChartCard>
      )}

      {/* Ad headlines list by competitor */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {COMPETITORS.map(comp => {
          const compAds = byDomain[comp.domain] || [];
          const headlines = compAds
            .map(a => a.Headline)
            .filter(Boolean)
            .slice(0, 8);

          return (
            <div key={comp.domain} className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
              <h4 className="font-semibold text-slate-800 mb-3 flex items-center gap-2 text-sm">
                <span
                  className="w-3 h-3 rounded-full flex-shrink-0"
                  style={{ backgroundColor: comp.color }}
                />
                {comp.name} Headlines
              </h4>
              {headlines.length === 0 ? (
                <p className="text-sm text-slate-400">No headline data available</p>
              ) : (
                <ul className="space-y-2">
                  {headlines.map((h, i) => (
                    <li key={i} className="text-xs text-slate-600 flex items-start gap-2 leading-relaxed">
                      <span
                        className="font-bold flex-shrink-0 mt-0.5"
                        style={{ color: comp.color }}
                      >
                        {i + 1}.
                      </span>
                      {h}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
