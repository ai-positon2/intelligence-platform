"""Apollo's industry vocabulary, and the mapping from what people type onto it.

Apollo has no industry filter and no endpoint that enumerates its industries, so
this module holds the vocabulary itself. Two sources feed it:

  SEED_INDUSTRIES -- Apollo's classification, which is the LinkedIn industry
      taxonomy: lowercase, ampersands rather than "and", and slashes in a few
      compound names. Written down here so a picker can offer real values instead
      of asking someone to guess.

  learned values -- every industry string the app has actually seen on an Apollo
      company record. These are correct by construction: Apollo returned them.
      They are merged over the seed at read time, so if Apollo renames, adds or
      retires a value, the picker follows without a code change, and a seed entry
      that never appears in real data is visibly never confirmed.

Keeping the two apart matters. A picker offering a value Apollo does not use
sends a search that quietly matches nothing, which is the failure this whole
module exists to prevent, so the API marks which values are confirmed.
"""

# Apollo's own values, verbatim: lowercase, "&" not "and", slashes preserved.
# Anything typed by a user is matched against these rather than compared to them,
# so a near-miss degrades to a broader match instead of to nothing.
SEED_INDUSTRIES = (
    "accounting", "airlines/aviation", "alternative dispute resolution",
    "alternative medicine", "animation", "apparel & fashion",
    "architecture & planning", "arts & crafts", "automotive",
    "aviation & aerospace", "banking", "biotechnology", "broadcast media",
    "building materials", "business supplies & equipment", "capital markets",
    "chemicals", "civic & social organization", "civil engineering",
    "commercial real estate", "computer & network security", "computer games",
    "computer hardware", "computer networking", "computer software",
    "construction", "consumer electronics", "consumer goods",
    "consumer services", "cosmetics", "dairy", "defense & space", "design",
    "e-learning", "education management",
    "electrical/electronic manufacturing", "entertainment",
    "environmental services", "events services", "executive office",
    "facilities services", "farming", "financial services", "fine art",
    "fishery", "food & beverages", "food production", "fund-raising",
    "furniture", "gambling & casinos", "glass, ceramics & concrete",
    "government administration", "government relations", "graphic design",
    "health, wellness & fitness", "higher education", "hospital & health care",
    "hospitality", "human resources", "import & export",
    "individual & family services", "industrial automation",
    "information services", "information technology & services", "insurance",
    "international affairs", "international trade & development", "internet",
    "investment banking", "investment management", "judiciary",
    "law enforcement", "law practice", "legal services", "legislative office",
    "leisure, travel & tourism", "libraries", "logistics & supply chain",
    "luxury goods & jewelry", "machinery", "management consulting", "maritime",
    "market research", "marketing & advertising",
    "mechanical or industrial engineering", "media production",
    "medical devices", "medical practice", "mental health care", "military",
    "mining & metals", "motion pictures & film", "museums & institutions",
    "music", "nanotechnology", "newspapers",
    "nonprofit organization management", "oil & energy", "online media",
    "outsourcing/offshoring", "package/freight delivery",
    "packaging & containers", "paper & forest products", "performing arts",
    "pharmaceuticals", "philanthropy", "photography", "plastics",
    "political organization", "primary/secondary education", "printing",
    "professional training & coaching", "program development", "public policy",
    "public relations & communications", "public safety", "publishing",
    "railroad manufacture", "ranching", "real estate",
    "recreational facilities & services", "religious institutions",
    "renewables & environment", "research", "restaurants", "retail",
    "security & investigations", "semiconductors", "shipbuilding",
    "sporting goods", "sports", "staffing & recruiting", "supermarkets",
    "telecommunications", "textiles", "think tanks", "tobacco",
    "translation & localization", "transportation/trucking/railroad",
    "utilities", "venture capital & private equity", "veterinary",
    "warehousing", "wholesale", "wine & spirits", "wireless",
    "writing & editing",
)

# What people type -> the Apollo values they mean. Nobody types "hospital &
# health care"; they type "healthcare", which appears nowhere in the taxonomy
# above. Without this, the honest strict filter returns nothing and looks broken.
#
# Each family is a search aid, not a claim that these industries are the same
# thing: selecting "healthcare" is selecting all eight of its values at once, and
# the picker shows exactly which.
FAMILIES = {
    "healthcare": ("hospital & health care", "medical practice",
                   "medical devices", "pharmaceuticals", "biotechnology",
                   "mental health care", "health, wellness & fitness",
                   "veterinary", "alternative medicine"),
    "technology": ("computer software", "information technology & services",
                   "internet", "computer & network security",
                   "computer hardware", "computer networking",
                   "semiconductors", "nanotechnology", "consumer electronics",
                   "information services"),
    "software": ("computer software", "internet",
                 "information technology & services", "computer games"),
    "telecom": ("telecommunications", "wireless"),
    "finance": ("financial services", "banking", "insurance",
                "investment banking", "investment management",
                "capital markets", "venture capital & private equity",
                "accounting"),
    "retail & consumer": ("retail", "consumer goods", "consumer services",
                          "apparel & fashion", "luxury goods & jewelry", "supermarkets",
                          "wholesale", "consumer electronics", "cosmetics", "furniture",
                          "sporting goods"),
    "manufacturing": ("industrial automation", "machinery",
                      "electrical/electronic manufacturing", "automotive",
                      "aviation & aerospace", "chemicals", "building materials",
                      "plastics", "packaging & containers", "textiles",
                      "mechanical or industrial engineering", "shipbuilding",
                      "glass, ceramics & concrete", "paper & forest products"),
    "education": ("education management", "higher education", "e-learning",
                  "primary/secondary education",
                  "professional training & coaching", "libraries"),
    "marketing": ("marketing & advertising",
                  "public relations & communications", "market research",
                  "design", "graphic design"),
    "media": ("media production", "broadcast media", "publishing",
              "online media", "entertainment", "music",
              "motion pictures & film", "newspapers", "animation",
              "photography", "writing & editing", "printing"),
    "real estate & construction": ("real estate", "commercial real estate", "construction",
                                   "architecture & planning", "civil engineering",
                                   "building materials"),
    "energy": ("oil & energy", "renewables & environment", "utilities",
               "mining & metals", "environmental services"),
    "logistics": ("transportation/trucking/railroad",
                  "logistics & supply chain", "package/freight delivery",
                  "maritime", "airlines/aviation", "warehousing",
                  "import & export"),
    "hospitality & food": ("hospitality", "restaurants", "food & beverages",
                           "leisure, travel & tourism",
                           "recreational facilities & services", "food production",
                           "wine & spirits", "gambling & casinos", "events services"),
    "legal": ("law practice", "legal services",
              "alternative dispute resolution", "judiciary"),
    "government": ("government administration", "public policy",
                   "government relations", "military", "political organization",
                   "legislative office", "public safety", "law enforcement",
                   "executive office", "judiciary"),
    "nonprofit": ("nonprofit organization management", "philanthropy",
                  "civic & social organization", "international affairs",
                  "religious institutions", "fund-raising",
                  "individual & family services", "think tanks",
                  "program development"),
    "staffing": ("staffing & recruiting", "human resources",
                 "professional training & coaching"),
    "consulting": ("management consulting", "outsourcing/offshoring",
                   "business supplies & equipment", "research"),
    "agriculture": ("farming", "ranching", "dairy", "fishery",
                    "food production", "tobacco"),
    "sports & recreation": ("sports", "sporting goods",
                            "recreational facilities & services", "performing arts"),
}

# Read as aliases of a family, so FAMILIES stays a list of industries rather
# than doubling as a thesaurus.
#
# Nothing here may be an exact Apollo industry, and no family may be named after
# one either. "banking", "farming" and "utilities" are all real Apollo values that
# were aliased to broad families, so asking for banks returned insurers and
# accountants: the same over-broad match this module exists to prevent, one level
# up. Four families were named after Apollo values too, which put two
# identical-looking rows in the picker meaning different things, the broad one
# shadowing the precise one. A word Apollo uses now means that value, and the
# family sits beside it under a name of its own.
ALIASES = {
    "health": "healthcare", "health care": "healthcare",
    "medical": "healthcare", "healthtech": "healthcare",
    "health tech": "healthcare", "pharma": "healthcare",
    "biotech": "healthcare", "life sciences": "healthcare",
    "hospitals": "healthcare",
    "tech": "technology", "it": "technology", "information technology": "technology",
    "saas": "software", "b2b software": "software", "cloud": "software",
    "telecommunication": "telecom", "telco": "telecom",
    "fintech": "finance", "financial": "finance", "financial technology": "finance", "insurance tech": "finance",
    "insurtech": "finance",
    "ecommerce": "retail & consumer", "e-commerce": "retail & consumer", "consumer": "retail & consumer",
    "cpg": "retail & consumer", "fmcg": "retail & consumer", "fashion": "retail & consumer",
    "industrial": "manufacturing", "factory": "manufacturing",
    "advertising": "marketing", "adtech": "marketing", "martech": "marketing",
    "agency": "marketing", "pr": "marketing",
    "edtech": "education", "schools": "education", "universities": "education",
    "proptech": "real estate & construction", "property": "real estate & construction",
    "supply chain": "logistics", "transportation": "logistics",
    "shipping": "logistics", "freight": "logistics",
    "travel": "hospitality & food", "food": "hospitality & food", "restaurant": "hospitality & food",
    "hotels": "hospitality & food", "tourism": "hospitality & food",
    "non-profit": "nonprofit", "ngo": "nonprofit", "charity": "nonprofit",
    "public sector": "government", "govtech": "government",
    "recruiting": "staffing", "hr": "staffing", "hrtech": "staffing",
    "agritech": "agriculture", "agtech": "agriculture",
    "renewable": "energy", "cleantech": "energy", "oil": "energy",
    "mining": "energy", 
}


import re


def norm(s: str) -> str:
    """Lowercased with punctuation and spacing removed, so "Hospital & Health
    Care", "hospital and health care" and "hospital/health-care" all compare
    equal. Apollo is not consistent about any of the three, and neither are
    people typing into a box."""
    s = str(s or "").strip().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", s)


def family_for(term: str) -> str:
    """The family a typed term names, or "" if it names none."""
    t = str(term or "").strip().lower()
    if not t:
        return ""
    fam = ALIASES.get(t, t)
    return fam if fam in FAMILIES else ""


def expand(terms) -> set:
    """The requested terms as the set of normalized values they mean.

    A term that names a family expands to that family's industries. A term that
    does not is kept as itself, so an exact Apollo value picked from the dropdown,
    and a fragment someone typed by hand, both still work.
    """
    out: set = set()
    for raw in (terms or []):
        term = str(raw or "").strip().lower()
        if not term:
            continue
        out.add(norm(term))
        fam = family_for(term)
        if fam:
            out.add(norm(fam))
            for v in FAMILIES[fam]:
                out.add(norm(v))
    out.discard("")
    return out


def industries_for(term: str) -> list:
    """The Apollo values a single typed term resolves to, for display.

    This is what makes the picker honest: choosing "healthcare" is choosing nine
    Apollo industries, and the UI can name them instead of implying Apollo has a
    value spelled "healthcare".
    """
    fam = family_for(term)
    if fam:
        return list(FAMILIES[fam])
    t = norm(term)
    if not t:
        return []
    exact = [i for i in SEED_INDUSTRIES if norm(i) == t]
    return exact or [i for i in SEED_INDUSTRIES if t in norm(i) or norm(i) in t]


# How many entries one picker request may return. It used to be 40, which was
# below the size of every vocabulary this app holds, so the list was a hard
# alphabetical stop rather than a list: opening the industry picker and scrolling
# to the bottom reached "executive office" and nothing after it. Families sort
# above individual industries, so those first 40 entries were 21 families and
# only 19 industries: 128 of the
# 147 industries Apollo actually uses could not be browsed to at all. The cap now
# sits above every seed vocabulary, so browsing reaches the end of the real list,
# and `meta` reports when it is hit anyway (a user's learned locations can exceed
# it) so a capped list can say so instead of ending silently.
PICKER_LIMIT = 300


def suggest(query: str, learned=None, limit: int = PICKER_LIMIT, meta=None) -> list:
    """Ranked picker entries for a partly-typed query.

    Each entry is {value, kind, confirmed, covers}:
      value      what gets sent as the filter
      kind       "family" (expands to several industries) or "industry"
      confirmed  this exact string has been seen on a real Apollo record
      covers     for a family, the industries it selects

    Families rank above individual industries, and a prefix match above a
    mid-string one, because someone typing "heal" wants "healthcare" first and
    "mental health care" after it, not alphabetically.

    If `meta` is passed a dict it is filled in with {"total", "truncated"}: how
    many entries actually matched, before `limit` was applied. Same out-param
    shape apollo_client.search_people uses for Apollo's pagination totals, and
    for the same reason: a caller that shows a capped list must be able to say
    it is capped rather than presenting the first N as the whole vocabulary.
    """
    q = norm(query)
    learned_norm = {norm(v): v for v in (learned or []) if str(v or "").strip()}
    out = []

    for fam in sorted(FAMILIES):
        if q and q not in norm(fam) and not any(q in norm(v) for v in FAMILIES[fam]):
            continue
        out.append({"value": fam, "kind": "family", "confirmed": False,
                    "covers": list(FAMILIES[fam]),
                    "_rank": (0, 0 if norm(fam).startswith(q) else 1, fam)})

    # Learned values that are not in the seed list are real Apollo values this
    # code did not know about, so they are offered too rather than hidden.
    known = {norm(i): i for i in SEED_INDUSTRIES}
    for key, original in learned_norm.items():
        known.setdefault(key, original)
    for key in sorted(known, key=lambda k: known[k]):
        value = known[key]
        if q and q not in key:
            continue
        out.append({"value": value, "kind": "industry",
                    "confirmed": key in learned_norm, "covers": [],
                    "_rank": (1, 0 if key.startswith(q) else 1, value)})

    out.sort(key=lambda e: e["_rank"])
    for e in out:
        e.pop("_rank", None)
    if meta is not None:
        meta["total"] = len(out)
        meta["truncated"] = len(out) > limit
    return out[:limit]
