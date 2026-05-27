import type { Ad, Competitor } from './types';
import { COMPETITORS } from './types';

export function getCompetitor(domain: string): Competitor | undefined {
  return COMPETITORS.find(c => c.domain === domain);
}

export function formatDate(dateStr: string): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return dateStr;
  }
}

export function getImageUrls(ad: Ad): string[] {
  if (!ad['Image URLs']) return [];
  return ad['Image URLs'].split(',').map(u => u.trim()).filter(Boolean);
}

export function getThumbnailUrls(ad: Ad): string[] {
  if (!ad['Thumbnail URLs']) return [];
  return ad['Thumbnail URLs'].split(',').map(u => u.trim()).filter(Boolean);
}

export function getKeywords(ad: Ad): string[] {
  if (!ad.Keywords) return [];
  return ad.Keywords.split(',').map(k => k.trim()).filter(Boolean);
}

export function getMessagingPoints(ad: Ad): string[] {
  if (!ad['Messaging Angle']) return [];
  return ad['Messaging Angle'].split(';').map(p => p.trim()).filter(Boolean);
}

export function countByField(ads: Ad[], field: keyof Ad): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const ad of ads) {
    const val = String(ad[field] || 'Unknown').trim();
    if (val) counts[val] = (counts[val] || 0) + 1;
  }
  return counts;
}

export function getAdsByDomain(ads: Ad[]): Record<string, Ad[]> {
  const map: Record<string, Ad[]> = {};
  for (const ad of ads) {
    const d = ad.Domain;
    if (!map[d]) map[d] = [];
    map[d].push(ad);
  }
  return map;
}

export function getTopKeywords(ads: Ad[], limit = 20): { keyword: string; count: number }[] {
  const counts: Record<string, number> = {};
  for (const ad of ads) {
    const kws = getKeywords(ad);
    for (const kw of kws) {
      const norm = kw.toLowerCase().trim();
      if (norm.length > 2) {
        counts[norm] = (counts[norm] || 0) + 1;
      }
    }
  }
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([keyword, count]) => ({ keyword, count }));
}

export function getAdActivityByDate(ads: Ad[]): { date: string; [domain: string]: number | string }[] {
  const dateMap: Record<string, Record<string, number>> = {};
  for (const ad of ads) {
    const ls = ad['Last Shown'];
    if (!ls) continue;
    const date = ls.substring(0, 10);
    if (!dateMap[date]) dateMap[date] = {};
    const domain = ad.Domain;
    dateMap[date][domain] = (dateMap[date][domain] || 0) + 1;
  }
  return Object.entries(dateMap)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, domainCounts]) => ({ date, ...domainCounts }));
}

export function getCTACounts(ads: Ad[]): { cta: string; count: number }[] {
  const counts: Record<string, number> = {};
  for (const ad of ads) {
    const cta = ad.CTA?.trim();
    if (!cta || cta.length > 40) continue; // skip very long ones that are likely descriptions
    counts[cta] = (counts[cta] || 0) + 1;
  }
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([cta, count]) => ({ cta, count }));
}

export function truncate(str: string, len: number): string {
  if (!str) return '';
  return str.length > len ? str.slice(0, len) + '…' : str;
}

export function getAdPreviewText(ad: Ad): string {
  if (ad.Headline) return ad.Headline;
  if (ad['Full Ad Text']) return ad['Full Ad Text'].slice(0, 120);
  return '(No headline)';
}

export function getDomainDisplayName(domain: string): string {
  const comp = getCompetitor(domain);
  return comp?.name ?? domain;
}
