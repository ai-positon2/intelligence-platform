import type { Ad } from './types';

const SHEET_ID = '16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s';

/**
 * Fetch live data from Google Sheets using the gviz (Visualization Query) endpoint.
 * This endpoint supports CORS and doesn't require an API key, as long as the sheet is
 * shared publicly or shared with the viewer.
 */
export async function fetchSheetData(): Promise<Ad[]> {
  const url = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/gviz/tq?tqx=out:json`;

  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch sheet data`);

  const text = await res.text();

  // Strip the JSONP wrapper: /*O_o*/ google.visualization.Query.setResponse({...});
  const jsonStr = text.replace(/^[^{]*/, '').replace(/\);?\s*$/, '');
  const parsed = JSON.parse(jsonStr);

  const table = parsed.table;
  if (!table || !table.cols || !table.rows) {
    throw new Error('Unexpected response format from Google Sheets');
  }

  // Map column labels
  const headers: string[] = table.cols.map((c: { label: string }) => c.label);

  const ads: Ad[] = table.rows.map((row: { c: Array<{ v: unknown } | null> }) => {
    const obj: Record<string, string> = {};
    headers.forEach((h, i) => {
      const cell = row.c?.[i];
      obj[h] = cell?.v != null ? String(cell.v) : '';
    });
    return obj as unknown as Ad;
  });

  // Filter out header/separator rows
  return ads.filter(a => a.Domain && a.Domain !== 'Domain');
}
