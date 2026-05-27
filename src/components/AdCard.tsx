import { ExternalLink, Image as ImageIcon, FileText, Video } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITOR_COLORS } from '../lib/types';
import { formatDate, getImageUrls, getAdPreviewText, truncate } from '../lib/utils';

const FORMAT_ICONS: Record<string, React.ReactNode> = {
  image: <ImageIcon size={14} />,
  text: <FileText size={14} />,
  video: <Video size={14} />,
};

const FORMAT_LABELS: Record<string, string> = {
  image: 'Image',
  text: 'Text',
  video: 'Video',
};

interface AdCardProps {
  ad: Ad;
  onClick?: () => void;
}

export function AdCard({ ad, onClick }: AdCardProps) {
  const images = getImageUrls(ad);
  const primaryImage = images[0];
  const color = COMPETITOR_COLORS[ad.Domain] || '#64748b';
  const headline = getAdPreviewText(ad);
  const format = ad.Format?.toLowerCase() || 'text';

  return (
    <div
      className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 cursor-pointer overflow-hidden group"
      onClick={onClick}
    >
      {/* Image area */}
      {primaryImage ? (
        <div className="relative h-40 bg-slate-100 overflow-hidden">
          <img
            src={primaryImage}
            alt="Ad creative"
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onError={e => {
              (e.target as HTMLImageElement).style.display = 'none';
            }}
          />
          <div
            className="absolute top-2 left-2 text-xs font-semibold px-2 py-1 rounded-full text-white flex items-center gap-1"
            style={{ backgroundColor: color }}
          >
            {FORMAT_ICONS[format]}
            {FORMAT_LABELS[format] || format}
          </div>
        </div>
      ) : (
        <div
          className="h-24 flex items-center justify-center"
          style={{ backgroundColor: `${color}15` }}
        >
          <div style={{ color }} className="opacity-30">
            {format === 'video' ? <Video size={36} /> : format === 'image' ? <ImageIcon size={36} /> : <FileText size={36} />}
          </div>
          <div
            className="absolute top-2 left-2 text-xs font-semibold px-2 py-1 rounded-full text-white flex items-center gap-1"
            style={{ backgroundColor: color }}
          >
            {FORMAT_ICONS[format]}
            {FORMAT_LABELS[format] || format}
          </div>
        </div>
      )}

      {/* Content */}
      <div className="p-4">
        {/* Competitor badge */}
        <div className="flex items-center justify-between mb-2">
          <span
            className="text-xs font-semibold px-2 py-0.5 rounded-full"
            style={{ backgroundColor: `${color}18`, color }}
          >
            {ad.Domain}
          </span>
          {ad['Last Shown'] && (
            <span className="text-xs text-slate-400">{formatDate(ad['Last Shown'])}</span>
          )}
        </div>

        {/* Headline */}
        <h3 className="text-sm font-semibold text-slate-800 leading-snug line-clamp-2 mb-2">
          {truncate(headline, 100)}
        </h3>

        {/* Description */}
        {ad.Description && (
          <p className="text-xs text-slate-500 line-clamp-2 mb-3">
            {truncate(ad.Description, 120)}
          </p>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-100">
          {ad.CTA && ad.CTA.length < 40 && (
            <span className="text-xs font-medium text-white px-2 py-1 rounded-md" style={{ backgroundColor: color }}>
              {ad.CTA}
            </span>
          )}
          {ad['Destination URL'] && (
            <a
              href={ad['Destination URL'].startsWith('http') ? ad['Destination URL'] : `https://${ad['Destination URL']}`}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto text-slate-400 hover:text-slate-600 transition-colors"
              onClick={e => e.stopPropagation()}
            >
              <ExternalLink size={14} />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
