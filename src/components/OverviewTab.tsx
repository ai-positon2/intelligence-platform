import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend, AreaChart, Area
} from 'recharts';
import type { Ad } from '../lib/types';
import { COMPETITORS, COMPETITOR_COLORS, FORMAT_COLORS } from '../lib/types';
import { countByField, getAdActivityByDate, getCTACounts, getAdsByDomain } from '../lib/utils';

interface OverviewTabProps { ads: Ad[]; }

/* ── Tooltip ──────────────────────────────────────────── */
const DarkTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-900 border border-white/10 rounded-xl shadow-2xl p-3 text-sm">
      {label && <p className="font-semibold text-white/80 mb-1.5 text-xs">{label}</p>}
      {payload.map(p => (
        <p key={p.name} className="flex items-center gap-2 text-xs">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: p.color }} />
          <span className="text-white/60">{p.name}:</span>
          <span className="font-bold text-white">{p.value}</span>
        </p>
      ))}
    </div>
  );
};

/* ── Card wrapper ─────────────────────────────────────── */
function Card({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
      <div className="mb-4">
        <h3 className="text-sm font-bold text-slate-800">{title}</h3>
        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

/* ── Pie label ───────────────────────────────────────── */
interface PLProps { cx?: number; cy?: number; midAngle?: number; innerRadius?: number; outerRadius?: number; percent?: number; }
const RADIAN = Math.PI / 180;
function PieLabel({ cx = 0, cy = 0, midAngle = 0, innerRadius = 0, outerRadius = 0, percent = 0 }: PLProps) {
  if (percent < 0.07) return null;
  const r = innerRadius + (outerRadius - innerRadius) * 0.55;
  return (
    <text x={cx + r * Math.cos(-midAngle * RADIAN)} y={cy + r * Math.sin(-midAngle * RADIAN)}
          fill="white" textAnchor="middle" dominantBaseline="central" fontSize={11} fontWeight={700}>
      {`${(percent * 100).toFixed(0)}%`}
    </text>
  );
}

export function OverviewTab({ ads }: OverviewTabProps) {
  const byDomain   = countByField(ads, 'Domain');
  const byFormat   = countByField(ads, 'Format');
  const activity   = getAdActivityByDate(ads);
  const ctaCounts  = getCTACounts(ads);
  const byDomainGroups = getAdsByDomain(ads);

  const compData = COMPETITORS.map(c => ({ name: c.name.replace('Aesthetics', 'Aesth.'), ads: byDomain[c.domain] || 0, fill: c.color }));
  const fmtData  = Object.entries(byFormat).map(([n, v]) => ({ name: n[0].toUpperCase() + n.slice(1), value: v, fill: FORMAT_COLORS[n.toLowerCase()] || '#94a3b8' }));
  const ctaData  = ctaCounts.slice(0, 8).map(({ cta, count }) => ({ name: cta, count }));

  const recentAds = [...ads]
    .filter(a => a['Last Shown'])
    .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))
    .slice(0, 8);

  return (
    <div className="space-y-5">

      {/* Row 1: bar + pie + cta */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">

        {/* Competitor bar */}
        <Card title="Ads by Competitor" subtitle="Total creatives tracked">
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={compData} barSize={36} margin={{ top: 4 }}>
              <defs>
                {compData.map((c, i) => (
                  <linearGradient key={i} id={`cg${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={c.fill} stopOpacity={1} />
                    <stop offset="100%" stopColor={c.fill} stopOpacity={0.55} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
              <Tooltip content={<DarkTooltip />} cursor={{ fill: 'rgba(99,102,241,0.04)' }} />
              <Bar dataKey="ads" radius={[8, 8, 0, 0]} name="Ads">
                {compData.map((_, i) => <Cell key={i} fill={`url(#cg${i})`} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        {/* Format pie */}
        <Card title="Format Distribution" subtitle="Image · Text · Video mix">
          <ResponsiveContainer width="100%" height={190}>
            <PieChart>
              <defs>
                {fmtData.map((f, i) => (
                  <linearGradient key={i} id={`fg${i}`} x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor={f.fill} stopOpacity={1} />
                    <stop offset="100%" stopColor={f.fill} stopOpacity={0.7} />
                  </linearGradient>
                ))}
              </defs>
              <Pie data={fmtData} cx="50%" cy="45%" outerRadius={68} innerRadius={28}
                   dataKey="value" labelLine={false} label={PieLabel}>
                {fmtData.map((_, i) => <Cell key={i} fill={`url(#fg${i})`} />)}
              </Pie>
              <Legend iconType="circle" iconSize={8}
                      formatter={v => <span style={{ fontSize: 11, color: '#64748b' }}>{v}</span>} />
              <Tooltip content={<DarkTooltip />} />
            </PieChart>
          </ResponsiveContainer>
        </Card>

        {/* CTA bars */}
        <Card title="Top CTAs" subtitle="Most used calls-to-action">
          <div className="space-y-3 mt-1">
            {ctaData.map(({ name, count }, i) => {
              const pct = (count / (ctaData[0]?.count || 1)) * 100;
              const colors = ['#6366f1','#8b5cf6','#0ea5e9','#10b981','#f59e0b','#ef4444','#ec4899','#14b8a6'];
              const c = colors[i % colors.length];
              return (
                <div key={i}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-slate-600 truncate mr-2 font-medium">{name}</span>
                    <span className="font-bold text-slate-800 flex-shrink-0">{count}</span>
                  </div>
                  <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full transition-all duration-700"
                         style={{ width: `${pct}%`, background: c }} />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      {/* Timeline */}
      {activity.length > 1 && (
        <Card title="Ad Activity Timeline" subtitle="Number of ads active by date (Last Shown)">
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={activity} margin={{ top: 4 }}>
              <defs>
                {COMPETITORS.map(c => (
                  <linearGradient key={c.domain} id={`tg-${c.domain.split('.')[0]}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor={c.color} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={c.color} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false}
                     tickFormatter={d => d.slice(5)} />
              <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip content={<DarkTooltip />} />
              <Legend iconType="circle" iconSize={8}
                      formatter={(v) => {
                        const c = COMPETITORS.find(x => x.domain === v);
                        return <span style={{ fontSize: 10, color: '#64748b' }}>{c?.name ?? v}</span>;
                      }} />
              {COMPETITORS.map(c => (
                <Area key={c.domain} type="monotone" dataKey={c.domain} name={c.domain}
                      stroke={c.color} strokeWidth={2} fill={`url(#tg-${c.domain.split('.')[0]})`}
                      dot={{ r: 3, fill: c.color, strokeWidth: 0 }} connectNulls />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Recent ads table */}
      <Card title="Most Recent Ads" subtitle="Sorted by last shown date">
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
                  <tr key={i} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/80 transition-colors">
                    <td className="py-3 px-4">
                      <span className="text-xs font-bold px-2.5 py-1 rounded-full"
                            style={{ background: `${col}18`, color: col }}>
                        {ad.Domain.split('.')[0]}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <span className="text-xs capitalize text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full">
                        {ad.Format}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-slate-700 max-w-xs">
                      <p className="truncate text-xs font-medium">
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

      {/* Competitor snapshot */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {COMPETITORS.map(comp => {
          const compAds   = byDomainGroups[comp.domain] || [];
          const fmts      = countByField(compAds, 'Format');
          const lastActive = compAds.filter(a => a['Last Shown']).sort((a,b) => b['Last Shown'].localeCompare(a['Last Shown']))[0]?.['Last Shown'];
          const statusActive = compAds.filter(a => a.Status === 'active').length;
          return (
            <div key={comp.domain} className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5 card-lift">
              {/* Header bar */}
              <div className="h-1 rounded-full mb-4" style={{ background: `linear-gradient(90deg, ${comp.color}, ${comp.color}50)` }} />
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-black text-base shadow-md"
                     style={{ background: `linear-gradient(135deg, ${comp.color}, ${comp.color}aa)` }}>
                  {comp.name[0]}
                </div>
                <div>
                  <p className="font-bold text-slate-800 text-sm leading-none">{comp.name}</p>
                  <p className="text-xs text-slate-400 mt-0.5">{comp.domain}</p>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center mb-3">
                {[['Total',compAds.length,'text-slate-800'],['Active',statusActive,`font-bold`],['Formats',Object.keys(fmts).length,'text-slate-800']].map(([label,val],i) => (
                  <div key={i} className="bg-slate-50 rounded-xl py-2.5">
                    <p className="text-xl font-black text-slate-800" style={i===1 ? { color: comp.color } : {}}>{val}</p>
                    <p className="text-[10px] text-slate-400">{label}</p>
                  </div>
                ))}
              </div>
              {lastActive && (
                <p className="text-center text-[10px] text-slate-400">
                  Last active: <strong className="text-slate-600">{new Date(lastActive).toLocaleDateString()}</strong>
                </p>
              )}
            </div>
          );
        })}
      </div>

    </div>
  );
}
