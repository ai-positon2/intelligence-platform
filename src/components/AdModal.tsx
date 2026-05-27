import { X, ExternalLink, Calendar, Globe, Tag, Target, Lightbulb, DollarSign, Users, FileText } from 'lucide-react';
import type { Ad } from '../lib/types';
import { COMPETITOR_COLORS } from '../lib/types';
import { formatDate, getImageUrls, getKeywords, getMessagingPoints, truncate } from '../lib/utils';

interface AdModalProps { ad: Ad; onClose: () => void; }

function Sect({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
        <span className="text-slate-300">{icon}</span>{title}
      </h4>
      <div className="text-sm text-slate-700 leading-relaxed">{children}</div>
    </div>
  );
}

export function AdModal({ ad, onClose }: AdModalProps) {
  const images   = getImageUrls(ad);
  const keywords = getKeywords(ad);
  const points   = getMessagingPoints(ad);
  const color    = COMPETITOR_COLORS[ad.Domain] || '#6366f1';
  const fmt      = ad.Format?.toLowerCase() || 'text';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 anim-fade-in"
         style={{ background: 'rgba(10,14,35,0.75)', backdropFilter: 'blur(8px)' }}
         onClick={onClose}>
      <div
        className="bg-white rounded-3xl shadow-2xl w-full max-w-xl max-h-[90vh] overflow-hidden flex flex-col"
        style={{ boxShadow: `0 40px 80px -12px rgba(0,0,0,.5), 0 0 0 1px ${color}30` }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header stripe */}
        <div className="h-1 w-full" style={{ background: `linear-gradient(90deg, ${color}, ${color}88)` }} />

        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 border-b border-slate-100">
          <div>
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="text-xs font-bold px-2.5 py-1 rounded-full"
                    style={{ background: `${color}18`, color }}>{ad.Domain}</span>
              <span className="text-xs capitalize text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full">{fmt}</span>
              {ad.Status === 'active' && (
                <span className="text-xs font-semibold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block animate-pulse" /> Active
                </span>
              )}
            </div>
            <p className="text-slate-500 text-xs">Creative ID: <code className="text-slate-700">{ad['Creative ID']?.slice(0,20)}…</code></p>
          </div>
          <button onClick={onClose}
                  className="w-8 h-8 rounded-full flex items-center justify-center text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors ml-3 flex-shrink-0">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-6 py-5">
          {/* Images */}
          {images.length > 0 && (
            <div className="grid grid-cols-3 gap-2 mb-5">
              {images.slice(0, 6).map((url, i) => (
                <img key={i} src={url} alt="" className="rounded-xl w-full h-24 object-cover bg-slate-100"
                     onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }} />
              ))}
            </div>
          )}

          {ad.Headline && (
            <Sect icon={<Tag size={12} />} title="Headline">
              <p className="font-semibold text-slate-900">{ad.Headline}</p>
            </Sect>
          )}

          {ad.Description && (
            <Sect icon={<FileText size={12} />} title="Description">
              <p>{ad.Description}</p>
            </Sect>
          )}

          {ad.CTA && (
            <Sect icon={<Target size={12} />} title="Call to Action">
              {ad.CTA.length < 50
                ? <span className="inline-block text-white text-sm font-semibold px-4 py-1.5 rounded-xl"
                        style={{ backgroundColor: color }}>{ad.CTA}</span>
                : <p>{ad.CTA}</p>}
            </Sect>
          )}

          <Sect icon={<Calendar size={12} />} title="Activity">
            <div className="grid grid-cols-2 gap-3">
              {[['First Shown', ad['First Shown']], ['Last Shown', ad['Last Shown']], ['Region', ad['Regions Served']], ['Impressions', ad['Impression Data']]].map(([k, v]) =>
                v ? (
                  <div key={k} className="bg-slate-50 rounded-xl p-3">
                    <p className="text-[10px] text-slate-400 uppercase tracking-wider mb-0.5">{k}</p>
                    <p className="text-sm font-medium text-slate-800">{k?.includes('Shown') ? formatDate(v) : v}</p>
                  </div>
                ) : null
              )}
            </div>
          </Sect>

          {points.length > 0 && (
            <Sect icon={<Lightbulb size={12} />} title="Messaging Angles">
              <ul className="space-y-1.5">
                {points.map((p, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0" style={{ background: color }} />
                    {p}
                  </li>
                ))}
              </ul>
            </Sect>
          )}

          {ad['Value Proposition'] && (
            <Sect icon={<Target size={12} />} title="Value Proposition">
              <p>{truncate(ad['Value Proposition'], 300)}</p>
            </Sect>
          )}

          {ad.Services && (
            <Sect icon={<Tag size={12} />} title="Services">
              <p>{truncate(ad.Services, 300)}</p>
            </Sect>
          )}

          {ad['Pricing Model'] && (
            <Sect icon={<DollarSign size={12} />} title="Pricing Model">
              <p>{truncate(ad['Pricing Model'], 250)}</p>
            </Sect>
          )}

          {ad['Audience Type'] && (
            <Sect icon={<Users size={12} />} title="Target Audience">
              <p>{truncate(ad['Audience Type'], 250)}</p>
            </Sect>
          )}

          {keywords.length > 0 && (
            <Sect icon={<Tag size={12} />} title={`Keywords (${keywords.length})`}>
              <div className="flex flex-wrap gap-1.5">
                {keywords.slice(0, 30).map((kw, i) => (
                  <span key={i} className="text-xs px-2.5 py-1 rounded-full bg-slate-100 text-slate-600 font-medium">{kw}</span>
                ))}
              </div>
            </Sect>
          )}

          {ad['Destination URL'] && (
            <Sect icon={<Globe size={12} />} title="Destination URL">
              <a href={ad['Destination URL'].startsWith('http') ? ad['Destination URL'] : `https://${ad['Destination URL']}`}
                 target="_blank" rel="noopener noreferrer"
                 className="flex items-center gap-1.5 text-indigo-600 hover:text-indigo-800 break-all font-medium">
                {truncate(ad['Destination URL'], 70)} <ExternalLink size={12} className="flex-shrink-0" />
              </a>
            </Sect>
          )}
        </div>
      </div>
    </div>
  );
}
