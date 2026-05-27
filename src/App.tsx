import { useState, useEffect, useCallback } from 'react';
import {
  LayoutDashboard, Image, Users, Sparkles,
  RefreshCw, ExternalLink, AlertCircle, CheckCircle2, Clock
} from 'lucide-react';
import type { Ad, TabId } from './lib/types';
import { StatCard } from './components/StatCard';
import { OverviewTab } from './components/OverviewTab';
import { GalleryTab } from './components/GalleryTab';
import { CompetitorsTab } from './components/CompetitorsTab';
import { CreativeTab } from './components/CreativeTab';
import { fetchSheetData } from './lib/sheets';
import embeddedAds from './data/ads.json';

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: 'overview', label: 'Overview', icon: <LayoutDashboard size={16} /> },
  { id: 'gallery', label: 'Ad Gallery', icon: <Image size={16} /> },
  { id: 'competitors', label: 'Competitors', icon: <Users size={16} /> },
  { id: 'creative', label: 'Creative Analysis', icon: <Sparkles size={16} /> },
];

const SHEET_URL = 'https://docs.google.com/spreadsheets/d/16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s/edit';

type DataStatus = 'embedded' | 'loading' | 'live' | 'error';

export default function App() {
  const [ads, setAds] = useState<Ad[]>(embeddedAds as Ad[]);
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [dataStatus, setDataStatus] = useState<DataStatus>('embedded');
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [errorMsg, setErrorMsg] = useState('');

  const loadLiveData = useCallback(async () => {
    setDataStatus('loading');
    setErrorMsg('');
    try {
      const liveAds = await fetchSheetData();
      if (liveAds.length > 0) {
        setAds(liveAds);
        setDataStatus('live');
        setLastUpdated(new Date());
      } else {
        throw new Error('No data returned from sheet');
      }
    } catch (err) {
      console.error('Failed to fetch live data:', err);
      setDataStatus('error');
      setErrorMsg(err instanceof Error ? err.message : 'Unknown error');
      // Keep embedded data
    }
  }, []);

  // Attempt to load live data on mount
  useEffect(() => {
    loadLiveData();
  }, [loadLiveData]);

  // Summary stats
  const totalAds = ads.length;
  const activeAds = ads.filter(a => a.Status === 'active').length;
  const competitors = [...new Set(ads.map(a => a.Domain).filter(Boolean))].length;
  const withImages = ads.filter(a => a['Image URLs']).length;
  const withVideos = ads.filter(a => a.Format?.toLowerCase() === 'video').length;
  const mostRecent = [...ads]
    .filter(a => a['Last Shown'])
    .sort((a, b) => b['Last Shown'].localeCompare(a['Last Shown']))[0];

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            {/* Logo / Title */}
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center">
                <Sparkles size={16} className="text-white" />
              </div>
              <div>
                <h1 className="text-base font-bold text-slate-900 leading-none">Ad Intelligence</h1>
                <p className="text-xs text-slate-400 leading-none mt-0.5">Competitor Ad Dashboard</p>
              </div>
            </div>

            {/* Nav tabs */}
            <nav className="hidden md:flex items-center gap-1">
              {TABS.map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                    activeTab === tab.id
                      ? 'bg-indigo-50 text-indigo-700'
                      : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                </button>
              ))}
            </nav>

            {/* Right: data status + actions */}
            <div className="flex items-center gap-3">
              {/* Data status indicator */}
              <div className="flex items-center gap-2">
                {dataStatus === 'loading' && (
                  <div className="flex items-center gap-1.5 text-xs text-amber-600">
                    <RefreshCw size={12} className="animate-spin" />
                    <span className="hidden sm:inline">Fetching live data…</span>
                  </div>
                )}
                {dataStatus === 'live' && (
                  <div className="flex items-center gap-1.5 text-xs text-emerald-600">
                    <CheckCircle2 size={12} />
                    <span className="hidden sm:inline">Live data</span>
                  </div>
                )}
                {dataStatus === 'embedded' && (
                  <div className="flex items-center gap-1.5 text-xs text-slate-400">
                    <Clock size={12} />
                    <span className="hidden sm:inline">Cached data</span>
                  </div>
                )}
                {dataStatus === 'error' && (
                  <div className="flex items-center gap-1.5 text-xs text-slate-500" title={errorMsg}>
                    <AlertCircle size={12} className="text-amber-400" />
                    <span className="hidden sm:inline">Using cached data</span>
                  </div>
                )}
              </div>

              <button
                onClick={loadLiveData}
                disabled={dataStatus === 'loading'}
                className="flex items-center gap-1.5 text-xs font-medium text-slate-600 hover:text-indigo-600 px-2.5 py-1.5 rounded-lg hover:bg-indigo-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed border border-slate-200"
              >
                <RefreshCw size={12} className={dataStatus === 'loading' ? 'animate-spin' : ''} />
                Refresh
              </button>

              <a
                href={SHEET_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs font-medium text-slate-600 hover:text-indigo-600 px-2.5 py-1.5 rounded-lg hover:bg-indigo-50 transition-all border border-slate-200"
              >
                <ExternalLink size={12} />
                Sheet
              </a>
            </div>
          </div>
        </div>
      </header>

      {/* Mobile nav */}
      <div className="md:hidden bg-white border-b border-slate-200 px-4 py-2 flex gap-2 overflow-x-auto">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium whitespace-nowrap transition-all ${
              activeTab === tab.id
                ? 'bg-indigo-50 text-indigo-700'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Summary stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <StatCard
            label="Total Ads Tracked"
            value={totalAds}
            sub={`Across ${competitors} competitors`}
            icon={<LayoutDashboard size={20} />}
            color="bg-indigo-50 text-indigo-600"
          />
          <StatCard
            label="Active Ads"
            value={activeAds}
            sub={`${Math.round((activeAds / totalAds) * 100)}% of total`}
            icon={<CheckCircle2 size={20} />}
            color="bg-emerald-50 text-emerald-600"
          />
          <StatCard
            label="Image Creatives"
            value={withImages}
            sub={`${withVideos} video ad${withVideos !== 1 ? 's' : ''}`}
            icon={<Image size={20} />}
            color="bg-sky-50 text-sky-600"
          />
          <StatCard
            label="Competitors Tracked"
            value={competitors}
            sub={mostRecent ? `Last seen ${new Date(mostRecent['Last Shown']).toLocaleDateString()}` : 'Google Ads'}
            icon={<Users size={20} />}
            color="bg-purple-50 text-purple-600"
          />
        </div>

        {/* Tab content */}
        <div>
          {activeTab === 'overview' && <OverviewTab ads={ads} />}
          {activeTab === 'gallery' && <GalleryTab ads={ads} />}
          {activeTab === 'competitors' && <CompetitorsTab ads={ads} />}
          {activeTab === 'creative' && <CreativeTab ads={ads} />}
        </div>

        {/* Footer */}
        <footer className="mt-8 pt-6 border-t border-slate-200 text-center">
          <p className="text-xs text-slate-400">
            Data sourced from Google Ads Transparency Center •{' '}
            {dataStatus === 'live' ? (
              <>Last refreshed {lastUpdated.toLocaleTimeString()}</>
            ) : (
              <>Snapshot taken May 27, 2026</>
            )}
            {' '}•{' '}
            <a href={SHEET_URL} target="_blank" rel="noopener noreferrer" className="text-indigo-500 hover:text-indigo-700">
              View source sheet
            </a>
          </p>
        </footer>
      </main>
    </div>
  );
}
