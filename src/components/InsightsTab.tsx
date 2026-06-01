import { useMemo } from 'react';
import {
  Brain, Target, Zap, AlertTriangle, TrendingUp,
  Hash, MessageSquare, Activity, ChevronRight,
  Lightbulb, Eye, BarChart2, Flame,
} from 'lucide-react';
import type { Ad, NavFn } from '../lib/types';
import { COMPETITORS } from '../lib/types';
import { getTopKeywords, getCTACounts, countByField } from '../lib/utils';

interface Props { ads: Ad[]; onNav: NavFn; }

/* ── Stop-word list for headline vocabulary mining ─────── */
const STOP = new Set([
  'the','a','an','and','or','but','in','on','at','to','for','of','with','by','from',
  'as','is','are','was','were','be','been','being','have','has','had','do','does',
  'did','will','would','could','should','may','might','can','not','no','its','our',
  'your','their','this','that','these','those','we','you','i','my','me','us','get',
  'now','all','any','more','also','just','new','top','best','out','up','it','if',
  'how','what','when','who','into','over','after','before','about','than','then',
  'them','view','page','site','click','here','see','find','amp','dr','md','m','d',
]);

/* ── Extract vocabulary from headline + description ────── */
function extractVocabulary(
  ads: Ad[],
  limit = 20,
): { word: string; count: number }[] {
  const counts: Record<string, number> = {};
  ads.forEach(ad => {
    const text = [ad.Headline, ad.Description].filter(Boolean).join(' ');
    text.toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/)
      .filter(w => w.length > 3 && !STOP.has(w))
      .forEach(w => { counts[w] = (counts[w] || 0) + 1; });
  });
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([word, count]) => ({ word, count }));
}

/* ── Compute competitive intensity score (0-100) ──────── */
function intensityScore(
  compAds: Ad[],
  recentMonth: string, // YYYY-MM
): number {
  if (compAds.length === 0) return 0;
  const active = compAds.filter(a => a.Status === 'active').length;
  const activeRate = (active / compAds.length) * 100;
  const recent = compAds.filter(a => a['Last Shown']?.startsWith(recentMonth)).length;
  const recentRate = (recent / compAds.length) * 100;
  const fmts = new Set(compAds.map(a => a.Format).filter(Boolean)).size;
  const fmtScore = Math.min(fmts / 3, 1) * 100;
  return Math.round(activeRate * 0.5 + recentRate * 0.3 + fmtScore * 0.2);
}

/* ── Activity heatmap (12 months) ─────────────────────── */
const FORMAT_COLORS: Record<string, string> = {
  image: '#10b981', text: '#6366f1', video: '#f59e0b',
};

function ActivityHeatmap({ ads }: { ads: Ad[] }) {
  const months = useMemo(() => {
    const ref = new Date('2026-06-01');
    return Array.from({ length: 12 }, (_, i) => {
      const d = new Date(ref.getFullYear(), ref.getMonth() - 11 + i, 1);
      return d.toISOString().slice(0, 7);
    });
  }, []);

  const byMonth = useMemo(() => {
    const map: Record<string, Record<string, number>> = {};
    ads.forEach(ad => {
      const m = ad['Last Shown']?.slice(0, 7);
      if (!m) return;
      if (!map[m]) map[m] = {};
      map[m][ad.Domain] = (map[m][ad.Domain] || 0) + 1;
    });
    return map;
  }, [ads]);

  const compMaxes = useMemo(() =>
    COMPETITORS.map(c => ({
      ...c,
      max: Math.max(...months.map(m => byMonth[m]?.[c.domain] ?? 0), 1),
      total: months.reduce((s, m) => s + (byMonth[m]?.[c.domain] ?? 0), 0),
    })),
  [byMonth, months]);

  return (
    <div className="space-y-2.5">
      {compMaxes.map(c => (
        <div key={c.domain} className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: c.color }} />
          <span className="text-white/40 text-[10px] w-20 flex-shrink-0 truncate">{c.name.split(' ')[0]}</span>
          <div className="flex gap-1 flex-1">
            {months.map(m => {
              const count = byMonth[m]?.[c.domain] ?? 0;
              const opacity = count === 0 ? 0.07 : 0.2 + (count / c.max) * 0.8;
              return (
                <div key={m} title={`${m}: ${count} ads`}
                     className="flex-1 rounded-sm cursor-default transition-opacity hover:opacity-100"
                     style={{ height: 16, backgroundColor: c.color, opacity }} />
              );
            })}
          </div>
          <span className="text-white/30 text-[10px] w-6 text-right flex-shrink-0">{c.total}</span>
        </div>
      ))}
      <div className="flex gap-1 pl-[104px]">
        {months.map(m => (
          <div key={m} className="flex-1 text-center text-[9px] text-white/18 truncate">
            {new Date(m + '-15').toLocaleDateString('en-US', { month: 'short' }).toUpperCase()}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Format bar ─────────────────────────────────────────── */
function FormatBar({ formats, total }: { formats: Record<string, number>; total: number }) {
  if (total === 0) return <div className="w-full h-1.5 rounded-full bg-white/6" />;
  return (
    <div className="w-full h-1.5 rounded-full overflow-hidden flex gap-px">
      {Object.entries(formats).filter(([, v]) => v > 0).map(([fmt, count]) => (
        <div key={fmt} className="h-full bar-fill" style={{
          width: `${(count / total) * 100}%`,
          backgroundColor: FORMAT_COLORS[fmt] ?? '#475569',
          borderRadius: 4,
        }} />
      ))}
    </div>
  );
}

/* ── Intensity bar ──────────────────────────────────────── */
function IntensityBar({ score, color }: { score: number; color: string }) {
  return (
    <div className="w-full h-1 bg-white/5 rounded-full overflow-hidden">
      <div className="h-full rounded-full bar-fill"
           style={{ width: `${score}%`, background: `linear-gradient(90deg, ${color}99, ${color})` }} />
    </div>
  );
}

/* ── Signal type config ─────────────────────────────────── */
const SIG_CFG: Record<string, { label: string; icon: React.ReactNode }> = {
  opportunity: { label: 'OPPORTUNITY', icon: <Lightbulb size={12} /> },
  trend:       { label: 'TREND',       icon: <TrendingUp size={12} /> },
  alert:       { label: 'ALERT',       icon: <AlertTriangle size={12} /> },
  watch:       { label: 'WATCH',       icon: <Eye size={12} /> },
  hot:         { label: 'HOT',         icon: <Flame size={12} /> },
  info:        { label: 'INFO',        icon: <Activity size={12} /> },
};

/* ══════════════════════════════════════════════════════════
   InsightsTab — fully dynamic from ads data
   ══════════════════════════════════════════════════════════ */
export function InsightsTab({ ads, onNav }: Props) {

  /* ── Determine the most recent month in the dataset ─── */
  const recentMonth = useMemo(() => {
    const dates = ads.map(a => a['Last Shown']?.slice(0, 7)).filter(Boolean) as string[];
    return dates.sort((a, b) => b.localeCompare(a))[0] ?? new Date().toISOString().slice(0, 7);
  }, [ads]);

  /* ── Market-wide numbers ─────────────────────────────── */
  const totalActive = ads.filter(a => a.Status === 'active').length;
  const formatCounts = countByField(ads, 'Format');
  const dominantFormat = Object.entries(formatCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'text';

  /* ── Per-competitor stats ────────────────────────────── */
  const compStats = useMemo(() => COMPETITORS.map(c => {
    const ca = ads.filter(a => a.Domain === c.domain);
    const activeCount = ca.filter(a => a.Status === 'active').length;
    const formats = countByField(ca, 'Format');
    const dominantFmt = Object.entries(formats).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'text';
    const latestDate = ca.filter(a => a['Last Shown'])
      .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))[0]?.['Last Shown'];
    const firstDate = ca.filter(a => a['Last Shown'])
      .sort((a, b) => a['Last Shown'].localeCompare(b['Last Shown']))[0]?.['Last Shown'];
    const topKeywords = getTopKeywords(ca, 5);
    const vocabulary  = extractVocabulary(ca, 8);
    const topCtas     = getCTACounts(ca).slice(0, 3);
    const score       = intensityScore(ca, recentMonth);
    return {
      ...c, ca,
      total: ca.length,
      activeCount,
      activeRate: ca.length > 0 ? Math.round((activeCount / ca.length) * 100) : 0,
      formats, dominantFmt,
      latestDate, firstDate,
      topKeywords,
      vocabulary,
      topCtas,
      score,
    };
  }), [ads, recentMonth]);

  /* ── Market-wide intelligence ────────────────────────── */
  const allTopCtas     = useMemo(() => getCTACounts(ads), [ads]);
  const allTopKeywords = useMemo(() => getTopKeywords(ads, 14), [ads]);
  const marketVocab    = useMemo(() => extractVocabulary(ads, 14), [ads]);

  const topAngles = useMemo(() => {
    const counts: Record<string, number> = {};
    ads.forEach(ad => {
      ad['Messaging Angle']?.split(';').forEach(s => {
        const clean = s.trim().toLowerCase();
        if (clean.length > 5) counts[clean] = (counts[clean] || 0) + 1;
      });
    });
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([a, count]) => ({ angle: a.charAt(0).toUpperCase() + a.slice(1), count }));
  }, [ads]);

  /* ── Activity by month ────────────────────────────────── */
  const activityByMonth = useMemo(() => {
    const map: Record<string, Record<string, number>> = {};
    ads.forEach(ad => {
      const m = ad['Last Shown']?.slice(0, 7);
      if (!m) return;
      if (!map[m]) map[m] = {};
      map[m][ad.Domain] = (map[m][ad.Domain] || 0) + 1;
    });
    return map;
  }, [ads]);

  /* ── Strategic signals — all derived from data ──────── */
  const signals = useMemo(() => {
    const out: { type: string; title: string; detail: string; color: string }[] = [];

    // Text-only competitors
    const textOnly = compStats.find(c => c.formats['text'] === c.total && c.total > 0);
    if (textOnly) out.push({
      type: 'opportunity',
      title: `${textOnly.name} — zero visual ads`,
      detail: 'Runs exclusively text creatives. Image and video formats are completely unchallenged territory against them.',
      color: '#10b981',
    });

    // Video leader
    const videoLeader = [...compStats]
      .filter(c => (c.formats['video'] || 0) > 0)
      .sort((a, b) => (b.formats['video'] || 0) - (a.formats['video'] || 0))[0];
    if (videoLeader) out.push({
      type: 'trend',
      title: `${videoLeader.name} leads in video`,
      detail: `${videoLeader.formats['video']} video ads — the heaviest video investment in this market.`,
      color: '#f59e0b',
    });

    // Paused competitor
    const paused = compStats.find(c => c.activeRate === 0 && c.total > 0);
    if (paused) out.push({
      type: 'alert',
      title: `${paused.name} — all ads inactive`,
      detail: 'No active ads detected. Could be a budget pause, campaign reset, or creative refresh in progress.',
      color: '#f43f5e',
    });

    // Full-active competitors
    compStats.filter(c => c.activeRate === 100 && c.total > 0).forEach(c => out.push({
      type: 'watch',
      title: `${c.name} — running at full capacity`,
      detail: `All ${c.total} tracked ads are simultaneously active. Peak spend period.`,
      color: '#6366f1',
    }));

    // New entrant — first seen within last 2 months of data
    const [recentYear, recentMon] = recentMonth.split('-').map(Number);
    const twoMonthsAgo = new Date(recentYear, recentMon - 3, 1).toISOString().slice(0, 7);
    const newEntrant = compStats.find(c => c.firstDate && c.firstDate >= twoMonthsAgo);
    if (newEntrant) out.push({
      type: 'hot',
      title: `${newEntrant.name} — recent market entry`,
      detail: `First ad detected ${new Date(newEntrant.firstDate!).toLocaleDateString('en-US',{month:'short',year:'numeric'})}. New or significantly ramped-up presence.`,
      color: '#ec4899',
    });

    // Dominant vocabulary theme (most concentrated word)
    const mostFocused = [...compStats]
      .filter(c => c.vocabulary.length > 0)
      .sort((a, b) => {
        const aConc = a.vocabulary[0]?.count / (a.ca.length || 1);
        const bConc = b.vocabulary[0]?.count / (b.ca.length || 1);
        return bConc - aConc;
      })[0];
    if (mostFocused?.vocabulary[0]) out.push({
      type: 'info',
      title: `${mostFocused.name} — most focused messaging`,
      detail: `"${mostFocused.vocabulary[0].word}" appears in ${mostFocused.vocabulary[0].count} of their ads — tightly themed campaign.`,
      color: '#0ea5e9',
    });

    // Market vocabulary theme from headlines (excluding brand names)
    const topWord = marketVocab.filter(w =>
      !COMPETITORS.some(c => c.name.toLowerCase().includes(w.word) || w.word.includes('bello') || w.word.includes('dana') || w.word.includes('inspire'))
    )[0];
    if (topWord) out.push({
      type: 'trend',
      title: `"${topWord.word}" — market's hottest term`,
      detail: `Appears ${topWord.count}× across headlines and descriptions — the most competitive keyword in play.`,
      color: '#f59e0b',
    });

    // Recent-month surge: who posted the most in the latest month?
    const recentMonthCounts = Object.entries(activityByMonth[recentMonth] ?? {})
      .sort((a, b) => b[1] - a[1]);
    if (recentMonthCounts.length > 0) {
      const [topDomain, topCount] = recentMonthCounts[0];
      const comp = COMPETITORS.find(c => c.domain === topDomain);
      if (comp) out.push({
        type: 'hot',
        title: `${comp.name} — ${topCount} ads in ${new Date(recentMonth+'-15').toLocaleDateString('en-US',{month:'long',year:'numeric'})}`,
        detail: 'Highest single-month ad volume in the dataset — strong push right now.',
        color: '#ec4899',
      });
    }

    return out;
  }, [compStats, recentMonth, activityByMonth, marketVocab]);

  /* ── Show CTAs if any competitor has them ─────────────── */
  const hasCtas    = allTopCtas.length > 0;
  const hasKeywords = allTopKeywords.length > 0;
  const hasAngles  = topAngles.length > 0;

  /* ── Use vocabulary as fallback keyword source ────────── */
  const keywordSource = hasKeywords ? allTopKeywords.map(k => ({ word: k.keyword, count: k.count })) : marketVocab;

  return (
    <div className="space-y-5">

      {/* ── Hero Banner ──────────────────────────────────── */}
      <div className="relative overflow-hidden rounded-2xl border border-white/8 p-6 anim-fade-up"
           style={{ background:'linear-gradient(135deg,#0d1124 0%,#1a1040 50%,#0d1124 100%)' }}>
        <div style={{ position:'absolute',top:'-60px',right:'-60px',width:'240px',height:'240px',
                      background:'radial-gradient(circle,rgba(99,102,241,0.38),transparent 68%)',
                      borderRadius:'50%',pointerEvents:'none' }} />
        <div style={{ position:'absolute',bottom:'-50px',left:'25%',width:'200px',height:'200px',
                      background:'radial-gradient(circle,rgba(139,92,246,0.22),transparent 70%)',
                      borderRadius:'50%',pointerEvents:'none' }} />

        <div style={{ position:'relative',zIndex:1 }}>
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
            <span style={{ background:'linear-gradient(135deg,#818cf8,#c084fc)',
                           WebkitBackgroundClip:'text',WebkitTextFillColor:'transparent',backgroundClip:'text' }}>
              Overview
            </span>
          </h2>
          <p className="text-white/40 text-sm mb-5">
            Derived entirely from live ad data — formats, headlines, activity patterns, and messaging signals.
          </p>

          <div className="flex flex-wrap gap-2.5">
            {([
              { label:'Total Ads',       val: ads.length,                                    color:'#818cf8' },
              { label:'Active Now',      val: totalActive,                                   color:'#10b981' },
              { label:'Brands Tracked',  val: COMPETITORS.length,                            color:'#f59e0b' },
              { label:`${dominantFormat[0].toUpperCase()+dominantFormat.slice(1)}-led`,
                val: formatCounts[dominantFormat]??0,                                         color:'#0ea5e9' },
              { label:`Latest month`,    val: recentMonth,                                   color:'#a78bfa' },
            ] as { label: string; val: string|number; color: string }[]).map(s => (
              <div key={s.label} className="flex items-center gap-2 bg-white/5 border border-white/8 rounded-xl px-3 py-2">
                <span className="text-base font-black" style={{ color:s.color }}>{s.val}</span>
                <span className="text-white/40 text-xs">{s.label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Competitor Scorecards ─────────────────────────── */}
      <div>
        <h3 className="text-[11px] font-bold text-white/25 uppercase tracking-widest mb-3 px-0.5">
          Competitor Scorecards
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {compStats.map((c, i) => (
            <div key={c.domain}
                 className="relative overflow-hidden rounded-2xl border card-glow anim-fade-up cursor-pointer group"
                 style={{ background:`linear-gradient(135deg,${c.color}10 0%,#0e1526 65%)`,
                          borderColor:`${c.color}20`, animationDelay:`${i * 0.08}s` }}
                 onClick={() => onNav({ tab:'competitors', competitor:c.domain })}>

              <div style={{ position:'absolute',top:'-30px',right:'-30px',width:'120px',height:'120px',
                            background:`radial-gradient(circle,${c.color}30,transparent 70%)`,
                            borderRadius:'50%',pointerEvents:'none' }} />

              <div className="p-5 pb-3">
                {/* Header */}
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

                {/* Metrics */}
                <div className="grid grid-cols-3 gap-1.5 mb-3">
                  {([
                    { label:'Total',  val: String(c.total),         active: false },
                    { label:'Active', val: String(c.activeCount),   active: true  },
                    { label:'Rate',   val: `${c.activeRate}%`,      active: false },
                  ] as { label: string; val: string; active: boolean }[]).map(m => (
                    <div key={m.label} className="text-center bg-white/4 rounded-xl py-2 border border-white/5">
                      <p className="font-black text-lg leading-none"
                         style={{ color: m.active ? (c.activeCount > 0 ? '#34d399' : '#fb7185') : '#f1f5f9' }}>
                        {m.val}
                      </p>
                      <p className="text-white/30 text-[10px] mt-0.5">{m.label}</p>
                    </div>
                  ))}
                </div>

                {/* Format mix */}
                <div className="mb-3">
                  <div className="flex justify-between mb-1.5">
                    <span className="text-white/30 text-[11px]">Format mix</span>
                    <span className="text-white/45 text-[11px] capitalize">{c.dominantFmt}-led</span>
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

                {/* Intensity score */}
                <div className="mb-3">
                  <div className="flex justify-between mb-1.5">
                    <span className="text-white/30 text-[11px]">Market intensity</span>
                    <span className="text-[11px] font-bold" style={{ color: c.color }}>{c.score}/100</span>
                  </div>
                  <IntensityBar score={c.score} color={c.color} />
                </div>

                {/* Headline vocabulary */}
                {c.vocabulary.length > 0 && (
                  <div>
                    <p className="text-white/25 text-[10px] mb-1.5">Top headline words</p>
                    <div className="flex flex-wrap gap-1">
                      {c.vocabulary.slice(0, 6).map(v => (
                        <span key={v.word}
                              className="text-[10px] px-2 py-0.5 rounded-full border capitalize"
                              style={{ background:`${c.color}12`, borderColor:`${c.color}25`, color:`${c.color}cc` }}>
                          {v.word}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
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
                          color:       c.activeRate===100 ? '#34d399' : c.activeRate===0 ? '#fb7185' : '#fbbf24',
                          background:  c.activeRate===100 ? 'rgba(52,211,153,0.12)' : c.activeRate===0 ? 'rgba(251,113,133,0.12)' : 'rgba(251,191,36,0.12)',
                        }}>
                    {c.activeRate===100 ? 'LIVE' : c.activeRate===0 ? 'PAUSED' : 'PARTIAL'}
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── Activity Timeline ─────────────────────────────── */}
      <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-4"
           style={{ background:'rgba(13,18,38,0.75)' }}>
        <div className="flex items-center gap-2.5 mb-5">
          <div className="w-8 h-8 rounded-xl bg-indigo-500/18 border border-indigo-500/22 flex items-center justify-center flex-shrink-0">
            <BarChart2 size={15} className="text-indigo-400" />
          </div>
          <div>
            <h3 className="text-white font-bold text-sm leading-none">Ad Activity Pulse</h3>
            <p className="text-white/30 text-[11px] mt-0.5">12-month activity heatmap — darker = more ads that month</p>
          </div>
          <div className="ml-auto flex items-center gap-3 flex-shrink-0">
            {COMPETITORS.map(c => (
              <div key={c.domain} className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-sm" style={{ backgroundColor: c.color }} />
                <span className="text-white/30 text-[10px]">{c.name.split(' ')[0]}</span>
              </div>
            ))}
          </div>
        </div>
        <ActivityHeatmap ads={ads} />
      </div>

      {/* ── Intelligence Grid ─────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        {/* Headline Vocabulary (always shown — derived from all headlines) */}
        <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-5"
             style={{ background:'rgba(13,18,38,0.75)' }}>
          <div className="flex items-center gap-2.5 mb-4">
            <div className="w-8 h-8 rounded-xl bg-sky-500/18 border border-sky-500/22 flex items-center justify-center flex-shrink-0">
              <Hash size={15} className="text-sky-400" />
            </div>
            <div>
              <h3 className="text-white font-bold text-sm leading-none">
                {hasKeywords ? 'Keyword Battlefield' : 'Headline Intelligence'}
              </h3>
              <p className="text-white/30 text-[11px] mt-0.5">
                {hasKeywords ? 'Top targeted search terms' : 'Most-used words across all ad headlines'}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {keywordSource.map((kw, i) => {
              const max  = keywordSource[0].count;
              const intensity = kw.count / max;
              return (
                <span key={'word' in kw ? kw.word : (kw as {keyword:string}).keyword}
                      className="px-3 py-1.5 rounded-full text-xs font-medium border transition-all duration-200 hover:scale-105 cursor-default"
                      style={{
                        background:`rgba(14,165,233,${0.05 + intensity * 0.18})`,
                        borderColor:`rgba(14,165,233,${0.1 + intensity * 0.3})`,
                        color:`rgba(186,230,253,${0.5 + intensity * 0.45})`,
                        animationDelay:`${0.3 + i * 0.03}s`,
                      }}>
                  {'word' in kw ? kw.word : (kw as {keyword:string}).keyword}
                  <span className="ml-1.5 opacity-40 text-[10px]">{kw.count}</span>
                </span>
              );
            })}
          </div>
        </div>

        {/* CTA Arsenal — always shown with fallback */}
        <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-6"
             style={{ background:'rgba(13,18,38,0.75)' }}>
          <div className="flex items-center gap-2.5 mb-4">
            <div className="w-8 h-8 rounded-xl bg-violet-500/18 border border-violet-500/22 flex items-center justify-center flex-shrink-0">
              <Target size={15} className="text-violet-400" />
            </div>
            <div>
              <h3 className="text-white font-bold text-sm leading-none">CTA Arsenal</h3>
              <p className="text-white/30 text-[11px] mt-0.5">
                {hasCtas ? 'Most-used calls-to-action' : 'Top ad action words from headlines'}
              </p>
            </div>
          </div>

          {hasCtas ? (
            <div className="space-y-3">
              {allTopCtas.slice(0, 7).map((item, i) => {
                const max = allTopCtas[0].count;
                return (
                  <div key={item.cta} className="group">
                    <div className="flex justify-between mb-1">
                      <span className="text-white/70 text-xs font-medium truncate flex-1 mr-3 group-hover:text-white/90 transition-colors">{item.cta}</span>
                      <span className="text-white/35 text-[11px] flex-shrink-0">{item.count}×</span>
                    </div>
                    <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                      <div className="h-full rounded-full bar-fill"
                           style={{ width:`${(item.count/max)*100}%`, background:'linear-gradient(90deg,#818cf8,#c084fc)', animationDelay:`${i*0.05}s` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            /* Fallback: derive action words from all headlines */
            <div className="space-y-2.5">
              {[
                ...(extractVocabulary(
                  ads.filter(a => ['book','get','schedule','start','find','view','remove','loss','results','recovery','immediate','tightening'].some(w => a.Headline?.toLowerCase().includes(w))),
                  6,
                )),
                ...extractVocabulary(ads, 6),
              ]
                .filter((v, i, arr) => arr.findIndex(x => x.word === v.word) === i)
                .slice(0, 6)
                .map((v, i) => {
                  const max = 20;
                  return (
                    <div key={v.word} className="group">
                      <div className="flex justify-between mb-1">
                        <span className="text-white/70 text-xs font-medium capitalize group-hover:text-white/90 transition-colors">{v.word}</span>
                        <span className="text-white/35 text-[11px]">{v.count} ads</span>
                      </div>
                      <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                        <div className="h-full rounded-full bar-fill"
                             style={{ width:`${Math.min((v.count/max)*100,100)}%`, background:'linear-gradient(90deg,#818cf8,#c084fc)', animationDelay:`${i*0.05}s` }} />
                      </div>
                    </div>
                  );
                })}
            </div>
          )}
        </div>

        {/* Messaging Themes — with fallback to per-competitor vocabulary */}
        <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-7"
             style={{ background:'rgba(13,18,38,0.75)' }}>
          <div className="flex items-center gap-2.5 mb-4">
            <div className="w-8 h-8 rounded-xl bg-emerald-500/18 border border-emerald-500/22 flex items-center justify-center flex-shrink-0">
              <MessageSquare size={15} className="text-emerald-400" />
            </div>
            <div>
              <h3 className="text-white font-bold text-sm leading-none">
                {hasAngles ? 'Messaging Themes' : 'Per-Competitor Vocabulary'}
              </h3>
              <p className="text-white/30 text-[11px] mt-0.5">
                {hasAngles ? 'Recurring ad angles across all competitors' : 'Top words from each competitor\'s headlines'}
              </p>
            </div>
          </div>

          {hasAngles ? (
            <div className="space-y-2.5">
              {topAngles.map((item, i) => {
                const max = topAngles[0].count;
                return (
                  <div key={item.angle} className="flex items-center gap-3 group">
                    <span className="text-white/20 text-[10px] font-mono w-4 flex-shrink-0 text-right">{i+1}</span>
                    <span className="text-white/65 text-xs flex-1 truncate group-hover:text-white/85 transition-colors capitalize">{item.angle}</span>
                    <div className="w-16 h-1 bg-white/5 rounded-full overflow-hidden flex-shrink-0">
                      <div className="h-full rounded-full bar-fill"
                           style={{ width:`${(item.count/max)*100}%`, background:'linear-gradient(90deg,#34d399,#10b981)', animationDelay:`${0.4+i*0.04}s` }} />
                    </div>
                    <span className="text-white/25 text-[10px] w-3 text-right flex-shrink-0">{item.count}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            /* Fallback: per-competitor vocabulary breakdown */
            <div className="space-y-4">
              {compStats.map(c => (
                <div key={c.domain}>
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-4 h-4 rounded flex items-center justify-center text-white text-[9px] font-black"
                         style={{ backgroundColor: c.color }}>{c.name[0]}</div>
                    <span className="text-white/50 text-xs font-semibold">{c.name}</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {c.vocabulary.slice(0, 6).map(v => (
                      <span key={v.word}
                            className="text-[10px] px-2 py-0.5 rounded-full border capitalize"
                            style={{ background:`${c.color}10`, borderColor:`${c.color}22`, color:`${c.color}bb` }}>
                        {v.word} <span className="opacity-50">{v.count}</span>
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Strategic Signals */}
        <div className="rounded-2xl border border-white/7 p-5 card-glow anim-fade-up delay-8"
             style={{ background:'rgba(13,18,38,0.75)' }}>
          <div className="flex items-center gap-2.5 mb-4">
            <div className="w-8 h-8 rounded-xl bg-amber-500/18 border border-amber-500/22 flex items-center justify-center flex-shrink-0">
              <Zap size={15} className="text-amber-400" />
            </div>
            <div>
              <h3 className="text-white font-bold text-sm leading-none">Strategic Signals</h3>
              <p className="text-white/30 text-[11px] mt-0.5">Auto-detected patterns from ad data</p>
            </div>
            <span className="ml-auto text-[10px] text-white/25 bg-white/5 border border-white/8 px-2 py-0.5 rounded-full flex-shrink-0">
              {signals.length} signals
            </span>
          </div>
          <div className="space-y-2.5">
            {signals.map((sig, i) => {
              const cfg = SIG_CFG[sig.type] ?? SIG_CFG.info;
              return (
                <div key={i} className="rounded-xl p-3.5 border border-white/5 anim-fade-up"
                     style={{ background:`${sig.color}08`, borderLeftColor:sig.color, borderLeftWidth:'2px', animationDelay:`${0.35+i*0.06}s` }}>
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <span style={{ color:sig.color }}>{cfg.icon}</span>
                    <span className="text-[10px] font-bold tracking-widest uppercase" style={{ color:sig.color }}>{cfg.label}</span>
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
