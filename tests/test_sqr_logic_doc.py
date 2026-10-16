"""Search Query Analysis against the team's written logic (sqr-agent-logic.md).

That document is the account-by-account statement of what each ruleset does: its order of rules, its word lists, its statuses and negative
instructions. Each row of it is a case here, run through static/js/sqr_core.js in Node, so a rule that is described but not implemented (or
broken by a later change) fails by name. Where the page deliberately does better than the document (whole words instead of substrings, a term
the model reads when it hints at jobs or school) the case says so.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "static", "js", "sqr_core.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

REL, COMP, NR, CR, MODEL = "Relevant", "Competitor", "Not Relevant", "Client Review", None
NOTCOMP = "not a Competitor"


def run(ruleset, cases):
    """cases: [(term, campaign slug, status, subtype prefix or None)] -> list of mismatch strings."""
    code = ("const C = require(%s); const rs = C.RULESETS[%s]; const out = [];\n"
            "for (const [t, slug] of %s) { const v = rs.classifyDeterministic(t, slug); out.push(v ? [v.status, v.subtype, v.negMatchType || '', v.negText || '', v.negLevel || ''] : null); }\n"
            "console.log(JSON.stringify(out));") % (json.dumps(CORE), json.dumps(ruleset), json.dumps([[c[0], c[1]] for c in cases]))
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    bad = []
    for (term, slug, status, sub), g in zip(cases, got):
        if status == NOTCOMP:
            if g is not None and g[0] == COMP:
                bad.append("%r: a protected phrase was called a Competitor" % term)
        elif status is MODEL:
            if g is not None:
                bad.append("%r: expected the model (no rule), got %s" % (term, g[:2]))
        elif g is None or g[0] != status or (sub and not g[1].lower().startswith(sub.lower())):
            bad.append("%r: expected %s / %s, got %s" % (term, status, sub, g and g[:2]))
    return bad, got


def check(ruleset, cases):
    bad, _ = run(ruleset, cases)
    assert not bad, "\n".join(bad)


# ── 3. Riccobene ────────────────────────────────────────────────────────────

def test_riccobene_1_brand_words():
    check("riccobene", [(t, "x", REL, "Brand") for t in
          ["brush and floss dentist", "brush&floss", "brushandfloss near me", "riccobene dentist", "ricobene dental", "riccoboni dentist", "riccobeni",
           "rico bene dentistry", "commonwealth dentistry", "brush and floss vs aspen dental"]])


def test_riccobene_2_competitors_individuals_and_the_protected_phrases():
    check("riccobene", [(t, "x", COMP, "Named competitor") for t in
          ["aspen dental near me", "clearchoice implants", "heartland dental", "pacific dental", "smile direct club", "smiledirectclub reviews", "tend dental",
           "great expressions", "bright now dental", "western dental", "kool smiles", "cary family dental", "dr jason sain dds", "dr smith", "john smith dds",
           "jones pllc", "smith dmd"]])
    check("riccobene", [(t, "x", REL, "Core dental") for t in
          ["walk in dentistry", "walk-in dentistry", "dental urgent care", "magnetic dentures cost", "child dentist near me", "24 hour emergency dental clinic", "urgent care dental"]])


def test_riccobene_3_to_8_address_phone_school_ppt_spanish_price_medicare():
    check("riccobene", [
        ("123 main street dentist", "x", NR, "Specific Address Query"), ("4500 falls of neuse rd dentist", "x", NR, "Specific Address Query"),
        ("919-555-1234 dentist", "x", NR, "No Clear Dental Intent"), ("(919) 555 1234", "x", NR, "No Clear Dental Intent"),
        ("hygiene school near me", "x", NR, "Education/Training"), ("dental ppt", "x", NR, "Informational"),
        ("dentista sin dinero", "x", NR, "Price-Seeking"), ("no tengo dinero dentista", "x", NR, "Price-Seeking"),
        ("dentist that takes medicare", "x", NR, "Medicare Not Accepted"), ("medicare dental plans", "x", NR, "Medicare Not Accepted"),
    ])
    bad, got = run("riccobene", [("hygiene school near me", "x", NR, None), ("dentist that takes medicare", "x", NR, None)])
    assert got[0][2:] == ["Phrase", "dental school", "Account"] and got[1][2:] == ["Phrase", "medicare", "Account"]


def test_riccobene_9_to_12_client_review():
    check("riccobene", [("dentist that takes medicaid", "x", CR, "Medicaid Unconfirmed")] +
          [("%s dentist" % c, "x", CR, "Insurance Network Brand") for c in
           ["delta dental", "deltacare", "metlife", "cigna", "aetna", "humana", "guardian", "spirit dental", "united concordia", "united healthcare dental", "tricare dental", "benecare", "principal dental"]] +
          [("dentist mint hill nc", "x", CR, "Potential New Service Area (Mint Hill"), ("dentist selma nc", "x", CR, "Potential New Service Area (Selma")] +
          [(t, "x", CR, "Bilingual") for t in ["dentista cerca de mi", "dolor de muela", "limpieza dental", "ortodoncia", "frenillos", "implante dental", "clinica dental"]])


def test_riccobene_13_out_of_area_cities_are_phrase_negatives_at_campaign_level():
    cities = ["boston ma", "new york", "los angeles", "chicago", "houston tx", "phoenix az", "san antonio", "san diego", "dallas tx", "san jose ca", "austin tx", "jacksonville fl",
              "columbus oh", "san francisco", "indianapolis", "seattle wa", "denver co", "washington dc", "nashville tn", "portland or", "las vegas nv", "louisville ky", "memphis tn",
              "baltimore md", "richmond va", "norfolk va", "virginia beach", "hampton va", "chesapeake va", "suffolk va", "roanoke va", "blacksburg va", "charlottesville va",
              "lynchburg va", "miami fl", "orlando fl", "tampa fl", "atlanta ga", "michigan state", "detroit mi"]
    bad, got = run("riccobene", [("dentist " + c, "x", NR, "Out-of-Area Location") for c in cities])
    assert not bad, "\n".join(bad)
    assert all(g[2] == "Phrase" and g[4] == "Campaign" for g in got)


def test_riccobene_14_territory_check_and_its_exceptions():
    check("riccobene", [
        ("dentist in apex nc", "p2genericapex", REL, "Core dental"),                 # its own city: no mismatch, the next rule decides
        ("dentist in charlotte nc", "p2genericapex", NR, "Location Mismatch"),      # another campaign's city
        ("dentist wilmington nc", "p2genericportersneck", REL, "Core dental"),
        ("dentist rock hill sc", "p2genericfortmill", REL, "Core dental"),                 # known issue in the doc: not a mismatch for each other
        ("dentist fort mill sc", "p2genericrockhill", REL, "Core dental"),
        ("dentist wilmington nc", "p2genericapex", NR, "Location Mismatch"),
        ("dentist winston-salem", "p2genericunknowncampaign", REL, "Core dental"),         # a campaign not in the map: never a mismatch
        ("dentist raleigh", "p2genericsixforks", REL, "Core dental"),
        ("dentist clemmons", "p2genericwinstonsalem", REL, "Core dental"),
        ("dentist ashland va", "p2genericmechanicsville", REL, "Core dental"),
    ])


def test_riccobene_15_core_dental_words_and_what_is_left_to_the_model():
    words = ["dentist", "dental care", "tooth pain", "teeth cleaning", "oral surgery", "gum disease", "tmj treatment", "tmd", "invisalign", "braces for adults", "dental implant", "veneers",
             "crowns", "dental bridge", "dentures", "teeth whitening", "tooth extraction", "root canal", "endodontist", "periodontist", "prosthodontist", "sedation dentistry",
             "cosmetic dentist", "pediatric dentist", "kids dentist", "family dentist", "emergency dentist", "urgent dentist", "sleep apnea dentist", "dental cleaning", "dental checkup",
             "dentist appt", "dentist appointment"]
    check("riccobene", [(w + " near me", "x", REL, "Core dental intent") for w in words])
    check("riccobene", [("random unrelated words", "x", MODEL, None), ("zebra", "x", MODEL, None)])


def test_riccobene_order_brand_beats_competitor_and_competitor_beats_address():
    check("riccobene", [("riccobene vs aspen dental", "x", REL, "Brand"), ("aspen dental 123 main street", "x", COMP, None), ("123 main street medicare", "x", NR, "Specific Address")])


# ── 4. OIA ──────────────────────────────────────────────────────────────────

def test_oia_1_to_4_disqualifiers_run_before_brand():
    check("oia", [("heartland imaging reviews", "x", NR, "Reviews/Reputation"), ("heartland imaging jobs", "x", NR, "Jobs/Recruitment"), ("heartland imaging careers", "x", NR, "Jobs/Recruitment"),
                  ("heartland imaging salary", "x", NR, "Jobs/Recruitment"), ("apply heartland imaging", "x", NR, "Jobs/Recruitment"), ("heartland imaging job opening", "x", NR, "Jobs/Recruitment"),
                  ("radiology tech school", "x", NR, "Education/Training"), ("mri certification", "x", NR, "Education/Training"), ("mri course", "x", NR, "Education/Training"),
                  ("how to become a radiologic technologist", "x", NR, "Education/Training"), ("radiology tech program", "x", NR, "Education/Training"), ("mri training program", "x", NR, "Education/Training"),
                  ("mri complaints", "x", NR, "Reviews/Reputation"), ("mri ratings", "x", NR, "Reviews/Reputation"), ("imaging center scam", "x", NR, "Reviews/Reputation"),
                  ("imaging ripoff", "x", NR, "Reviews/Reputation"), ("imaging bad experience", "x", NR, "Reviews/Reputation"),
                  ("free mri", "x", NR, "Free-Seeking"), ("free ct scan", "x", NR, "Free-Seeking"), ("free mammogram", "x", NR, "Free-Seeking"), ("no cost scan", "x", NR, "Free-Seeking"), ("free imaging", "x", NR, "Free-Seeking")])
    bad, got = run("oia", [("heartland imaging reviews", "x", NR, None)])
    assert got[0][2:] == ["Phrase", "", "Account"]


def test_oia_5_ami_is_the_facility_only_with_kokomo_or_indiana():
    bad, got = run("oia", [("ami kokomo", "x", REL, "Brand (AMI"), ("ami indiana mri", "x", REL, "Brand (AMI"), ("ami mri", "x", COMP, "AMI Imaging"), ("amimaging.com", "x", COMP, "AMI Imaging"),
                           ("ami imaging georgia", "x", COMP, "AMI Imaging")])
    assert not bad, "\n".join(bad)
    assert got[2][2:] == ["Exact", "", "Account"]


def test_oia_6_to_8_facility_brands_corporate_and_joint_ventures():
    check("oia", [("heartland imaging", "x", REL, "Brand (Heartland"), ("hartland imaging", "x", REL, "Brand"), ("nebraska health imaging", "x", REL, "Brand (NHI"), ("outpatient imaging culpeper", "x", REL, "Brand (OIC"),
                  ("outpatient diagnostic center", "x", REL, "Brand (ODC"), ("cardinal points imaging", "x", REL, "Brand (CPIC"), ("cardinal point imaging", "x", REL, "Brand (CPIC"),
                  ("raleigh radiology", "x", REL, "Brand (CPIC"), ("raleighrad", "x", REL, "Brand (CPIC"), ("institute of diagnostic imaging", "x", REL, "Brand (IDL"),
                  ("spectrum medical imaging", "x", REL, "Brand (SMI"), ("platte valley medical", "x", REL, "Brand (SMI"), ("holmdel imaging", "x", REL, "Brand (Holmdel"),
                  ("ft jesse imaging", "x", REL, "Brand (Ft Jesse"), ("ft. jesse imaging", "x", REL, "Brand (Ft Jesse"), ("gale keeran", "x", REL, "Brand (Ft Jesse"),
                  ("university diagnostics", "x", REL, "Brand (UD"), ("imaging center at wolf river", "x", REL, "Brand (Wolf River"), ("wolf river imaging", "x", REL, "Brand (Wolf River"),
                  ("outpatient imaging affiliates", "x", REL, "Brand (OIA corporate"), ("oia rad", "x", REL, "Brand (OIA corporate"), ("oiarad", "x", REL, "Brand (OIA corporate"),
                  ("uva health culpeper", "x", REL, "Brand (OIC-Culpeper via"), ("uva health charlottesville", "x", COMP, "UVA Health"),
                  ("osf healthcare bloomington", "x", REL, "Brand (Ft Jesse via"), ("osf healthcare normal il", "x", REL, "Brand (Ft Jesse via"), ("osf healthcare peoria", "x", COMP, "OSF HealthCare"),
                  ("osf imaging", "x", COMP, "OSF HealthCare")])


def test_oia_9_to_11_gearstar_competitors_and_the_protected_phrases():
    check("oia", [("gearstar", "x", NR, "Unverified Term")] +
          [(t, "x", COMP, "Named competitor") for t in ["radnet", "simonmed", "simonmed imaging", "akumin", "alliance healthcare radiology", "insight imaging", "envision radiology", "solis mammography",
                                                     "community health network mri", "chi health", "nebraska medicine mri", "ecommunity.com", "touchstoneimaging.com", "zwanger-pesiri", "us imaging network"]] +
          [(t, "x", NOTCOMP, None) for t in ["imaging center near me", "walk in mri", "walk-in mri", "open mri near me", "diagnostic center near me", "radiology clinic near me"]] +
          [("123 main street mri", "x", NR, "Specific Address Query"), ("812-555-1234 mri", "x", NR, "Specific Phone Query")])


def test_oia_12_to_16_informational_vet_insurance_and_out_of_area():
    check("oia", [("what is an mri", "x", NR, "Informational/Research"), ("how does a ct scan work", "x", NR, "Informational/Research"), ("mri procedures", "x", NR, "Informational/Research"),
                  ("side effects of contrast", "x", NR, "Informational/Research"),
                  ("dog mri", "x", NR, "Veterinary"), ("dog ct scan", "x", NR, "Veterinary"), ("pet mri", "x", NR, "Veterinary"), ("cat scan near me", "x", REL, "Core imaging"), ("pet ct scan near me", "x", REL, "Core imaging"), ("pet/ct imaging", "x", REL, "Core imaging"), ("cat ultrasound", "x", NR, "Veterinary"), ("vet x-ray", "x", NR, "Veterinary"),
                  ("mri medicare", "x", CR, "Insurance Coverage Unconfirmed"), ("mri medicaid", "x", CR, "Insurance Coverage Unconfirmed")] +
          [("mri %s" % i, "x", CR, "Insurance Network Unconfirmed") for i in ["aetna", "cigna", "blue cross", "unitedhealthcare", "united healthcare", "humana"]] +
          [("mri " + c, "x", NR, "Out-of-Area Location") for c in ["los angeles ca", "new york ny", "houston tx", "phoenix az", "san antonio tx", "san diego ca", "dallas tx", "san jose ca",
                                                                "austin tx", "san francisco ca", "seattle wa", "portland or", "las vegas nv", "boston ma", "miami fl", "atlanta ga", "detroit mi"]])


def test_oia_17_territory_map_and_18_core_words():
    check("oia", [("mri kokomo", "p2amikokomo", REL, "Core imaging"), ("mri omaha", "p2amikokomo", NR, "Location Mismatch"), ("mri louisville", "p2heartland", REL, "Core imaging"), ("mri elizabethtown", "p2heartland", REL, "Core imaging"),
                  ("mri huntsville al", "p2odcal", REL, "Core imaging"), ("mri raleigh", "p2cpic", REL, "Core imaging"), ("mri wake forest", "p2cpic", REL, "Core imaging"), ("mri raleigh", "p2heartland", NR, "Location Mismatch"),
                  ("mri fort walton beach", "p2idl", REL, "Core imaging"), ("mri brighton co", "p2smi", REL, "Core imaging"), ("mri holmdel", "p2holmdel", REL, "Core imaging"), ("mri bloomington il", "p2ftjesse", REL, "Core imaging"),
                  ("mri knoxville", "p2ud", REL, "Core imaging"), ("mri memphis", "p2wolfriver", REL, "Core imaging"), ("mri germantown tn", "p2wolfriver", REL, "Core imaging"), ("mri somewhere", "p2unmapped", REL, "Core imaging")])
    words = ["mri", "open mri", "ct scan", "cat scan", "mammogram", "mammography", "3d mammogram", "ultrasound", "sonogram", "x-ray", "bone density", "dexa scan", "women's imaging", "fluoroscopy",
             "pet scan", "nuclear medicine", "arthrogram", "biopsy", "radiology", "diagnostic imaging", "imaging center", "schedule a scan", "mri appointment"]
    check("oia", [(w + " near me", "x", REL, "Core imaging intent") for w in words])
    check("oia", [("something else entirely", "x", MODEL, None)])


# ── 5. Eventgroove ──────────────────────────────────────────────────────────

def test_eventgroove_rules_in_the_documents_order():
    check("eventgroove", [(t, "x", REL, "Brand") for t in ["event groove", "eventgroove", "eventgrooves", "event grove", "groove events", "groove event"]] +
          [(t, "x", COMP, "Named competitor") for t in ["gearstar", "uline lanyards", "uprinting", "uprinting.com", "amazon.com wristbands", "ticketprint.com", "vistaprint", "vistaprint.com", "eventbrite", "eventbrite.com"]] +
          [("silicone wristbands", "x", NR, "Excluded term"), ("lanyard reviews", "x", NR, "Reviews/Reputation"), ("event printing complaints", "x", NR, "Reviews/Reputation"),
           ("careers at event printing", "x", NR, "Jobs/Training"), ("how to design a ticket", "x", NR, "Jobs/Training"),
           ("free printing", "x", NR, "Promotions/Freebies"), ("free event tickets", "x", NR, "Promotions/Freebies"), ("free raffle tickets", "x", NR, "Promotions/Freebies"),
           ("buy event tickets", "x", NR, "Promotions/Freebies"), ("giveaway lanyards", "x", NR, "Promotions/Freebies"), ("free lanyards", "x", NR, "Promotions/Freebies"),
           ("fundraising ideas", "x", NR, "Irrelevant Service"), ("saas platform", "x", NR, "Irrelevant Service")])
    bad, got = run("eventgroove", [("lanyard reviews", "x", NR, None), ("uline lanyards", "x", COMP, None), ("silicone wristbands", "x", NR, None), ("fundraising ideas", "x", NR, None)])
    assert got[0][2:] == ["Phrase", "", "Account"] and got[1][2:] == ["Exact", "", "Account"] and got[2][2] == "" and got[3][2] == ""      # the document gives no negative to excluded terms


def test_eventgroove_named_products_product_words_and_near_me():
    named = ["cheap raffle tickets", "credential printing", "backstage pass printing", "tradeshow lanyards", "concert laminates", "vip lanyards", "plastic event badges", "chinese auction tickets",
             "lanyard printing", "tour credentials", "vip cards", "arm bands for events", "band laminates", "laminate passes", "custom lanyards", "tour laminate printing", "printing credentials",
             "event lanyards", "vendor badges", "ticket printers", "name tag printing", "pvc badges", "festival ticketing"]
    check("eventgroove", [(t, "x", REL, "Named product/service") for t in named])
    check("eventgroove", [(t + " printing", "x", REL, None) for t in ["ticket", "raffle ticket", "banner", "poster", "flyer", "wristband", "certificate", "badge", "lanyard", "credential", "laminate",
                                                                                      "yard sign", "directional sign"]])
    check("eventgroove", [("printing near me", "x", NR, "Location-based"), ("custom near me", "x", NR, "Location-based"), ("wristbands near me", "x", REL, "Core product intent"),
                          ("custom", "x", MODEL, None), ("printing", "x", MODEL, None)])


# ── 6. 42 North (Gentle Dental) ─────────────────────────────────────────────

def test_42_north_rules_in_the_documents_order():
    check("gentledental_42north", [("gentle dental", "x", REL, "Brand"), ("dental gentle", "x", REL, "Brand"), ("first dental", "x", NR, "Excluded term"),
                                   ("aspen dental", "x", COMP, "Named competitor"), ("pacific dental", "x", COMP, "Named competitor"),
                                   ("montshire pediatric dentistry keene", "x", COMP, "Named competitor"), ("perfect dental", "x", COMP, "Named competitor")] +
          [(t, "x", NR, "Free/Jobs/Education/Products") for t in ["free dentist", "dentist hiring", "dental careers", "dental jobs", "dental school", "hygienist program", "dental supplies", "dental tools",
                                                                 "toothbrush", "toothpaste"]] +
          [(t, "x", REL, "Insurance/provider") for t in ["medicaid dentist", "delta dental", "aetna dentist", "cigna dentist", "altus dental", "dental providers"]] +
          [(t, "x", REL, "Cost/price") for t in ["cheap dentist", "low cost dentist", "dentist cost", "dentist price", "dentist fees", "dentist fee"]] +
          [(t, "x", REL, "Core dental intent") for t in ["dentist near me", "dental office", "tooth pain", "teeth cleaning", "implants", "invisalign", "braces", "whitening", "veneers", "extraction",
                                                       "crown", "dentures", "oral surgery", "periodontist", "pediatric dentist", "emergency dentist", "urgent care dentist", "root canal", "orthodontist"]] +
          [("blanqueamiento dental", "x", REL, None), ("something unrelated", "x", MODEL, None)])
    bad, got = run("gentledental_42north", [("aspen dental", "x", COMP, None), ("dental jobs", "x", NR, None)])
    assert got[0][2:] == ["Exact", "", "Account"] and got[1][2] == ""        # the document gives disqualifiers no negative


# ── 7. McHale ───────────────────────────────────────────────────────────────

def test_mchale_rules_in_the_documents_order():
    check("mchale", [("mchale landscape", "x", REL, "Brand"), ("mc hale landscaping", "x", REL, "Brand"), ("mchale garden design", "x", REL, "Brand"), ("mchale patio", "x", REL, "Brand"),
                     ("mchale", "x", MODEL, None), ("mchale manufacturing", "x", MODEL, None)] +
          [(t, "x", COMP, "Named DMV") for t in ["rossen landscape", "sisson landscape", "sebastian design build", "great falls landscape", "fine landscapes", "wheat's landscape", "fine garden design",
                                                 "moody graham", "joshua schaaf", "native plant landscape design", "craig richmond landscape", "ed ball landscape", "outdoor spaces", "anthony wilder",
                                                 "br design build", "maryland lawn & landscape", "luxe landscape group", "garcia patios and landscaping"]] +
          [("landscaper jobs", "x", NR, "Jobs/Recruitment"), ("landscaping hiring", "x", NR, "Jobs/Recruitment"), ("landscape careers", "x", NR, "Jobs/Recruitment"), ("apply landscaping", "x", NR, "Jobs/Recruitment"),
           ("landscaper salary", "x", NR, "Jobs/Recruitment"), ("landscape school", "x", NR, "Education/Training"), ("landscaping certification", "x", NR, "Education/Training"),
           ("landscape course", "x", NR, "Education/Training"), ("how to become a landscaper", "x", NR, "Education/Training"), ("landscape training program", "x", NR, "Education/Training"),
           ("landscape degree", "x", NR, "Education/Training"), ("landscaper reviews", "x", NR, "Reviews/Reputation"), ("diy patio", "x", NR, "DIY/Home Project"),
           ("how to build a retaining wall", "x", NR, "DIY/Home Project"), ("how to install pavers", "x", NR, "DIY/Home Project"), ("how to plant a tree", "x", NR, "DIY/Home Project")] +
          [("landscaping " + c, "x", NR, "Out-of-Area Location") for c in ["los angeles ca", "new york ny", "chicago il", "houston tx", "phoenix az", "san antonio tx", "san diego ca", "dallas tx",
                                                                        "san jose ca", "austin tx", "miami fl", "atlanta ga", "boston ma", "seattle wa", "denver co", "detroit mi", "philadelphia pa",
                                                                        "charlotte nc", "san francisco ca"]] +
          [(w, "x", REL, "Core landscaping intent") for w in ["landscaping near me", "hardscape design", "hardscaping", "lawn care", "lawn maintenance", "lawn service", "garden design", "gardening tips",
                                                              "design build firm", "design and build", "patio design", "paver patio", "retaining wall", "outdoor living", "outdoor kitchen",
                                                              "fire pit", "pergola", "walkway", "planting design", "tree service", "irrigation", "drainage system", "mulch delivery",
                                                              "sod installation", "yard design", "backyard design", "landscape contractor", "landscape architect", "landscape maintenance",
                                                              "landscape lighting", "nursery"]] +
          [("something unrelated", "x", MODEL, None)])
    bad, got = run("mchale", [("landscape reviews", "x", NR, None), ("diy patio", "x", NR, None), ("landscaping boston ma", "x", NR, None)])
    assert got[0][2:] == ["Phrase", "", "Account"] and got[1][2] == "" and got[2][2:] == ["Phrase", "", "Campaign"]


# ── 9. The rules every run applies (section 1) ──────────────────────────────

def test_a_ruleset_with_no_rules_defers_everything_to_the_model():
    check("generic", [("anything at all", "x", MODEL, None)])


# ── What real accounts showed that the document does not say ────────────────

def test_oia_facility_names_as_people_actually_type_them():
    check("oia", [("fort jesse imaging", "x", REL, "Brand (Ft Jesse"), ("fort jesse bloomington il", "x", REL, "Brand (Ft Jesse"), ("fr jesse imaging", "x", REL, "Brand (Ft Jesse"),
                  ("nebraska imaging omaha", "x", REL, "Brand (NHI"), ("nebraska medical imaging", "x", REL, "Brand (NHI"), ("nhi omaha", "x", REL, "Brand (NHI"),
                  ("cardinal imaging clayton nc", "x", REL, "Brand (CPIC"), ("cardinal points midtown", "x", REL, "Brand (CPIC"), ("cpic raleigh", "x", REL, "Brand (CPIC"),
                  ("idi fort walton beach", "x", REL, "Brand (IDL"), ("idi imaging", "x", REL, "Brand (IDL"), ("odc huntsville", "x", REL, "Brand (ODC"),
                  ("outpatient diagnostics madison alabama", "x", REL, "Brand (ODC"), ("uva imaging culpeper va", "x", REL, "Brand (OIC"), ("culpeper imaging", "x", REL, "Brand (OIC"),
                  ("spectrum imaging brighton co", "x", REL, "Brand (SMI"), ("holmdel medical imaging", "x", REL, "Brand (Holmdel"), ("university diagnostic", "x", REL, "Brand (UD"),
                  ("imaging at wolf river", "x", REL, "Brand (Wolf River"), ("advanced medical imaging kokomo", "x", REL, "Brand (AMI"),
                  ("advanced medical imaging", "p2amibrande", REL, "Brand (AMI"), ("advanced medical imaging", "p2nhictscanb", MODEL, None),      # elsewhere it is another business's name
                  ("heartland diagnostics", "x", REL, "Brand (Heartland")])


def test_a_cat_scan_or_a_pet_scan_is_not_a_veterinary_search():
    check("oia", [("cat scan near me", "x", REL, "Core imaging"), ("pet scan cost", "x", REL, "Core imaging"), ("pet/ct near me", "x", REL, "Core imaging"), ("dog mri cost", "x", NR, "Veterinary"),
                  ("cat ultrasound", "x", NR, "Veterinary")])


def test_a_place_named_in_a_performance_max_search_is_not_a_location_mismatch():
    code = ("const C = require(%s); const out = [];\n"
            "for (const [rs, term, slug, row] of %s) { const v = C.RULESETS[rs].classifyDeterministic(term, slug, row); out.push(v ? v.subtype : null); }\n"
            "console.log(JSON.stringify(out));") % (json.dumps(CORE), json.dumps([
                ["riccobene", "dentist in charlotte nc", "p2genericapex", {"campaign": "P2_Generic_Apex", "campaignType": "SEARCH"}],
                ["riccobene", "dentist in charlotte nc", "p2pmaxapex", {"campaign": "P2_Pmax_Apex", "campaignType": "PERFORMANCE_MAX"}],
                ["riccobene", "dentist in charlotte nc", "p2pmaxapex", {"campaign": "P2_Pmax_Apex", "campaignType": ""}],
                ["oia", "mri omaha", "p2amikokomo", {"campaign": "P2_AMI_Mammogram", "campaignType": "SEARCH"}],
                ["oia", "mri omaha", "p2amipmax", {"campaign": "P2_AMI_PMax", "campaignType": "PERFORMANCE_MAX"}],
                ["riccobene", "dentist boston ma", "p2pmaxapex", {"campaign": "P2_Pmax_Apex", "campaignType": "PERFORMANCE_MAX"}]]))
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["Location Mismatch", "Core dental intent", "Core dental intent", "Location Mismatch", "Core imaging intent", "Out-of-Area Location"]   # a far-away city is still out of area


def test_the_service_words_real_searches_use_are_core_words():
    check("riccobene", [("orthodontist near me", "x", REL, "Core dental"), ("orthodontists in raleigh", "x", REL, "Core dental")])
    check("gentledental_42north", [("oral surgeon near me", "x", REL, "Core dental"), ("endodontics", "x", REL, "Core dental"), ("endodontist plymouth ma", "x", REL, "Core dental")])
    check("eventgroove", [("wrist band for events", "x", REL, "Core product"), ("custom name tags", "x", REL, "Core product"), ("backstage passes", "x", REL, "Core product")])


# ── 1.2 Reading the CSVs ────────────────────────────────────────────────────

def _csv(text):
    code = "const C = require(%s); const r = C.rowsFromCsv(%s); console.log(JSON.stringify(r));" % (json.dumps(CORE), json.dumps(text))
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


HEADER = "Search term,Campaign,Ad group,Campaign type,Clicks,Impr.,Cost,Conversions"


def test_csv_title_and_date_lines_above_the_header_are_skipped_and_total_rows_dropped():
    r = _csv("Search terms report\nOctober 1, 2026 - October 15, 2026\n" + HEADER + '\ndentist near me,C1,A1,Search,5,50,12.50,1\n"Total: Search terms",,,,5,50,12.50,1\nTotal: Account,,,,5,50,12.50,1\n')
    assert [x["term"] for x in r["rows"]] == ["dentist near me", "Total: Search terms", "Total: Account"]      # dropped later, by prepareRows (the next test)
    assert r["rows"][0]["clicks"] == 5 and r["rows"][0]["impressions"] == 50 and r["rows"][0]["cost"] == 12.5 and r["rows"][0]["conversions"] == 1


def test_csv_delimiters_bom_null_bytes_and_alternate_column_names():
    base = ["dentist near me", "C1", "A1", "Search", "5", "50", "12.50", "1"]
    for delim in [",", ";", "\t"]:
        hdr = delim.join(["Search term", "Campaign", "Ad group", "Campaign type", "Clicks", "Impressions", "Cost", "Conv."])
        r = _csv("﻿" + hdr + "\n" + delim.join(base) + "\n")
        assert r.get("rows") and r["rows"][0]["term"] == "dentist near me" and r["rows"][0]["impressions"] == 50 and r["rows"][0]["conversions"] == 1, delim
    # a UTF-16 export read as UTF-8 puts a null byte between every character
    nul = "\u0000".join(HEADER + "\n" + ",".join(base) + "\n")
    r = _csv(nul)
    assert r.get("rows") and r["rows"][0]["term"] == "dentist near me"
    # zero-width characters inside a cell
    r = _csv(HEADER + "\ndent​ist near me,C1,A1,Search,5,50,12.50,1\n")
    assert r["rows"][0]["term"] == "dentist near me"
    # a quoted field with a comma, and an error that says what it did see when there is no header
    r = _csv(HEADER + '\n"dentist, near me",C1,A1,Search,5,50,12.50,1\n')
    assert r["rows"][0]["term"] == "dentist, near me"
    r = _csv("nothing,useful\n1,2\n")
    assert r["rows"] == [] and "Search term" in r["error"] and "nothing" in r["error"]


def test_google_subtotal_rows_and_empty_terms_never_reach_the_analysis():
    code = ("const C = require(%s); const rows = C.prepareRows([{term: 'dentist', clicks: 1}, {term: 'Total: Account'}, {term: 'total: search terms'}, {term: '  '}, {term: 'Other search terms'}, {term: '--'}, {term: 'tooth'}]);"
            "console.log(JSON.stringify(rows.map(r => r.term)));") % json.dumps(CORE)
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert json.loads(out.stdout) == ["dentist", "tooth"]
