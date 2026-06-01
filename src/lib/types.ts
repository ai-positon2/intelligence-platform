export interface Ad {
  Domain: string;
  "Advertiser Name": string;
  "Advertiser ID": string;
  "Creative ID": string;
  Format: string;
  Platform: string;
  Headline: string;
  Description: string;
  "Full Ad Text": string;
  CTA: string;
  "Destination URL": string;
  "Landing Page": string;
  "Image URLs": string;
  "Video URLs": string;
  "Thumbnail URLs": string;
  "Logo URLs": string;
  "Regions Served": string;
  "Impression Data": string;
  "First Shown": string;
  "Last Shown": string;
  Language: string;
  Status: string;
  "Messaging Angle": string;
  Offer: string;
  "Product Category": string;
  Keywords: string;
  "Value Proposition": string;
  "Social Profiles": string;
  "Website Summary": string;
  Services: string;
  "Pricing Model": string;
  "Audience Type": string;
  Timestamp: string;
}

export type AdFormat = 'image' | 'text' | 'video' | 'all';
export type TabId = 'insights' | 'overview' | 'gallery' | 'competitors' | 'creative';

/** Cross-tab navigation params — any subset can be passed */
export interface NavParams {
  tab?: TabId;
  domain?: string;      // gallery domain filter
  format?: string;      // gallery format filter
  search?: string;      // gallery search text
  competitor?: string;  // active competitor domain in Competitors tab
}
export type NavFn = (p: NavParams) => void;

export interface Competitor {
  domain: string;
  name: string;
  color: string;
  bgColor: string;
  textColor: string;
  borderColor: string;
}

export const COMPETITORS: Competitor[] = [
  { domain: 'inspireaesthetics.com', name: 'Inspire Aesthetics', color: '#6366f1', bgColor: 'bg-indigo-50', textColor: 'text-indigo-700', borderColor: 'border-indigo-200' },
  { domain: 'drdanamd.com',          name: 'Dr. Dana MD',        color: '#0ea5e9', bgColor: 'bg-sky-50',    textColor: 'text-sky-700',    borderColor: 'border-sky-200'   },
  { domain: 'sonobello.com',         name: 'Sono Bello',         color: '#f59e0b', bgColor: 'bg-amber-50',  textColor: 'text-amber-700',  borderColor: 'border-amber-200' },
];

export const FORMAT_COLORS: Record<string, string> = {
  image: '#10b981',
  text:  '#6366f1',
  video: '#f59e0b',
};

export const COMPETITOR_COLORS: Record<string, string> = {
  'inspireaesthetics.com': '#6366f1',
  'drdanamd.com':          '#0ea5e9',
  'sonobello.com':         '#f59e0b',
};
