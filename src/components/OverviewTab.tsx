import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend, LineChart, Line
} from 'recharts';
import type { Ad } from '../lib/types';
import { COMPETITORS, COMPETITOR_COLORS, FORMAT_COLORS } from '../lib/types';
import {
  countByField, getAdActivityByDate, getCTACounts, getAdsByDomain
} from '../lib/utils';

interface OverviewTabProps {
  ads: Ad[];
}

const RADIAN = Math.PI / 180;
interface PieLabelProps {
  cx?: number; cy?: number; midAngle?: number;
  innerRadius?: number; outerRadius?: number; percent?: number;
}
function renderCustomizedLabel({ cx = 0, cy = 0, midAngle = 0, innerRadius = 0, outerRadius = 0, percent = 0 }: PieLabelProps) {
  if (percent < 0.06) return null;
  const radius = innerRadius + (outerRadius - innerRadius) * 0.55;
  const x = cx + radius * Math.cos(-midAngle * RADIAN);
  const y = cy + radius * Math.sin(-midAngle * RADIAN);
  return (
    <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central" fontSize={12} fontWeight={600}>
      {`${(percent * 100).toFixed(0)}%`}
    </text>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
      <h3 className="text-sm font-semibold text-slate-700 mb-4">{title}</h3>
      {children}
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-lg p-3 text-sm">
      <p className="font-semibold text-slate-700 mb-1">{label}</p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: <strong>{p.value}</strong>
        </p>
      ))}
    </div>
  );
};

export function OverviewTab({ ads }: OverviewTabProps) {
  const byDomain = countByField(ads, 'Domain');
  const byFormat = countByField(ads, 'Format');
  const activity = getAdActivityByDate(ads);
  const ctaCounts = getCTACounts(ads);
  const byDomainGroups = getAdsByDomain(ads);

  const competitorChartData = COMPETITORS.map(c => ({
    name: c.name,
    ads: byDomain[c.domain] || 0,
    color: c.color,
  }));

  const formatData = Object.entries(byFormat).map(([name, value]) => ({
    name: name.charAt(0).toUpperCase() + name.slice(1),
    value,
    fill: FORMAT_COLORS[name.toLowerCase()] || '#94a3b8',
  }));

  const ctaData = ctaCounts.slice(0, 8).map(({ cta, count }) => ({ name: cta, count }));

  // Recent activity table
  const recentAds = [...ads]
    .filter(a => a['Last Shown'])
    .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))
    .slice(0, 8);

  return (
    <div className="space-y-6">
      {/* Charts Row 1 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {/* Competitor bar chart */}
        <ChartCard title="Ads by Competitor">
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={competitorChartData} barSize={40}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="ads" radius={[6, 6, 0, 0]}>
                {competitorChartData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* Format pie chart */}
        <ChartCard title="Ad Format Distribution">
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie
                data={formatData}
                cx="50%"
                cy="50%"
                outerRadius={70}
                dataKey="value"
                labelLine={false}
                label={renderCustomizedLabel}
              >
                {formatData.map((entry, i) => (
                  <Cell key={i} fill={entry.fill} />
                ))}
              </Pie>
              <Legend
                formatter={(value) => <span className="text-xs text-slate-600">{value}</span>}
              />
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* CTA frequency */}
        <ChartCard title="Top CTAs">
          <div className="space-y-2 mt-1">
            {ctaData.map(({ name, count }, i) => {
              const max = ctaData[0]?.count || 1;
              return (
                <div key={i}>
                  <div className="flex justify-between text-xs mb-0.5">
                    <span className="text-slate-600 truncate mr-2">{name}</span>
                    <span className="font-semibold text-slate-700 flex-shrink-0">{count}</span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-indigo-400"
                      style={{ width: `${(count / max) * 100}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </ChartCard>
      </div>

      {/* Timeline */}
      {activity.length > 1 && (
        <ChartCard title="Ad Activity Timeline (Last Shown Date)">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={activity}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={d => d.slice(5)}
              />
              <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip content={<CustomTooltip />} />
              <Legend formatter={v => {
                const comp = COMPETITORS.find(c => c.domain === v);
                return <span className="text-xs">{comp?.name ?? v}</span>;
              }} />
              {COMPETITORS.map(c => (
                <Line
                  key={c.domain}
                  type="monotone"
                  dataKey={c.domain}
                  stroke={c.color}
                  strokeWidth={2}
                  dot={{ r: 3, fill: c.color }}
                  connectNulls
                  name={c.domain}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      )}

      {/* Recent Ads Table */}
      <ChartCard title="Most Recent Ads">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100">
                <th className="text-left py-2 px-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Competitor</th>
                <th className="text-left py-2 px-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Format</th>
                <th className="text-left py-2 px-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Headline</th>
                <th className="text-left py-2 px-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Last Shown</th>
              </tr>
            </thead>
            <tbody>
              {recentAds.map((ad, i) => {
                const color = COMPETITOR_COLORS[ad.Domain] || '#64748b';
                return (
                  <tr key={i} className="border-b border-slate-50 hover:bg-slate-50 transition-colors">
                    <td className="py-2 px-3">
                      <span
                        className="text-xs font-semibold px-2 py-0.5 rounded-full"
                        style={{ backgroundColor: `${color}15`, color }}
                      >
                        {ad.Domain.split('.')[0]}
                      </span>
                    </td>
                    <td className="py-2 px-3">
                      <span className="text-xs capitalize text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                        {ad.Format}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-slate-700 max-w-xs">
                      <p className="truncate">{ad.Headline || ad['Full Ad Text']?.slice(0, 60) || '—'}</p>
                    </td>
                    <td className="py-2 px-3 text-slate-500 text-xs whitespace-nowrap">
                      {ad['Last Shown'] ? new Date(ad['Last Shown']).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </ChartCard>

      {/* Competitor at-a-glance */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {COMPETITORS.map(comp => {
          const compAds = byDomainGroups[comp.domain] || [];
          const formats = countByField(compAds, 'Format');
          const statusActive = compAds.filter(a => a.Status === 'active').length;
          const lastActive = compAds
            .filter(a => a['Last Shown'])
            .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))[0]?.['Last Shown'];

          return (
            <div key={comp.domain} className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
              <div className="flex items-center gap-3 mb-3">
                <div
                  className="w-9 h-9 rounded-full flex items-center justify-center text-white font-bold text-sm flex-shrink-0"
                  style={{ backgroundColor: comp.color }}
                >
                  {comp.name[0]}
                </div>
                <div>
                  <p className="font-semibold text-slate-800 text-sm">{comp.name}</p>
                  <p className="text-xs text-slate-400">{comp.domain}</p>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="bg-slate-50 rounded-lg p-2">
                  <p className="text-lg font-bold text-slate-800">{compAds.length}</p>
                  <p className="text-xs text-slate-400">Total Ads</p>
                </div>
                <div className="bg-slate-50 rounded-lg p-2">
                  <p className="text-lg font-bold" style={{ color: comp.color }}>{statusActive}</p>
                  <p className="text-xs text-slate-400">Active</p>
                </div>
                <div className="bg-slate-50 rounded-lg p-2">
                  <p className="text-lg font-bold text-slate-800">{Object.keys(formats).length}</p>
                  <p className="text-xs text-slate-400">Formats</p>
                </div>
              </div>
              {lastActive && (
                <p className="text-xs text-slate-400 mt-3 text-center">
                  Last active: {new Date(lastActive).toLocaleDateString()}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
