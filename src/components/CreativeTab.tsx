import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell
} from 'recharts';
import type { Ad } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { getTopKeywords, getAdsByDomain, getMessagingPoints } from '../lib/utils';

interface CreativeTabProps { ads: Ad[]; }

const PALETTE = ['#6366f1','#8b5cf6','#0ea5e9','#10b981','#f59e0b','#ef4444','#ec4899','#14b8a6','#f97316','#84cc16'];

const DarkTip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-900 border border-white/10 rounded-xl shadow-2xl p-3 text-xs">
      <p className="font-semibold text-white/70 mb-1 max-w-[160px] leading-tight">{label}</p>
      <p className="font-black text-white text-base">{payload[0]?.value}</p>
    </div>
  );
};

function Card({ title, subtitle, children, className = '' }: { title: string; subtitle?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-white rounded-2xl border border-slate-100 shadow-sm p-5 ${className}`}>
      <div className="mb-4">
        <h3 className="text-sm font-bold text-slate-800">{title}</h3>
        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

export function CreativeTab({ ads }: CreativeTabProps) {
  const byDomain    = getAdsByDomain(ads);
  const topKW       = getTopKeywords(ads, 18);
  const compKW      = COMPETITORS.map(c => ({ comp: c, kw: getTopKeywords(byDomain[c.domain] || [], 12) }));

  /* Messaging word frequency */
  const msgWords: Record<string,number> = {};
  for (const ad of ads) {
    for (const pt of getMessagingPoints(ad)) {
      for (const w of pt.toLowerCase().split(/[\s,;:—.!?]+/).filter(w => w.length > 4)) {
        msgWords[w] = (msgWords[w] || 0) + 1;
      }
    }
  }
  const topMsg = Object.entries(msgWords).sort((a,b) => b[1]-a[1]).slice(0,16).map(([w,count]) => ({ w, count }));

  /* Headline openers */
  const openers: Record<string,number> = {};
  for (const ad of ads) {
    if (!ad.Headline) continue;
    const key = ad.Headline.trim().split(' ').slice(0,3).join(' ').toLowerCase();
    if (key.length > 3) openers[key] = (openers[key] || 0) + 1;
  }
  const topOpeners = Object.entries(openers).sort((a,b) => b[1]-a[1]).slice(0,12).map(([phrase,count]) => ({ phrase, count }));

  /* Content stats */
  const stats = [
    { label: 'Have Image Creative', count: ads.filter(a=>a['Image URLs']).length },
    { label: 'Have Video Creative',  count: ads.filter(a=>a.Format?.toLowerCase()==='video').length },
    { label: 'Have Clear CTA',       count: ads.filter(a=>a.CTA&&a.CTA.length<40).length },
    { label: 'Have Keyword Data',    count: ads.filter(a=>a.Keywords).length },
  ];

  return (
    <div className="space-y-5">

      {/* Content stat bubbles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {stats.map(({ label, count }, i) => {
          const pct  = Math.round((count / ads.length) * 100);
          const col  = PALETTE[i];
          return (
            <div key={label} className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5 text-center card-lift anim-fade-up" style={{ animationDelay: `${i*0.06}s` }}>
              {/* Ring */}
              <div className="relative w-20 h-20 mx-auto mb-3">
                <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                  <circle cx="18" cy="18" r="15.9" fill="none" stroke="#f1f5f9" strokeWidth="3" />
                  <circle cx="18" cy="18" r="15.9" fill="none" stroke={col} strokeWidth="3"
                          strokeDasharray={`${pct} ${100 - pct}`} strokeLinecap="round"
                          style={{ transition: 'stroke-dasharray 1s ease' }} />
                </svg>
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className="text-xl font-black" style={{ color: col }}>{pct}%</span>
                </div>
              </div>
              <p className="text-xs font-semibold text-slate-600 leading-snug">{label}</p>
              <p className="text-[10px] text-slate-400 mt-0.5">{count} of {ads.length} ads</p>
            </div>
          );
        })}
      </div>

      {/* Top keywords across all */}
      <Card title="Top Keywords — All Competitors" subtitle="Based on keyword data extracted from each ad">
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={topKW} layout="vertical" barSize={14} margin={{ left: 10 }}>
            <defs>
              {topKW.map((_, i) => (
                <linearGradient key={i} id={`kwg${i}`} x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={1} />
                  <stop offset="100%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={0.5} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#f8fafc" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
            <YAxis dataKey="keyword" type="category" tick={{ fontSize: 10, fill: '#475569' }} tickLine={false} axisLine={false} width={170} />
            <Tooltip content={<DarkTip />} cursor={{ fill: 'rgba(99,102,241,0.04)' }} />
            <Bar dataKey="count" radius={[0, 6, 6, 0]} name="Count">
              {topKW.map((_, i) => <Cell key={i} fill={`url(#kwg${i})`} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {/* Per-competitor keyword bars */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {compKW.map(({ comp, kw }) => (
          <Card key={comp.domain} title={`${comp.name}`} subtitle={`Top ${kw.length} keywords`}>
            <div className="space-y-2.5">
              {kw.map(({ keyword, count }, i) => {
                const pct = (count / (kw[0]?.count || 1)) * 100;
                return (
                  <div key={i}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-slate-600 truncate mr-2 font-medium capitalize">{keyword}</span>
                      <span className="font-bold flex-shrink-0" style={{ color: comp.color }}>{count}</span>
                    </div>
                    <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${comp.color}, ${comp.color}80)`, transition: 'width 0.8s ease' }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        ))}
      </div>

      {/* Messaging word cloud */}
      {topMsg.length > 0 && (
        <Card title="Messaging Language" subtitle="Most frequent words used in competitor messaging angles">
          <div className="flex flex-wrap gap-2 pt-1">
            {topMsg.map(({ w, count }, i) => {
              const size = 11 + (count / (topMsg[0]?.count || 1)) * 12;
              const col  = PALETTE[i % PALETTE.length];
              return (
                <span key={i} className="px-3 py-1.5 rounded-full font-semibold text-white transition-transform hover:scale-105 cursor-default"
                      style={{ fontSize: `${size}px`, background: col, opacity: 0.75 + (1 - i / (topMsg.length || 1)) * 0.25 }}>
                  {w}
                </span>
              );
            })}
          </div>
        </Card>
      )}

      {/* Headline openers */}
      {topOpeners.length > 0 && (
        <Card title="Common Headline Openers" subtitle="First 3 words of headlines — reveals messaging patterns">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {topOpeners.map(({ phrase, count }, i) => {
              const col = PALETTE[i % PALETTE.length];
              return (
                <div key={i} className="rounded-xl p-3 card-lift" style={{ background: `${col}0f` }}>
                  <p className="text-xs font-bold capitalize leading-snug mb-1" style={{ color: col }}>"{phrase}"</p>
                  <p className="text-[11px] text-slate-400">{count} ad{count > 1 ? 's' : ''}</p>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Full headline lists per competitor */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {COMPETITORS.map(comp => {
          const headlines = (byDomain[comp.domain] || []).map(a => a.Headline).filter(Boolean).slice(0, 10);
          return (
            <Card key={comp.domain} title={`${comp.name} Headlines`} subtitle={`${headlines.length} unique headlines`}>
              {headlines.length === 0
                ? <p className="text-sm text-slate-400">No headline data</p>
                : (
                  <ol className="space-y-2">
                    {headlines.map((h, i) => (
                      <li key={i} className="flex items-start gap-2 text-xs text-slate-600 leading-relaxed">
                        <span className="font-black flex-shrink-0 mt-0.5 text-[11px]" style={{ color: comp.color }}>{i+1}.</span>
                        {h}
                      </li>
                    ))}
                  </ol>
                )}
            </Card>
          );
        })}
      </div>

    </div>
  );
}
