import { useState } from 'react';
import { X, ExternalLink, Calendar, Globe, Tag, Lightbulb, ChevronDown, ChevronUp, ImageIcon, FileText, Video } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITOR_COLORS, COMPETITORS } from '../lib/types';
import { formatDate, getImageUrls, getKeywords, getMessagingPoints, truncate } from '../lib/utils';

interface AdModalProps { ad: Ad; onClose: () => void; }

/* ── Google Ad text preview mockup ──────────────────────── */
function TextAdPreview({ ad, color }: { ad: Ad; color: string }) {
  const displayUrl = ad['Destination URL']?.replace(/https?:\/\//, '').split('/')[0] || ad.Domain;
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 text-left">
      {/* Sponsored label */}
      <div className="flex items-center gap-1.5 mb-2">
        <span className="text-[10px] font-semibold text-slate-500 border border-slate-300 px-1.5 py-0.5 rounded">Sponsored</span>
        <div className="flex items-center gap-1">
          {ad['Logo URLs'] && (
            <img src={ad['Logo URLs']} alt="" className="w-4 h-4 rounded-full" onError={() => {}}/>
          )}
          <span className="text-xs text-slate-600 font-medium">{ad['Advertiser Name'] || ad.Domain}</span>
        </div>
      </div>
      {/* URL */}
      <p className="text-xs mb-1.5" style={{ color }}>
        {displayUrl}
      </p>
      {/* Headline */}
      {ad.Headline && (
        <p className="text-sm font-semibold mb-1 leading-snug" style={{ color }}>
          {ad.Headline}
        </p>
      )}
      {/* Description */}
      {ad.Description && (
        <p className="text-xs text-slate-600 leading-relaxed">{truncate(ad.Description, 150)}</p>
      )}
    </div>
  );
}

/* ── Image ad preview ───────────────────────────────────── */
function ImageAdPreview({ images }: { images: string[] }) {
  const [current, setCurrent] = useState(0);
  if (!images.length) return null;
  return (
    <div className="relative rounded-xl overflow-hidden bg-slate-100">
      <img src={images[current]} alt="Ad creative"
           className="w-full h-40 object-cover"
           onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}/>
      {images.length > 1 && (
        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 flex gap-1">
          {images.map((_, i) => (
            <button key={i} onClick={() => setCurrent(i)}
                    className={`w-1.5 h-1.5 rounded-full transition-all ${i === current ? 'bg-white w-3' : 'bg-white/50'}`}/>
          ))}
        </div>
      )}
      {images.length > 1 && (
        <div className="absolute top-2 right-2 bg-black/50 text-white text-[10px] px-1.5 py-0.5 rounded-full">
          {current + 1}/{images.length}
        </div>
      )}
    </div>
  );
}

/* ── Collapsible intel section ──────────────────────────── */
function Intel({ title, icon, children, defaultOpen = false }: {
  title: string; icon: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-slate-100 last:border-0">
      <button onClick={() => setOpen(!open)}
              className="w-full flex items-center justify-between py-2.5 px-1 text-left hover:bg-slate-50 transition-colors rounded-lg">
        <span className="flex items-center gap-2 text-xs font-semibold text-slate-600">
          <span className="text-slate-400">{icon}</span>{title}
        </span>
        {open ? <ChevronUp size={13} className="text-slate-300"/> : <ChevronDown size={13} className="text-slate-300"/>}
      </button>
      {open && <div className="pb-3 px-1 text-xs text-slate-600 leading-relaxed">{children}</div>}
    </div>
  );
}

export function AdModal({ ad, onClose }: AdModalProps) {
  const images   = getImageUrls(ad);
  const keywords = getKeywords(ad);
  const points   = getMessagingPoints(ad);
  const color    = COMPETITOR_COLORS[ad.Domain] || '#6366f1';
  const fmt      = ad.Format?.toLowerCase() || 'text';
  const comp     = COMPETITORS.find(c => c.domain === ad.Domain);

  const FORMAT_ICON: Record<string, React.ReactNode> = {
    image: <ImageIcon size={11}/>, text: <FileText size={11}/>, video: <Video size={11}/>
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 anim-fade-in"
         style={{ background: 'rgba(10,14,35,0.8)', backdropFilter: 'blur(10px)' }}
         onClick={onClose}>
      <div
        className="bg-white rounded-3xl shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col"
        style={{ maxHeight: '88vh', boxShadow: `0 40px 80px -12px rgba(0,0,0,.5), 0 0 0 1px ${color}25` }}
        onClick={e => e.stopPropagation()}
      >
        {/* ── Color stripe ── */}
        <div className="h-1 flex-shrink-0" style={{ background: `linear-gradient(90deg, ${color}, ${color}60)` }}/>

        {/* ── Header ── */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100 flex-shrink-0">
          <div className="flex items-center gap-2 flex-wrap">
            {/* Competitor badge */}
            <span className="flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-full"
                  style={{ background: `${color}15`, color }}>
              <span className="w-4 h-4 rounded-full flex items-center justify-center text-white text-[9px] font-black"
                    style={{ background: color }}>{comp?.name[0]}</span>
              {comp?.name ?? ad.Domain}
            </span>
            {/* Format */}
            <span className="flex items-center gap-1 text-xs capitalize text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full font-medium">
              {FORMAT_ICON[fmt]} {fmt}
            </span>
            {/* Status */}
            {ad.Status === 'active' && (
              <span className="flex items-center gap-1 text-xs font-semibold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block"/> Active
              </span>
            )}
          </div>
          <button onClick={onClose}
                  className="w-7 h-7 rounded-full flex items-center justify-center text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors flex-shrink-0 ml-2">
            <X size={15}/>
          </button>
        </div>

        {/* ── Scrollable body ── */}
        <div className="overflow-y-auto flex-1">

          {/* Two-column layout */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-0 divide-y sm:divide-y-0 sm:divide-x divide-slate-100">

            {/* LEFT — ad preview + quick stats */}
            <div className="p-5 space-y-4">
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Ad Preview</p>

              {/* Preview */}
              {images.length > 0
                ? <ImageAdPreview images={images}/>
                : <TextAdPreview ad={ad} color={color}/>
              }

              {/* CTA */}
              {ad.CTA && ad.CTA.length < 45 && (
                <div>
                  <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">Call to Action</p>
                  <span className="inline-block text-white text-xs font-bold px-4 py-2 rounded-xl"
                        style={{ background: color }}>{ad.CTA}</span>
                </div>
              )}

              {/* Quick stats grid */}
              <div>
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">Details</p>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: 'Last Shown',   val: ad['Last Shown']  ? formatDate(ad['Last Shown'])  : null, icon: <Calendar size={11}/> },
                    { label: 'First Shown',  val: ad['First Shown'] ? formatDate(ad['First Shown']) : null, icon: <Calendar size={11}/> },
                    { label: 'Region',       val: ad['Regions Served'] || null,    icon: <Globe size={11}/> },
                    { label: 'Impressions',  val: ad['Impression Data'] || null,   icon: <Tag   size={11}/> },
                    { label: 'Language',     val: ad.Language || null,             icon: <Globe size={11}/> },
                    { label: 'Platform',     val: ad.Platform || null,             icon: <Tag   size={11}/> },
                  ].filter(x => x.val).map(({ label, val, icon }) => (
                    <div key={label} className="bg-slate-50 rounded-xl p-2.5">
                      <p className="flex items-center gap-1 text-[9px] font-semibold text-slate-400 uppercase tracking-wider mb-0.5">
                        {icon} {label}
                      </p>
                      <p className="text-xs font-semibold text-slate-700 leading-snug">{val}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Destination */}
              {ad['Destination URL'] && (
                <a href={ad['Destination URL'].startsWith('http') ? ad['Destination URL'] : `https://${ad['Destination URL']}`}
                   target="_blank" rel="noopener noreferrer"
                   className="flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-xl hover:opacity-90 transition-opacity"
                   style={{ background: `${color}12`, color }}>
                  <Globe size={12}/> {truncate(ad['Destination URL'].replace(/https?:\/\//, ''), 45)}
                  <ExternalLink size={11} className="ml-auto flex-shrink-0"/>
                </a>
              )}
            </div>

            {/* RIGHT — intelligence */}
            <div className="p-5">
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-3">Intelligence</p>

              <div className="space-y-0.5">

                {/* Messaging */}
                {points.length > 0 && (
                  <Intel title="Messaging Angles" icon={<Lightbulb size={12}/>} defaultOpen={true}>
                    <ul className="space-y-1.5 mt-1">
                      {points.map((p, i) => (
                        <li key={i} className="flex items-start gap-2">
                          <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-black flex-shrink-0 mt-0.5 text-white"
                                style={{ background: color }}>{i+1}</span>
                          {p}
                        </li>
                      ))}
                    </ul>
                  </Intel>
                )}

                {ad['Value Proposition'] && (
                  <Intel title="Value Proposition" icon={<Tag size={12}/>} defaultOpen={!!points.length === false}>
                    <p className="mt-1">{truncate(ad['Value Proposition'], 300)}</p>
                  </Intel>
                )}

                {ad.Services && (
                  <Intel title="Services Advertised" icon={<Tag size={12}/>}>
                    <p className="mt-1">{truncate(ad.Services, 250)}</p>
                  </Intel>
                )}

                {ad['Pricing Model'] && (
                  <Intel title="Pricing Model" icon={<Tag size={12}/>}>
                    <p className="mt-1">{truncate(ad['Pricing Model'], 220)}</p>
                  </Intel>
                )}

                {ad['Audience Type'] && (
                  <Intel title="Target Audience" icon={<Tag size={12}/>}>
                    <p className="mt-1">{truncate(ad['Audience Type'], 220)}</p>
                  </Intel>
                )}

                {ad['Website Summary'] && (
                  <Intel title="About Advertiser" icon={<Globe size={12}/>}>
                    <p className="mt-1">{truncate(ad['Website Summary'], 280)}</p>
                  </Intel>
                )}

                {/* Keywords */}
                {keywords.length > 0 && (
                  <Intel title={`Keywords (${keywords.length})`} icon={<Tag size={12}/>}>
                    <div className="flex flex-wrap gap-1.5 mt-1">
                      {keywords.slice(0, 25).map((kw, i) => (
                        <span key={i} className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                              style={{ background: `${color}12`, color }}>
                          {kw}
                        </span>
                      ))}
                    </div>
                  </Intel>
                )}
              </div>

              {/* Creative ID */}
              <p className="text-[10px] text-slate-300 mt-4 font-mono truncate">
                ID: {ad['Creative ID']}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
