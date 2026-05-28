import { useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend, AreaChart, Area
} from 'recharts';
import { ArrowRight } from 'lucide-react';
import type { Ad, NavFn } from '../lib/types';
import { COMPETITORS, COMPETITOR_COLORS, FORMAT_COLORS } from '../lib/types';
import { countByField, getAdActivityByDate, getCTACounts, getAdsByDomain } from '../lib/utils';
import { AdModal } from './AdModal';

interface OverviewTabProps { ads: Ad[]; onNav: NavFn; }

/* ── Tooltip ──────────────────────────────────────────── */
const DarkTip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-900 border border-white/10 rounded-xl shadow-2xl p-3 text-sm pointer-events-none">
      {label && <p className="font-semibold text-white/70 mb-1.5 text-xs">{label}</p>}
      {payload.map(p => (
        <p key={p.name} className="flex items-center gap-2 text-xs">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: p.color }}/>
          <span className="text-white/60">{p.name}:</span>
          <span className="font-bold text-white">{p.value}</span>
        </p>
      ))}
      <p className="text-white/30 text-[10px] mt-1.5">Click to explore →</p>
    </div>
  );
};

/* ── Card ─────────────────────────────────────────────── */
function Card({ title, subtitle, action, children }: {
  title: string; subtitle?: string;
  action?: { label: string; onClick: () => void };
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-4 flex flex-col card-glow h-full">
      <div className="flex items-start justify-between mb-3 flex-shrink-0">
        <div>
          <h3 className="text-sm font-bold text-slate-800">{title}</h3>
          {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
        </div>
        {action && (
          <button onClick={action.onClick}
                  className="flex items-center gap-1 text-xs font-semibold text-indigo-500 hover:text-indigo-700 transition-colors whitespace-nowrap ml-4">
            {action.label} <ArrowRight size={11}/>
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 flex flex-col justify-center">
        {children}
      </div>
    </div>
  );
}

/* ── Pie label ───────────────────────────────────────── */
interface PLProps { cx?: number; cy?: number; midAngle?: number; innerRadius?: number; outerRadius?: number; percent?: number; }
const R = Math.PI / 180;
function PieLabel({ cx=0,cy=0,midAngle=0,innerRadius=0,outerRadius=0,percent=0 }: PLProps) {
  if (percent < 0.07) return null;
  const r = innerRadius + (outerRadius - innerRadius) * 0.55;
  return (
    <text x={cx + r * Math.cos(-midAngle * R)} y={cy + r * Math.sin(-midAngle * R)}
          fill="white" textAnchor="middle" dominantBaseline="central" fontSize={11} fontWeight={700}>
      {`${(percent * 100).toFixed(0)}%`}
    </text>
  );
}

export function OverviewTab({ ads, onNav }: OverviewTabProps) {
  const [selectedAd, setSelectedAd] = useState<Ad | null>(null);

  const byDomain       = countByField(ads, 'Domain');
  const byFormat       = countByField(ads, 'Format');
  const activity       = getAdActivityByDate(ads);
  const ctaCounts      = getCTACounts(ads);
  const byDomainGroups = getAdsByDomain(ads);

  /* Chart data — store domain for click handling */
  const compData = COMPETITORS.map(c => ({
    name: c.name.replace(' Aesthetics', '\nAesth.'),
    shortName: c.name.split(' ')[0],
    domain: c.domain,
    ads: byDomain[c.domain] || 0,
    fill: c.color,
  }));

  const fmtData = Object.entries(byFormat).map(([n, v]) => ({
    name: n[0].toUpperCase() + n.slice(1),
    rawName: n.toLowerCase(),
    value: v,
    fill: FORMAT_COLORS[n.toLowerCase()] || '#94a3b8',
  }));

  const ctaData = ctaCounts.slice(0, 8).map(({ cta, count }) => ({ name: cta, count }));

  const recentAds = [...ads]
    .filter(a => a['Last Shown'])
    .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))
    .slice(0, 8);

  return (
    <div className="space-y-5">

      {/* Row 1 */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">

        {/* Competitor bar — click bar → Competitors tab */}
        <div className="anim-pop-in delay-1"><Card title="Ads by Competitor" subtitle="Click a bar to deep-dive"
              action={{ label: 'View all', onClick: () => onNav({ tab: 'competitors' }) }}>
          <ResponsiveContainer width="100%" height="100%" minHeight={180}>
            <BarChart data={compData} barSize={36} maxBarSize={44} barCategoryGap="30%" margin={{ top: 4, bottom: 0 }}
                      style={{ cursor: 'pointer' }}>
              <defs>
                {compData.map((c, i) => (
                  <linearGradient key={i} id={`cg${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%"   stopColor={c.fill} stopOpacity={1}   />
                    <stop offset="100%" stopColor={c.fill} stopOpacity={0.55}/>
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)" vertical={false}/>
              <XAxis dataKey="shortName" tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false}/>
              <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false}/>
              <Tooltip content={<DarkTip />} cursor={{ fill: 'rgba(99,102,241,0.06)' }}/>
              <Bar dataKey="ads" radius={[8,8,0,0]} name="Ads"
                   onClick={(data: unknown) => { const d = data as { domain: string }; onNav({ tab: 'competitors', competitor: d.domain }); }}>
                {compData.map((_, i) => <Cell key={i} fill={`url(#cg${i})`}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card></div>

        {/* Format pie — click slice → Gallery filtered */}
        <div className="anim-pop-in delay-2"><Card title="Format Distribution" subtitle="Click a slice to filter gallery"
              action={{ label: 'Gallery', onClick: () => onNav({ tab: 'gallery', format: 'all' }) }}>
          <ResponsiveContainer width="100%" height="100%" minHeight={180}>
            <PieChart style={{ cursor: 'pointer' }}>
              <defs>
                {fmtData.map((f, i) => (
                  <linearGradient key={i} id={`fg${i}`} x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%"   stopColor={f.fill} stopOpacity={1}  />
                    <stop offset="100%" stopColor={f.fill} stopOpacity={0.7}/>
                  </linearGradient>
                ))}
              </defs>
              <Pie data={fmtData} cx="50%" cy="42%" outerRadius={58} innerRadius={24}
                   dataKey="value" labelLine={false} label={PieLabel}
                   onClick={(data: unknown) => { const d = data as { rawName: string }; onNav({ tab: 'gallery', format: d.rawName, domain: 'all', search: '' }); }}>
                {fmtData.map((_, i) => <Cell key={i} fill={`url(#fg${i})`}/>)}
              </Pie>
              <Legend iconType="circle" iconSize={8}
                      formatter={v => <span style={{ fontSize: 11, color: '#94a3b8', cursor: 'pointer' }}>{v}</span>}/>
              <Tooltip content={<DarkTip/>}/>
            </PieChart>
          </ResponsiveContainer>
        </Card></div>

        {/* CTA bars — click → Gallery with search */}
        <div className="anim-pop-in delay-3"><Card title="Top CTAs" subtitle="Click a CTA to search gallery"
              action={{ label: 'Browse', onClick: () => onNav({ tab: 'gallery' }) }}>
          <div className="space-y-2 w-full">
            {ctaData.slice(0, 6).map(({ name, count }, i) => {
              const pct = (count / (ctaData[0]?.count || 1)) * 100;
              const cols = ['#6366f1','#8b5cf6','#0ea5e9','#10b981','#f59e0b','#ef4444','#ec4899','#14b8a6'];
              const c = cols[i % cols.length];
              return (
                <button key={i} className="w-full text-left group hover:opacity-80 transition-opacity"
                        onClick={() => onNav({ tab: 'gallery', search: name, domain: 'all', format: 'all' })}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-slate-600 truncate mr-2 font-medium group-hover:text-indigo-600 transition-colors">{name}</span>
                    <span className="font-bold text-slate-800 flex-shrink-0">{count}</span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full bar-fill"
                         style={{ width: `${pct}%`, background: c }}/>
                  </div>
                </button>
              );
            })}
          </div>
        </Card></div>
      </div>

      {/* Timeline */}
      {activity.length > 1 && (
        <Card title="Ad Activity Timeline" subtitle="When competitors were most active"
              action={{ label: 'View Gallery', onClick: () => onNav({ tab: 'gallery' }) }}>
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart data={activity} margin={{ top: 4 }}>
              <defs>
                {COMPETITORS.map(c => (
                  <linearGradient key={c.domain} id={`tg-${c.domain.split('.')[0]}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor={c.color} stopOpacity={0.3}/>
                    <stop offset="95%" stopColor={c.color} stopOpacity={0}/>
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.07)"/>
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false} tickFormatter={d => d.slice(5)}/>
              <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false} allowDecimals={false}/>
              <Tooltip content={<DarkTip/>}/>
              <Legend iconType="circle" iconSize={8}
                      onClick={(e: unknown) => { const d = e as { value?: string }; if (d.value) onNav({ tab: 'competitors', competitor: d.value }); }}
                      formatter={(v: string) => {
                        const c = COMPETITORS.find(x => x.domain === v);
                        return <span style={{ fontSize: 10, color: '#94a3b8', cursor: 'pointer' }}>{c?.name ?? v}</span>;
                      }}/>
              {COMPETITORS.map(c => (
                <Area key={c.domain} type="monotone" dataKey={c.domain} name={c.domain}
                      stroke={c.color} strokeWidth={2} fill={`url(#tg-${c.domain.split('.')[0]})`}
                      dot={{ r: 3, fill: c.color, strokeWidth: 0 }} connectNulls
                      onClick={() => onNav({ tab: 'gallery', domain: c.domain })}
                      style={{ cursor: 'pointer' }}/>
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Recent ads table — click row → modal */}
      <Card title="Most Recent Ads" subtitle="Click any row to open ad detail"
            action={{ label: 'View all in Gallery', onClick: () => onNav({ tab: 'gallery' }) }}>
        <div className="overflow-x-auto rounded-xl border border-slate-100">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-100">
                {['Competitor','Format','Headline','Last Shown'].map(h => (
                  <th key={h} className="text-left py-2.5 px-4 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recentAds.map((ad, i) => {
                const col = COMPETITOR_COLORS[ad.Domain] || '#64748b';
                return (
                  <tr key={i}
                      onClick={() => setSelectedAd(ad)}
                      className="border-b border-slate-50 last:border-0 hover:bg-indigo-50/50 cursor-pointer transition-colors group">
                    <td className="py-3 px-4">
                      <span className="text-xs font-bold px-2.5 py-1 rounded-full group-hover:opacity-90"
                            style={{ background: `${col}18`, color: col }}>
                        {ad.Domain.split('.')[0]}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <span className="text-xs capitalize text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full">{ad.Format}</span>
                    </td>
                    <td className="py-3 px-4 text-slate-700 max-w-xs">
                      <p className="truncate text-xs font-medium group-hover:text-indigo-700 transition-colors">
                        {ad.Headline || ad['Full Ad Text']?.slice(0, 60) || '—'}
                      </p>
                    </td>
                    <td className="py-3 px-4 text-slate-400 text-xs whitespace-nowrap">
                      {ad['Last Shown'] ? new Date(ad['Last Shown']).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Competitor snapshot cards — click → Competitors tab */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {COMPETITORS.map((comp, ci) => {
          const compAds   = byDomainGroups[comp.domain] || [];
          const fmts      = countByField(compAds, 'Format');
          const lastActive = compAds.filter(a => a['Last Shown']).sort((a,b) => b['Last Shown'].localeCompare(a['Last Shown']))[0]?.['Last Shown'];
          const statusAct  = compAds.filter(a => a.Status === 'active').length;
          return (
            <div key={comp.domain}
                 onClick={() => onNav({ tab: 'competitors', competitor: comp.domain })}
                 className={`anim-pop-in delay-${ci + 1} bg-white rounded-2xl border border-slate-100 shadow-sm p-4 card-lift cursor-pointer group active:scale-[0.98] transition-all`}>
              <div className="h-1 rounded-full mb-4 transition-all" style={{ background: `linear-gradient(90deg, ${comp.color}, ${comp.color}50)` }}/>
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-black text-base shadow-md"
                     style={{ background: `linear-gradient(135deg, ${comp.color}, ${comp.color}aa)` }}>
                  {comp.name[0]}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-bold text-slate-800 text-sm leading-none group-hover:text-indigo-700 transition-colors">{comp.name}</p>
                  <p className="text-xs text-slate-400 mt-0.5 truncate">{comp.domain}</p>
                </div>
                <ArrowRight size={15} className="text-white/20 group-hover:text-indigo-400 transition-colors flex-shrink-0"/>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center mb-3">
                {[['Total', compAds.length, '#f1f5f9'], ['Active', statusAct, comp.color], ['Formats', Object.keys(fmts).length, '#f1f5f9']].map(([l,v,col],i) => (
                  <div key={i} className="rounded-xl py-2.5" style={{ background: `${comp.color}0a` }}>
                    <p className="text-xl font-black" style={{ color: col as string }}>{v}</p>
                    <p className="text-[10px] text-slate-400">{l}</p>
                  </div>
                ))}
              </div>
              {lastActive && (
                <p className="text-center text-[10px] text-slate-400">
                  Last active: <strong className="text-slate-600">{new Date(lastActive).toLocaleDateString()}</strong>
                </p>
              )}
              {/* Format pills */}
              <div className="flex gap-1.5 flex-wrap mt-3 justify-center">
                {Object.entries(fmts).map(([fmt]) => (
                  <button key={fmt}
                          onClick={e => { e.stopPropagation(); onNav({ tab: 'gallery', domain: comp.domain, format: fmt }); }}
                          className="text-[10px] font-semibold px-2 py-0.5 rounded-full capitalize hover:opacity-80 transition-opacity"
                          style={{ background: `${comp.color}18`, color: comp.color }}>
                    {fmt}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {selectedAd && <AdModal ad={selectedAd} onClose={() => setSelectedAd(null)}/>}
    </div>
  );
}
