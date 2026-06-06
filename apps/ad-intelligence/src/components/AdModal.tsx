import { useState } from 'react';
import { createPortal } from 'react-dom';
import { X, ExternalLink, Globe, Tag, Lightbulb, ChevronDown, ChevronUp, ImageIcon, FileText, Video } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITOR_COLORS, COMPETITORS } from '../lib/types';
import { formatDate, getImageUrls, getKeywords, getMessagingPoints, truncate } from '../lib/utils';

interface AdModalProps { ad: Ad; onClose: () => void; }

/* ── Google Text Ad mockup ─────────────────────────────── */
function TextAdPreview({ ad, color }: { ad: Ad; color: string }) {
  const displayUrl = (ad['Destination URL'] || ad.Domain).replace(/https?:\/\//, '').split('/')[0];
  return (
    <div className="bg-slate-50 border border-slate-200 rounded-xl p-3.5 text-left">
      <div className="flex items-center gap-1.5 mb-2">
        <span className="text-[9px] font-semibold text-slate-500 border border-slate-300 px-1.5 py-0.5 rounded-sm">Sponsored</span>
        <span className="text-[11px] text-slate-600 font-medium truncate">{ad['Advertiser Name'] || ad.Domain}</span>
      </div>
      <p className="text-xs mb-1 font-medium" style={{ color }}>{displayUrl}</p>
      {ad.Headline && <p className="text-sm font-bold leading-snug mb-1.5" style={{ color }}>{truncate(ad.Headline, 80)}</p>}
      {ad.Description && <p className="text-xs text-slate-500 leading-relaxed">{truncate(ad.Description, 130)}</p>}
    </div>
  );
}

/* ── Image carousel ────────────────────────────────────── */
function ImagePreview({ images }: { images: string[] }) {
  const [idx, setIdx] = useState(0);
  return (
    <div className="relative rounded-xl overflow-hidden bg-slate-100 h-32">
      <img src={images[idx]} alt="" className="w-full h-full object-cover"
           onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}/>
      {images.length > 1 && (
        <>
          <div className="absolute bottom-1.5 left-1/2 -translate-x-1/2 flex gap-1">
            {images.map((_, i) => (
              <button key={i} onClick={() => setIdx(i)}
                      className={`rounded-full transition-all ${i === idx ? 'w-3 h-1.5 bg-white' : 'w-1.5 h-1.5 bg-white/50'}`}/>
            ))}
          </div>
          <span className="absolute top-2 right-2 text-[10px] bg-black/50 text-white px-1.5 py-0.5 rounded-full">
            {idx+1}/{images.length}
          </span>
        </>
      )}
    </div>
  );
}

/* ── Collapsible section ───────────────────────────────── */
function Section({ title, icon, children, defaultOpen = false }: {
  title: string; icon: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-slate-100 last:border-0">
      <button onClick={() => setOpen(!open)}
              className="w-full flex items-center justify-between py-2.5 hover:bg-slate-50 transition-colors rounded-lg px-1">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600">
          <span className="text-slate-400">{icon}</span>{title}
        </span>
        {open ? <ChevronUp size={12} className="text-slate-300 flex-shrink-0"/> : <ChevronDown size={12} className="text-slate-300 flex-shrink-0"/>}
      </button>
      {open && <div className="pb-3 px-1">{children}</div>}
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
    image: <ImageIcon size={10}/>, text: <FileText size={10}/>, video: <Video size={10}/>
  };

  const metaItems = [
    { label: 'Last Shown',  val: ad['Last Shown']  ? formatDate(ad['Last Shown'])  : null },
    { label: 'First Shown', val: ad['First Shown'] ? formatDate(ad['First Shown']) : null },
    { label: 'Region',      val: ad['Regions Served'] || null },
    { label: 'Impressions', val: ad['Impression Data'] || null },
    { label: 'Language',    val: ad.Language || null },
    { label: 'Platform',    val: ad.Platform || null },
  ].filter(x => x.val);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 anim-fade-in"
         style={{ background: 'rgba(10,14,35,0.8)', backdropFilter: 'blur(10px)' }}
         onClick={onClose}>
      <div
        className="modal-anim bg-white rounded-3xl shadow-2xl w-full max-w-2xl flex flex-col overflow-hidden"
        style={{ height: 'min(620px, 88vh)', boxShadow: `0 40px 80px -12px rgba(0,0,0,.5), 0 0 0 1px ${color}25` }}
        onClick={e => e.stopPropagation()}
      >
        {/* Accent stripe */}
        <div className="h-1 flex-shrink-0" style={{ background: `linear-gradient(90deg,${color},${color}60)` }}/>

        {/* Header — fixed, never scrolls */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100 flex-shrink-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-full"
                  style={{ background: `${color}15`, color }}>
              <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-black text-white flex-shrink-0"
                    style={{ background: color }}>{comp?.name[0]}</span>
              {comp?.name ?? ad.Domain}
            </span>
            <span className="flex items-center gap-1 text-[11px] capitalize text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full font-medium">
              {FORMAT_ICON[fmt]} {fmt}
            </span>
            {ad.Status === 'active' && (
              <span className="flex items-center gap-1 text-[11px] font-semibold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block"/> Active
              </span>
            )}
          </div>
          <button onClick={onClose}
                  className="w-7 h-7 rounded-full flex items-center justify-center text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors flex-shrink-0 ml-2">
            <X size={15}/>
          </button>
        </div>

        {/* ── Two columns, each independently scrollable ── */}
        <div className="flex flex-1 overflow-hidden divide-x divide-slate-100">

          {/* LEFT column */}
          <div className="w-[52%] flex-shrink-0 overflow-y-auto p-4 space-y-3.5">
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">Ad Preview</p>

            {images.length > 0 ? <ImagePreview images={images}/> : <TextAdPreview ad={ad} color={color}/>}

            {/* CTA */}
            {ad.CTA && ad.CTA.length < 45 && (
              <div>
                <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Call to Action</p>
                <span className="inline-block text-white text-xs font-bold px-4 py-1.5 rounded-xl"
                      style={{ background: color }}>{ad.CTA}</span>
              </div>
            )}

            {/* Meta grid */}
            {metaItems.length > 0 && (
              <div>
                <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-2">Details</p>
                <div className="grid grid-cols-2 gap-1.5">
                  {metaItems.map(({ label, val }) => (
                    <div key={label} className="bg-slate-50 rounded-lg p-2">
                      <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider mb-0.5">{label}</p>
                      <p className="text-[11px] font-semibold text-slate-700 leading-snug">{val}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* URL */}
            {ad['Destination URL'] && (
              <a href={ad['Destination URL'].startsWith('http') ? ad['Destination URL'] : `https://${ad['Destination URL']}`}
                 target="_blank" rel="noopener noreferrer"
                 className="cta-shine flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-xl"
                 style={{ background: `${color}12`, color }}>
                <Globe size={11}/>
                <span className="truncate">{truncate(ad['Destination URL'].replace(/https?:\/\//, ''), 38)}</span>
                <ExternalLink size={10} className="ml-auto flex-shrink-0"/>
              </a>
            )}
          </div>

          {/* RIGHT column */}
          <div className="flex-1 overflow-y-auto p-4">
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-2">Intelligence</p>

            <div className="space-y-0">
              {points.length > 0 && (
                <Section title="Messaging Angles" icon={<Lightbulb size={11}/>} defaultOpen>
                  <ul className="space-y-1.5">
                    {points.slice(0, 5).map((p, i) => (
                      <li key={i} className="flex items-start gap-2 text-[11px] text-slate-600 leading-relaxed">
                        <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-black flex-shrink-0 mt-0.5 text-white"
                              style={{ background: color }}>{i+1}</span>
                        {p}
                      </li>
                    ))}
                    {points.length > 5 && (
                      <p className="text-[10px] text-slate-400 pl-6">+{points.length - 5} more…</p>
                    )}
                  </ul>
                </Section>
              )}

              {ad['Value Proposition'] && (
                <Section title="Value Proposition" icon={<Tag size={11}/>}>
                  <p className="text-[11px] text-slate-600 leading-relaxed">{truncate(ad['Value Proposition'], 220)}</p>
                </Section>
              )}

              {ad.Services && (
                <Section title="Services" icon={<Tag size={11}/>}>
                  <p className="text-[11px] text-slate-600 leading-relaxed">{truncate(ad.Services, 200)}</p>
                </Section>
              )}

              {ad['Pricing Model'] && (
                <Section title="Pricing Model" icon={<Tag size={11}/>}>
                  <p className="text-[11px] text-slate-600 leading-relaxed">{truncate(ad['Pricing Model'], 180)}</p>
                </Section>
              )}

              {ad['Audience Type'] && (
                <Section title="Target Audience" icon={<Tag size={11}/>}>
                  <p className="text-[11px] text-slate-600 leading-relaxed">{truncate(ad['Audience Type'], 180)}</p>
                </Section>
              )}

              {ad['Website Summary'] && (
                <Section title="About Advertiser" icon={<Globe size={11}/>}>
                  <p className="text-[11px] text-slate-600 leading-relaxed">{truncate(ad['Website Summary'], 200)}</p>
                </Section>
              )}

              {keywords.length > 0 && (
                <Section title={`Keywords (${keywords.length})`} icon={<Tag size={11}/>}>
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {keywords.slice(0, 20).map((kw, i) => (
                      <span key={i} className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                            style={{ background: `${color}12`, color }}>{kw}</span>
                    ))}
                    {keywords.length > 20 && (
                      <span className="text-[10px] text-slate-400 px-2 py-0.5">+{keywords.length - 20} more</span>
                    )}
                  </div>
                </Section>
              )}
            </div>

            <p className="text-[9px] text-slate-300 mt-4 font-mono truncate">
              {ad['Creative ID']}
            </p>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
