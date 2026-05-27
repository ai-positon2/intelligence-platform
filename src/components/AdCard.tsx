import { ExternalLink, Image as ImageIcon, FileText, Video, Calendar } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITOR_COLORS } from '../lib/types';
import { formatDate, getImageUrls, getAdPreviewText, truncate } from '../lib/utils';

const FORMAT_ICONS: Record<string, React.ReactNode> = {
  image: <ImageIcon size={11} />,
  text:  <FileText  size={11} />,
  video: <Video     size={11} />,
};

interface AdCardProps { ad: Ad; onClick?: () => void; }

export function AdCard({ ad, onClick }: AdCardProps) {
  const images  = getImageUrls(ad);
  const primary = images[0];
  const color   = COMPETITOR_COLORS[ad.Domain] || '#6366f1';
  const fmt     = ad.Format?.toLowerCase() || 'text';
  const headline = getAdPreviewText(ad);

  return (
    <div
      onClick={onClick}
      className="bg-white rounded-2xl overflow-hidden cursor-pointer group shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-250 border border-slate-100"
      style={{ borderTop: `3px solid ${color}` }}
    >
      {/* Image */}
      {primary ? (
        <div className="relative h-40 bg-slate-100 overflow-hidden">
          <img
            src={primary} alt="Ad creative"
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-400"
            onError={e => { (e.target as HTMLImageElement).parentElement!.style.display = 'none'; }}
          />
          {/* Gradient overlay */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/50 via-transparent to-transparent" />
          {/* Format badge */}
          <div className="absolute top-2.5 right-2.5 flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full text-white backdrop-blur-md"
               style={{ backgroundColor: `${color}cc` }}>
            {FORMAT_ICONS[fmt]}
            {fmt}
          </div>
        </div>
      ) : (
        <div className="h-28 flex items-center justify-center relative" style={{ background: `${color}12` }}>
          <div style={{ color: `${color}60` }}>
            {fmt === 'video' ? <Video size={32} /> : fmt === 'image' ? <ImageIcon size={32} /> : <FileText size={32} />}
          </div>
          <div className="absolute top-2.5 right-2.5 flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full text-white"
               style={{ backgroundColor: color }}>
            {FORMAT_ICONS[fmt]} {fmt}
          </div>
        </div>
      )}

      {/* Body */}
      <div className="p-4">
        {/* Domain + date */}
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-[11px] font-bold px-2.5 py-1 rounded-full"
                style={{ background: `${color}18`, color }}>
            {ad.Domain.split('.')[0]}
          </span>
          {ad['Last Shown'] && (
            <span className="flex items-center gap-1 text-[10px] text-slate-400">
              <Calendar size={9} /> {formatDate(ad['Last Shown'])}
            </span>
          )}
        </div>

        {/* Headline */}
        <h3 className="text-sm font-semibold text-slate-800 line-clamp-2 leading-snug mb-2">
          {truncate(headline, 110)}
        </h3>

        {/* Description */}
        {ad.Description && (
          <p className="text-xs text-slate-400 line-clamp-2 mb-3 leading-relaxed">
            {truncate(ad.Description, 120)}
          </p>
        )}

        {/* Footer */}
        <div className="flex items-center gap-2 pt-2.5 border-t border-slate-100">
          {ad.CTA && ad.CTA.length < 40 && (
            <span className="text-[11px] font-semibold text-white px-2.5 py-1 rounded-lg flex-shrink-0"
                  style={{ backgroundColor: color }}>
              {ad.CTA}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {ad['Destination URL'] && (
              <a
                href={ad['Destination URL'].startsWith('http') ? ad['Destination URL'] : `https://${ad['Destination URL']}`}
                target="_blank" rel="noopener noreferrer"
                onClick={e => e.stopPropagation()}
                className="text-slate-300 hover:text-indigo-500 transition-colors"
              >
                <ExternalLink size={13} />
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
