/* Search Query Analysis: the rules and the arithmetic, with no page code.
 *
 * Every search term runs through its account's deterministic waterfall (below); only the terms no rule settles go to the model, through
 * /p2/paid-marketing/api/sqr/classify (tracker/paid_sqr.py holds each account's remaining categories). What is left is what to DO about
 * it: the negatives and the keywords worth adding, ranked. That is all here, in one file, so tests/test_sqr_core_js.py can run it in Node.
 *
 * The account rulesets are carried over from Agent Holmes (Riccobene and OIA are ports of the team's sqr-analysis-* skills; Eventgroove,
 * 42 North and McHale of client prompts; Beta Bionics is a placeholder). The ruleset ids here and in tracker/paid_sqr.py must match:
 * tests/test_paid_marketing.py checks.
 */
(function(root){
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
  const n = parseFloat(String(v).replace(/[^0-9.\-]/g, ""));   // 1,234.50  $12  €12  CA$12  5%
  return isNaN(n) ? 0 : n;
}
// Conversions are kept as exact decimals for scoring/sorting (impact score,
// tier eligibility), but summing many CSV rows produces floating-point
// noise (171.44999999999999): round only where the number is DISPLAYED.
function fmtConv(v){ return Math.round(Number(v) || 0); }


// =====================================================================
// Matching. A rule's word list matches WHOLE WORDS (or the start of a
// word, for stems like "dentis" or "landscap"), never the middle of one:
// "dent" is in "student", "uline" in "masculine", "uk" in "duke", "oral"
// in "floral", "gum" in "argument".
// =====================================================================
function normText(s){ return String(s == null ? "" : s).toLowerCase().replace(/\s+/g, " ").trim(); }
function reEsc(s){ return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
const LEAD = "(?:^|[^a-z0-9])";

// A list of names or phrases: each must stand alone as words.
function phraseRe(list){
  const alts = list.map(normText).filter(Boolean).sort((a, b) => b.length - a.length).map(x => reEsc(x).replace(/ /g, "\\s+"));
  return alts.length ? new RegExp(LEAD + "(?:" + alts.join("|") + ")(?![a-z0-9])", "i") : null;
}
// Words short enough to sit inside longer ones, or that are real words on their own ("crown", "bridge"), must stand alone
// (with an optional plural); anything longer is a stem and may continue ("dentis" -> dentist, dentistry).
const WHOLE_WORDS = new Set(["crown", "bridge"]);
function signalRe(list){
  const stems = [], whole = [];
  list.map(normText).filter(Boolean).forEach(x => {
    const e = reEsc(x).replace(/ /g, "\\s+");
    (/^[a-z]+$/.test(x) && (x.length <= 4 || WHOLE_WORDS.has(x)) ? whole : stems).push(e);
  });
  const parts = [];
  if (stems.length) parts.push(LEAD + "(?:" + stems.join("|") + ")");
  if (whole.length) parts.push(LEAD + "(?:" + whole.join("|") + ")(?:s|es)?(?![a-z0-9])");
  return parts.length ? new RegExp(parts.join("|"), "i") : null;
}
const CACHE = new WeakMap();
function matcher(rs, key, make){
  let c = CACHE.get(rs); if (!c) { c = {}; CACHE.set(rs, c); }
  if (!(key in c)) c[key] = make(rs[key] || []);
  return c[key];
}
function has(rs, key, term){ const re = matcher(rs, key, phraseRe); return !!re && re.test(term); }
function core(rs, term){
  const re = matcher(rs, "coreSignal", signalRe);
  // A service word alone does not settle a term that also carries a hint of a category the model is asked about (jobs, schools, DIY, price
  // shopping, general questions, pets, products, charities): "dental hygienist jobs" has "dental" in it and is not a patient.
  return !!re && re.test(term) && !(rs.coreVeto && rs.coreVeto.test(term)) && !(rs.modelHint && rs.modelHint.test(term));
}

// "Dr Smith" is a practitioner, "dr near me" is not.
const NOT_A_NAME = new Set(("near nearby me office offices for who that what how in on at and or the a an is are open appointment appointments visit " +
  "visits consult consultation phone number address hours review reviews location locations clinic center centre dentist dental today now accepts " +
  "takes specialist services service cost price prices best top local good find any").split(" "));
// The competitor from the ruleset's list that a term names ("" when it names none, or is a practitioner or another rule's competitor).
function competitorName(rs, term){
  const re = matcher(rs, "competitors", phraseRe);
  const m = re && re.exec(term);
  return m ? normText(m[0].replace(/^[^a-z0-9]/i, "")) : "";
}

// "Dr" is a doctor when it opens the term or follows a word that introduces one ("dentist dr warren", "my dr patel"). After a street name it is
// Drive ("coliseum dr winston salem", "1590 westbrook plaza dr suite 202"), and after a house number it is always Drive.
const DR_AFTER = new Set(("dentist dentists dentistry dental my see best top with by for of and office doctor ortho orthodontist surgeon pediatric kids family " +
  "emergency cosmetic new a the visit find meet near nearby at from is").split(" "));
function individual(rs, t){
  const low = t.toLowerCase();
  const re = /(^|[^a-z0-9])dr\.?\s+([a-z]{3,})\b/g; let m;
  while ((m = re.exec(low))) {
    if (NOT_A_NAME.has(m[2])) continue;
    const before = low.slice(0, m.index + m[1].length).trim().split(/\s+/).filter(Boolean);
    if (/\b\d{2,5}\b/.test(before.join(" "))) continue;                 // after a house number
    if (!before.length || DR_AFTER.has(before[before.length - 1])) return true;
  }
  const re2 = /\b([a-z]{3,})\s+(dds|dmd|pllc)\b/gi;
  while ((m = re2.exec(t))) if (!NOT_A_NAME.has(m[1].toLowerCase())) return true;
  return false;
}

// =====================================================================
// Shared helpers
// =====================================================================
// A street address is a number, a street name and a street word. A number that is a measure ("64 slice ct", "24 hour dentist
// way", "10 year warranty st") is not one.
const MEASURE_RE = /\b\d{1,6}\s+(slice|slices|hour|hours|hr|hrs|day|days|minute|minutes|min|week|weeks|month|months|year|years|mg|ml|mm|cm|tesla|percent|pack|piece|pcs|count|oz|lb|lbs|gb|tb)\b/i;
const ADDRESS_RE = /\b\d{1,6}\s+([a-z0-9]+\s+){1,4}(street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|parkway|pkwy|court|ct|circle|cir|place|pl)\b/i;
const PHONE_RE = /\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b/;
const PPT_RE = /\bppt\b/i;
function isAddress(t){
  const m = ADDRESS_RE.exec(t);
  if (!m) return false;
  return !MEASURE_RE.test(m[0]);
}

// A Performance Max campaign is targeted by radius, not by the city in its name, so a place named in a search is not a mismatch there.
function radiusTargeted(row){ return !!row && (/pmax|performance[\s_]?max/i.test(row.campaignType || "") || /pmax|performance[\s_-]?max/i.test(row.campaign || "")); }
function normSlug(campaign){
  return String(campaign || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}
const CITY_RE = new Map();
function cityHit(city, t, ambiguous){
  let re = CITY_RE.get(city); if (!re) { re = phraseRe([city]); CITY_RE.set(city, re); }
  if (!re.test(t)) return false;
  const guard = ambiguous && ambiguous[city.toLowerCase()];
  return guard ? guard.test(t) : true;
}
function campaignServesCity(slug, territoryMap, term, ambiguous){
  // true if `term` names a city in this campaign's own territory (no mismatch), false if it names a DIFFERENT mapped campaign's city
  // (mismatch), null if the campaign isn't in the map at all (unknown territory -> never flag a mismatch). A city name that is also an
  // ordinary word or a surname (Normal, Cary, Apex, Independence) only counts as another campaign's city when the state is named too.
  const key = Object.keys(territoryMap).sort((a, b) => b.length - a.length).find(k => slug.includes(k));
  if (!key) return null;
  const t = term.toLowerCase();
  if (territoryMap[key].some(c => cityHit(c, t, null))) return true;
  for (const [otherKey, cities] of Object.entries(territoryMap)){
    if (otherKey === key) continue;
    if (cities.some(c => cityHit(c, t, ambiguous))) return false;
  }
  return null;
}

// =====================================================================
// RICCOBENE ruleset
// =====================================================================
const RICCOBENE = {
  id: "riccobene", label: "Riccobene Associates Family Dentistry",
  note: "Full deterministic port of the <b>sqr-analysis-riccobene</b> skill (brand terms, 90+ named competitors, campaign→territory map, insurance rules, address/phone/ppt patterns). The skill only <i>names</i> (without an exact word list) the Jobs, Education, DIY, generic Price-Seeking, Informational, Veterinary, Dental-Products and Non-Profit disqualifier categories, so those rows are resolved by asking the model per the skill's own category definitions rather than a guessed keyword list. A term with a service word in it (\u2018dental hygienist jobs\u2019) is still sent to the model when it carries a hint of one of those categories, instead of being called relevant for the service word.",
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
  coreSignal: ["dental","tooth","teeth","oral","gum","tmj","tmd","invisalign","braces","implant","veneer","crown","bridge","denture","whitening","extraction","root canal","endodonti","periodonti","prosthodonti","sedation","cosmetic","pediatric","kids dent","family dent","emergency dent","urgent dent","sleep apnea","cleaning","checkup","appt","appointment","dentis","orthodont"],

  classifyDeterministic(term, campaignSlug, row){
    const t = term.trim(); if (!t) return null;
    if (this.brandRe.test(t)) return { status: "Relevant", subtype: "Brand" };
    if (this.falsePositiveGuard.some(re => re.test(t))) { /* fall through, never Competitor */ }
    else if (has(this, "competitors", t) || individual(this, t)) {
      return { status: "Competitor", subtype: "Named competitor / practitioner", negMatchType: "Exact", negLevel: "Account" };
    }
    if (isAddress(t)) return { status: "Not Relevant", subtype: "Specific Address Query" };
    if (PHONE_RE.test(t)) return { status: "Not Relevant", subtype: "No Clear Dental Intent" };
    if (/\bschool\b/i.test(t) && !/dental school/i.test(t)) return { status: "Not Relevant", subtype: "Education/Training", negText: "dental school", negMatchType: "Phrase", negLevel: "Account" };
    if (PPT_RE.test(t)) return { status: "Not Relevant", subtype: "Informational/Research" };
    if (this.spanishPriceRe.test(t)) return { status: "Not Relevant", subtype: "Price-Seeking" };
    if (this.medicareRe.test(t)) return { status: "Not Relevant", subtype: "Medicare Not Accepted", negText: "medicare", negMatchType: "Phrase", negLevel: "Account" };
    if (this.medicaidRe.test(t)) return { status: "Client Review", subtype: "Medicaid Unconfirmed" };
    if (has(this, "insuranceBrands", t)) return { status: "Client Review", subtype: "Insurance Network Brand" };
    if (this.mintHillRe.test(t)) return { status: "Client Review", subtype: "Potential New Service Area (Mint Hill NC)" };
    if (this.selmaRe.test(t)) return { status: "Client Review", subtype: "Potential New Service Area (Selma NC)" };
    if (this.spanishDentalRe.test(t)) return { status: "Client Review", subtype: "Bilingual – Confirm Staffing" };
    if (has(this, "outOfArea", t)) return { status: "Not Relevant", subtype: "Out-of-Area Location", negMatchType: "Phrase", negLevel: "Campaign" };
    const mismatch = radiusTargeted(row) ? null : campaignServesCity(campaignSlug, this.territoryMap, t, this.ambiguousCities);
    if (mismatch === false) return { status: "Not Relevant", subtype: "Location Mismatch" };
    if (core(this, t)) return { status: "Relevant", subtype: "Core dental intent" };
    return null; // needs the model
  },
};

// =====================================================================
// OIA ruleset
// =====================================================================
const OIA_FACILITIES = [
  // The official names are the start, not the end: real searches say "fort jesse imaging", "nebraska imaging", "cardinal imaging", "idi fort walton",
  // "odc huntsville", "uva imaging culpeper", and they are the facility's best-converting searches.
  { code:"AMI", re:/\b(advanced medical imaging)\b/i, market:"kokomo|indiana" },
  { code:"Heartland", re:/heartland imaging|hartland imaging|heartland (diagnostics?|radiology|medical imaging)/i },
  { code:"NHI", re:/nebraska (health |medical )?imaging|nebreska health imaging|\bnhi\b/i },
  { code:"OIC-Culpeper", re:/outpatient imaging culpeper|culpeper (outpatient )?imaging|uva (outpatient )?imaging culpeper|uva culpeper|culpeper uva/i },
  { code:"ODC-AL", re:/outpatient diagnostics?( center)?|\bodc\b/i },
  { code:"CPIC", re:/cardinal points?( imaging)?|cardinal imaging|raleigh radiology|raleighrad|\bcpic\b/i },
  { code:"IDL-FL", re:/institute of diagnostic imaging|\bidi\b/i },
  { code:"SMI", re:/spectrum (medical )?imaging|platte valley medical/i },
  { code:"Holmdel-NJ", re:/holmdel (medical )?imaging/i },
  { code:"Ft Jesse", re:/\b(ft|fort|fr)\.? jesse( imaging)?|gale keeran/i },
  { code:"UD", re:/university diagnostics?/i },
  { code:"Wolf River", re:/(imaging|radiology) (center )?(at )?wolf river|wolf river (imaging|radiology)/i },
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

  classifyDeterministic(term, campaignSlug, row){
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

    // "advanced medical imaging" is AMI only with Kokomo/Indiana in the term or in an AMI campaign: elsewhere it is a different, national, name
    if (/\badvanced (medical )?imaging\b/i.test(t) && (/(kokomo|indiana)/i.test(t) || /(^|[^a-z])ami([^a-z]|$)|p2ami/.test(campaignSlug || ""))) return { status: "Relevant", subtype: "Brand (AMI: Kokomo)" };
    for (const f of this.facilities){ if (f.code !== "AMI" && f.re.test(t)) return { status: "Relevant", subtype: "Brand (" + f.code + ")" }; }
    if (this.brandGeneric.test(t)) return { status: "Relevant", subtype: "Brand (OIA corporate)" };

    // JV same-name caution (Section 5a)
    if (/uva health/i.test(t)) return /culpeper/i.test(t) ? { status: "Relevant", subtype: "Brand (OIC-Culpeper via UVA Health JV)" } : { status: "Competitor", subtype: "UVA Health (non-Culpeper market)", negMatchType: "Exact", negLevel: "Account" };
    if (/osf ?healthcare|osf imaging/i.test(t)) return /(bloomington|normal)/i.test(t) ? { status: "Relevant", subtype: "Brand (Ft Jesse via OSF JV)" } : { status: "Competitor", subtype: "OSF HealthCare (non-Bloomington/Normal market)", negMatchType: "Exact", negLevel: "Account" };

    if (/gearstar/i.test(t)) return { status: "Not Relevant", subtype: "Unverified Term: flag for client confirmation" };
    if (this.falsePositiveGuard.some(re => re.test(t))) { /* never Competitor */ }
    else if (has(this, "competitors", t)) {
      return { status: "Competitor", subtype: "Named competitor / health system", negMatchType: "Exact", negLevel: "Account" };
    }
    if (isAddress(t)) return { status: "Not Relevant", subtype: "Specific Address Query" };
    if (PHONE_RE.test(t)) return { status: "Not Relevant", subtype: "Specific Phone Query" };
    if (this.infoRe.test(t)) return { status: "Not Relevant", subtype: "Informational/Research" };
    // "cat scan" and "pet scan" are imaging services, not animals: take the scan names out before looking for a pet
    if (this.vetRe.test(t.replace(/\b(cat[\s-]*scans?|pet[\s\/-]*ct|pet[\s-]*scans?)\b/gi, " "))) return { status: "Not Relevant", subtype: "Veterinary/Non-Human" };
    if (this.medicareRe.test(t) || this.medicaidRe.test(t)) return { status: "Client Review", subtype: "Insurance Coverage Unconfirmed" };
    if (has(this, "insuranceBrands", t)) return { status: "Client Review", subtype: "Insurance Network Unconfirmed" };
    if (has(this, "outOfArea", t)) return { status: "Not Relevant", subtype: "Out-of-Area Location", negMatchType: "Phrase", negLevel: "Campaign" };
    const mismatch = radiusTargeted(row) ? null : campaignServesCity(campaignSlug, this.territoryMap, t, this.ambiguousCities);
    if (mismatch === false) return { status: "Not Relevant", subtype: "Location Mismatch" };
    if (core(this, t)) return { status: "Relevant", subtype: "Core imaging intent" };
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
  coreSignal: ["ticket","raffle","banner","poster","flyer","wristband","wrist band","name tag","backstage pass","certificate","badge","lanyard","credential","laminate","yard sign","directional sign"],

  classifyDeterministic(term){
    const t = term.trim(); if (!t) return null;
    const tl = t.toLowerCase();
    if (this.brandRe.test(t)) return { status: "Relevant", subtype: "Brand" };
    if (has(this, "competitors", t)) return { status: "Competitor", subtype: "Named competitor", negMatchType: "Exact", negLevel: "Account" };
    if (has(this, "notRelevantExact", t)) return { status: "Not Relevant", subtype: "Excluded term" };
    if (this.reviewsRe.test(t)) return { status: "Not Relevant", subtype: "Reviews/Reputation", negMatchType: "Phrase", negLevel: "Account" };
    if (this.jobsRe.test(t)) return { status: "Not Relevant", subtype: "Jobs/Training", negMatchType: "Phrase", negLevel: "Account" };
    if (this.freeRe.test(t)) return { status: "Not Relevant", subtype: "Promotions/Freebies", negMatchType: "Phrase", negLevel: "Account" };
    if (this.irrelevantRe.test(t)) return { status: "Not Relevant", subtype: "Irrelevant Service" };
    if (has(this, "productPhrases", t)) return { status: "Relevant", subtype: "Named product/service" };
    if (core(this, t)) return { status: "Relevant", subtype: "Core product intent" };
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
  coreSignal: ["dentist","dental","tooth","teeth","cleaning","implant","invisalign","braces","whitening","veneer","extraction","crown","denture","oral surgery","oral surgeon","endodont","periodont","pediatric dent","emergency dent","urgent care dent","root canal","orthodont"],

  classifyDeterministic(term){
    const t = term.trim(); if (!t) return null;
    const tl = t.toLowerCase();
    if (this.brandRe.test(t)) return { status: "Relevant", subtype: "Brand" };
    if (this.firstDentalRe.test(t)) return { status: "Not Relevant", subtype: "Excluded term (first dental)" };
    if (has(this, "competitors", t)) return { status: "Competitor", subtype: "Named competitor practice", negMatchType: "Exact", negLevel: "Account" };
    if (this.disqualifyRe.test(t)) return { status: "Not Relevant", subtype: "Free/Jobs/Education/Products" };
    if (this.insuranceRe.test(t)) return { status: "Relevant", subtype: "Insurance/provider" };
    if (this.costRe.test(t)) return { status: "Relevant", subtype: "Cost/price intent" };
    if (core(this, t)) return { status: "Relevant", subtype: "Core dental intent" };
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
    if (has(this, "competitors", t)) return { status: "Competitor", subtype: "Named DMV-area design/landscape competitor", negMatchType: "Exact", negLevel: "Account" };
    if (this.jobsRe.test(t)) return { status: "Not Relevant", subtype: "Jobs/Recruitment", negMatchType: "Phrase", negLevel: "Account" };
    if (this.eduRe.test(t)) return { status: "Not Relevant", subtype: "Education/Training", negMatchType: "Phrase", negLevel: "Account" };
    if (this.reviewsRe.test(t)) return { status: "Not Relevant", subtype: "Reviews/Reputation", negMatchType: "Phrase", negLevel: "Account" };
    if (this.diyRe.test(t)) return { status: "Not Relevant", subtype: "DIY/Home Project" };
    if (has(this, "outOfArea", t)) return { status: "Not Relevant", subtype: "Out-of-Area Location", negMatchType: "Phrase", negLevel: "Campaign" };
    if (core(this, t)) return { status: "Relevant", subtype: "Core landscaping intent" };
    return null;
  },
};

// =====================================================================
// BETA BIONICS ruleset: the client's own sqr-analysis-beta-bionics skill (a national US medical-device account: the iLet Bionic Pancreas,
// type 1 diabetes, ages 6+, US only). The skill's 25 priorities run in its order, the first match wins, with two deliberate departures that
// are named in the note: the pipeline products (iLet Duo, Mint) are caught before the brand check, because "ilet duo" carries the brand word
// and the skill itself says a pipeline product is never a keyword; and "apply" is not a jobs word when the term is about coverage or
// ordering. Medicare is covered here, cost and price are the funnel, and there are no city rules: dental habits are wrong on this account.
// =====================================================================
function trigger(re, t){ const m = re.exec(t); return m ? normText(m[0].replace(/^[^a-z0-9]/i, "")) : ""; }
// the word that fired plus its neighbour, so a bare "free" (FreeStyle Libre risk) is never what gets blocked
function window2(t, word){
  const w = t.split(" "), i = w.indexOf(word);
  if (i < 0) return word;
  return i + 1 < w.length ? w[i] + " " + w[i + 1] : i > 0 ? w[i - 1] + " " + w[i] : word;
}
const BB = {
  investor: phraseRe(["stock", "share price", "shares", "ticker", "bbnx", "nasdaq", "ipo", "investor", "investors", "earnings", "market cap", "sec filing", "10-k", "analyst", "analysts"]),
  inStock: /\b(in|out of) stock\b/,
  jobs: phraseRe(["jobs", "job", "hiring", "career", "careers", "vacancy", "vacancies", "recruitment", "salary", "salaries", "wage", "wages", "internship", "internships", "intern", "resume", "cv", "apply", "glassdoor"]),
  applyContext: /\b(medicare|medicaid|insurance|coverage|prescription|rx|order|pharmacy|assistance|program|grant|financial|covered|benefits?)\b/,
  // a rival named next to the brand makes it a comparison, even when only the company is named ("switching from tandem to ilet")
  rivalBare: /\b(tandem|omnipod|medtronic|minimed|insulet|twiist|t:?slim|control-?iq|ypsomed|ypsopump|camaps|cequr)\b/,
  // the Mint is the not-yet-available patch pump: "mint insulin pump" and "beta bionics mint" are as much the pipeline as "mint patch pump"
  mint: /\bmint\b/, mintCtx: /\b(pump|pumps|insulin|patch|diabetes|diabetic|bionics?|ilet|tubeless|bihormonal)\b/,
  pipeline: /(^|[^a-z0-9])(ilet\s+duo|mint\s+(?:patch\s+)?pump|mint\s+patch|bi-?hormonal|dual[\s-]hormone|glucagon\s+pump)(?![a-z0-9])/,
  brand: /(^|[^a-z0-9])(ilet|i-let|i\s+let\s+pump|beta\s*bionics|betabionics|bionic\s+pancreas|color\s+ilet|bionic\s+circle)(?![a-z0-9])/,
  support: /\b(not\s+charging|customer\s+service|customer\s+support|cartridges?|manuals?|log\s*in|app|support|troubleshoot\w*|alarms?|errors?|replacement|charger|warranty|phone\s+number|contact|reset|help|bionic\s+circle|battery)\b/,
  diy: /(^|[^a-z0-9])(openaps|androidaps|diy\s+loop|iaps|trio\s+app|nightscout|loop\s+and\s+learn|build\s+your\s+own|homemade)(?![a-z0-9])/,
  otherTopic: /(^|[^a-z0-9])(islets?\s+(?:cells?|transplant\w*)|pancreas\s+transplant\w*|pancreatitis|pancreatic\s+\w+|islets?)(?![a-z0-9])/,
  prosthetic: /(^|[^a-z0-9])(bionic\s+(?:arm|arms|leg|legs|hand|hands|eye|eyes|limb|limbs|ear|ears|knee|foot|man|woman)|prosthe\w+)(?![a-z0-9])/,
  pumpOther: phraseRe(["heat pump", "breast pump", "penis pump", "pain pump", "baclofen pump", "morphine pump", "sump pump", "well pump", "fuel pump", "hydraulic pump", "air pump", "water pump", "gas pump"]),
  animal: /(^|[^a-z0-9])(dogs?|cats?|pets?|canine|feline|veterinary|veterinarian)(?![a-z0-9])/,
  diabetesCtx: /\b(insulin|pump|pumps|diabetes|diabetic|t1d|glucose|ilet)\b/,
  device: /\b(insulin|pump|pumps|ilet|omnipod|tandem|medtronic|minimed|dexcom|libre|cgm|device|glucose|diabetes)\b/,
  used: /(^|[^a-z0-9])(refurbished|second\s*hand|secondhand|pre-?owned|ebay|craigslist|facebook\s+marketplace|marketplace|resale|for\s+sale|used\s+(?:insulin|pump|pumps|ilet|omnipod|tandem|medtronic|minimed|dexcom|cgm|diabetes|medical))(?![a-z0-9])|\b(?:pump|pumps|ilet|omnipod)\s+used\s*$/,
  education: /(^|[^a-z0-9])(courses?|certification|degree|diploma|cdces?\s+exam|diabetes\s+educator\s+program)(?![a-z0-9])/,
  freebie: /(^|[^a-z0-9])(free\s+trial|free|cheapest|cheap|coupons?|promo\s+code|discount\s+code|giveaway|gratis|barato|sin\s+dinero)(?![a-z0-9])/,
  freeNotFree: /\b(tube|tubing|carb|carbs|sugar|gluten|latex|hassle|worry|lag|pain|needle|injection|alarm)[\s-]+free\b|\bfree\s+(of|from)\b/,
  info: /(^|[^a-z0-9])(what\s+is|what\s+are|meaning|definition|wiki|wikipedia|pdf|ppt|pictures?\s+of|images?\s+of|video|videos|youtube|history\s+of|who\s+invented)(?![a-z0-9])/,
  underage: /(^|[^a-z0-9])(toddlers?|infants?|newborns?|baby|babies|[2-5]\s*(?:year|yr)s?[\s-]*old)(?![a-z0-9])/,
  shopping: /\b(best|top|newest|latest|new|better|compare|comparison|vs|versus|cost|price|prices|pricing|copay|covered|coverage|insurance)\b/,
  gestational: /\bgestational\b/,
  type2: /\b(type\s*(?:2|ii|two)|t2d|t2dm)\b/,
  medicaid: /\bmedicaid\b/,
  insurers: phraseRe(["unitedhealthcare", "united healthcare", "aetna", "cigna", "blue cross", "bcbs", "anthem", "humana", "kaiser", "tricare", "va benefits", "optum", "express scripts", "caremark"]),
  pumpCtx: /\b(pump|pumps|ilet|insulin|aid|bionic|diabetes|diabetic|cgm|supplies|t1d)\b/,
  trial: /\b(clinical\s+trials?|research\s+stud(?:y|ies)|enroll\s+in\s+(?:a\s+)?stud(?:y|ies))\b/,
  territory: /\b(puerto\s+rico|guam|u\.?s\.?\s+virgin\s+islands|american\s+samoa)\b/,
  abroad: phraseRe(["canada", "canadian", "toronto", "vancouver", "uk", "united kingdom", "nhs", "england", "scotland", "ireland", "australia", "new zealand", "india", "pakistan", "philippines",
    "germany", "france", "spain", "italy", "netherlands", "sweden", "norway", "denmark", "uae", "dubai", "saudi", "qatar", "south africa", "nigeria", "kenya", "brazil", "argentina", "japan", "china",
    "korea", "singapore", "malaysia", "indonesia", "in mexico", "mexico city", "mexican", "london uk", "london england", "london ontario", "paris france", "dublin ireland", "melbourne australia", "ontario canada"]),
  cgm: /\b(dexcom|libre|freestyle|eversense|cgm|g6|g7|continuous\s+glucose)\b/,
  deliveryCtx: /\b(pump|pumps|aid|automated\s+insulin|insulin\s+delivery|ilet|closed[\s-]?loop|artificial\s+pancreas|bionic|compatible|omnipod|tandem|medtronic|minimed)\b/,
  spanish: /\b(bomba\s+de\s+insulina|pancreas\s+artificial|diabetes\s+tipo\s+1)\b/,
  t1d: /\b(t1d|type\s*(?:1|i|one)\s+diabetes)\b/,
  t1dDevice: /\b(pump|pumps|device|technology|tech|insulin\s+delivery|aid|closed[\s-]?loop|monitor|cgm|automated|system|app|ilet|bionic)\b/,
};
const BETA_BIONICS = {
  id: "beta_bionics", label: "Beta Bionics (iLet Bionic Pancreas)",
  note: "Ported in full from the client\'s own <b>sqr-analysis-beta-bionics</b> skill: its 25 priorities in its order, first match wins. A national US medical-device account, so there is <b>no city or territory logic</b>, <b>Medicare is covered</b> (never a negative), and cost, price and copay searches are the buying funnel, not bargain hunting. Brand wins over most things (‘ilet vs omnipod’ is a brand comparison, never a competitor) but not over investor or jobs words (‘beta bionics careers’, ‘bbnx stock’). Partner brands (Dexcom, Libre) are relevant next to a pump and never negated. Type 2, Medicaid, insurance networks, pipeline products (iLet Duo, Mint), clinical trials, Spanish-language searches and US territories go to <b>Client Review</b>. Never suggested as a negative: pump, loop, patch, trial, bionic, insulin, free, dexcom, libre, freestyle, cgm, medicare, cost, price, baby, mexico. Brand searches that land in a NonBrand campaign get a campaign-level routing negative so they go to the Brand campaign. Two departures from the skill: pipeline products are checked before the brand word (‘ilet duo’ has it), and ‘apply’ is not a jobs word in a coverage or ordering search. What no priority settles is read by the model with this account\'s facts. One difference to know about: the skill ranks negatives by cost plus five times conversions; this page ranks by money wasted on terms that never converted, and holds back any negative whose terms converted.",
  brandRe: /(ilet|i-let|beta\s*bionics|betabionics|bionic\s+pancreas)/i,
  routeCampaignRe: /non[\s_-]?brand/i,
  forbiddenNegatives: ["pump", "pumps", "loop", "patch", "trial", "bionic", "insulin", "free", "dexcom", "libre", "freestyle", "cgm", "medicare", "cost", "price", "baby", "mexico"],
  competitors: ["omnipod 5", "omnipod dash", "omnipod go", "omnipod5", "omnipod", "omni pod", "insulet", "tslim", "t:slim", "t slim", "tandem mobi", "mobi insulin pump", "mobi pump", "tandem diabetes", "tandem insulin pump", "tandem pump",
    "tandem t:slim", "control-iq", "control iq", "minimed", "medtronic minimed", "medtronic pump", "medtronic insulin pump", "medtronic diabetes", "780g", "770g", "inpen", "twiist", "sequel med tech",
    "cequr", "mannkind", "medtronic", "smartguard", "simplera", "v-go", "ypsomed", "ypsopump", "camaps", "camaps fx", "sigi patch", "sigi pump", "eoflow", "eopatch", "bigfoot unity", "bigfoot biomedical", "accu-chek insight", "accu-chek combo",
    "accu-chek spirit", "accu chek insight", "accu chek combo", "accu chek spirit"],
  coreSignal: ["insulin pump", "automated insulin delivery", "aid system", "artificial pancreas", "closed loop", "closed-loop", "hybrid closed loop", "no carb counting", "without carb counting",
    "avoid carb counting", "stop carb counting", "mdi to pump", "switching insulin pump", "switch insulin pump", "tubeless insulin pump", "insulin delivery system", "insulin delivery device",
    "easiest insulin pump", "insulin pump without settings", "meal announcement free", "t1d management", "diabetes management system"],

  classifyDeterministic(term){
    const t = normText(term); if (!t) return null;
    let w;
    // 1 investors
    if ((w = trigger(BB.investor, t)) && !BB.inStock.test(t)) return { status: "Not Relevant", subtype: "Investor intent", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 2 jobs, even next to the brand ("apply" is not a jobs word in a coverage or ordering search)
    if ((w = trigger(BB.jobs, t)) && !(w === "apply" && BB.applyContext.test(t))) return { status: "Not Relevant", subtype: "Jobs/Recruitment", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // pipeline products: never a keyword, a client decision (the skill lists them at 19, but "ilet duo" would otherwise be taken for the brand)
    if (BB.pipeline.test(t) || (BB.mint.test(t) && BB.mintCtx.test(t))) return { status: "Client Review", subtype: "Pipeline product (not yet available)" };
    // 3 brand
    const brand = BB.brand.test(t);
    if (brand) {
      const rival = has(this, "competitors", t) || BB.rivalBare.test(t);
      if (rival) return { status: "Relevant", subtype: "Brand: comparison" };
      if (BB.support.test(t)) return { status: "Relevant", subtype: "Brand: support intent", routeNeg: trigger(BB.brand, t) };
      return { status: "Relevant", subtype: "Brand", routeNeg: trigger(BB.brand, t) };
    }
    // 4 a named competitor, no iLet brand word
    if (has(this, "competitors", t)) return { status: "Competitor", subtype: "Named insulin delivery competitor", negMatchType: "Exact", negLevel: "Account" };
    // 5 DIY looping
    if ((w = trigger(BB.diy, t))) return { status: "Not Relevant", subtype: "DIY looping", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 6 islet and other-condition topics (an "islet pump" is read by the model: it may be a typo for ilet)
    if ((w = trigger(BB.otherTopic, t)) && !(w.indexOf("islet") === 0 && /\bpumps?\b/.test(t) && !/\b(cells?|transplant\w*)\b/.test(w))) return { status: "Not Relevant", subtype: "Different medical topic", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 7 bionic prosthetics
    if ((w = trigger(BB.prosthetic, t)) && !/\b(diabetes|diabetic|insulin|pancreas|glucose)\b/.test(t)) return { status: "Not Relevant", subtype: "Bionic prosthetics", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 8 pumps that are not insulin pumps
    if ((w = trigger(BB.pumpOther, t))) return { status: "Not Relevant", subtype: "Non-diabetes pump", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 9 veterinary
    if ((w = trigger(BB.animal, t)) && BB.diabetesCtx.test(t)) return { status: "Not Relevant", subtype: "Veterinary/Non-human", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 10 used and resale
    if ((w = trigger(BB.used, t)) && BB.device.test(t)) return { status: "Not Relevant", subtype: "Used/Resale", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 11 education
    if ((w = trigger(BB.education, t))) return { status: "Not Relevant", subtype: "Education/Training", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 12 freebies (\bfree\b never matches FreeStyle; a scoped phrase is blocked, never bare "free")
    if ((w = trigger(BB.freebie, t)) && !BB.freeNotFree.test(t)) return { status: "Not Relevant", subtype: "Price-seeking (freebie)", negMatchType: "Phrase", negText: w === "free" ? window2(t, "free") : w, negLevel: "Account" };
    // 13 information only
    // ("what is the best insulin pump" is comparison shopping, the buying funnel on this account, not a definition)
    if ((w = trigger(BB.info, t)) && !BB.shopping.test(t)) return { status: "Not Relevant", subtype: "Informational/Research", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 14 under the minimum age (a bare "baby" is reviewed, not blocked wholesale)
    if ((w = trigger(BB.underage, t)) && BB.diabetesCtx.test(t)) {
      const d = { status: "Not Relevant", subtype: "Under minimum age (6+)" };
      if (!/^(baby|babies)$/.test(w)) Object.assign(d, { negMatchType: "Phrase", negText: w, negLevel: "Account" });
      return d;
    }
    // 15 gestational
    if (BB.gestational.test(t)) return { status: "Not Relevant", subtype: "Not indicated (gestational)", negMatchType: "Phrase", negText: "gestational", negLevel: "Account" };
    // 16 to 20: the client decides
    if (BB.type2.test(t)) return { status: "Client Review", subtype: "Off-label: type 2 (label expansion pending)" };
    if (BB.medicaid.test(t)) return { status: "Client Review", subtype: "Medicaid: state coverage" };
    if (BB.insurers.test(t) && BB.pumpCtx.test(t)) return { status: "Client Review", subtype: "Insurance network / formulary" };
    if (BB.trial.test(t) && BB.pumpCtx.test(t)) return { status: "Client Review", subtype: "Clinical trial recruitment" };
    if (BB.territory.test(t)) return { status: "Client Review", subtype: "US territory: distribution unconfirmed" };
    // 21 outside the US
    if ((w = trigger(BB.abroad, t))) return { status: "Not Relevant", subtype: "Out of market (US only)", negMatchType: "Phrase", negText: w, negLevel: "Account" };
    // 22 a CGM or partner brand with no pump in the search
    if (BB.cgm.test(t) && !BB.deliveryCtx.test(t)) return { status: "Not Relevant", subtype: "Partner product only", noNeg: true };
    // 23 Spanish: relevant, and the client confirms Spanish support
    if (BB.spanish.test(t)) return { status: "Client Review", subtype: "Bilingual: confirm Spanish support" };
    // 24 the product category
    if ((BB.t1d.test(t) && BB.t1dDevice.test(t)) || core(this, t)) return { status: "Relevant", subtype: "Core product intent" };
    return null;                                  // 25 the skill defaults to Not Relevant; here the model reads it with this account's facts
  },
};

// =====================================================================
// Generic: no client rulebook (CSV mode, and the base of the Automatic rules below)
// =====================================================================
const GENERIC = {
  id: "generic", label: "Automatic",
  note: "For any account without a client rulebook. Nothing is typed in by hand: the account\'s own Google Ads data is the rulebook. Its brand comes from its name, and a search that is exactly a keyword it bids on is relevant. Everything else is read by the model, which is told what the account is: its campaigns, the keywords it bids on and the searches that converted. The model follows the standard Search Query process: relevant, competitor (another named business selling the same thing), not relevant (jobs, courses, do-it-yourself, free or bargain hunting, general questions, wrong area), and Client Review where only the client can decide, such as insurance. Where a client rulebook exists it is more exact, and it is picked for that account automatically.",
  classifyDeterministic(){ return null; },
};

// =====================================================================
// AUTOMATIC: the rules for any account that has no client rulebook. Nothing is written by hand: the account's own Google Ads data is the rulebook.
// Two rules are certain enough to apply without the model: the account's own brand (taken from its name) and a search that is exactly a keyword
// the account bids on. Everything else is read by the model, which is told what the account is: its campaigns, the keywords it bids on, and what
// has converted. A client rulebook (below) always beats this.
// =====================================================================
const NAME_NOISE = new Set(("the and of for llc inc ltd corp co company products product ads ad ppc google search digital account us usa ca au uk nz eu na nam eng sem paid adwords " +
  "group holdings international global limited pty gmbh").split(" "));
const NAME_DESCRIPTORS = new Set(("racing dental dentistry family health medical center centre clinic studio design landscape landscaping construction plumbing systems technologies " +
  "technology consulting marketing agency services service solutions associates partners machine precision").split(" "));
// "Beta Bionics Inc." -> "beta bionics"; "AU - Eventgroove Products" -> "eventgroove"; "Brian Tooley Racing" -> "brian tooley racing", "brian tooley"
function brandPhrases(name){
  const raw = String(name || "").toLowerCase(), parts = [raw.replace(/\([^)]*\)/g, " ")];
  (raw.match(/\(([^)]*)\)/g) || []).forEach(p => parts.push(p.slice(1, -1)));
  const out = new Set();
  parts.forEach(part => {
    const toks = [];
    let letters = false;                       // "d&j" is two one-letter tokens: joined, so they match how the name is typed
    part.replace(/&/g, " ").split(/[^a-z0-9]+/).filter(Boolean).forEach(w => {
      if (NAME_NOISE.has(w) || /^\d{5,}$/.test(w)) return;
      if (w.length === 1) { if (letters) toks[toks.length - 1] += w; else toks.push(w); letters = true; }
      else { toks.push(w); letters = false; }
    });
    if (!toks.length) return;
    const add = ws => { const s = ws.join(" "); if (s.replace(/ /g, "").length >= (ws.length === 1 ? 5 : 8)) { out.add(s); if (ws.length > 1) out.add(ws.join("")); } };
    add(toks);
    if (toks.length >= 3) add(toks.slice(0, 2));
    const last = toks[toks.length - 1];
    // dropping a trailing descriptor ("racing", "dental") leaves two words, or one long enough not to be an ordinary word
    if (toks.length >= 2 && NAME_DESCRIPTORS.has(last) && (toks.length >= 3 || toks[0].length >= 8)) add(toks.slice(0, -1));
  });
  return Array.from(out);
}
const AUTO_JOBS = /\b(jobs?|careers?|hiring|salar(?:y|ies)|employment|openings?|internships?|resumes?|stock|investor|investors)\b/i;
function autoRuleset(profile){
  profile = profile || {};
  const phrases = brandPhrases(profile.name), brandRe = phrases.length ? phraseRe(phrases) : null;
  const kw = new Set((profile.keywords || []).map(k => keyText(typeof k === "string" ? k : k && k.text)).filter(Boolean));
  return Object.assign({}, GENERIC, {
    profile, brandPhrases: phrases,
    classifyDeterministic(term){
      const k = normText(term); if (!k) return null;
      // the brand's own jobs and investor searches are not customers: the model reads those
      if (brandRe && brandRe.test(k.replace(/\b([a-z])\s*&\s*([a-z])\b/g, "$1$2").replace(/[^a-z0-9]+/g, " ")) && !AUTO_JOBS.test(k)) return { status: "Relevant", subtype: "Brand" };
      if (kw.has(k)) return { status: "Relevant", subtype: "A keyword the account bids on" };
      return null;
    },
  });
}

const RULESETS = { generic: GENERIC, riccobene: RICCOBENE, oia: OIA, eventgroove: EVENTGROOVE, gentledental_42north: GENTLEDENTAL_42NORTH, mchale: MCHALE, beta_bionics: BETA_BIONICS };

// Place names that are also ordinary words or surnames (Normal, Apex, Independence, Cary) count as ANOTHER campaign's city only when the
// state is named as well, or "normal mri results" is a Location Mismatch in every campaign but Normal, IL's.
const NC = /\b(nc|n\.c\.|north carolina)\b/i;
RICCOBENE.ambiguousCities = { apex: NC, independence: NC, columbia: /\b(sc|south carolina)\b/i, matthews: NC, garner: NC, benson: NC, selma: NC, cary: NC, concord: NC,
  harrisburg: /\b(nc|north carolina)\b/i, ashland: /\b(va|virginia)\b/i, southport: NC };
OIA.ambiguousCities = { normal: /\b(il|illinois)\b/i, bloomington: /\b(il|illinois)\b/i, halls: /\b(tn|tennessee)\b/i, madison: /\b(al|alabama)\b/i,
  athens: /\b(al|alabama)\b/i, clayton: /\b(nc|north carolina)\b/i, brighton: /\b(co|colorado)\b/i };
// A core service word is not trusted when the term is plainly another trade or another kind of thing: "carpet cleaning", "breast implants",
// "oil extraction", "crown royal", "cleaning services near me". Such terms go to the model instead of being called Relevant.
const DENTAL_VETO = /\b(carpet|house|home|window|pool|duct|gutter|car|vehicle|kitchen|office|commercial|pressure|steam|janitorial|maid|oil)\s+cleaning\b|\bcleaning\s+(services?|company|companies|supplies|products|jobs?)\b|\b(breast|hair|penile|cochlear|eye|knee|hip)\s+(implants?|surgery)\b|\b(oil|data|dna|honey|vanilla|stump)\s+extraction\b|\b(crown royal|crowne plaza|crown molding|bridge (loan|card|game)|bridgewater|golf|tire|brake|roof)\b/i;
RICCOBENE.coreVeto = DENTAL_VETO;
GENTLEDENTAL_42NORTH.coreVeto = DENTAL_VETO;
MCHALE.coreVeto = /\bnursery\s+(rhymes?|school|furniture|decor|bedding)\b|\b(baby|kids?|child|childs?|childrens?)\s+nursery\b/i;

// The categories each account's rules leave to the model (tracker/paid_sqr.py) are mostly written with the account's own service words in them,
// so a core word cannot be allowed to settle a term that hints at one. This only routes the term to the model; it decides nothing. The words
// "affordable" and "offer" are deliberately absent: Riccobene's skill says they are NOT price-seeking triggers.
const HINT_JOBS = "jobs?|careers?|hiring|salar(?:y|ies)|employment|openings?|internships?|resumes?";
const HINT_SCHOOL = "schools?|colleges?|universit(?:y|ies)|courses?|certif\\w*|degrees?|programs?|classes|training|how to become";
const HINT_DIY = "diy|home remed\\w*|at home|homemade|natural remed\\w*|kits?";
const HINT_PRICE = "cheap|cheapest|free|discounts?|coupons?|low cost|no cost|bargain";
const HINT_INFO = "what is|what are|how to|how do|why|causes? of|symptoms? of|definition|meaning";
const HINT_PET = "dogs?|cats?|pets?|horses?|veterinar\\w*|vet";
const HINT_PRODUCT = "toothpaste|toothbrush(?:es)?|floss|mouthwash|buy|amazon|walmart|cvs|store";
const HINT_CHARITY = "charity|non-?profit|volunteer|free clinic|donat\\w*";
const hint = (...parts) => new RegExp("\\b(?:" + parts.join("|") + ")\\b", "i");
RICCOBENE.modelHint = hint(HINT_JOBS, HINT_SCHOOL, HINT_DIY, HINT_PRICE, HINT_INFO, HINT_PET, HINT_PRODUCT, HINT_CHARITY);
MCHALE.modelHint = hint(HINT_PRICE, HINT_INFO, "ideas?", "pictures?", "photos?", "images?", "pdf", HINT_CHARITY);
// (jobs, courses, freebies and general questions are settled by the skill's own priorities before this; cost and price are the buying funnel here)
BETA_BIONICS.modelHint = hint(HINT_JOBS, HINT_SCHOOL, "reviews?", "complaints?", HINT_DIY, HINT_INFO, "recalls?", "lawsuits?", "class action");


// =====================================================================
// Negative keywords: what a negative blocks, and what is already blocked
// =====================================================================
function matchOf(m){
  const x = String(m || "").toUpperCase().replace(/\s*MATCH$/, "").trim();
  return x === "EXACT" || x === "BROAD" ? x : x === "PHRASE" ? "PHRASE" : "";
}
function keyText(s){ return normText(String(s == null ? "" : s).replace(/[\[\]"+]/g, "")); }
// Does a negative keyword stop `text` (already lowercase, single-spaced) from showing an ad?
function blocks(neg, text){
  const n = neg.text; if (!n) return false;
  const m = neg.match || "PHRASE";
  if (m === "EXACT") return text === n;
  if (m === "BROAD"){ const tw = text.split(" "); return n.split(" ").every(w => tw.indexOf(w) >= 0); }
  return (" " + text + " ").indexOf(" " + n + " ") >= 0;
}

// Existing negatives, indexed by where they apply. `level`: account | campaign | adgroup | list; `campaigns`: the campaigns a campaign-level
// negative or an attached list reaches. An ad-group negative covers one ad group only, so it never counts as covering a campaign.
function buildCover(existing){
  const account = { exact: new Set(), loose: [] }, camp = new Map();
  (existing || []).forEach(e => {
    const n = { text: keyText(e.text), match: matchOf(e.match) || "EXACT" };
    if (!n.text || e.level === "adgroup") return;
    const put = (b) => { if (n.match === "EXACT") b.exact.add(n.text); else b.loose.push(n); };
    if (e.level === "account") put(account);
    else (e.campaigns || []).forEach(c => { let b = camp.get(c); if (!b) { b = { exact: new Set(), loose: [] }; camp.set(c, b); } put(b); });
  });
  return { account, camp };
}
// Does a bucket of existing negatives already stop a suggested negative (text, match)? An existing EXACT negative stops only that one query, so
// it never covers a phrase or broad suggestion's wider reach.
function covers(b, text, match){
  if (!b) return false;
  if (match === "EXACT") return b.exact.has(text) || b.loose.some(n => blocks(n, text));
  return b.loose.some(n => blocks(n, text));
}
// Is a suggested negative already in place? Account-level suggestions need an account-level negative; a campaign suggestion is also covered
// by that campaign's own negatives and attached lists.
function isCovered(cover, text, match, level, campaigns){
  if (covers(cover.account, text, match)) return true;
  if (level.indexOf("Account") === 0) return false;
  return campaigns.length > 0 && campaigns.every(c => covers(cover.camp.get(c), text, match));
}
// Would the active negatives already stop this term in this campaign?
function isBlockedHere(cover, text, campaign){
  const hit = (b) => !!b && (b.exact.has(text) || b.loose.some(n => blocks(n, text)));
  return hit(cover.account) || hit(cover.camp.get(campaign));
}

// =====================================================================
// The pipeline. Rows in, a verdict on every row, then the negatives and
// keywords worth acting on, ranked.
// =====================================================================
const NO_TERM = /^(total:.*|other search terms?|--)$/i;
const PRIORITY = 30, MODEL_CAP = 1000, IMPLIED_MIN_COST = 1, IMPLIED_NOTE = "Not relevant, spent and never converted";
const STATUS_ORDER = ["Relevant", "Competitor", "Not Relevant", "Client Review", "Not reviewed"];
const isPmax = ct => /pmax|performance[\s_]?max/i.test(ct || "");
const isSearchType = ct => !ct || /^search/i.test(String(ct).trim());   // keywords can be added only to Search campaigns

function prepareRows(raw){
  const rows = [];
  (raw || []).forEach(r => {
    const term = String(r.term == null ? "" : r.term).trim();
    if (!term || NO_TERM.test(term)) return;
    rows.push({ term, key: normText(term), campaign: String(r.campaign || "").trim(), adGroup: String(r.adGroup || "").trim(),
      campaignType: String(r.campaignType || "").trim(), clicks: +r.clicks || 0, impressions: +r.impressions || 0, cost: +r.cost || 0,
      conversions: +r.conversions || 0, status: String(r.status || "").toUpperCase() });
  });
  return rows;
}

// What the account itself says is its brand. A keyword of a Brand campaign (not Non-Brand, not a competitor campaign) is the account's own name for
// itself, and the searches containing it are its brand searches whatever spelling a rulebook knew. A one-word keyword is trusted only as the whole
// search (a bare "heartland" also names dental chains), and a search that mentions jobs, reviews, investors or a firm's money is read by the model.
const BRAND_CAMPAIGN = /brand/i, NOT_BRAND_CAMPAIGN = /non[\s_-]?brand|competitor|conquest/i;
const NOT_A_CUSTOMER = /\b(partners|capital|equity|holdings|investors?|investments?|stock|ceo|funds?|funding|jobs?|careers?|hiring|salary|reviews?|complaints?)\b/i;
function brandKeywords(keywords, accountName){
  const exact = new Set(), phrases = new Set();
  (keywords || []).forEach(k => {
    const t = keyText(typeof k === "string" ? k : k && k.text); if (!t) return;
    exact.add(t);
    const camp = typeof k === "string" ? "" : String((k && k.campaign) || "");
    if (BRAND_CAMPAIGN.test(camp) && !NOT_BRAND_CAMPAIGN.test(camp) && t.split(" ").length >= 2) phrases.add(t);
  });
  // The account's own name, when it is more than one word ("Great Hill Dental"): a one-word name ("Heartland", "Piedmont") is also other businesses' name.
  const named = brandPhrases(accountName).filter(p => p.indexOf(" ") >= 0);
  return { exact, re: phrases.size ? phraseRe(Array.from(phrases)) : null, nameRe: named.length ? phraseRe(named) : null };
}
function fromKeywords(row, B, ruleset){
  const k = normText(row.term);
  if (B.exact.has(k)) return { status: "Relevant", subtype: "A keyword the account bids on" };
  const guarded = NOT_A_CUSTOMER.test(k) || (ruleset.modelHint && ruleset.modelHint.test(k));
  if (B.re && B.re.test(k) && !guarded) return { status: "Relevant", subtype: "Brand (a keyword of the account's Brand campaign)" };
  if (B.nameRe && B.nameRe.test(k.replace(/\b([a-z])\s*&\s*([a-z])\b/g, "$1$2").replace(/[^a-z0-9]+/g, " ")) && !guarded) return { status: "Relevant", subtype: "Brand (the account's name)" };
  return null;
}

async function analyze(ruleset, rawRows, ctx){
  ctx = ctx || {};
  const status = ctx.status || function(){};
  const rows = prepareRows(rawRows);
  if (!rows.length) throw new Error("There are no search terms in that range.");

  // 1 · the account's own rules, on every row
  const B = brandKeywords(ctx.keywords, ctx.accountName);
  const pending = new Map();                       // term -> what it cost, for choosing what the model reads
  rows.forEach((r, i) => {
    r._slug = normSlug(r.campaign);
    r._det = ruleset.classifyDeterministic(r.term, r._slug, r) || fromKeywords(r, B, ruleset);
    if (r._det) r._det.by = "rule";
    else { const p = pending.get(r.key) || { term: r.term, key: r.key, cost: 0, clicks: 0, conversions: 0 }; p.cost += r.cost; p.clicks += r.clicks; p.conversions += r.conversions; pending.set(r.key, p); }
    if (i > 0 && i % 2000 === 0) status("Applying " + ruleset.label + " rules… " + i.toLocaleString() + " / " + rows.length.toLocaleString(), 0.05 + 0.25 * i / rows.length);
  });

  // 2 · the model reads only terms that were clicked (a term nobody clicked cost nothing and decides nothing), the costliest first, up to a limit
  const cap = ctx.modelCap == null ? MODEL_CAP : ctx.modelCap;
  const ranked = Array.from(pending.values()).sort((a, b) => (b.cost - a.cost) || (b.clicks - a.clicks));
  const eligible = ranked.filter(p => p.clicks > 0 || p.cost > 0 || p.conversions > 0);
  const toRead = eligible.slice(0, cap);
  const readKeys = new Set(toRead.map(p => p.key));
  status(toRead.length ? "Asking the model about " + toRead.length.toLocaleString() + " terms no rule settled…" : "Every term that mattered was settled by a rule.", 0.32);
  const llm = toRead.length && ctx.classify ? await ctx.classify(toRead.map(p => p.term)) : { results: {}, fallback: 0, failures: [] };
  const answers = new Map();
  Object.keys(llm.results || {}).forEach(t => answers.set(normText(t), llm.results[t]));
  rows.forEach(r => {
    if (r._det) return;
    if (readKeys.has(r.key)) r._det = Object.assign({}, answers.get(r.key) || { status: "Not Relevant", subtype: "No Clear Intent", by: "fallback" });
    else r._det = { status: "Not reviewed", subtype: r.clicks > 0 || r.cost > 0 ? "Over the review limit" : "No clicks", by: "skipped" };
  });

  status("Building negatives, keywords and the summary…", 0.95);
  const cover = buildCover(ctx.negatives);
  const kwSet = new Set((ctx.keywords || []).map(k => keyText(k.text)).filter(Boolean));
  const out = { negatives: buildNegatives(rows, cover, ctx.keywords || [], ruleset), keywords: buildKeywords(rows, kwSet, cover) };
  const review = {};
  rows.filter(r => r._det.status === "Client Review").forEach(r => {
    const k = r._det.subtype; const e = review[k] || (review[k] = { subtype: k, count: 0, cost: 0, conversions: 0, ex: [] });
    e.count++; e.cost += r.cost; e.conversions += r.conversions; e.ex.push(r);
  });
  const clientReview = Object.values(review).map(e => ({ subtype: e.subtype, count: e.count, cost: e.cost, conversions: e.conversions,
    examples: Array.from(new Set(e.ex.sort((a, b) => b.cost - a.cost).map(r => r.term))).slice(0, 5) })).sort((a, b) => (b.cost - a.cost) || (b.count - a.count));

  const byStatus = {}, byBy = {};
  rows.forEach(r => { byStatus[r._det.status] = (byStatus[r._det.status] || 0) + 1; byBy[r._det.by || ""] = (byBy[r._det.by || ""] || 0) + 1; });
  const sum = (f, p) => rows.reduce((s, r) => s + (p(r) ? f(r) : 0), 0), all = () => true;
  const totalSpend = sum(r => r.cost, all);
  const relevantSpend = sum(r => r.cost, r => r._det.status === "Relevant");
  return {
    ruleset, rows, allNegatives: out.negatives.all, priorityNegatives: out.negatives.priority, reviewNegatives: out.negatives.review,
    allKeywords: out.keywords.all, priorityKeywords: out.keywords.all.slice(0, PRIORITY), keywordsSkipped: out.keywords.skipped,
    negativesSkipped: out.negatives.skipped, clientReview,
    summary: { totalRows: rows.length, uniqueTerms: new Set(rows.map(r => r.key)).size, totalSpend, totalConv: sum(r => r.conversions, all), byStatus,
      byRule: byBy.rule || 0, byModel: byBy.model || 0, fallback: byBy.fallback || 0, skipped: byBy.skipped || 0, failures: llm.failures || [],
      notReviewedSpend: sum(r => r.cost, r => r._det.status === "Not reviewed"), overLimit: rows.filter(r => r._det.subtype === "Over the review limit").length,
      wastedSpend: sum(r => r.cost, r => r._det.status === "Not Relevant" && r.conversions === 0),
      competitorSpend: sum(r => r.cost, r => r._det.status === "Competitor"),
      relevantPct: Math.round(100 * (byStatus["Relevant"] || 0) / rows.length), relevantSpendPct: totalSpend > 0 ? Math.round(100 * relevantSpend / totalSpend) : null },
  };
}

function negativeSpec(r, ruleset){
  const d = r._det;
  if (d.by === "fallback" || d.by === "skipped" || d.noNeg || r.status === "EXCLUDED" || r.status === "ADDED_EXCLUDED") return null;
  // Brand searches that land in a NonBrand campaign belong in the Brand campaign: a campaign-level negative on the brand word routes them there.
  if (d.routeNeg && ruleset && ruleset.routeCampaignRe && ruleset.routeCampaignRe.test(r.campaign) && !isPmax(r.campaignType)) {
    return { text: normText(d.routeNeg), match: "Phrase", level: "Campaign", explicit: true, routing: true };
  }
  const explicit = d.status === "Competitor" || (d.status === "Not Relevant" && d.negMatchType);
  if (!explicit && d.status !== "Not Relevant") return null;
  let match = explicit ? (d.negMatchType || "Exact") : "Exact";
  let text = normText(d.negText || r.term);
  // A suggested negative has to stop the term that raised it: "dental school" would not stop "school of dental hygiene".
  if (explicit && d.negText && match !== "Exact" && (" " + r.key + " ").indexOf(" " + text + " ") < 0) text = r.key;
  // Some words are never to be blocked on their own for an account (Beta Bionics: pump, insulin, medicare...): block the whole search instead, or nothing.
  if (ruleset && ruleset.forbiddenNegatives && ruleset.forbiddenNegatives.indexOf(text) >= 0) {
    if (text === r.key) return null;
    text = r.key; match = "Exact";
  }
  // A campaign's own negative, Performance Max included (Google accepts campaign-level negative keywords there now, and these accounts use them):
  // turning one into an account-wide block would also stop the term in campaigns where it belongs.
  const level = explicit ? (d.negLevel || "Account") : "Campaign";
  return { text, match, level, explicit };
}

function buildNegatives(rows, cover, keywords, ruleset){
  const groups = new Map(); let skipped = 0;
  rows.forEach(r => {
    const s = negativeSpec(r, ruleset); if (!s) return;
    if (isCovered(cover, s.text, matchOf(s.match), s.level, s.level === "Campaign" ? [r.campaign] : [])) { skipped++; return; }
    const key = s.text + "|" + s.match + "|" + s.level + "|" + (s.level === "Campaign" ? r.campaign : "");
    let g = groups.get(key);
    if (!g) { g = { text: s.text, matchType: s.match, level: s.level, campaign: s.level === "Campaign" ? r.campaign : "", campaigns: new Set(), reason: r._det.subtype, reasonCost: -1,
      by: new Set(), implied: true, routing: !!s.routing, cost: 0, wasted: 0, conversions: 0, clicks: 0, impressions: 0, terms: new Set(), ex: [], rivals: new Set(), rival: false }; groups.set(key, g); }
    if (r._det.status === "Competitor") g.rival = true;
    if (r._det.status === "Competitor") { const n = competitorName(ruleset || {}, r.term); if (n) g.rivals.add(n); }
    g.campaigns.add(r.campaign); g.by.add(r._det.by); if (s.explicit) g.implied = false;
    g.cost += r.cost; g.clicks += r.clicks; g.impressions += r.impressions; g.conversions += r.conversions;
    if (r.conversions === 0) g.wasted += r.cost;
    g.terms.add(r.key); g.ex.push(r);
    if (r.cost > g.reasonCost) { g.reasonCost = r.cost; g.reason = s.routing ? "Brand search in a NonBrand campaign: send it to the Brand campaign" : s.explicit ? r._det.subtype : IMPLIED_NOTE + " (" + r._det.subtype + ")"; }
  });
  // what the relevant rows would lose: the words of each relevant term, so a negative is only compared with terms that share its words
  const rel = rows.filter(r => r._det.status === "Relevant" && r.status !== "EXCLUDED");
  const index = new Map();
  rel.forEach((r, i) => new Set(r.key.split(" ")).forEach(w => { let a = index.get(w); if (!a) { a = []; index.set(w, a); } a.push(i); }));
  // An account with a campaign aimed at competitors wants competitor searches: a block at account level would reach that campaign too.
  const CONQUEST = /compet|conquest/i;
  const conquests = rows.some(r => CONQUEST.test(r.campaign)) || keywords.some(k => CONQUEST.test(k.campaign || ""));
  const kwTexts = keywords.map(k => ({ text: keyText(k.text), campaign: k.campaign || "" })).filter(k => k.text);
  const kwByText = new Map(); kwTexts.forEach(k => { let a = kwByText.get(k.text); if (!a) { a = []; kwByText.set(k.text, a); } a.push(k); });
  const kwConflicts = (neg, g) => {
    const inScope = k => g.level !== "Campaign" || k.campaign === g.campaign;
    if (neg.match === "EXACT") return (kwByText.get(neg.text) || []).filter(inScope).slice(0, 3).map(k => k.text);
    const first = neg.text.split(" ")[0], out = [];
    for (let i = 0; i < kwTexts.length && out.length < 3; i++) { const k = kwTexts[i]; if (k.text.indexOf(first) >= 0 && inScope(k) && blocks(neg, k.text)) out.push(k.text); }
    return out;
  };
  const all = [];
  groups.forEach(g => {
    // an implied negative needs real spend and no conversion: below that it is noise, or a risk
    if (g.implied && (g.cost < IMPLIED_MIN_COST || g.conversions > 0)) { skipped++; return; }
    const neg = { text: g.text, match: matchOf(g.matchType) };
    const words = g.text.split(" ");
    const cand = g.matchType === "Exact" ? (index.get(words[0]) || []) : words.map(w => index.get(w) || []).sort((a, b) => a.length - b.length)[0] || [];
    const hit = { n: 0, clicks: 0, conversions: 0, cost: 0, ex: [] };
    cand.forEach(i => {
      const r = rel[i];
      if (g.level === "Campaign" && r.campaign !== g.campaign) return;
      if (!blocks(neg, r.key)) return;
      hit.n++; hit.clicks += r.clicks; hit.conversions += r.conversions; hit.cost += r.cost; if (hit.ex.length < 3 && hit.ex.indexOf(r.term) < 0) hit.ex.push(r.term);
    });
    const conflicts = kwConflicts(neg, g);
    // An account that bids on a competitor's name on purpose does not want that competitor's searches blocked.
    const bids = Array.from(g.rivals).filter(name => kwTexts.some(k => (g.level !== "Campaign" || k.campaign === g.campaign) && (" " + k.text + " ").indexOf(" " + name + " ") >= 0));
    const risks = [];
    // A routing negative is meant to stop brand terms in this campaign, converted or not; what it stops is the point, not a side effect.
    if (g.routing) { hit.n = 0; hit.clicks = 0; hit.conversions = 0; hit.cost = 0; hit.ex = []; }
    if (g.routing) conflicts.length = 0;
    if (!g.routing && g.conversions > 0) risks.push("converted");
    if (g.routing) { /* no competitor checks */ }
    else if (bids.length) risks.push("you bid on this competitor");
    else if (g.rival && conquests && g.level.indexOf("Account") === 0) risks.push("the account runs a competitor campaign");
    if (!g.routing && (hit.clicks > 0 || hit.conversions > 0)) risks.push("blocks relevant terms");
    if (!g.routing && conflicts.length) risks.push("blocks a keyword you bid on");
    const examples = Array.from(new Map(g.ex.sort((a, b) => b.cost - a.cost).map(r => [r.key, r.term])).values()).slice(0, 3);
    all.push({ text: g.text, matchType: g.matchType, level: g.level, campaign: g.campaign, reason: g.reason, by: g.by.size > 1 ? "mixed" : Array.from(g.by)[0] || "rule",
      implied: g.implied, routing: g.routing, cost: g.cost, wasted: g.routing ? g.cost : g.wasted, impact: g.routing ? g.cost : g.wasted, conversions: g.conversions, clicks: g.clicks, impressions: g.impressions, terms: g.terms.size,
      examples, risks, collateral: hit, conflicts });
  });
  // Ranked by the money wasted on terms that never converted. A negative whose terms did convert, or that would also stop relevant terms or
  // a keyword the account bids on, is held back for a person to look at: it is never in the priority list.
  all.sort((a, b) => (b.wasted - a.wasted) || (b.clicks - a.clicks) || (b.impressions - a.impressions) || (a.text < b.text ? -1 : 1));
  return { all, priority: all.filter(n => !n.risks.length && n.wasted > 0).slice(0, PRIORITY), review: all.filter(n => n.risks.length).sort((a, b) => (b.cost - a.cost) || (b.conversions - a.conversions)).slice(0, PRIORITY), skipped };
}

function buildKeywords(rows, kwSet, cover){
  const groups = new Map(); const skipped = { existing: 0, blocked: 0, type: 0, weak: 0 };
  rows.forEach(r => {
    if (r._det.status !== "Relevant") return;
    const pm = isPmax(r.campaignType);
    const k = r.key + "|" + r.campaign;
    let g = groups.get(k);
    if (!g) { g = { key: r.key, term: r.term, campaign: r.campaign, campaignType: r.campaignType, pmax: pm, status: new Set(), ad: new Map(), clicks: 0, impressions: 0, cost: 0, conversions: 0 }; groups.set(k, g); }
    g.status.add(r.status); g.clicks += r.clicks; g.impressions += r.impressions; g.cost += r.cost; g.conversions += r.conversions;
    const a = g.ad.get(r.adGroup) || { conversions: 0, clicks: 0 }; a.conversions += r.conversions; a.clicks += r.clicks; g.ad.set(r.adGroup, a);
  });
  const list = [];
  groups.forEach(g => {
    if (!g.pmax && !isSearchType(g.campaignType)) { skipped.type++; return; }         // Shopping, Display and the rest take no keywords
    if (kwSet.has(g.key) || g.status.has("ADDED") || g.status.has("ADDED_EXCLUDED") || g.status.has("EXCLUDED")) { skipped.existing++; return; }
    if (isBlockedHere(cover, g.key, g.campaign)) { skipped.blocked++; return; }
    const ctr = g.impressions > 0 ? g.clicks / g.impressions : 0;
    const tier = g.conversions >= 1 ? 1 : (g.conversions > 0 || (g.clicks >= 3 && ctr >= 0.05)) ? 2 : 0;
    if (!tier) { skipped.weak++; return; }
    const best = Array.from(g.ad.entries()).sort((a, b) => (b[1].conversions - a[1].conversions) || (b[1].clicks - a[1].clicks))[0];
    const wordCount = g.term.trim().split(/\s+/).length;
    list.push({ term: g.term, assetType: g.pmax ? "Search Theme" : "Keyword", matchType: g.pmax ? "Search Theme" : (wordCount >= 3 && (g.conversions > 0 || ctr >= 0.05)) ? "Exact Match" : "Phrase Match",
      addLevel: g.pmax ? "Asset Group" : "Ad Group", whereToAdd: g.campaign + (g.pmax || !best || !best[0] ? "" : " / " + best[0]), tier, conversions: g.conversions, ctr,
      clicks: g.clicks, impressions: g.impressions, cost: g.cost, cpa: g.conversions > 0 ? g.cost / g.conversions : null });
  });
  list.sort((a, b) => (a.tier - b.tier) || (b.conversions - a.conversions) || (b.ctr - a.ctr) || (b.clicks - a.clicks) || (a.term < b.term ? -1 : 1));
  return { all: list, skipped };
}

// =====================================================================
// The three CSV exports, for when Google Ads cannot be read
// =====================================================================
function rowsFromCsv(text){
  const p = parseCSV(text);
  if (!p.headerFound) return { error: "Couldn't find a 'Search term' column in that file. First few lines: " + p.preview.map(r => "[" + r.join(" | ") + "]").join("  "), rows: [] };
  const rows = p.rows.map(raw => ({ term: pick(raw, "Search term", "Search Term"), campaign: pick(raw, "Campaign"), adGroup: pick(raw, "Ad group", "Ad Group"),
    campaignType: pick(raw, "Campaign type", "Campaign Type"), clicks: num(pick(raw, "Clicks")), impressions: num(pick(raw, "Impr.", "Impressions")),
    cost: num(pick(raw, "Cost")), conversions: num(pick(raw, "Conversions", "Conv.")), status: pick(raw, "Added/Excluded", "Added / Excluded") }));
  rows.forEach(r => { const s = String(r.status || "").toLowerCase(); r.status = s === "added" ? "ADDED" : s === "excluded" ? "EXCLUDED" : ""; });
  return { rows, header: p.header };
}
function keywordsFromCsv(text){
  if (!text) return [];
  return parseCSV(text).rows.map(r => ({ text: keyText(pick(r, "Keyword")), match: matchOf(pick(r, "Match type")), campaign: pick(r, "Campaign"), adGroup: pick(r, "Ad group") })).filter(k => k.text);
}
function negativesFromCsv(text){
  if (!text) return [];
  return parseCSV(text).rows.map(r => {
    const level = /campaign/i.test(String(pick(r, "Level"))) ? "campaign" : /ad group/i.test(String(pick(r, "Level"))) ? "adgroup" : "account";
    const c = String(pick(r, "Campaign")).trim();
    return { text: keyText(pick(r, "Negative keyword", "Keyword")), match: matchOf(pick(r, "Match type")), level: level === "campaign" && !c ? "account" : level, campaigns: c ? [c] : [] };
  }).filter(k => k.text);
}

const api = { autoRuleset, brandPhrases, brandKeywords, parseCSV, pick, num, fmtConv, normText, keyText, matchOf, blocks, buildCover, isCovered, isBlockedHere, phraseRe, signalRe, has, core, individual, isAddress, competitorName,
  campaignServesCity, prepareRows, analyze, rowsFromCsv, keywordsFromCsv, negativesFromCsv, RULESETS, PRIORITY, MODEL_CAP, STATUS_ORDER };
root.SQRCore = api;
if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
