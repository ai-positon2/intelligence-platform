import { useState, useEffect, useCallback, useRef } from 'react';
import {
  LayoutDashboard, Image, Users, Sparkles,
  RefreshCw, ExternalLink, CheckCircle2,
  Menu, X, TrendingUp, Zap, ArrowRight
} from 'lucide-react';
import type { Ad, TabId, NavParams } from './lib/types';
import { COMPETITORS } from './lib/types';
import { OverviewTab }    from './components/OverviewTab';
import { GalleryTab }     from './components/GalleryTab';
import { CompetitorsTab } from './components/CompetitorsTab';
import { CreativeTab }    from './components/CreativeTab';
import { fetchSheetData } from './lib/sheets';
import embeddedAds from './data/ads.json';

/* ── Count-up ─────────────────────────────────────────── */
function useCountUp(target: number, duration = 1200) {
  const [val, setVal] = useState(0);
  const raf = useRef<number>(0);
  useEffect(() => {
    let start: number | null = null;
    const tick = (ts: number) => {
      if (!start) start = ts;
      const p = Math.min((ts - start) / duration, 1);
      setVal(Math.round((1 - Math.pow(1 - p, 4)) * target));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, duration]);
  return val;
}

/* ── Nav tabs ─────────────────────────────────────────── */
const NAV: { id: TabId; label: string; icon: React.ReactNode; desc: string }[] = [
  { id: 'overview',    label: 'Overview',         icon: <LayoutDashboard size={18}/>, desc: 'Charts & summary'     },
  { id: 'gallery',     label: 'Ad Gallery',        icon: <Image           size={18}/>, desc: 'Browse all creatives' },
  { id: 'competitors', label: 'Competitors',       icon: <Users           size={18}/>, desc: 'Deep competitor intel' },
  { id: 'creative',    label: 'Creative Analysis', icon: <Sparkles        size={18}/>, desc: 'Keywords & messaging'  },
];

const SHEET_URL = 'https://docs.google.com/spreadsheets/d/16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s/edit';
type DataStatus = 'embedded' | 'loading' | 'live' | 'error';

/* ── Gradient stat card ───────────────────────────────── */
function GradientCard({
  value, label, sub, gradient, icon, delay = '', hint, onClick,
}: {
  value: number; label: string; sub: string;
  gradient: string; icon: React.ReactNode;
  delay?: string; hint: string; onClick: () => void;
}) {
  const count = useCountUp(value);
  return (
    <div
      onClick={onClick}
      className={`relative overflow-hidden rounded-2xl p-5 text-white cursor-pointer anim-fade-up ${delay}
                  group transition-all duration-200 hover:scale-[1.03] hover:shadow-2xl`}
      style={{ background: gradient }}
    >
      <div className="absolute inset-0 stat-shimmer pointer-events-none" />
      <div className="absolute -right-6 -top-6 w-28 h-28 rounded-full bg-white/10 pointer-events-none" />
      <div className="absolute -right-2 -bottom-8 w-20 h-20 rounded-full bg-white/5 pointer-events-none" />
      <div className="relative w-10 h-10 rounded-xl bg-white/20 flex items-center justify-center mb-4">{icon}</div>
      <div className="relative">
        <p className="text-4xl font-black tracking-tight leading-none">{count}</p>
        <p className="text-sm font-semibold mt-1 text-white/90">{label}</p>
        <p className="text-xs mt-0.5 text-white/60">{sub}</p>
        {/* hover hint */}
        <p className="flex items-center gap-1 text-[11px] font-semibold text-white/0 group-hover:text-white/80 transition-all mt-2">
          {hint} <ArrowRight size={11} />
        </p>
      </div>
    </div>
  );
}

/* ── Main App ─────────────────────────────────────────── */
export default function App() {
  const [ads, setAds]             = useState<Ad[]>(embeddedAds as Ad[]);
  const [tab, setTab]             = useState<TabId>('overview');
  const [status, setStatus]       = useState<DataStatus>('embedded');
  const [lastUpdated, setLast]    = useState<Date>(new Date());
  const [now, setNow]             = useState<Date>(new Date());
  const [sidebarOpen, setSidebar] = useState(false);

  /* ── Tick every minute so "X mins ago" stays fresh */
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);

  /* ── Human-readable "X mins ago" */
  const timeAgo = (() => {
    const diff = Math.floor((now.getTime() - lastUpdated.getTime()) / 60_000);
    if (diff < 1)  return 'just now';
    if (diff === 1) return '1 min ago';
    if (diff < 60)  return `${diff} mins ago`;
    const h = Math.floor(diff / 60);
    return h === 1 ? '1 hr ago' : `${h} hrs ago`;
  })();

  /* ── Global filter state (controlled across all tabs) */
  const [galleryDomain,   setGalleryDomain]   = useState('all');
  const [galleryFormat,   setGalleryFormat]   = useState('all');
  const [gallerySearch,   setGallerySearch]   = useState('');
  const [competitorActive, setCompetitorActive] = useState(COMPETITORS[0].domain);

  /* ── Central navigate function ───────────────────── */
  const navigateTo = useCallback((p: NavParams) => {
    if (p.domain     !== undefined) setGalleryDomain(p.domain);
    if (p.format     !== undefined) setGalleryFormat(p.format);
    if (p.search     !== undefined) setGallerySearch(p.search);
    if (p.competitor !== undefined) setCompetitorActive(p.competitor);
    if (p.tab        !== undefined) setTab(p.tab);
  }, []);

  const loadLive = useCallback(async () => {
    setStatus('loading');
    try {
      const live = await fetchSheetData();
      if (live.length > 0) { setAds(live); setStatus('live'); setLast(new Date()); }
      else throw new Error('empty');
    } catch { setStatus('error'); }
  }, []);

  useEffect(() => { loadLive(); }, [loadLive]);

  const total       = ads.length;
  const active      = ads.filter(a => a.Status === 'active').length;
  const withImg     = ads.filter(a => a['Image URLs']).length;
  const competitors = [...new Set(ads.map(a => a.Domain).filter(Boolean))].length;
  const tabLabel    = NAV.find(n => n.id === tab)?.label ?? '';

  return (
    <div className="flex h-screen overflow-hidden bg-[#0c1120]">

      {/* Mobile overlay */}
      {sidebarOpen && <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setSidebar(false)} />}

      {/* ── SIDEBAR ───────────────────────────────────── */}
      <aside className={`sidebar fixed lg:static inset-y-0 left-0 z-50 w-64 flex flex-col
        transition-transform duration-300 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}>

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
          <button onClick={() => setSidebar(false)} className="lg:hidden text-white/40 hover:text-white"><X size={18}/></button>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          <p className="text-white/25 text-[10px] font-semibold uppercase tracking-widest px-3 mb-3">Navigation</p>
          {NAV.map(item => {
            const isActive = tab === item.id;
            return (
              <button key={item.id} onClick={() => { setTab(item.id); setSidebar(false); }}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-all duration-150 group
                        ${isActive ? 'bg-indigo-500/20 text-white' : 'text-white/45 hover:text-white/80 hover:bg-white/5'}`}>
                <span className={`flex-shrink-0 transition-colors ${isActive ? 'text-indigo-400' : 'text-white/30 group-hover:text-white/60'}`}>{item.icon}</span>
                <span>
                  <span className="block text-sm font-medium leading-none">{item.label}</span>
                  <span className={`block text-[10px] mt-0.5 ${isActive ? 'text-indigo-300/70' : 'text-white/25'}`}>{item.desc}</span>
                </span>
                {isActive && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-400 flex-shrink-0" />}
              </button>
            );
          })}

          <p className="text-white/25 text-[10px] font-semibold uppercase tracking-widest px-3 mt-5 mb-3">Competitors</p>
          {COMPETITORS.map(c => (
            <button key={c.domain}
                    onClick={() => navigateTo({ tab: 'competitors', competitor: c.domain })}
                    className="w-full flex items-center gap-3 px-3 py-2 rounded-xl hover:bg-white/5 transition-all group">
              <div className="w-7 h-7 rounded-lg flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
                   style={{ backgroundColor: c.color }}>{c.name[0]}</div>
              <div className="min-w-0 text-left">
                <p className="text-white/60 text-xs font-medium truncate group-hover:text-white/80 transition-colors">{c.name}</p>
                <p className="text-white/25 text-[10px] truncate">{c.domain}</p>
              </div>
              <ArrowRight size={11} className="ml-auto text-white/0 group-hover:text-white/30 transition-colors flex-shrink-0" />
            </button>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-4 py-4 border-t border-white/5 space-y-2">

          {/* Action buttons */}
          <div className="flex gap-2">
            <button onClick={loadLive} disabled={status === 'loading'}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-xl bg-white/6 hover:bg-white/10 text-white/50 hover:text-white/80 text-xs font-semibold transition-all disabled:opacity-40 border border-white/8">
              <RefreshCw size={11} className={status === 'loading' ? 'animate-spin' : ''}/> Sync
            </button>
            <a href={SHEET_URL} target="_blank" rel="noopener noreferrer"
               className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-xl bg-white/6 hover:bg-white/10 text-white/50 hover:text-white/80 text-xs font-semibold transition-all border border-white/8">
              <ExternalLink size={11}/> Sheet
            </a>
          </div>
        </div>
      </aside>

      {/* ── MAIN ──────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* Top bar */}
        <header className="flex-shrink-0 bg-[#0d1324]/95 backdrop-blur-md border-b border-white/6 px-5 py-0 flex items-center gap-4 sticky top-0 z-30" style={{ minHeight: '52px' }}>
          <button onClick={() => setSidebar(true)} className="lg:hidden text-white/50 hover:text-white flex-shrink-0"><Menu size={20}/></button>

          {/* Left: breadcrumb-style title */}
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-7 h-7 rounded-lg animated-gradient flex items-center justify-center flex-shrink-0 shadow-md shadow-indigo-500/20">
              <Zap size={13} className="text-white"/>
            </div>
            <div className="hidden sm:flex items-center gap-1.5 font-medium">
              <span className="text-[13px] text-white/70">Ad Intelligence</span>
              <span className="text-xs text-white/30">/</span>
            </div>
            <h1 className="text-sm font-bold text-white leading-none truncate">{tabLabel}</h1>
          </div>

          {/* Right: pills */}
          <div className="ml-auto flex items-center gap-2 flex-shrink-0">
            {/* Updated pill */}
            <button
              onClick={loadLive}
              disabled={status === 'loading'}
              className="hidden sm:flex items-center gap-1.5 text-[11px] font-medium text-white/35 hover:text-white/65 px-2.5 py-1.5 rounded-lg hover:bg-white/6 transition-all disabled:opacity-40"
            >
              <RefreshCw size={10} className={status === 'loading' ? 'animate-spin text-indigo-400' : ''}/>
              {status === 'loading' ? 'Syncing…' : `Updated ${timeAgo}`}
            </button>

            {/* Live badge */}
            {status === 'live' && (
              <div className="hidden sm:flex items-center gap-1.5 bg-emerald-500/12 text-emerald-400 text-[11px] font-semibold px-2.5 py-1.5 rounded-lg border border-emerald-500/18">
                <span className="live-dot" style={{ width: 6, height: 6 }}/> Live
              </div>
            )}

            <a href={SHEET_URL} target="_blank" rel="noopener noreferrer"
               className="hidden sm:flex items-center gap-1.5 text-[11px] font-medium text-white/35 hover:text-white/65 px-2.5 py-1.5 rounded-lg hover:bg-white/6 transition-all border border-white/8">
              <ExternalLink size={11}/> Sheet
            </a>
          </div>
        </header>

        {/* Scrollable body */}
        <main className="flex-1 overflow-y-auto dot-bg">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6">

            {/* Clickable stat cards */}
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-7">
              <GradientCard value={total} label="Total Ads Tracked" sub={`${competitors} competitors`}
                gradient="linear-gradient(135deg,#6366f1 0%,#8b5cf6 100%)" delay="delay-1"
                icon={<TrendingUp size={20} className="text-white"/>}
                hint="Browse all ads"
                onClick={() => navigateTo({ tab: 'gallery', domain: 'all', format: 'all', search: '' })} />

              <GradientCard value={active} label="Active Ads" sub={`${Math.round((active/total)*100)}% of total`}
                gradient="linear-gradient(135deg,#10b981 0%,#0891b2 100%)" delay="delay-2"
                icon={<CheckCircle2 size={20} className="text-white"/>}
                hint="View competitor breakdown"
                onClick={() => navigateTo({ tab: 'competitors' })} />

              <GradientCard value={withImg} label="Image Creatives" sub={`${ads.filter(a=>a.Format==='video').length} video ads`}
                gradient="linear-gradient(135deg,#0ea5e9 0%,#3b82f6 100%)" delay="delay-3"
                icon={<Image size={20} className="text-white"/>}
                hint="Filter image ads"
                onClick={() => navigateTo({ tab: 'gallery', format: 'image', domain: 'all', search: '' })} />

              <GradientCard value={competitors} label="Competitors" sub="Google Ads data"
                gradient="linear-gradient(135deg,#f59e0b 0%,#ef4444 100%)" delay="delay-4"
                icon={<Users size={20} className="text-white"/>}
                hint="Explore all competitors"
                onClick={() => navigateTo({ tab: 'competitors' })} />
            </div>

            {/* Active tab */}
            <div className="anim-fade-up">
              {tab === 'overview' && (
                <OverviewTab ads={ads} onNav={navigateTo} />
              )}
              {tab === 'gallery' && (
                <GalleryTab
                  ads={ads}
                  domain={galleryDomain}   setDomain={setGalleryDomain}
                  format={galleryFormat}   setFormat={setGalleryFormat}
                  search={gallerySearch}   setSearch={setGallerySearch}
                  onNav={navigateTo}
                />
              )}
              {tab === 'competitors' && (
                <CompetitorsTab
                  ads={ads}
                  activeCompetitor={competitorActive}
                  setActiveCompetitor={setCompetitorActive}
                  onNav={navigateTo}
                />
              )}
              {tab === 'creative' && (
                <CreativeTab ads={ads} onNav={navigateTo} />
              )}
            </div>

          </div>
        </main>
      </div>
    </div>
  );
}
