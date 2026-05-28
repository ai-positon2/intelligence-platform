import { ExternalLink, Image as ImageIcon, FileText, Video, Calendar } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITOR_COLORS } from '../lib/types';
import { formatDate, getImageUrls, getAdPreviewText, truncate } from '../lib/utils';

const FORMAT_ICONS: Record<string, React.ReactNode> = {
  image: <ImageIcon size={11}/>,
  text:  <FileText  size={11}/>,
  video: <Video     size={11}/>,
};

interface AdCardProps {
  ad: Ad;
  onClick?: () => void;
  onDomainClick?: (domain: string) => void;
}

export function AdCard({ ad, onClick, onDomainClick }: AdCardProps) {
  const images   = getImageUrls(ad);
  const primary  = images[0];
  const color    = COMPETITOR_COLORS[ad.Domain] || '#6366f1';
  const fmt      = ad.Format?.toLowerCase() || 'text';
  const headline = getAdPreviewText(ad);

  return (
    <div
      onClick={onClick}
      className="bg-white rounded-2xl overflow-hidden cursor-pointer group shadow-sm hover:shadow-xl hover:-translate-y-2 active:scale-[0.98] active:shadow-md transition-all duration-200 border border-slate-100 flex flex-col"
      style={{ borderTop: `3px solid ${color}`, height: '300px' }}
    >
      {/* ── Media zone — same height for every card ── */}
      <div className="relative h-36 flex-shrink-0 overflow-hidden">
        {primary ? (
          <>
            <img
              src={primary} alt="Ad creative"
              className="img-fade-in w-full h-full object-cover group-hover:scale-110 transition-transform duration-700 ease-out"
              onLoad={e => (e.target as HTMLImageElement).classList.add('loaded')}
              onError={e => {
                const img = e.target as HTMLImageElement;
                img.style.display = 'none';
                // show fallback placeholder inside parent
                const parent = img.parentElement!;
                parent.style.background = `${color}12`;
                parent.querySelector('.fallback-icon')?.removeAttribute('style');
              }}
            />
            <div className="absolute inset-0 bg-gradient-to-t from-black/40 via-transparent to-transparent"/>
          </>
        ) : (
          <div className="w-full h-full flex items-center justify-center" style={{ background: `${color}12` }}>
            <div style={{ color: `${color}50` }}>
              {fmt === 'video' ? <Video size={32}/> : fmt === 'image' ? <ImageIcon size={32}/> : <FileText size={32}/>}
            </div>
          </div>
        )}

        {/* Live badge — top-left, only when active */}
        {ad.Status === 'active' && (
          <div className="anim-badge-in absolute top-2.5 left-2.5 flex items-center gap-1 text-[9px] font-bold px-2 py-0.5 rounded-full text-white bg-emerald-500/90 backdrop-blur-sm z-10">
            <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse inline-block"/>
            Live
          </div>
        )}

        {/* Format badge — always top-right */}
        <div className="anim-badge-in absolute top-2.5 right-2.5 flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full text-white backdrop-blur-md transition-transform group-hover:scale-110"
             style={{ backgroundColor: `${color}cc`, animationDelay: '0.08s' }}>
          {FORMAT_ICONS[fmt]} {fmt}
        </div>
      </div>

      {/* ── Body — flex-1 so it fills remaining card height ── */}
      <div className="flex-1 flex flex-col p-4 overflow-hidden">

        {/* Domain + date */}
        <div className="flex items-center justify-between mb-2 flex-shrink-0">
          <button
            onClick={e => { e.stopPropagation(); onDomainClick?.(ad.Domain); }}
            className="text-[11px] font-bold px-2.5 py-0.5 rounded-full hover:opacity-80 transition-opacity"
            style={{ background: `${color}18`, color }}
          >
            {ad.Domain.split('.')[0]}
          </button>
          {ad['Last Shown'] && (
            <span className="flex items-center gap-1 text-[10px] text-slate-400">
              <Calendar size={9}/> {formatDate(ad['Last Shown'])}
            </span>
          )}
        </div>

        {/* Headline — always 2 lines */}
        <h3 className="text-sm font-semibold text-slate-800 leading-snug mb-1.5 flex-shrink-0"
            style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
          {headline}
        </h3>

        {/* Description — fills remaining space, clamped to 2 lines */}
        <p className="text-xs text-slate-400 leading-relaxed flex-1"
           style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
          {ad.Description ? truncate(ad.Description, 130) : ' '}
        </p>

        {/* Footer — always at bottom */}
        <div className="flex items-center gap-2 pt-2 border-t border-slate-100 mt-2 flex-shrink-0">
          {ad.CTA && ad.CTA.length < 40 ? (
            <span className="cta-shine text-[11px] font-semibold text-white px-2.5 py-1 rounded-lg truncate max-w-[70%] inline-block"
                  style={{ backgroundColor: color }}>
              {ad.CTA}
            </span>
          ) : (
            <span/>
          )}
          <div className="ml-auto flex items-center">
            {ad['Destination URL'] && (
              <a
                href={ad['Destination URL'].startsWith('http') ? ad['Destination URL'] : `https://${ad['Destination URL']}`}
                target="_blank" rel="noopener noreferrer"
                onClick={e => e.stopPropagation()}
                className="text-slate-300 hover:text-indigo-500 transition-colors"
              >
                <ExternalLink size={13}/>
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
