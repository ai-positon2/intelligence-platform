import { useMemo } from 'react';
import {
  Brain, Target, Zap, AlertTriangle, TrendingUp,
  Hash, MessageSquare, Activity, ChevronRight,
  Lightbulb, Eye,
} from 'lucide-react';
import type { Ad, NavFn } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { getTopKeywords, getCTACounts, countByField } from '../lib/utils';

interface Props { ads: Ad[]; onNav: NavFn; }

/* ── Format bar ─────────────────────────────────────── */
const FORMAT_COLORS: Record<string, string> = {
  image: '#10b981', text: '#6366f1', video: '#f59e0b',
};
function FormatBar({ formats, total }: { formats: Record<string, number>; total: number }) {
  if (total === 0) return <div className="w-full h-1.5 rounded-full bg-white/6" />;
  return (
    <div className="w-full h-1.5 rounded-full overflow-hidden flex gap-px">
      {Object.entries(formats).filter(([, v]) => v > 0).map(([fmt, count]) => (
        <div key={fmt} className="h-full bar-fill"
          style={{ width: `${(count / total) * 100}%`, backgroundColor: FORMAT_COLORS[fmt] ?? '#475569', borderRadius: 4 }} />
      ))}
    </div>
  );
}

/* ── Signal type config ──────────────────────────────── */
const SIG_CFG: Record<string, { label: string; icon: React.ReactNode }> = {
  opportunity: { label: 'OPPORTUNITY', icon: <Lightbulb size={12} /> },
  trend:       { label: 'TREND',       icon: <TrendingUp size={12} /> },
  alert:       { label: 'ALERT',       icon: <AlertTriangle size={12} /> },
  watch:       { label: 'WATCH',       icon: <Eye size={12} /> },
  info:        { label: 'INFO',        icon: <Activity size={12} /> },
};

/* ── Main component ──────────────────────────────────── */
export function InsightsTab({ ads, onNav }: Props) {
  const data = useMemo(() => {
    const compStats = COMPETITORS.map(c => {
      const ca = ads.filter(a => a.Domain === c.domain);
      const activeCount = ca.filter(a => a.Status === 'active').length;
      const formats = countByField(ca, 'Format');
      const topKeywords = getTopKeywords(ca, 5);
      const topCtas = getCTACounts(ca).slice(0, 3);
      const latestDate = ca
        .filter(a => a['Last Shown'])
        .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))[0]?.['Last Shown'];
      const dominantFormat = Object.entries(formats).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'text';
      return {
        ...c,
        total: ca.length,
        activeCount,
        activeRate: ca.length > 0 ? Math.round((activeCount / ca.length) * 100) : 0,
        formats,
        topKeywords,
        topCtas,
        latestDate,
        dominantFormat,
      };
    });

    const allTopCtas = getCTACounts(ads);
    const allTopKeywords = getTopKeywords(ads, 14);

    const angleCounts: Record<string, number> = {};
    ads.forEach(ad => {
      if (!ad['Messaging Angle']) return;
      ad['Messaging Angle'].split(';').forEach(s => {
        const clean = s.trim().toLowerCase();
        if (clean.length > 5) angleCounts[clean] = (angleCounts[clean] || 0) + 1;
      });
    });
    const topAngles = Object.entries(angleCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([a, count]) => ({ angle: a.charAt(0).toUpperCase() + a.slice(1), count }));

    const signals: { type: string; title: string; detail: string; color: string }[] = [];

    const textOnly = compStats.find(c => c.formats['text'] === c.total && c.total > 0);
    if (textOnly) signals.push({
      type: 'opportunity',
      title: `${textOnly.name} — text-only ads`,
      detail: 'Zero image or video creative. Visual formats are completely unchallenged by them.',
      color: '#10b981',
    });

    const videoLeader = [...compStats]
      .filter(c => (c.formats['video'] || 0) > 0)
      .sort((a, b) => (b.formats['video'] || 0) - (a.formats['video'] || 0))[0];
    if (videoLeader) signals.push({
      type: 'trend',
      title: `${videoLeader.name} leads in video`,
      detail: `${videoLeader.formats['video']} video ads — heaviest video investment in the market.`,
      color: '#f59e0b',
    });

    const paused = compStats.find(c => c.activeRate === 0 && c.total > 0);
    if (paused) signals.push({
      type: 'alert',
      title: `${paused.name} — all ads inactive`,
      detail: 'May signal a budget pause or full creative refresh. Good time to capture their traffic.',
      color: '#f43f5e',
    });

    compStats.filter(c => c.activeRate === 100 && c.total > 0).forEach(c => signals.push({
      type: 'watch',
      title: `${c.name} — maximum intensity`,
      detail: `All ${c.total} tracked ads are live simultaneously. High market aggression right now.`,
      color: '#6366f1',
    }));

    const mostRecent = [...compStats]
      .filter(c => c.latestDate)
      .sort((a, b) => (b.latestDate || '').localeCompare(a.latestDate || ''))[0];
    if (mostRecent?.latestDate) signals.push({
      type: 'info',
      title: `${mostRecent.name} — most recent activity`,
      detail: `Last ad seen ${new Date(mostRecent.latestDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}.`,
      color: '#0ea5e9',
    });

    return { compStats, allTopCtas, allTopKeywords, topAngles, signals };
  }, [ads]);

  const totalActive = ads.filter(a => a.Status === 'active').length;
  const formatCounts = countByField(ads, 'Format');
  const dominantFormat = Object.entries(formatCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'text';

  return (
    <div className="space-y-5">

      {/* ── Hero Banner ─────────────────────────────────── */}
      <div className="relative overflow-hidden rounded-2xl border border-white/8 p-6 anim-fade-up"
           style={{ background: 'linear-gradient(135deg,#0d1124 0%,#1a1040 50%,#0d1124 100%)' }}>
        <div style={{ position:'absolute', top:'-60px', right:'-60px', width:'240px', height:'240px',
                      background:'radial-gradient(circle,rgba(99,102,241,0.38),transparent 68%)',
                      borderRadius:'50%', pointerEvents:'none' }} />
        <div style={{ position:'absolute', bottom:'-50px', left:'25%', width:'200px', height:'200px',
                      background:'radial-gradient(circle,rgba(139,92,246,0.22),transparent 70%)',
                      borderRadius:'50%', pointerEvents:'none' }} />

        <div style={{ position:'relative', zIndex:1 }}>
          <div className="flex items-center gap-2 mb-3">
            <div className="flex items-center gap-1.5 bg-indigo-500/18 border border-indigo-500/30 rounded-full px-3 py-1">
              <Brain size={12} className="text-indigo-400" />
              <span className="text-indigo-300 text-[11px] font-bold tracking-widest uppercase">Market Brief</span>
            </div>
            <span className="text-white/25 text-xs hidden sm:block">
              · {COMPETITORS.length} competitors · {ads.length} ads tracked
            </span>
          </div>

          <h2 className="text-2xl font-black text-white mb-1 leading-tight" style={{ letterSpacing:'-0.03em' }}>
            Competitive Intelligence{' '}
            <span style={{ background:'linear-gradient(135deg,#818cf8,#c084fc)', WebkitBackgroundClip:'text',
                           WebkitTextFillColor:'transparent', backgroundClip:'text' }}>Overview</span>
          </h2>
          <p className="text-white/40 text-sm mb-5">
            Real-time analysis of ad strategies, formats, and messaging across the market.
          </p>

          <div className="flex flex-wrap gap-2.5">
            {[
              { label:'Total Ads',       val: ads.length,          color:'#818cf8' },
              { label:'Active Now',      val: totalActive,          color:'#10b981' },
              { label:'Brands Tracked',  val: COMPETITORS.length,   color:'#f59e0b' },
              { label:`${dominantFormat[0].toUpperCase()+dominantFormat.slice(1)}-led`, val: formatCounts[dominantFormat]||0, color:'#0ea5e9' },
            ].map(s => (
              <div key={s.label}
                   className="flex items-center gap-2 bg-white/5 border border-white/8 rounded-xl px-3 py-2">
                <span className="text-base font-black" style={{ color:s.color }}>{s.val}</span>
                <span className="text-white/40 text-xs">{s.label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Competitor Scorecards ─────────────────────── */}
      <div>
        <h3 className="text-[11px] font-bold text-white/25 uppercase tracking-widest mb-3 px-0.5">
          Competitor Scorecards
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {data.compStats.map((c, i) => (
            <div key={c.domain}
                 className="relative overflow-hidden rounded-2xl border card-glow anim-fade-up cursor-pointer group"
                 style={{
                   background: `linear-gradient(135deg,${c.color}10 0%,#0e1526 65%)`,
                   borderColor: `${c.color}20`,
                   animationDelay: `${i * 0.08}s`,
                 }}
                 onClick={() => onNav({ tab:'competitors', competitor:c.domain })}>

              <div style={{ position:'absolute', top:'-30px', right:'-30px', width:'120px', height:'120px',
                            background:`radial-gradient(circle,${c.color}30,transparent 70%)`,
                            borderRadius:'50%', pointerEvents:'none' }} />

              {/* Header */}
              <div className="p-5 pb-3">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <div className="w-7 h-7 rounded-lg flex items-center justify-center text-white text-[11px] font-black flex-shrink-0"
                         style={{ backgroundColor:c.color }}>{c.name[0]}</div>
                    <div>
                      <p className="text-white font-bold text-sm leading-none">{c.name}</p>
                      <p className="text-white/30 text-[10px] mt-0.5">{c.domain}</p>
                    </div>
                  </div>
                  <ChevronRight size={13} className="text-white/15 group-hover:text-white/45 transition-colors mt-0.5 flex-shrink-0" />
                </div>

                {/* Metrics row */}
                <div className="grid grid-cols-3 gap-1.5 mb-3">
                  {([
                    { label:'Total',  val: String(c.total),          hi: false },
                    { label:'Active', val: String(c.activeCount),    hi: true  },
                    { label:'Rate',   val: `${c.activeRate}%`,       hi: false },
                  ] as { label:string; val:string; hi:boolean }[]).map(m => (
                    <div key={m.label}
                         className="text-center bg-white/4 rounded-xl py-2 border border-white/5">
                      <p className="font-black text-lg leading-none"
                         style={{ color: m.hi ? (c.activeCount > 0 ? '#34d399' : '#fb7185') : '#f1f5f9' }}>
                        {m.val}
                      </p>
                      <p className="text-white/30 text-[10px] mt-0.5">{m.label}</p>
                    </div>
                  ))}
                </div>

                {/* Format mix */}
                <div className="mb-1">
                  <div className="flex justify-between mb-1.5">
                    <span className="text-white/30 text-[11px]">Format mix</span>
                    <span className="text-white/45 text-[11px] capitalize">{c.dominantFormat}-led</span>
                  </div>
                  <FormatBar formats={c.formats} total={c.total} />
                  <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 mt-1.5">
                    {Object.entries(c.formats).map(([fmt, n]) => (
                      <span key={fmt} className="text-[10px] text-white/35 capitalize">
                        <span style={{ color: FORMAT_COLORS[fmt] ?? '#64748b' }}>●</span> {fmt} {n}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* Footer */}
              {c.latestDate && (
                <div className="flex items-center gap-2 px-5 py-2.5 border-t border-white/5">
                  <Activity size={10} className="text-white/25" />
                  <span className="text-white/30 text-[11px]">
                    Last seen {new Date(c.latestDate).toLocaleDateString('en-US',{month:'short',day:'numeric'})}
                  </span>
                  <span className="ml-auto text-[10px] font-bold px-2 py-0.5 rounded-full"
                        style={{
                          color:   c.activeRate === 100 ? '#34d399' : c.activeRate === 0 ? '#fb7185' : '#fbbf24',
                          background: c.activeRate === 100 ? 'rgba(52,211,153,0.12)' : c.activeRate === 0 ? 'rgba(251,113,133,0.12)' : 'rgba(251,191,36,0.12)',
                        }}>
                    {c.activeRate === 100 ? 'LIVE' : c.activeRate === 0 ? 'PAUSED' : 'PARTIAL'}
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── Intelligence Grid ─────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        {/* CTA Arsenal */}
        {data.allTopCtas.length > 0 && (
          <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-3"
               style={{ background:'rgba(13,18,38,0.75)' }}>
            <div className="flex items-center gap-2.5 mb-4">
              <div className="w-8 h-8 rounded-xl bg-violet-500/18 border border-violet-500/22 flex items-center justify-center flex-shrink-0">
                <Target size={15} className="text-violet-400" />
              </div>
              <div>
                <h3 className="text-white font-bold text-sm leading-none">CTA Arsenal</h3>
                <p className="text-white/30 text-[11px] mt-0.5">Most-used calls-to-action across all ads</p>
              </div>
            </div>
            <div className="space-y-3">
              {data.allTopCtas.slice(0, 7).map((item, i) => {
                const max = data.allTopCtas[0].count;
                return (
                  <div key={item.cta} className="group">
                    <div className="flex justify-between mb-1">
                      <span className="text-white/70 text-xs font-medium truncate flex-1 mr-3 group-hover:text-white/90 transition-colors">
                        {item.cta}
                      </span>
                      <span className="text-white/35 text-[11px] flex-shrink-0">{item.count}×</span>
                    </div>
                    <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                      <div className="h-full rounded-full bar-fill"
                           style={{
                             width:`${(item.count / max) * 100}%`,
                             background:'linear-gradient(90deg,#818cf8,#c084fc)',
                             animationDelay:`${i * 0.05}s`,
                           }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Keyword Battlefield */}
        {data.allTopKeywords.length > 0 && (
          <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-4"
               style={{ background:'rgba(13,18,38,0.75)' }}>
            <div className="flex items-center gap-2.5 mb-4">
              <div className="w-8 h-8 rounded-xl bg-sky-500/18 border border-sky-500/22 flex items-center justify-center flex-shrink-0">
                <Hash size={15} className="text-sky-400" />
              </div>
              <div>
                <h3 className="text-white font-bold text-sm leading-none">Keyword Battlefield</h3>
                <p className="text-white/30 text-[11px] mt-0.5">Top terms competitors actively target</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {data.allTopKeywords.map((kw, i) => {
                const intensity = kw.count / data.allTopKeywords[0].count;
                return (
                  <span key={kw.keyword}
                        className="px-3 py-1.5 rounded-full text-xs font-medium border transition-all duration-200 hover:scale-105 cursor-default"
                        style={{
                          background:`rgba(14,165,233,${0.05 + intensity * 0.18})`,
                          borderColor:`rgba(14,165,233,${0.1 + intensity * 0.3})`,
                          color:`rgba(186,230,253,${0.5 + intensity * 0.45})`,
                          animationDelay:`${0.3 + i * 0.03}s`,
                        }}>
                    {kw.keyword}
                    <span className="ml-1.5 opacity-40 text-[10px]">{kw.count}</span>
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* Messaging Themes */}
        {data.topAngles.length > 0 && (
          <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-5"
               style={{ background:'rgba(13,18,38,0.75)' }}>
            <div className="flex items-center gap-2.5 mb-4">
              <div className="w-8 h-8 rounded-xl bg-emerald-500/18 border border-emerald-500/22 flex items-center justify-center flex-shrink-0">
                <MessageSquare size={15} className="text-emerald-400" />
              </div>
              <div>
                <h3 className="text-white font-bold text-sm leading-none">Messaging Themes</h3>
                <p className="text-white/30 text-[11px] mt-0.5">Recurring angles across competitor ads</p>
              </div>
            </div>
            <div className="space-y-2.5">
              {data.topAngles.map((item, i) => {
                const max = data.topAngles[0].count;
                return (
                  <div key={item.angle} className="flex items-center gap-3 group">
                    <span className="text-white/20 text-[10px] font-mono w-4 flex-shrink-0 text-right">{i + 1}</span>
                    <span className="text-white/65 text-xs flex-1 truncate group-hover:text-white/85 transition-colors capitalize">
                      {item.angle}
                    </span>
                    <div className="w-16 h-1 bg-white/5 rounded-full overflow-hidden flex-shrink-0">
                      <div className="h-full rounded-full bar-fill"
                           style={{ width:`${(item.count/max)*100}%`,
                                    background:'linear-gradient(90deg,#34d399,#10b981)',
                                    animationDelay:`${0.4 + i * 0.04}s` }} />
                    </div>
                    <span className="text-white/25 text-[10px] w-3 text-right flex-shrink-0">{item.count}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Strategic Signals */}
        <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-6"
             style={{ background:'rgba(13,18,38,0.75)' }}>
          <div className="flex items-center gap-2.5 mb-4">
            <div className="w-8 h-8 rounded-xl bg-amber-500/18 border border-amber-500/22 flex items-center justify-center flex-shrink-0">
              <Zap size={15} className="text-amber-400" />
            </div>
            <div>
              <h3 className="text-white font-bold text-sm leading-none">Strategic Signals</h3>
              <p className="text-white/30 text-[11px] mt-0.5">Actionable observations from the data</p>
            </div>
          </div>
          <div className="space-y-2.5">
            {data.signals.map((sig, i) => {
              const cfg = SIG_CFG[sig.type] ?? SIG_CFG.info;
              return (
                <div key={i}
                     className="rounded-xl p-3.5 border border-white/5 anim-fade-up"
                     style={{
                       background:`${sig.color}08`,
                       borderLeftColor:sig.color,
                       borderLeftWidth:'2px',
                       animationDelay:`${0.35 + i * 0.06}s`,
                     }}>
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <span style={{ color:sig.color }}>{cfg.icon}</span>
                    <span className="text-[10px] font-bold tracking-widest uppercase"
                          style={{ color:sig.color }}>{cfg.label}</span>
                  </div>
                  <p className="text-white/85 text-xs font-semibold leading-snug mb-1">{sig.title}</p>
                  <p className="text-white/40 text-[11px] leading-relaxed">{sig.detail}</p>
                </div>
              );
            })}
          </div>
        </div>

      </div>
    </div>
  );
}
