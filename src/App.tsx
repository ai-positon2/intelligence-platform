import { useState, useEffect, useCallback, useRef } from 'react';
import {
  LayoutDashboard, Image, Users, Sparkles,
  RefreshCw, ExternalLink, CheckCircle2, AlertCircle,
  Clock, Menu, X, TrendingUp, Zap
} from 'lucide-react';
import type { Ad, TabId } from './lib/types';
import { COMPETITORS } from './lib/types';
import { OverviewTab } from './components/OverviewTab';
import { GalleryTab } from './components/GalleryTab';
import { CompetitorsTab } from './components/CompetitorsTab';
import { CreativeTab } from './components/CreativeTab';
import { fetchSheetData } from './lib/sheets';
import embeddedAds from './data/ads.json';

/* ── Count-up hook ─────────────────────────────────────── */
function useCountUp(target: number, duration = 1200) {
  const [val, setVal] = useState(0);
  const raf = useRef<number>(0);
  useEffect(() => {
    let start: number | null = null;
    const tick = (ts: number) => {
      if (!start) start = ts;
      const p = Math.min((ts - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 4);
      setVal(Math.round(eased * target));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, duration]);
  return val;
}

/* ── Nav items ─────────────────────────────────────────── */
const NAV: { id: TabId; label: string; icon: React.ReactNode; desc: string }[] = [
  { id: 'overview',   label: 'Overview',          icon: <LayoutDashboard size={18} />, desc: 'Charts & summary' },
  { id: 'gallery',    label: 'Ad Gallery',         icon: <Image size={18} />,           desc: 'Browse all creatives' },
  { id: 'competitors',label: 'Competitors',        icon: <Users size={18} />,           desc: 'Deep competitor intel' },
  { id: 'creative',   label: 'Creative Analysis',  icon: <Sparkles size={18} />,        desc: 'Keywords & messaging' },
];

const SHEET_URL = 'https://docs.google.com/spreadsheets/d/16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s/edit';
type DataStatus = 'embedded' | 'loading' | 'live' | 'error';

/* ── Gradient stat card ─────────────────────────────────── */
function GradientCard({
  value, label, sub, gradient, icon, delay = ''
}: {
  value: number; label: string; sub: string;
  gradient: string; icon: React.ReactNode; delay?: string;
}) {
  const count = useCountUp(value);
  return (
    <div className={`relative overflow-hidden rounded-2xl p-5 text-white card-lift anim-fade-up ${delay} cursor-default`}
         style={{ background: gradient }}>
      {/* shimmer overlay */}
      <div className="absolute inset-0 stat-shimmer pointer-events-none" />
      {/* decorative circle */}
      <div className="absolute -right-6 -top-6 w-28 h-28 rounded-full bg-white/10 pointer-events-none" />
      <div className="absolute -right-2 -bottom-8 w-20 h-20 rounded-full bg-white/5 pointer-events-none" />
      {/* icon */}
      <div className="relative w-10 h-10 rounded-xl bg-white/20 flex items-center justify-center mb-4">
        {icon}
      </div>
      {/* content */}
      <div className="relative">
        <p className="text-4xl font-black tracking-tight leading-none">{count}</p>
        <p className="text-sm font-semibold mt-1 text-white/90">{label}</p>
        <p className="text-xs mt-0.5 text-white/60">{sub}</p>
      </div>
    </div>
  );
}

/* ── Main App ───────────────────────────────────────────── */
export default function App() {
  const [ads, setAds] = useState<Ad[]>(embeddedAds as Ad[]);
  const [tab, setTab] = useState<TabId>('overview');
  const [status, setStatus] = useState<DataStatus>('embedded');
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const loadLive = useCallback(async () => {
    setStatus('loading');
    try {
      const live = await fetchSheetData();
      if (live.length > 0) { setAds(live); setStatus('live'); setLastUpdated(new Date()); }
      else throw new Error('Empty response');
    } catch { setStatus('error'); }
  }, []);

  useEffect(() => { loadLive(); }, [loadLive]);

  /* stats */
  const total       = ads.length;
  const active      = ads.filter(a => a.Status === 'active').length;
  const withImg     = ads.filter(a => a['Image URLs']).length;
  const competitors = [...new Set(ads.map(a => a.Domain).filter(Boolean))].length;

  const tabLabel = NAV.find(n => n.id === tab)?.label ?? '';

  return (
    <div className="flex h-screen overflow-hidden bg-[#eef2ff]">

      {/* ── SIDEBAR ──────────────────────────────────────── */}
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      <aside className={`
        sidebar fixed lg:static inset-y-0 left-0 z-50 w-64 flex flex-col
        transition-transform duration-300 ease-in-out
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
      `}>
        {/* Logo */}
        <div className="flex items-center justify-between px-5 py-5 border-b border-white/5">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl animated-gradient flex items-center justify-center flex-shrink-0 shadow-lg shadow-indigo-500/30">
              <Zap size={18} className="text-white" />
            </div>
            <div>
              <p className="text-white font-bold text-sm leading-none">Ad Intelligence</p>
              <p className="text-white/40 text-xs mt-0.5">Competitor Tracker</p>
            </div>
          </div>
          <button onClick={() => setSidebarOpen(false)} className="lg:hidden text-white/40 hover:text-white">
            <X size={18} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          <p className="text-white/25 text-[10px] font-semibold uppercase tracking-widest px-3 mb-3">Navigation</p>
          {NAV.map(item => {
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => { setTab(item.id); setSidebarOpen(false); }}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-all duration-150 group ${
                  active
                    ? 'bg-indigo-500/20 text-white'
                    : 'text-white/45 hover:text-white/80 hover:bg-white/5'
                }`}
              >
                <span className={`flex-shrink-0 transition-colors ${active ? 'text-indigo-400' : 'text-white/30 group-hover:text-white/60'}`}>
                  {item.icon}
                </span>
                <span>
                  <span className="block text-sm font-medium leading-none">{item.label}</span>
                  <span className={`block text-[10px] mt-0.5 ${active ? 'text-indigo-300/70' : 'text-white/25'}`}>{item.desc}</span>
                </span>
                {active && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-400 flex-shrink-0" />}
              </button>
            );
          })}

          {/* Competitors section */}
          <p className="text-white/25 text-[10px] font-semibold uppercase tracking-widest px-3 mt-5 mb-3">Competitors</p>
          {COMPETITORS.map(c => (
            <div key={c.domain} className="flex items-center gap-3 px-3 py-2 rounded-xl">
              <div className="w-7 h-7 rounded-lg flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
                   style={{ backgroundColor: c.color }}>
                {c.name[0]}
              </div>
              <div className="min-w-0">
                <p className="text-white/60 text-xs font-medium truncate">{c.name}</p>
                <p className="text-white/25 text-[10px] truncate">{c.domain}</p>
              </div>
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-4 py-4 border-t border-white/5 space-y-3">
          {/* Status */}
          <div className="flex items-center gap-2">
            {status === 'live'     && <><span className="live-dot" /><span className="text-emerald-400 text-xs font-medium">Live data</span></>}
            {status === 'loading'  && <><RefreshCw size={10} className="text-amber-400 animate-spin" /><span className="text-amber-400 text-xs">Syncing…</span></>}
            {status === 'embedded' && <><Clock size={10} className="text-white/30" /><span className="text-white/30 text-xs">Cached data</span></>}
            {status === 'error'    && <><AlertCircle size={10} className="text-amber-400" /><span className="text-white/50 text-xs">Cached data</span></>}
          </div>
          <p className="text-white/25 text-[10px]">
            {total} ads · {competitors} competitors<br />
            Updated {lastUpdated.toLocaleTimeString()}
          </p>
          {/* Action buttons */}
          <div className="flex gap-2">
            <button
              onClick={loadLive}
              disabled={status === 'loading'}
              className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg bg-white/8 hover:bg-white/12 text-white/60 hover:text-white text-xs font-medium transition-all disabled:opacity-40"
            >
              <RefreshCw size={12} className={status === 'loading' ? 'animate-spin' : ''} />
              Refresh
            </button>
            <a
              href={SHEET_URL} target="_blank" rel="noopener noreferrer"
              className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg bg-white/8 hover:bg-white/12 text-white/60 hover:text-white text-xs font-medium transition-all"
            >
              <ExternalLink size={12} /> Sheet
            </a>
          </div>
        </div>
      </aside>

      {/* ── MAIN CONTENT ─────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* Top bar */}
        <header className="flex-shrink-0 bg-white/70 backdrop-blur-md border-b border-slate-200/80 px-5 py-3 flex items-center gap-4 sticky top-0 z-30">
          <button onClick={() => setSidebarOpen(true)} className="lg:hidden text-slate-500 hover:text-slate-700">
            <Menu size={20} />
          </button>
          <div>
            <h1 className="text-base font-bold text-slate-900 leading-none">{tabLabel}</h1>
            <p className="text-xs text-slate-400 mt-0.5">Google Ads Transparency Intelligence</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {status === 'live' && (
              <div className="hidden sm:flex items-center gap-1.5 bg-emerald-50 text-emerald-600 text-xs font-semibold px-3 py-1.5 rounded-full">
                <CheckCircle2 size={11} /> Live
              </div>
            )}
            <a href={SHEET_URL} target="_blank" rel="noopener noreferrer"
               className="hidden sm:flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50 transition-all border border-slate-200">
              <ExternalLink size={12} /> Open Sheet
            </a>
          </div>
        </header>

        {/* Scrollable body */}
        <main className="flex-1 overflow-y-auto dot-bg">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6">

            {/* Stat cards */}
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-7">
              <GradientCard
                value={total} label="Total Ads Tracked" sub={`${competitors} competitors`}
                gradient="linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)"
                icon={<TrendingUp size={20} className="text-white" />}
                delay="delay-1"
              />
              <GradientCard
                value={active} label="Active Ads" sub={`${Math.round((active/total)*100)}% of total`}
                gradient="linear-gradient(135deg, #10b981 0%, #0891b2 100%)"
                icon={<CheckCircle2 size={20} className="text-white" />}
                delay="delay-2"
              />
              <GradientCard
                value={withImg} label="Image Creatives" sub={`${ads.filter(a=>a.Format==='video').length} video ads`}
                gradient="linear-gradient(135deg, #0ea5e9 0%, #3b82f6 100%)"
                icon={<Image size={20} className="text-white" />}
                delay="delay-3"
              />
              <GradientCard
                value={competitors} label="Competitors" sub="Google Ads data"
                gradient="linear-gradient(135deg, #f59e0b 0%, #ef4444 100%)"
                icon={<Users size={20} className="text-white" />}
                delay="delay-4"
              />
            </div>

            {/* Tab content */}
            <div className="anim-fade-up">
              {tab === 'overview'    && <OverviewTab ads={ads} />}
              {tab === 'gallery'     && <GalleryTab ads={ads} />}
              {tab === 'competitors' && <CompetitorsTab ads={ads} />}
              {tab === 'creative'    && <CreativeTab ads={ads} />}
            </div>

          </div>
        </main>
      </div>
    </div>
  );
}
