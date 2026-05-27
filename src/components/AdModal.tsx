import { X, ExternalLink, Calendar, Globe, Tag, Target, Lightbulb, DollarSign, Users } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITOR_COLORS } from '../lib/types';
import { formatDate, getImageUrls, getKeywords, getMessagingPoints, truncate } from '../lib/utils';

interface AdModalProps {
  ad: Ad;
  onClose: () => void;
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5 mb-2">
        {icon} {title}
      </h4>
      <div className="text-sm text-slate-700">{children}</div>
    </div>
  );
}

export function AdModal({ ad, onClose }: AdModalProps) {
  const images = getImageUrls(ad);
  const keywords = getKeywords(ad);
  const messagingPoints = getMessagingPoints(ad);
  const color = COMPETITOR_COLORS[ad.Domain] || '#64748b';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-slate-100">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span
                className="text-xs font-semibold px-2 py-0.5 rounded-full"
                style={{ backgroundColor: `${color}18`, color }}
              >
                {ad.Domain}
              </span>
              <span className="text-xs text-slate-400 capitalize bg-slate-100 px-2 py-0.5 rounded-full">
                {ad.Format}
              </span>
              {ad.Status === 'active' && (
                <span className="text-xs font-semibold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
                  Active
                </span>
              )}
            </div>
            <h3 className="text-base font-semibold text-slate-800">Ad Creative Details</h3>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-full flex items-center justify-center text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto flex-1 p-5">
          {/* Images */}
          {images.length > 0 && (
            <div className="mb-5 grid grid-cols-3 gap-2">
              {images.slice(0, 6).map((url, i) => (
                <img
                  key={i}
                  src={url}
                  alt={`Ad image ${i + 1}`}
                  className="rounded-lg w-full h-28 object-cover bg-slate-100"
                  onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}
                />
              ))}
            </div>
          )}

          {/* Ad copy */}
          {ad.Headline && (
            <Section title="Headline" icon={<Tag size={12} />}>
              <p className="font-medium">{ad.Headline}</p>
            </Section>
          )}

          {ad.Description && (
            <Section title="Description" icon={<FileTextIcon />}>
              <p>{ad.Description}</p>
            </Section>
          )}

          {ad.CTA && (
            <Section title="Call to Action" icon={<Target size={12} />}>
              {ad.CTA.length < 50 ? (
                <span className="inline-block text-white text-sm font-medium px-3 py-1.5 rounded-lg" style={{ backgroundColor: color }}>
                  {ad.CTA}
                </span>
              ) : (
                <p>{ad.CTA}</p>
              )}
            </Section>
          )}

          {/* Timing */}
          <Section title="Ad Activity" icon={<Calendar size={12} />}>
            <div className="grid grid-cols-2 gap-3">
              {ad['First Shown'] && (
                <div>
                  <p className="text-xs text-slate-400 mb-0.5">First shown</p>
                  <p className="font-medium">{formatDate(ad['First Shown'])}</p>
                </div>
              )}
              {ad['Last Shown'] && (
                <div>
                  <p className="text-xs text-slate-400 mb-0.5">Last shown</p>
                  <p className="font-medium">{formatDate(ad['Last Shown'])}</p>
                </div>
              )}
              {ad['Regions Served'] && (
                <div>
                  <p className="text-xs text-slate-400 mb-0.5">Region</p>
                  <p className="font-medium flex items-center gap-1"><Globe size={12} />{ad['Regions Served']}</p>
                </div>
              )}
              {ad['Impression Data'] && (
                <div>
                  <p className="text-xs text-slate-400 mb-0.5">Impressions</p>
                  <p className="font-medium">{ad['Impression Data']}</p>
                </div>
              )}
            </div>
          </Section>

          {/* Messaging */}
          {messagingPoints.length > 0 && (
            <Section title="Messaging Angles" icon={<Lightbulb size={12} />}>
              <ul className="space-y-1">
                {messagingPoints.map((p, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ backgroundColor: color }} />
                    {p}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {/* Value Prop */}
          {ad['Value Proposition'] && (
            <Section title="Value Proposition" icon={<Target size={12} />}>
              <p>{truncate(ad['Value Proposition'], 300)}</p>
            </Section>
          )}

          {/* Services */}
          {ad.Services && (
            <Section title="Services Advertised" icon={<Tag size={12} />}>
              <p>{truncate(ad.Services, 300)}</p>
            </Section>
          )}

          {/* Pricing */}
          {ad['Pricing Model'] && (
            <Section title="Pricing Model" icon={<DollarSign size={12} />}>
              <p>{truncate(ad['Pricing Model'], 300)}</p>
            </Section>
          )}

          {/* Audience */}
          {ad['Audience Type'] && (
            <Section title="Target Audience" icon={<Users size={12} />}>
              <p>{truncate(ad['Audience Type'], 300)}</p>
            </Section>
          )}

          {/* Keywords */}
          {keywords.length > 0 && (
            <Section title="Keywords" icon={<Tag size={12} />}>
              <div className="flex flex-wrap gap-1.5">
                {keywords.slice(0, 30).map((kw, i) => (
                  <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                    {kw}
                  </span>
                ))}
              </div>
            </Section>
          )}

          {/* URLs */}
          {ad['Destination URL'] && (
            <Section title="Destination" icon={<Globe size={12} />}>
              <a
                href={ad['Destination URL'].startsWith('http') ? ad['Destination URL'] : `https://${ad['Destination URL']}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-indigo-600 hover:text-indigo-800 break-all"
              >
                {truncate(ad['Destination URL'], 80)} <ExternalLink size={12} className="flex-shrink-0" />
              </a>
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}

function FileTextIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14,2 14,8 20,8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <polyline points="10,9 9,9 8,9" />
    </svg>
  );
}
