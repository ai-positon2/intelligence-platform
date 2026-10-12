/* Search Query Analysis (Paid Marketing Agents).
 *
 * Ported from Agent Holmes' landing/sqr_agent.html. The account rulesets
 * below are carried over verbatim (Riccobene and OIA are ports of the team's
 * sqr-analysis-* skills; Eventgroove, 42 North and McHale of client prompts;
 * Beta Bionics is a placeholder). Every row runs through its account's
 * deterministic waterfall in the browser; only the terms no rule settles go
 * to the model, through /p2/paid-marketing/api/sqr/classify, which holds each
 * account's remaining categories (tracker/paid_sqr.py). The ruleset ids here
 * and there must match: tests/test_paid_marketing.py checks.
 */
(function(){
"use strict";

// =====================================================================
// CSV parsing: handles quoted fields (Google Ads exports quote fields
// that contain commas), and finds the real header row even when Google
// prepends a title/date-range line before it.
// =====================================================================
function cleanText(text){
  // Strip null bytes (shows up once per character when a UTF-16 export
  // gets misread as UTF-8) and zero-width/BOM characters some tools leave
  // in exported cells.
  return String(text).replace(/\u0000/g, "").replace(/[\u200B\uFEFF]/g, "").replace(/\r\n/g, "\n");
}

function splitCSVLine(line, delim){
  const out = []; let field = "", inQuotes = false;
  for (let i = 0; i < line.length; i++){
    const c = line[i];
    if (inQuotes){
      if (c === '"'){ if (line[i+1] === '"'){ field += '"'; i++; } else inQuotes = false; }
      else field += c;
    } else {
      if (c === '"') inQuotes = true;
      else if (c === delim) { out.push(field); field = ""; }
      else field += c;
    }
  }
  out.push(field);
  return out;
}

function normCell(c){ return String(c == null ? "" : c).trim().toLowerCase(); }
function normKey(c){ return normCell(c).replace(/[^a-z0-9]/g, ""); }

function parseCSV(text){
  text = cleanText(text);
  const lines = text.split("\n").filter((l, i, arr) => l.length || i < arr.length - 1);

  // Auto-detect the delimiter from the first non-empty line: most exports
  // are comma-separated, but some locales/tools use semicolon or tab.
  const sampleLine = lines.find(l => l.trim().length) || "";
  const delim = [",", ";", "\t"].reduce((best, d) => (sampleLine.split(d).length > sampleLine.split(best).length ? d : best), ",");

  const rows = lines.map(l => splitCSVLine(l, delim));

  // Google Ads exports lead with a title line ("Search terms report") and
  // a date-range line before the real header row: matching "search term"
  // as a SUBSTRING wrongly picks the title line itself ("Search terms
  // report" contains it). Match on a normalized (letters+digits only)
  // whole-cell comparison instead, and require more than one column (the
  // title/date lines are always single-cell).
  const HEADER_CELLS = ["searchterm", "searchquery", "query", "negativekeyword", "keyword"];
  const isHeaderRow = r => r.some(c => HEADER_CELLS.includes(normKey(c)));
  let headerIdx = rows.findIndex(r => r.length > 1 && isHeaderRow(r));
  if (headerIdx < 0) headerIdx = rows.findIndex(isHeaderRow);

  const header = headerIdx >= 0 ? rows[headerIdx].map(h => String(h).trim()) : [];
  const out = [];
  if (headerIdx >= 0) {
    for (let i = headerIdx + 1; i < rows.length; i++){
      const r = rows[i];
      if (!r.length || (r.length === 1 && !r[0])) continue;
      const obj = {};
      header.forEach((h, j) => obj[h] = r[j] !== undefined ? r[j] : "");
      out.push(obj);
    }
  }
  return { header, rows: out, headerFound: headerIdx >= 0, preview: rows.slice(0, 5), delim };
}

function pick(row, ...names){
  for (const n of names) if (n in row) return row[n];
  const keys = Object.keys(row);
  for (const n of names){
    const k = keys.find(k => k.toLowerCase().replace(/[^a-z0-9]/g,"") === n.toLowerCase().replace(/[^a-z0-9]/g,""));
    if (k) return row[k];
  }
  return "";
}
function num(v){
  if (v == null) return 0;
  const n = parseFloat(String(v).replace(/[,%$]/g, ""));
  return isNaN(n) ? 0 : n;
}
// Conversions are kept as exact decimals for scoring/sorting (impact score,
// tier eligibility), but summing many CSV rows produces floating-point
// noise (171.44999999999999): round only where the number is DISPLAYED.
function fmtConv(v){ return Math.round(Number(v) || 0); }

// =====================================================================
// Shared helpers
// =====================================================================
const ADDRESS_RE = /\b\d{1,6}\s+([a-z0-9]+\s+){1,4}(street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|parkway|pkwy|court|ct|circle|cir|place|pl)\b/i;
const PHONE_RE = /\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b/;
const PPT_RE = /\bppt\b/i;

function normSlug(campaign){
  return String(campaign || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}
function campaignServesCity(slug, territoryMap, term){
  // Returns true if `term` names a city that IS in this campaign's own
  // territory (no mismatch), false if it names a DIFFERENT mapped
  // campaign's city (mismatch), null if the campaign isn't in the map
  // at all (skill's rule: unknown territory -> never flag a mismatch).
  const key = Object.keys(territoryMap).find(k => slug.includes(k));
  if (!key) return null;
  const ownCities = territoryMap[key].map(c => c.toLowerCase());
  const termLc = term.toLowerCase();
  const hitsOwn = ownCities.some(c => termLc.includes(c.toLowerCase()));
  if (hitsOwn) return true;
  for (const [otherKey, cities] of Object.entries(territoryMap)){
    if (otherKey === key) continue;
    if (cities.some(c => termLc.includes(c.toLowerCase()))) return false;
  }
  return null;
}

// =====================================================================
// RICCOBENE ruleset
// =====================================================================
const RICCOBENE = {
  id: "riccobene", label: "Riccobene Associates Family Dentistry",
  note: "Full deterministic port of the <b>sqr-analysis-riccobene</b> skill (brand terms, 90+ named competitors, campaign→territory map, insurance rules, address/phone/ppt patterns). The skill only <i>names</i> (without an exact word list) the Jobs, Education, DIY, generic Price-Seeking, Informational, Veterinary, Dental-Products and Non-Profit disqualifier categories, so those rows are resolved by asking the model per the skill's own category definitions rather than a guessed keyword list.",
  brandRe: /(brush\s*and\s*floss|brush&floss|brushandfloss|riccobene|ricobene|riccoboni|riccobeni|rico\s*bene|commonwealth dentistry)/i,
  competitors: ["Lane & Associates","Village Dental","Aspen Dental","ClearChoice","Tend Dental","Gentle Dental","Great Expressions","Bright Now Dental","Smile Doctors","Heartland Dental","Pacific Dental","Western Dental","Coast Dental","Affordable Dentures","Affordable Dentures & Implants","Smile Direct Club","SmileDirectClub","Byte Aligners","Candid Co","Snap-On Smile","Dental Works","Kool Smiles","Cary Family Dental","Southpoint Dental","Coastal Cosmetic Family Dentistry","Harlow Dental","Forsyth Family Dental","Premier Smile Center","Northeast Family Dentistry","Bumgarner Dental","Hamby Family Dentistry","Stockton Family Dentistry","Fielden Family Dentistry","Triad Dental","Greenway Village Dental","Cameron Village Dental","Farmington Village Dental","Smith Family and Cosmetic Dentistry","Brush Dental Care","Night and Day Dental","Loveland Dental Group","Smile Sensations","Kids Smiles","Kid Smiles","Clemmons Family Dental","Clemmons Comprehensive Dental Care","Clemmons Village Smiles","Winston Endo","Rosewood Dental","Simply Smile","Columbia Smiles","Gee Family & Cosmetic Dentistry","Nuvia","Creston Dental","Webber Dentistry","Pleasant Mill Pediatric","Urban Dental","Apex Dental Rock Hill","Vivid Dental","Lakeside Dental Greensboro","Greensboro Dental Associates","Universal Smiles Kannapolis","Johnston Smiles","Rock Hill Modern Dentistry","Health Spring Dental","Carolina Smile Spa","Premier Smiles Rock Hill","Healthy Smiles Dental","University Dental Associates","University Modern Dentistry","University Pediatric Dentistry","Friendly Dental Group","Friendly Dental University","Spring Valley Dental","Carolina Emergency Dental","Total Smiles","Beach Road Dentistry","Mocksville Dent","Pearly Whites","Lume Pediatric","Flossy Dental","Jason Sain DDS","Dr William Watt","Campbell and Farrelly","Ronald Furman","Blake Nelson DDS","Naomi Lane DDS","Dr Tony Porter","David E Thome DDS","Diksha Katwal","Matthew Barnes DDS","Dr Greg Abrams","Douglas A Stuckey DDS","Dr Reimels","Koelling Johnstun & Associates","F Smith Strickland DDS","Silva and Silva","Dr Hodges","Dr Smith","Dr Green","Dr Oliver"],
  individualRe: /\bdr\.?\s+[a-z]{3,}\b|\b[a-z]{3,}\s+(dds|dmd|pllc)\b/i,
  falsePositiveGuard: [/walk[\s-]?in dentistry/i, /dental urgent care/i, /magnetic dentures cost/i, /child dentist near me/i, /24\s*hour emergency dental clinic/i, /urgent care dental/i],
  medicareRe: /\bmedicare\b/i,
  medicaidRe: /\bmedicaid\b/i,
  insuranceBrands: ["delta dental","deltacare","metlife","cigna","aetna","humana","guardian","spirit dental","united concordia","united healthcare dental","tricare dental","benecare","principal dental"],
  outOfArea: ["boston ma","new york","los angeles","chicago","houston tx","phoenix az","san antonio","san diego","dallas tx","san jose ca","austin tx","jacksonville fl","columbus oh","san francisco","indianapolis","seattle wa","denver co","washington dc","nashville tn","portland or","las vegas nv","louisville ky","memphis tn","baltimore md","richmond va","norfolk va","virginia beach","hampton va","chesapeake va","suffolk va","roanoke va","blacksburg va","charlottesville va","lynchburg va","miami fl","orlando fl","tampa fl","atlanta ga","michigan state","detroit mi"],
  mintHillRe: /mint\s*hill/i, selmaRe: /\bselma\b/i,
  spanishDentalRe: /(dentista|dolor de muela|limpieza dental|ortodoncia|frenillos|implante dental|clinica dental)/i,
  spanishPriceRe: /(no tengo dinero|sin dinero)/i,
  territoryMap: { apex:["Apex"], benson:["Benson","Garner","Goldsboro","Selma"], carywest:["Cary","Apex"], columbia:["Columbia"], concord:["Concord","Harrisburg","Kannapolis"], fortmill:["Fort Mill","Rock Hill"], hampstead:["Hampstead","Wilmington","Porters Neck"], harrisburg:["Harrisburg","Concord"], highpoint:["High Point","Greensboro"], independence:["Independence","Charlotte","Matthews"], knightdale:["Knightdale"], monkeyjunction:["Monkey Junction","Wilmington","Hampstead","Porters Neck"], myrtle:["Myrtle Beach","Myrtle"], portersneck:["Porters Neck","Wilmington","Hampstead"], rockhill:["Rock Hill","Fort Mill"], sixforks:["Six Forks","Raleigh"], southport:["Southport"], university:["University City","Charlotte"], winstonsalem:["Winston-Salem","Clemmons"], mechanicsville:["Mechanicsville","Ashland"] },
  coreSignal: ["dent","tooth","teeth","oral","gum","tmj","tmd","invisalign","braces","implant","veneer","crown","bridge","denture","whitening","extraction","root canal","endodonti","periodonti","prosthodonti","sedation","cosmetic","pediatric","kids dent","family dent","emergency dent","urgent dent","sleep apnea","cleaning","checkup","appt","appointment","dentis"],

  classifyDeterministic(term, campaignSlug){
    const t = term.trim(); if (!t) return null;
    if (this.brandRe.test(t)) return { status: "Relevant", subtype: "Brand" };
    if (this.falsePositiveGuard.some(re => re.test(t))) { /* fall through, never Competitor */ }
    else if (this.competitors.some(c => t.toLowerCase().includes(c.toLowerCase())) || this.individualRe.test(t)) {
      return { status: "Competitor", subtype: "Named competitor / practitioner", negMatchType: "Exact", negLevel: "Account" };
    }
    if (ADDRESS_RE.test(t)) return { status: "Not Relevant", subtype: "Specific Address Query" };
    if (PHONE_RE.test(t)) return { status: "Not Relevant", subtype: "No Clear Dental Intent" };
    if (/\bschool\b/i.test(t) && !/dental school/i.test(t)) return { status: "Not Relevant", subtype: "Education/Training", negText: "dental school", negMatchType: "Phrase", negLevel: "Account" };
    if (PPT_RE.test(t)) return { status: "Not Relevant", subtype: "Informational/Research" };
    if (this.spanishPriceRe.test(t)) return { status: "Not Relevant", subtype: "Price-Seeking" };
    if (this.medicareRe.test(t)) return { status: "Not Relevant", subtype: "Medicare Not Accepted", negText: "medicare", negMatchType: "Phrase", negLevel: "Account" };
    if (this.medicaidRe.test(t)) return { status: "Client Review", subtype: "Medicaid Unconfirmed" };
    if (this.insuranceBrands.some(b => t.toLowerCase().includes(b))) return { status: "Client Review", subtype: "Insurance Network Brand" };
    if (this.mintHillRe.test(t)) return { status: "Client Review", subtype: "Potential New Service Area (Mint Hill NC)" };
    if (this.selmaRe.test(t)) return { status: "Client Review", subtype: "Potential New Service Area (Selma NC)" };
    if (this.spanishDentalRe.test(t)) return { status: "Client Review", subtype: "Bilingual – Confirm Staffing" };
    if (this.outOfArea.some(c => t.toLowerCase().includes(c))) return { status: "Not Relevant", subtype: "Out-of-Area Location", negMatchType: "Phrase", negLevel: "Campaign" };
    const mismatch = campaignServesCity(campaignSlug, this.territoryMap, t);
    if (mismatch === false) return { status: "Not Relevant", subtype: "Location Mismatch" };
    if (this.coreSignal.some(k => t.toLowerCase().includes(k))) return { status: "Relevant", subtype: "Core dental intent" };
    return null; // needs the model
  },
};

// =====================================================================
// OIA ruleset
// =====================================================================
const OIA_FACILITIES = [
  { code:"AMI", re:/\b(advanced medical imaging)\b/i, market:"kokomo|indiana" },
  { code:"Heartland", re:/heartland imaging|hartland imaging/i },
  { code:"NHI", re:/nebraska health imaging|nebreska health imaging/i },
  { code:"OIC-Culpeper", re:/outpatient imaging culpeper/i },
  { code:"ODC-AL", re:/outpatient diagnostic center/i },
  { code:"CPIC", re:/cardinal points? imaging|raleigh radiology|raleighrad/i },
  { code:"IDL-FL", re:/institute of diagnostic imaging/i },
  { code:"SMI", re:/spectrum medical imaging|platte valley medical/i },
  { code:"Holmdel-NJ", re:/holmdel imaging/i },
  { code:"Ft Jesse", re:/ft\.? jesse imaging|gale keeran/i },
  { code:"UD", re:/university diagnostics/i },
  { code:"Wolf River", re:/imaging center at wolf river|wolf river imaging/i },
];
const OIA = {
  id: "oia", label: "Outpatient Imaging Affiliates (OIA)",
  note: "Full deterministic port of the <b>sqr-analysis-oia</b> skill: disqualifiers (jobs/education/reviews/free-seeking) are checked <i>before</i> brand match, per the skill's own explicit ordering, since imaging accounts see real brand+job/review overlap (e.g. ‘heartland imaging reviews’). This skill happened to give concrete example words for nearly every category, so almost all of it runs deterministically; only genuinely unclassifiable leftovers go to the model.",
  jobsRe: /\b(hiring|careers|apply|job opening|salary|radiologic technologist jobs|jobs?)\b/i,
  eduRe: /\b(school|certification|course|how to become a|radiology tech program|training program)\b/i,
  reviewsRe: /\b(reviews?|complaints?|ratings?|scam|ripoff|rip-off|bad experience)\b/i,
  freeRe: /\b(free mri|free ct scan|free mammogram|no cost scan|free imaging)\b/i,
  facilities: OIA_FACILITIES,
  brandGeneric: /(outpatient imaging affiliates|oia rad|oiarad)/i,
  competitors: ["ecommunity.com","Community Health Network","ascension.org","baptisthealth.com","nebraskamed.com","Nebraska Medicine","chihealth.com","CHI Health","americanhealthimaging.com","raleighrad.com","touchstoneimaging.com","thewomensimagingcenter.com","intermountainhealth.org","methodisthealth.org","fauquierhealth.org","amimaging.com","uvahealth.com","osfhealthcare.org","RadNet","SimonMed","SimonMed Imaging","US Radiology Specialists","Akumin","Alliance HealthCare Radiology","InSight Imaging","Zwanger-Pesiri","Envision Radiology","Solis Mammography","US Imaging Network"],
  falsePositiveGuard: [/imaging center near me/i, /walk-?in mri/i, /open mri near me/i, /diagnostic center near me/i, /radiology clinic near me/i],
  infoRe: /\b(what is|how does .* work|procedures|side effects of)\b/i,
  vetRe: /\b(dog|cat|pet|vet)\b.*\b(mri|ct|scan|imaging|x-?ray|ultrasound)\b|\b(mri|ct|scan|imaging|x-?ray|ultrasound)\b.*\b(dog|cat|pet|vet)\b/i,
  medicareRe: /\bmedicare\b/i, medicaidRe: /\bmedicaid\b/i,
  insuranceBrands: ["aetna","cigna","blue cross","unitedhealthcare","united healthcare","humana"],
  outOfArea: ["los angeles ca","new york ny","houston tx","phoenix az","san antonio tx","san diego ca","dallas tx","san jose ca","austin tx","san francisco ca","seattle wa","portland or","las vegas nv","boston ma","miami fl","atlanta ga","detroit mi"],
  territoryMap: { ami:["Kokomo"], heartland:["Louisville","Elizabethtown"], nhi:["Omaha"], culpeper:["Culpeper"], odcal:["Huntsville","Madison","Athens","Decatur"], cpic:["Raleigh","Cary","Wake Forest","Clayton"], idl:["Fort Walton Beach"], smi:["Brighton","Denver"], holmdel:["Holmdel"], ftjesse:["Normal","Bloomington"], ud:["Knoxville","Halls","Northshore"], wolfriver:["Germantown","Memphis"] },
  coreSignal: ["mri","open mri","ct scan","cat scan","mammogram","mammography","3d mammogram","ultrasound","sonogram","x-ray","xray","bone density","dexa scan","women's imaging","fluoroscopy","pet scan","pet/ct","nuclear medicine","arthrogram","biopsy","radiology","diagnostic imaging","imaging center","schedule a scan","mri appointment"],

  classifyDeterministic(term, campaignSlug){
    const t = term.trim(); if (!t) return null;
    if (this.jobsRe.test(t)) return { status: "Not Relevant", subtype: "Jobs/Recruitment", negMatchType: "Phrase", negLevel: "Account" };
    if (this.eduRe.test(t)) return { status: "Not Relevant", subtype: "Education/Training", negMatchType: "Phrase", negLevel: "Account" };
    if (this.reviewsRe.test(t)) return { status: "Not Relevant", subtype: "Reviews/Reputation", negMatchType: "Phrase", negLevel: "Account" };
    if (this.freeRe.test(t)) return { status: "Not Relevant", subtype: "Free-Seeking", negMatchType: "Phrase", negLevel: "Account" };

    // AMI ambiguity: only OIA brand with Kokomo/Indiana context. A bare
    // "AMI" with no location, or paired with a different city/state, or
    // the named competitor domain, is the UNRELATED national brand
    // (Atlantic Medical Imaging etc.): per the skill, that falls through
    // to Competitor, never a clean core-imaging "Relevant".
    const amiMatch = /\bami\b/i.test(t);
    if (amiMatch || /amimaging\.com/i.test(t)) {
      return /(kokomo|indiana)/i.test(t)
        ? { status: "Relevant", subtype: "Brand (AMI: Kokomo)" }
        : { status: "Competitor", subtype: "AMI Imaging (unrelated national brand)", negMatchType: "Exact", negLevel: "Account" };
    }

    for (const f of this.facilities){ if (f.code !== "AMI" && f.re.test(t)) return { status: "Relevant", subtype: "Brand (" + f.code + ")" }; }
    if (this.brandGeneric.test(t)) return { status: "Relevant", subtype: "Brand (OIA corporate)" };

    // JV same-name caution (Section 5a)
    if (/uva health/i.test(t)) return /culpeper/i.test(t) ? { status: "Relevant", subtype: "Brand (OIC-Culpeper via UVA Health JV)" } : { status: "Competitor", subtype: "UVA Health (non-Culpeper market)", negMatchType: "Exact", negLevel: "Account" };
    if (/osf ?healthcare|osf imaging/i.test(t)) return /(bloomington|normal)/i.test(t) ? { status: "Relevant", subtype: "Brand (Ft Jesse via OSF JV)" } : { status: "Competitor", subtype: "OSF HealthCare (non-Bloomington/Normal market)", negMatchType: "Exact", negLevel: "Account" };

    if (/gearstar/i.test(t)) return { status: "Not Relevant", subtype: "Unverified Term: flag for client confirmation" };
    if (this.falsePositiveGuard.some(re => re.test(t))) { /* never Competitor */ }
    else if (this.competitors.some(c => t.toLowerCase().includes(c.toLowerCase()))) {
      return { status: "Competitor", subtype: "Named competitor / health system", negMatchType: "Exact", negLevel: "Account" };
    }
    if (ADDRESS_RE.test(t)) return { status: "Not Relevant", subtype: "Specific Address Query" };
    if (PHONE_RE.test(t)) return { status: "Not Relevant", subtype: "Specific Phone Query" };
    if (this.infoRe.test(t)) return { status: "Not Relevant", subtype: "Informational/Research" };
    if (this.vetRe.test(t)) return { status: "Not Relevant", subtype: "Veterinary/Non-Human" };
    if (this.medicareRe.test(t) || this.medicaidRe.test(t)) return { status: "Client Review", subtype: "Insurance Coverage Unconfirmed" };
    if (this.insuranceBrands.some(b => t.toLowerCase().includes(b))) return { status: "Client Review", subtype: "Insurance Network Unconfirmed" };
    if (this.outOfArea.some(c => t.toLowerCase().includes(c))) return { status: "Not Relevant", subtype: "Out-of-Area Location", negMatchType: "Phrase", negLevel: "Campaign" };
    const mismatch = campaignServesCity(campaignSlug, this.territoryMap, t);
    if (mismatch === false) return { status: "Not Relevant", subtype: "Location Mismatch" };
    if (this.coreSignal.some(k => t.toLowerCase().includes(k))) return { status: "Relevant", subtype: "Core imaging intent" };
    return null;
  },
};

// =====================================================================
// EVENTGROOVE ruleset: ported from the client-supplied classifier prompt
// =====================================================================
const EVENTGROOVE = {
  id: "eventgroove", label: "Eventgroove",
  note: "Ported from the client-supplied Eventgroove classifier prompt. <b>One contradiction flagged in the source</b>, worth confirming with the client: ‘tour credentials’ is listed both as an explicit Relevant example (product list) and as a Not-Relevant ‘excluded pattern’ example in the prompt's own top definition. Resolved here as <b>Relevant</b>, matching the more specific numbered list.",
  brandRe: /(event\s*groove|eventgrooves?|event\s*grove|groove\s*events?)/i,
  productPhrases: ["cheap raffle tickets","credential printing","backstage pass printing","tradeshow lanyards","concert laminates","vip lanyards","plastic event badges","chinese auction tickets","lanyard printing","tour credentials","vip cards","arm bands for events","band laminates","laminate passes","custom lanyards","tour laminate printing","printing credentials","event lanyards","vendor badges","ticket printers","name tag printing","pvc badges","festival ticketing"],
  competitors: ["gearstar","uline","uprinting.com","uprinting","amazon.com","ticketprint.com","vistaprint.com","vistaprint","eventbrite.com","eventbrite"],
  notRelevantExact: ["silicone"],
  reviewsRe: /\b(reviews?|complaints?|ratings?|scam|ripoff|rip-off|bad experience)\b/i,
  jobsRe: /\b(careers?|how to design a? ?ticket)\b/i,
  freeRe: /\b(free printing|free event tickets?|free raffle tickets?|buy event tickets?|giveaway|free)\b/i,
  irrelevantRe: /\b(fundraising|saas)\b/i,
  nearMeRe: /\bnear me\b/i,
  coreSignal: ["ticket","raffle","banner","poster","flyer","wristband","certificate","badge","lanyard","credential","laminate","yard sign","directional sign"],

  classifyDeterministic(term){
    const t = term.trim(); if (!t) return null;
    const tl = t.toLowerCase();
    if (this.brandRe.test(t)) return { status: "Relevant", subtype: "Brand" };
    if (this.competitors.some(c => tl.includes(c))) return { status: "Competitor", subtype: "Named competitor", negMatchType: "Exact", negLevel: "Account" };
    if (this.notRelevantExact.some(c => tl.includes(c))) return { status: "Not Relevant", subtype: "Excluded term" };
    if (this.reviewsRe.test(t)) return { status: "Not Relevant", subtype: "Reviews/Reputation", negMatchType: "Phrase", negLevel: "Account" };
    if (this.jobsRe.test(t)) return { status: "Not Relevant", subtype: "Jobs/Training", negMatchType: "Phrase", negLevel: "Account" };
    if (this.freeRe.test(t)) return { status: "Not Relevant", subtype: "Promotions/Freebies", negMatchType: "Phrase", negLevel: "Account" };
    if (this.irrelevantRe.test(t)) return { status: "Not Relevant", subtype: "Irrelevant Service" };
    if (this.productPhrases.some(p => tl.includes(p))) return { status: "Relevant", subtype: "Named product/service" };
    if (this.coreSignal.some(k => tl.includes(k))) return { status: "Relevant", subtype: "Core product intent" };
    if (this.nearMeRe.test(t)) return { status: "Not Relevant", subtype: "Location-based (near me), no product context" };
    return null;
  },
};

// =====================================================================
// 42 NORTH / GENTLE DENTAL ruleset: ported from the client-supplied prompt
// =====================================================================
const GENTLEDENTAL_42NORTH = {
  id: "gentledental_42north", label: "42 North (Gentle Dental)",
  note: "Ported from the client-supplied 42 North / Gentle Dental classifier prompt. <b>Two contradictions flagged in the source</b>, worth confirming with the client: (1) ‘first dental’ appears both as a generic-Relevant example and, separately, as an explicit Not-Relevant trigger: resolved here as <b>Not Relevant</b> (the more specific, later rule). (2) ‘Montshire Pediatric Dentistry Keene’ and ‘perfect dental’ appear both as generic-Relevant examples and as explicit Competitor examples: resolved as <b>Competitor</b>, since they read as named practices. ‘blanqueamiento dental’ (Spanish for teeth whitening) sits in that same Competitor list despite being a generic service term, not a brand: resolved as <b>Relevant</b>. This account also treats insurance/cost mentions as a flat Relevant, not Client Review, per its own prompt.",
  brandRe: /(gentle\s*dental|dental\s*gentle)/i,
  competitors: ["aspen dental","pacific dental","montshire pediatric dentistry keene","perfect dental"],
  firstDentalRe: /\bfirst dental\b/i,
  insuranceRe: /\b(medicaid|delta dental|aetna|cigna|altus|dental providers?)\b/i,
  costRe: /\b(cheap|low[\s-]?cost|cost|price|fees?)\b/i,
  disqualifyRe: /\b(free|hiring|careers?|jobs?|dental school|hygienist program|dental supplies|dental tools|toothbrush|toothpaste)\b/i,
  coreSignal: ["dentist","dental","tooth","teeth","cleaning","implant","invisalign","braces","whitening","veneer","extraction","crown","denture","oral surgery","periodont","pediatric dent","emergency dent","urgent care dent","root canal","orthodont"],

  classifyDeterministic(term){
    const t = term.trim(); if (!t) return null;
    const tl = t.toLowerCase();
    if (this.brandRe.test(t)) return { status: "Relevant", subtype: "Brand" };
    if (this.firstDentalRe.test(t)) return { status: "Not Relevant", subtype: "Excluded term (first dental)" };
    if (this.competitors.some(c => tl.includes(c))) return { status: "Competitor", subtype: "Named competitor practice", negMatchType: "Exact", negLevel: "Account" };
    if (this.disqualifyRe.test(t)) return { status: "Not Relevant", subtype: "Free/Jobs/Education/Products" };
    if (this.insuranceRe.test(t)) return { status: "Relevant", subtype: "Insurance/provider" };
    if (this.costRe.test(t)) return { status: "Relevant", subtype: "Cost/price intent" };
    if (this.coreSignal.some(k => tl.includes(k))) return { status: "Relevant", subtype: "Core dental intent" };
    return null;
  },
};

// =====================================================================
// MCHALE LANDSCAPE DESIGN ruleset: standard/universal SQR structure
// (same shape as OIA), built from the client's zip-targeting file + the
// account's three landing-page URLs rather than a written prompt.
// =====================================================================
const MCHALE = {
  id: "mchale", label: "McHale Landscape Design",
  note: "Universal SQR structure (same shape as OIA) for a DC-metro landscaping company. Service area confirmed from the client's own zip-targeting file: 120 zip codes split across two campaign ‘Sets’ (A/B), all within DC/Maryland/Northern Virginia: but the two sets have essentially the SAME state mix (no geographic boundary between them), so there's no real per-campaign territory to build a location-mismatch map from; only a broad DMV out-of-area check runs here. <b>Competitor list researched, not client-supplied</b>: none was given for this account, so this pulls real design-build/luxury landscape firms confirmed (via web search) to actually serve the same DC/MD/VA zip codes McHale targets, spanning both the Northern Virginia side (Rossen, Sisson, Sebastian, Great Falls Landscapes, Fine Landscapes, Wheat's, Fine Garden Design, Moody Graham, Craig Richmond, Ed Ball, Outdoor Spaces, Anthony Wilder) and the Maryland side near McHale's own Upper Marlboro/Annapolis base (BR Design Build, Maryland Lawn & Landscape, Luxe Landscape Group, Garcia Patios and Landscaping). Worth a client/AM sanity check since it's research-assembled, not client-confirmed. Bare ‘McHale’ requires landscaping/garden context before counting as brand, since McHale Manufacturing (agricultural equipment, unrelated) is a real different company that shares the name.",
  brandCoreRe: /mc\s*hale/i,
  landscapeContextRe: /\b(landscap\w*|garden\w*|lawn|yard|hardscape\w*|patio|nursery)\b/i,
  competitors: ["rossen landscape","sisson landscape","sebastian design build","great falls landscape","fine landscapes","wheat's landscape","wheats landscape","fine garden design","moody graham","joshua schaaf","native plant landscape design","craig richmond landscape","ed ball landscape","outdoor spaces","anthony wilder","br design build","br designbuild","maryland lawn & landscape","maryland lawn and landscape","luxe landscape group","garcia patios and landscaping"],
  jobsRe: /\b(hiring|careers?|jobs?|apply|salary)\b/i,
  eduRe: /\b(school|certification|course|how to become a|training program|degree)\b/i,
  reviewsRe: /\b(reviews?|complaints?|ratings?|scam|ripoff|rip-off|bad experience)\b/i,
  diyRe: /\bdiy\b|\bhow to (build|install|plant|make)\b/i,
  outOfArea: ["los angeles ca","new york ny","chicago il","houston tx","phoenix az","san antonio tx","san diego ca","dallas tx","san jose ca","austin tx","miami fl","atlanta ga","boston ma","seattle wa","denver co","detroit mi","philadelphia pa","charlotte nc","san francisco ca"],
  coreSignal: ["landscap","hardscape","hardscaping","lawn care","lawn maintenance","lawn service","garden design","gardening","fine gardening","design build","design and build","patio design","paver patio","retaining wall","outdoor living","outdoor kitchen","fire pit","pergola","walkway","planting design","tree service","irrigation","drainage system","mulch","sod installation","yard design","backyard design","landscape contractor","landscape architect","landscape maintenance","landscape lighting","nursery"],

  classifyDeterministic(term){
    const t = term.trim(); if (!t) return null;
    const tl = t.toLowerCase();
    if (this.brandCoreRe.test(t)) {
      return this.landscapeContextRe.test(t) ? { status: "Relevant", subtype: "Brand" } : null; // bare "McHale" is ambiguous: let the model decide
    }
    if (this.competitors.some(c => tl.includes(c))) return { status: "Competitor", subtype: "Named DMV-area design/landscape competitor", negMatchType: "Exact", negLevel: "Account" };
    if (this.jobsRe.test(t)) return { status: "Not Relevant", subtype: "Jobs/Recruitment", negMatchType: "Phrase", negLevel: "Account" };
    if (this.eduRe.test(t)) return { status: "Not Relevant", subtype: "Education/Training", negMatchType: "Phrase", negLevel: "Account" };
    if (this.reviewsRe.test(t)) return { status: "Not Relevant", subtype: "Reviews/Reputation", negMatchType: "Phrase", negLevel: "Account" };
    if (this.diyRe.test(t)) return { status: "Not Relevant", subtype: "DIY/Home Project" };
    if (this.outOfArea.some(c => tl.includes(c))) return { status: "Not Relevant", subtype: "Out-of-Area Location", negMatchType: "Phrase", negLevel: "Campaign" };
    if (this.coreSignal.some(k => tl.includes(k))) return { status: "Relevant", subtype: "Core landscaping intent" };
    return null;
  },
};

// =====================================================================
// BETA BIONICS ruleset: PLACEHOLDER, not a port of the real client skill.
// The sqr-analysis-beta-bionics skill is confirmed to exist and be enabled
// on the account, but this session's Skill tool cannot reach it (Unknown
// skill error, retried after a session refresh) and it isn't synced to
// local disk the way Riccobene/OIA's skill files were. Rather than guess
// at its actual priority waterfall, known-issue log, or exact word lists,
// this ruleset only encodes facts independently verifiable from the
// public iLet Bionic Pancreas product record; everything else defers to
// the model. Replace this once the real skill text is available.
// =====================================================================
const BETA_BIONICS = {
  id: "beta_bionics", label: "Beta Bionics (iLet Bionic Pancreas)",
  note: "<b>Placeholder, not yet ported from the client\'s own SQR skill.</b> It encodes only public facts about the product: the iLet Bionic Pancreas is FDA-cleared for <b>type 1</b> diabetes, ages 6+, US only, and its named competitors are Omnipod/Insulet, Tandem (t:slim X2, Control-IQ), Medtronic MiniMed and twiist. Type 2 diabetes and Medicare/Medicaid mentions go to <b>Client Review</b> rather than being guessed. Everything else goes to the model with no account-specific knowledge. Share the skill\'s content and this gets a real port, as Riccobene and OIA did.",
  brandRe: /(beta\s*bionics|\bilet\b|bionic\s*pancreas)/i,
  competitors: ["omnipod","insulet","tandem diabetes","tandem t:slim","t:slim x2","control-iq","medtronic minimed","minimed 780g","twiist"],
  type2Re: /\btype\s*(2|ii|two)\s*diabetes\b/i,
  medicareRe: /\bmedicare\b/i, medicaidRe: /\bmedicaid\b/i,
  outOfCountry: ["uk","united kingdom","canada","australia","ireland","germany","france","india","mexico","new zealand"],
  coreSignal: ["insulin pump","automated insulin delivery","aid system","artificial pancreas","closed loop insulin","closed-loop system","bionic pancreas","ilet","insulin delivery system","tubeless insulin pump","no carb counting","meal announcement free","type 1 diabetes","t1d management","diabetes management system"],

  classifyDeterministic(term){
    const t = term.trim(); if (!t) return null;
    const tl = t.toLowerCase();
    if (this.brandRe.test(t)) return { status: "Relevant", subtype: "Brand" };
    if (this.competitors.some(c => tl.includes(c))) return { status: "Competitor", subtype: "Named insulin delivery competitor", negMatchType: "Exact", negLevel: "Account" };
    if (this.type2Re.test(t)) return { status: "Client Review", subtype: "Off-Label Interest (iLet is Type 1 only): confirm handling with client" };
    if (this.outOfCountry.some(c => tl.includes(c))) return { status: "Not Relevant", subtype: "Out-of-Area (US-only product)", negMatchType: "Phrase", negLevel: "Account" };
    if (this.medicareRe.test(t) || this.medicaidRe.test(t)) return { status: "Client Review", subtype: "Insurance Coverage Unconfirmed" };
    if (this.coreSignal.some(k => tl.includes(k))) return { status: "Relevant", subtype: "Core product intent" };
    return null;
  },
};

// =====================================================================
// Generic fallback (no dedicated ruleset yet)
// =====================================================================
const GENERIC = {
  id: "generic", label: "Other / not yet mapped",
  note: "No dedicated ruleset for this account yet: every row is classified by the model with a plain SQR rubric (Relevant / Competitor / Not Relevant / Client Review), no client-specific brand, competitor, or territory knowledge. Add a real ruleset for this account the same way the others were done, once its own SQR conventions are known.",
  classifyDeterministic(){ return null; },
};

const RULESETS = { riccobene: RICCOBENE, oia: OIA, eventgroove: EVENTGROOVE, gentledental_42north: GENTLEDENTAL_42NORTH, mchale: MCHALE, beta_bionics: BETA_BIONICS, generic: GENERIC };
// =====================================================================
// The model step: only the terms no rule settled, in batches, to the
// platform's own route. The categories each account is judged against
// live server-side (tracker/paid_sqr.py); this sends the ruleset id only.
// =====================================================================
const CLASSIFY_URL = "/p2/paid-marketing/api/sqr/classify";
const BATCH = 40, PARALLEL = 3;
function yieldToUI(){ return new Promise(r => setTimeout(r, 0)); }

async function classifyBatch(rulesetId, batch){
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 90000);
  try {
    const res = await fetch(CLASSIFY_URL, { method: "POST", credentials: "same-origin", signal: ctl.signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ruleset: rulesetId, terms: batch }) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || ("classify answered " + res.status));
    return j.results || {};
  } finally { clearTimeout(t); }
}

async function classifyPending(ruleset, terms, progress){
  const out = {}, failures = [];
  const batches = [];
  for (let i = 0; i < terms.length; i += BATCH) batches.push(terms.slice(i, i + BATCH));
  let done = 0, next = 0;
  async function worker(){
    while (next < batches.length){
      const b = batches[next++];
      try { Object.assign(out, await classifyBatch(ruleset.id, b)); }
      catch (e) { failures.push(e.message || String(e)); }
      done += b.length;
      progress(done, terms.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(PARALLEL, batches.length) }, worker));
  let fallback = 0;
  terms.forEach(t => {
    if (!out[t]) { out[t] = { status: "Not Relevant", subtype: "No Clear Intent", by: "fallback" }; fallback++; }
    else if (out[t].by === "fallback") fallback++;
  });
  return { results: out, fallback, failures };
}

// =====================================================================
// Pipeline (unchanged from Agent Holmes apart from the model call)
// =====================================================================
let lastResult = null;

async function runAnalysis(ruleset, termsCsv, keywordsCsv, negsCsv, status){
  status("Reading the files…", 0.03);
  const termsParsed = parseCSV(termsCsv);
  await yieldToUI();
  if (!termsParsed.headerFound) {
    const preview = termsParsed.preview.map(r => "[" + r.join(" | ") + "]").join("  ");
    throw new Error("Couldn't find a 'Search term' column in that file. First few lines: " + preview);
  }
  const existingKeywords = new Set(), existingNegs = new Set();
  if (keywordsCsv) parseCSV(keywordsCsv).rows.forEach(r => {
    const kw = String(pick(r, "Keyword")).trim().toLowerCase().replace(/[\[\]"]/g, ""); if (kw) existingKeywords.add(kw); });
  if (negsCsv) parseCSV(negsCsv).rows.forEach(r => {
    const kw = String(pick(r, "Negative keyword", "Keyword")).trim().toLowerCase().replace(/[\[\]"]/g, ""); if (kw) existingNegs.add(kw); });

  // Chunked, yielding to the browser, so a big export never freezes the tab.
  const rows = [], pendingTerms = new Set(), CHUNK = 1500, N = termsParsed.rows.length;
  for (let i = 0; i < N; i++){
    const raw = termsParsed.rows[i];
    const term = String(pick(raw, "Search term", "Search Term")).trim();
    if (term && !/^total:/i.test(term)){
      const r = { term, campaign: String(pick(raw, "Campaign")).trim(), adGroup: String(pick(raw, "Ad group", "Ad Group")).trim(),
        campaignType: String(pick(raw, "Campaign type", "Campaign Type")).trim(),
        clicks: num(pick(raw, "Clicks")), impressions: num(pick(raw, "Impr.", "Impressions")),
        cost: num(pick(raw, "Cost")), conversions: num(pick(raw, "Conversions", "Conv.")) };
      r._slug = normSlug(r.campaign);
      r._det = ruleset.classifyDeterministic(r.term, r._slug);
      if (r._det) r._det.by = "rule"; else pendingTerms.add(r.term);
      rows.push(r);
    }
    if (i > 0 && i % CHUNK === 0){ status("Applying " + ruleset.label + " rules… " + i.toLocaleString() + " / " + N.toLocaleString(), 0.05 + 0.25 * i / N); await yieldToUI(); }
  }
  if (!rows.length) throw new Error("Found a 'Search term' column, but every row was empty or a 'Total:' subtotal. Columns found: " + termsParsed.header.join(", "));

  const pending = Array.from(pendingTerms);
  status(pending.length ? "Asking the model about " + pending.length.toLocaleString() + " terms no rule settled…" : "Every term was settled by a rule.", 0.32);
  const llm = await classifyPending(ruleset, pending, (d, n) => status("Model: " + d.toLocaleString() + " / " + n.toLocaleString() + " ambiguous terms", 0.32 + 0.6 * d / n));
  rows.forEach(r => { if (!r._det) r._det = llm.results[r.term]; });

  status("Building negatives, keywords and the summary…", 0.95);
  const negMap = new Map();
  rows.forEach(r => {
    const d = r._det;
    if (d.status === "Competitor" || (d.status === "Not Relevant" && d.negMatchType)) {
      const negText = (d.negText || r.term).toLowerCase();
      if (existingNegs.has(negText)) return;
      const level = d.negLevel === "Campaign" && /pmax|performance max/i.test(r.campaignType) ? "Account (PMax)" : (d.negLevel || "Account");
      const key = negText + "|" + level + "|" + (d.negMatchType || "Exact");
      const impact = r.cost + 5 * r.conversions;
      if (!negMap.has(key)) negMap.set(key, { text: negText, level, matchType: d.negMatchType || "Exact", reason: d.subtype, impact, cost: r.cost, conversions: r.conversions, campaign: r.campaign });
      else { const ex = negMap.get(key); ex.impact += impact; ex.cost += r.cost; ex.conversions += r.conversions; }
    }
  });
  const allNegatives = Array.from(negMap.values()).sort((a, b) => b.impact - a.impact);

  const kwMap = new Map();
  rows.forEach(r => {
    const d = r._det; if (d.status !== "Relevant") return;
    const norm = r.term.toLowerCase(); if (existingKeywords.has(norm)) return;
    if (!(r.conversions > 0 || (r.impressions > 0 && r.clicks / r.impressions >= 0.02) || r.clicks >= 3)) return;
    const isPmax = /pmax|performance max/i.test(r.campaignType);
    const ctr = r.impressions > 0 ? r.clicks / r.impressions : 0;
    const wordCount = r.term.trim().split(/\s+/).length;
    const matchType = isPmax ? "Search Theme" : (wordCount >= 3 && (r.conversions > 0 || ctr >= 0.05)) ? "Exact Match" : "Phrase Match";
    const tier = r.conversions > 0 ? 1 : (r.clicks >= 3 && ctr >= 0.05 ? 2 : 0);
    if (tier === 0) return;
    const key = norm + "|" + r.campaign;
    const row = { term: r.term, assetType: isPmax ? "Search Theme" : "Keyword", matchType, addLevel: isPmax ? "Asset Group" : "Ad Group",
      whereToAdd: r.campaign + (isPmax ? "" : " / " + r.adGroup), tier, conversions: r.conversions, ctr, clicks: r.clicks, cost: r.cost };
    if (!kwMap.has(key) || kwMap.get(key).conversions < row.conversions) kwMap.set(key, row);
  });
  const allKeywords = Array.from(kwMap.values()).sort((a, b) => (a.tier - b.tier) || (b.conversions - a.conversions) || (b.ctr - a.ctr));

  const review = {};
  rows.filter(r => r._det.status === "Client Review").forEach(r => {
    const k = r._det.subtype; if (!review[k]) review[k] = { subtype: k, count: 0, examples: [] };
    review[k].count++; if (review[k].examples.length < 5) review[k].examples.push(r.term);
  });
  const byStatus = rows.reduce((acc, r) => { acc[r._det.status] = (acc[r._det.status] || 0) + 1; return acc; }, {});
  const byRule = rows.filter(r => r._det.by === "rule").length;
  return {
    ruleset, rows, allNegatives, priorityNegatives: allNegatives.slice(0, 30), allKeywords, priorityKeywords: allKeywords.slice(0, 30),
    clientReview: Object.values(review).sort((a, b) => b.count - a.count),
    summary: { totalRows: rows.length, totalSpend: rows.reduce((s, r) => s + r.cost, 0), totalConv: rows.reduce((s, r) => s + r.conversions, 0),
      byStatus, byRule, byModel: rows.length - byRule - llm.fallback, fallback: llm.fallback, failures: llm.failures,
      relevantPct: Math.round(100 * (byStatus["Relevant"] || 0) / rows.length) },
  };
}

// =====================================================================
// UI
// =====================================================================
function esc(s){ return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
const $id = id => document.getElementById(id);
const files = { terms: null, keywords: null, negs: null };
let current = "riccobene", orb = null;

const ACCT_HINT = { riccobene: "Dental · 20 locations", oia: "Imaging · 12 facilities", eventgroove: "Event printing",
  gentledental_42north: "Dental", mchale: "Landscaping · DC metro", beta_bionics: "Medical device · placeholder", generic: "Any account, no rules" };
const ACCT_HUE = { riccobene: 190, oia: 255, eventgroove: 320, gentledental_42north: 160, mchale: 120, beta_bionics: 280, generic: 220 };
function initials(label){ return label.replace(/\(.*?\)/g, "").split(/\s+/).filter(w => /^[A-Za-z0-9]/.test(w)).slice(0, 2).map(w => w[0]).join("").toUpperCase(); }
function badge(id){
  return id === "generic" ? '<span class="badge pm-pill mute">Model only</span>'
    : id === "beta_bionics" ? '<span class="badge pm-pill unclear">Partial</span>' : '<span class="badge pm-pill ok">Rules</span>';
}
function avatar(rs){ return '<span class="sq-av" style="--h:' + (ACCT_HUE[rs.id] || 200) + '">' + esc(initials(rs.label)) + '</span>'; }

/* ── Account dropdown ───────────────────────────────────────────── */
const dd = $id("dd"), ddBtn = $id("ddBtn"), ddMenu = $id("ddMenu"), ddList = $id("ddList"), ddSearch = $id("ddSearch");
let hl = 0, shown = [];
try { const saved = localStorage.getItem("sqr_account"); if (saved && RULESETS[saved]) current = saved; } catch (e) {}
const qAccount = new URLSearchParams(location.search).get("account");
if (qAccount && RULESETS[qAccount]) current = qAccount;

function paintButton(){
  const rs = RULESETS[current];
  ddBtn.innerHTML = avatar(rs) + '<span class="sq-dd-txt"><span class="nm">' + esc(rs.label) + '</span><span class="ds">' +
    esc(ACCT_HINT[rs.id] || "") + '</span></span>' + badge(rs.id) +
    '<span class="sq-chev"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></span>';
  $id("rulesetNote").innerHTML = rs.note;
}
function paintList(){
  const q = ddSearch.value.trim().toLowerCase();
  shown = Object.values(RULESETS).filter(rs => !q || (rs.label + " " + (ACCT_HINT[rs.id] || "")).toLowerCase().includes(q));
  if (hl >= shown.length) hl = Math.max(0, shown.length - 1);
  ddList.innerHTML = shown.length ? shown.map((rs, i) =>
    '<button type="button" role="option" class="sq-opt' + (i === hl ? " hl" : "") + '" data-id="' + rs.id + '" aria-selected="' + (rs.id === current) +
      '" style="animation-delay:' + (i * 25) + 'ms">' + avatar(rs) + '<span class="sq-dd-txt"><span class="nm" style="font-size:13.5px">' + esc(rs.label) +
      '</span><span class="ds">' + esc(ACCT_HINT[rs.id] || "") + '</span></span>' + badge(rs.id) + '<span class="tick">✓</span></button>').join("")
    : '<div class="sq-none">No account matches “' + esc(q) + '”.</div>';
}
function openMenu(){
  dd.classList.add("open"); ddMenu.hidden = false; ddBtn.setAttribute("aria-expanded", "true");
  ddSearch.value = ""; hl = Math.max(0, Object.keys(RULESETS).indexOf(current)); paintList();
  setTimeout(() => ddSearch.focus(), 20);
}
function closeMenu(focus){
  dd.classList.remove("open"); ddMenu.hidden = true; ddBtn.setAttribute("aria-expanded", "false");
  if (focus) ddBtn.focus();
}
function choose(id){
  if (!RULESETS[id]) return;
  current = id; try { localStorage.setItem("sqr_account", id); } catch (e) {}
  paintButton(); closeMenu(true);
  ddBtn.animate([{ transform: "scale(.98)" }, { transform: "scale(1.01)" }, { transform: "none" }], { duration: 320, easing: "ease-out" });
}
ddBtn.addEventListener("click", () => ddMenu.hidden ? openMenu() : closeMenu());
ddList.addEventListener("click", e => { const o = e.target.closest(".sq-opt"); if (o) choose(o.dataset.id); });
ddSearch.addEventListener("input", () => { hl = 0; paintList(); });
ddSearch.addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { e.preventDefault(); hl = Math.min(shown.length - 1, hl + 1); paintList(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); hl = Math.max(0, hl - 1); paintList(); }
  else if (e.key === "Enter") { e.preventDefault(); if (shown[hl]) choose(shown[hl].id); }
  else if (e.key === "Escape") { e.preventDefault(); closeMenu(true); }
});
ddBtn.addEventListener("keydown", e => { if (e.key === "ArrowDown" && ddMenu.hidden) { e.preventDefault(); openMenu(); } });
document.addEventListener("click", e => { if (!dd.contains(e.target) && !ddMenu.hidden) closeMenu(); });
$id("rulesBtn").addEventListener("click", () => {
  const n = $id("rulesetNote"), open = n.hidden;
  n.hidden = !open; $id("rulesBtn").setAttribute("aria-expanded", String(open));
});

/* ── Drop zones ────────────────────────────────────────────────── */
const meta = { terms: null, keywords: null, negs: null };
function countRows(text, key){
  const p = parseCSV(text);
  if (!p.headerFound) return { ok: false, n: 0 };
  const name = key === "terms" ? ["Search term", "Search Term"] : key === "negs" ? ["Negative keyword", "Keyword"] : ["Keyword"];
  const n = p.rows.filter(r => { const v = String(pick(r, ...name)).trim(); return v && !/^total:/i.test(v); }).length;
  return { ok: n > 0, n };
}
function sparkle(el){
  if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const r = el.getBoundingClientRect(), colors = ["#22d3ee", "#818cf8", "#34d399", "#fbbf24"];
  for (let i = 0; i < 14; i++){
    const s = document.createElement("span"); s.className = "sq-spark";
    const a = Math.PI * 2 * i / 14, d = 60 + Math.random() * 60;
    s.style.left = (r.width / 2) + "px"; s.style.top = (r.height / 2) + "px";
    s.style.setProperty("--dx", Math.cos(a) * d + "px"); s.style.setProperty("--dy", Math.sin(a) * d + "px");
    s.style.setProperty("--c", colors[i % colors.length]);
    el.appendChild(s); setTimeout(() => s.remove(), 900);
  }
}
const UNIT = { terms: "search terms", keywords: "keywords", negs: "negatives" };
function wireDrop(key){
  const input = $id("file-" + key), drop = $id("drop-" + key), chip = $id("file-" + key + "-name");
  const take = f => {
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      const c = countRows(reader.result, key);
      drop.classList.remove("bad");
      if (!c.ok){
        files[key] = null; meta[key] = null;
        drop.classList.add("filled", "bad");
        chip.innerHTML = '<span class="fn">' + esc(f.name) + '</span><span class="fm">no ' + UNIT[key] + ' column found</span><button type="button" class="x" aria-label="Remove file">×</button>';
      } else {
        files[key] = reader.result; meta[key] = c.n;
        drop.classList.add("filled");
        chip.innerHTML = '<span class="fn">' + esc(f.name) + '</span><span class="fm">' + c.n.toLocaleString() + ' ' + UNIT[key] + '</span><button type="button" class="x" aria-label="Remove file">×</button>';
        sparkle(drop);
      }
      refreshReady();
    };
    reader.readAsText(f);
  };
  input.addEventListener("change", () => take(input.files[0]));
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", e => { if (!drop.contains(e.relatedTarget)) drop.classList.remove("drag"); });
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("drag"); document.body.classList.remove("sq-dragging"); take(e.dataTransfer.files[0]); });
  chip.addEventListener("click", e => {
    if (!e.target.closest(".x")) return;
    e.preventDefault(); e.stopPropagation();
    files[key] = null; meta[key] = null; input.value = ""; chip.innerHTML = "";
    drop.classList.remove("filled", "bad"); refreshReady();
  });
}
["terms", "keywords", "negs"].forEach(wireDrop);
let dragDepth = 0;
document.addEventListener("dragenter", e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { dragDepth++; document.body.classList.add("sq-dragging"); } });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("sq-dragging"); } });
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => { e.preventDefault(); dragDepth = 0; document.body.classList.remove("sq-dragging"); });

function refreshReady(){
  const have = ["terms", "keywords", "negs"].filter(k => files[k]);
  document.querySelectorAll("#track i").forEach((t, i) => t.classList.toggle("on", i < have.length));
  $id("readyCount").textContent = have.length + " / 3";
  let txt;
  if (!files.terms) txt = "Add the search terms report to start.";
  else {
    txt = meta.terms.toLocaleString() + " search terms ready";
    const skip = [];
    if (files.keywords) skip.push(meta.keywords.toLocaleString() + " keywords");
    if (files.negs) skip.push(meta.negs.toLocaleString() + " negatives");
    txt += skip.length ? ", skipping " + skip.join(" and ") + " you already have." : ". Add the other two to skip what the account already has.";
  }
  $id("readyText").textContent = txt;
  $id("runBtn").disabled = !files.terms;
  $id("runBtn").classList.toggle("ready", !!files.terms);
}
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !$id("runBtn").disabled && $id("overlay").hidden) { e.preventDefault(); $id("runBtn").click(); }
});

function setStatus(t, frac){
  $id("statusLine").textContent = t; $id("ovStatus").textContent = t;
  if (frac != null) $id("ovBar").style.width = Math.round(Math.min(1, frac) * 100) + "%";
}

$id("runBtn").addEventListener("click", async () => {
  $id("runBtn").disabled = true; $id("dlBtn").disabled = true; $id("results").innerHTML = "";
  $id("overlay").hidden = false; setStatus("Starting…", 0);
  import("/static/js/thinking-orb.js").then(m => { if (!orb && !$id("overlay").hidden) orb = m.mountThinkingOrb($id("ovOrb"), { state: "solving", size: 64 }); }).catch(() => {});
  try {
    lastResult = await runAnalysis(RULESETS[current], files.terms, files.keywords, files.negs, setStatus);
    setStatus("Done: " + lastResult.summary.totalRows.toLocaleString() + " terms classified.", 1);
    renderResults(lastResult); $id("dlBtn").disabled = false;
    $id("results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    $id("results").innerHTML = '<div class="pm-err">Something went wrong: ' + esc(e && e.message ? e.message : e) + '</div>';
    setStatus("Failed.");
  } finally {
    $id("overlay").hidden = true; if (orb) { orb.destroy(); orb = null; }
    refreshReady();
  }
});

function pillClass(s){ return s === "Relevant" ? "ok" : s === "Competitor" ? "over" : s === "Client Review" ? "unclear" : "mute"; }
function rowClass(s){ return s === "Relevant" ? "sq-row-rel" : s === "Competitor" ? "sq-row-comp" : s === "Client Review" ? "sq-row-rev" : ""; }
function fmtConv(v){ return Math.round(Number(v) || 0); }
const money = v => "$" + Math.round(v).toLocaleString();

function renderResults(r){
  const s = r.summary, N = s.totalRows;
  const mix = [["Relevant", "c-rel"], ["Competitor", "c-comp"], ["Not Relevant", "c-nr"], ["Client Review", "c-rev"]];
  const kpi = (v, l) => '<div class="sq-kpi"><div class="v">' + v + '</div><div class="l">' + l + '</div></div>';
  const tab = (k, l, n) => '<button class="sq-tab" data-k="' + k + '">' + l + '<span class="n">' + n + '</span></button>';
  let warn = "";
  if (s.failures.length) warn = '<div class="pm-err">' + s.failures.length + ' model batch' + (s.failures.length === 1 ? "" : "es") +
    ' failed: ' + esc(s.failures[0]) + ' Their ' + s.fallback.toLocaleString() + ' terms are marked Not Relevant, No Clear Intent, and say "fallback" in the table. Run again to retry them.</div>';
  $id("results").innerHTML = warn + '<div class="pm-panel">' +
    '<div class="pm-panel-h"><span class="pm-panel-t">Results · ' + esc(r.ruleset.label) + '</span>' +
    '<span class="pm-panel-s">' + s.byRule.toLocaleString() + ' settled by rules · ' + s.byModel.toLocaleString() + ' by the model' + (s.fallback ? ' · ' + s.fallback.toLocaleString() + ' fallback' : '') + '</span></div>' +
    '<div class="sq-mix">' + mix.map(m => { const n = s.byStatus[m[0]] || 0; return n ? '<i class="' + m[1] + '" style="width:' + (100 * n / N).toFixed(2) + '%" title="' + m[0] + ': ' + n + '"></i>' : ""; }).join("") + '</div>' +
    '<div class="sq-legend">' + mix.map(m => '<span><span class="sw ' + m[1] + '"></span>' + m[0] + ' <b>' + (s.byStatus[m[0]] || 0).toLocaleString() + '</b></span>').join("") + '</div>' +
    '<div class="sq-kpis">' + kpi(N.toLocaleString(), "terms classified") + kpi(money(s.totalSpend), "total spend") + kpi(fmtConv(s.totalConv).toLocaleString(), "conversions") +
      kpi(s.relevantPct + "%", "relevant") + kpi(r.priorityNegatives.length, "priority negatives") + kpi(r.priorityKeywords.length, "priority keywords") + '</div>' +
    '<div class="sq-tabs">' + tab("negs", "Priority negatives", r.priorityNegatives.length) + tab("kws", "Priority keywords", r.priorityKeywords.length) +
      tab("review", "Client review", r.clientReview.reduce((a, c) => a + c.count, 0)) + tab("all", "Every term", N) + '</div>' +
    '<div id="panes">' + paneNegs(r) + paneKws(r) + paneReview(r) + paneAll(r) + '</div></div>';
  const tabs = $id("results").querySelectorAll(".sq-tab");
  const show = k => { tabs.forEach(t => t.classList.toggle("on", t.dataset.k === k)); $id("results").querySelectorAll(".pane").forEach(p => p.hidden = p.id !== "pane-" + k); };
  tabs.forEach(t => t.addEventListener("click", () => show(t.dataset.k)));
  show("negs");
}
function tbl(h, rows){ return '<div class="pm-scroll"><table><thead><tr>' + h.map(x => '<th' + (x[1] ? ' class="r"' : '') + '>' + x[0] + '</th>').join("") + '</tr></thead><tbody>' + rows + '</tbody></table></div>'; }
const none = () => '<p class="pm-empty">None found.</p>';
function paneNegs(r){
  const rows = r.priorityNegatives.map(n => '<tr><td class="sq-term">' + esc(n.text) + '</td><td>' + esc(n.matchType) + '</td><td>' + esc(n.level) + '</td><td>' + esc(n.reason || "") + '</td><td class="r num">' + money(n.impact) + '</td><td class="r num">' + fmtConv(n.conversions) + '</td></tr>').join("");
  const ref = r.allNegatives.slice(30).map(n => '<tr><td class="sq-term">' + esc(n.text) + '</td><td>' + esc(n.matchType) + '</td><td>' + esc(n.level) + '</td><td class="r num">' + money(n.impact) + '</td></tr>').join("");
  return '<div class="pane" id="pane-negs" hidden>' + (rows ? tbl([["Negative"], ["Match"], ["Level"], ["Reason"], ["Impact", 1], ["Conv", 1]], rows) : none()) +
    (ref ? '<details class="sq-ref"><summary>All negatives (' + r.allNegatives.length + ')</summary>' + tbl([["Negative"], ["Match"], ["Level"], ["Impact", 1]], ref) + '</details>' : "") + '</div>';
}
function paneKws(r){
  const rows = r.priorityKeywords.map(k => '<tr><td class="sq-term">' + esc(k.term) + '</td><td>' + esc(k.assetType) + '</td><td>' + esc(k.matchType) + '</td><td>' + esc(k.whereToAdd) + '</td><td class="r num">' + fmtConv(k.conversions) + '</td><td class="r num">' + (k.ctr * 100).toFixed(1) + '%</td><td><span class="pm-pill ' + (k.tier === 1 ? "ok" : "under") + '">Tier ' + k.tier + '</span></td></tr>').join("");
  const ref = r.allKeywords.slice(30).map(k => '<tr><td class="sq-term">' + esc(k.term) + '</td><td>' + esc(k.assetType) + '</td><td class="r num">' + fmtConv(k.conversions) + '</td></tr>').join("");
  return '<div class="pane" id="pane-kws" hidden>' + (rows ? tbl([["Term"], ["Type"], ["Match"], ["Where to add"], ["Conv", 1], ["CTR", 1], ["Tier"]], rows) : none()) +
    (ref ? '<details class="sq-ref"><summary>All eligible keywords (' + r.allKeywords.length + ')</summary>' + tbl([["Term"], ["Type"], ["Conv", 1]], ref) + '</details>' : "") + '</div>';
}
function paneReview(r){
  const rows = r.clientReview.map(c => '<tr><td>' + esc(c.subtype) + '</td><td class="r num">' + c.count + '</td><td class="sq-term">' + esc(c.examples.join(", ")) + '</td></tr>').join("");
  return '<div class="pane" id="pane-review" hidden>' + (rows ? tbl([["Issue"], ["Count", 1], ["Examples"]], rows) : none()) + '</div>';
}
function paneAll(r){
  const rows = r.rows.slice(0, 500).map(x => '<tr class="' + rowClass(x._det.status) + '"><td class="sq-term">' + esc(x.term) + '</td><td>' + esc(x.campaign) + '</td><td class="r num">$' + x.cost.toFixed(2) + '</td><td class="r num">' + fmtConv(x.conversions) + '</td><td><span class="pm-pill ' + pillClass(x._det.status) + '">' + esc(x._det.status) + '</span></td><td>' + esc(x._det.subtype || "") + '<span class="sq-by">' + esc(x._det.by || "") + '</span></td></tr>').join("");
  return '<div class="pane" id="pane-all" hidden>' + tbl([["Term"], ["Campaign"], ["Cost", 1], ["Conv", 1], ["Status"], ["Why"]], rows) +
    (r.rows.length > 500 ? '<p class="pm-panel-s" style="margin-top:10px">Showing the first 500 of ' + r.rows.length.toLocaleString() + ' terms. The workbook has every one.</p>' : "") + '</div>';
}

$id("dlBtn").addEventListener("click", () => {
  if (!lastResult || typeof XLSX === "undefined") { setStatus("The spreadsheet library didn't load. Refresh and try again."); return; }
  const r = lastResult, wb = XLSX.utils.book_new();
  const add = (name, aoa) => XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), name);
  add("SQR Analysis", [["Search Term", "Campaign", "Ad Group", "Clicks", "Impr.", "Cost", "Conversions", "Classification", "Subtype", "Decided by"],
    ...r.rows.map(x => [x.term, x.campaign, x.adGroup, x.clicks, x.impressions, x.cost, fmtConv(x.conversions), x._det.status, x._det.subtype || "", x._det.by || ""])]);
  add("Summary", [["Metric", "Value"], ["Account ruleset", r.ruleset.label], ["Total rows", r.summary.totalRows], ["Total spend", r.summary.totalSpend.toFixed(2)],
    ["Total conversions", fmtConv(r.summary.totalConv)], ["Relevant %", r.summary.relevantPct], ["Settled by rules", r.summary.byRule],
    ["Settled by the model", r.summary.byModel], ["Fallback (model unavailable)", r.summary.fallback], ...Object.entries(r.summary.byStatus)]);
  add("Negatives to Add (Priority)", [["Negative Text", "Match Type", "Level", "Reason", "Impact Score", "Cost", "Conversions"],
    ...r.priorityNegatives.map(n => [n.text, n.matchType, n.level, n.reason, n.impact.toFixed(2), n.cost.toFixed(2), fmtConv(n.conversions)])]);
  add("New Keywords to Add (Priority)", [["Term", "Asset Type", "Suggested Match Type", "Add To Level", "Where To Add", "Tier", "Conversions", "CTR", "Clicks", "Cost"],
    ...r.priorityKeywords.map(k => [k.term, k.assetType, k.matchType, k.addLevel, k.whereToAdd, k.tier, fmtConv(k.conversions), (k.ctr * 100).toFixed(2) + "%", k.clicks, k.cost.toFixed(2)])]);
  add("Client Review Required", [["Issue Type", "Count", "Example Terms"], ...r.clientReview.map(c => [c.subtype, c.count, c.examples.join("; ")])]);
  add("All Negatives (Reference)", [["Negative Text", "Match Type", "Level", "Reason", "Impact Score"], ...r.allNegatives.map(n => [n.text, n.matchType, n.level, n.reason, n.impact.toFixed(2)])]);
  add("All Keywords (Reference)", [["Term", "Asset Type", "Match Type", "Tier", "Conversions", "CTR", "Clicks"], ...r.allKeywords.map(k => [k.term, k.assetType, k.matchType, k.tier, fmtConv(k.conversions), (k.ctr * 100).toFixed(2) + "%", k.clicks])]);
  const blob = new Blob([XLSX.write(wb, { bookType: "xlsx", type: "array" })], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const filename = "SQR_Analysis_" + r.ruleset.id + "_" + new Date().toISOString().slice(0, 10) + ".xlsx";
  const url = URL.createObjectURL(blob), a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  setStatus("Saved " + filename);
});

paintButton();
refreshReady();
})();
