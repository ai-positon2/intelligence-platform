"""Apollo's other closed vocabularies, and pickers for them.

apollo_taxonomy does this job for industry. Four more filters on the Contact
Finder had the same shape of problem and no picker at all: NAICS codes, SIC
codes, technologies and locations. Each is a value Apollo either recognizes or
does not, with no error either way, so a typed guess produces an empty page that
reads as "nobody matches" rather than "that is not a value Apollo knows".

Verified live against this account on the free people endpoint, so each claim
below is measured rather than assumed. Baseline for every probe was
person_titles=["chief marketing officer"], 79,421 people:

  technologies   an invented uid ("salesforce_crm_platform_xyz") returned 0. A
                 real one returned 25,172. Apollo also accepts the display name
                 and normalizes it itself: "Google Analytics" and
                 "google_analytics" both returned exactly 25,172, so our uid
                 conversion is belt-and-braces rather than load-bearing.

  locations      an invented place ("Zzyzxville, Fakeland") returned 0. A
                 misspelled real one ("Bangalore, Karnatka") still returned 826,
                 because Apollo matches loosely enough to recover from a typo in
                 one component. So the failure is not reliable either way: some
                 wrong values silently return nothing and others silently widen.
                 "Texas" and "TX" both returned 1,914, so either form is safe.

  NAICS / SIC    documented as 2 to 5 digits (prefix matching, so a shorter code
                 is broader) and exactly 4 digits respectively. Real NAICS codes
                 are SIX digits, which is the trap: pasting 541511 from any
                 official source is rejected by Apollo's own schema, and nothing
                 in our UI said so.

The lists here are deliberately not presented as exhaustive. NAICS and SIC are
large government taxonomies and only the codes below are written down, so free
entry stays open under format validation: a picker that refuses a valid code
would be worse than the free text box it replaced. What the picker guarantees is
that anything it OFFERS is real, and `validate` guarantees that anything typed is
at least the right shape.

As in apollo_taxonomy, learned values are merged over these seeds at read time.
Anything Apollo has actually been seen to return is correct by construction, so
technologies and locations improve on their own with use.
"""

import re


# ── NAICS ─────────────────────────────────────────────────────────────────────
# Sectors first, then the subsectors and industry groups a B2B search actually
# reaches for. Apollo prefix-matches, so "54" is every professional service and
# "5415" narrows to computer systems design; both are useful picks.
#
# Retail and wholesale were renumbered in the 2022 vintage and Apollo does not
# document which vintage it holds, so those are represented at sector level only
# rather than by guessing which numbering is live.
NAICS = (
    ("11", "Agriculture, forestry, fishing and hunting"),
    ("111", "Crop production"),
    ("112", "Animal production and aquaculture"),
    ("113", "Forestry and logging"),
    ("115", "Support activities for agriculture and forestry"),
    ("21", "Mining, quarrying, and oil and gas extraction"),
    ("211", "Oil and gas extraction"),
    ("212", "Mining, except oil and gas"),
    ("213", "Support activities for mining"),
    ("22", "Utilities"),
    ("221", "Utilities"),
    ("23", "Construction"),
    ("236", "Construction of buildings"),
    ("237", "Heavy and civil engineering construction"),
    ("238", "Specialty trade contractors"),
    ("31", "Manufacturing (food, textiles, apparel)"),
    ("311", "Food manufacturing"),
    ("312", "Beverage and tobacco product manufacturing"),
    ("313", "Textile mills"),
    ("315", "Apparel manufacturing"),
    ("32", "Manufacturing (wood, paper, chemicals, plastics)"),
    ("321", "Wood product manufacturing"),
    ("322", "Paper manufacturing"),
    ("323", "Printing and related support activities"),
    ("324", "Petroleum and coal products manufacturing"),
    ("325", "Chemical manufacturing"),
    ("3254", "Pharmaceutical and medicine manufacturing"),
    ("326", "Plastics and rubber products manufacturing"),
    ("327", "Nonmetallic mineral product manufacturing"),
    ("33", "Manufacturing (metals, machinery, electronics, transport)"),
    ("331", "Primary metal manufacturing"),
    ("332", "Fabricated metal product manufacturing"),
    ("333", "Machinery manufacturing"),
    ("334", "Computer and electronic product manufacturing"),
    ("3341", "Computer and peripheral equipment manufacturing"),
    ("3342", "Communications equipment manufacturing"),
    ("3344", "Semiconductor and other electronic component manufacturing"),
    ("3345", "Navigational, measuring and control instruments manufacturing"),
    ("335", "Electrical equipment, appliance and component manufacturing"),
    ("336", "Transportation equipment manufacturing"),
    ("3361", "Motor vehicle manufacturing"),
    ("3364", "Aerospace product and parts manufacturing"),
    ("337", "Furniture and related product manufacturing"),
    ("339", "Miscellaneous manufacturing"),
    ("3391", "Medical equipment and supplies manufacturing"),
    ("42", "Wholesale trade"),
    ("44", "Retail trade"),
    ("45", "Retail trade"),
    ("48", "Transportation"),
    ("481", "Air transportation"),
    ("482", "Rail transportation"),
    ("483", "Water transportation"),
    ("484", "Truck transportation"),
    ("485", "Transit and ground passenger transportation"),
    ("486", "Pipeline transportation"),
    ("488", "Support activities for transportation"),
    ("49", "Warehousing, couriers and postal"),
    ("492", "Couriers and messengers"),
    ("493", "Warehousing and storage"),
    ("51", "Information"),
    ("512", "Motion picture and sound recording industries"),
    ("513", "Publishing industries"),
    ("5132", "Software publishers"),
    ("516", "Broadcasting and content providers"),
    ("517", "Telecommunications"),
    ("518", "Computing infrastructure, data processing and hosting"),
    ("519", "Web search portals, libraries and other information services"),
    ("52", "Finance and insurance"),
    ("521", "Monetary authorities, central bank"),
    ("522", "Credit intermediation and related activities"),
    ("5221", "Depository credit intermediation (banks)"),
    ("523", "Securities, commodity contracts and other financial investments"),
    ("524", "Insurance carriers and related activities"),
    ("5241", "Insurance carriers"),
    ("5242", "Insurance agencies, brokerages and related activities"),
    ("525", "Funds, trusts and other financial vehicles"),
    ("53", "Real estate and rental and leasing"),
    ("531", "Real estate"),
    ("532", "Rental and leasing services"),
    ("533", "Lessors of nonfinancial intangible assets"),
    ("54", "Professional, scientific and technical services"),
    ("5411", "Legal services"),
    ("5412", "Accounting, tax preparation, bookkeeping and payroll services"),
    ("5413", "Architectural, engineering and related services"),
    ("5414", "Specialized design services"),
    ("5415", "Computer systems design and related services"),
    ("5416", "Management, scientific and technical consulting services"),
    ("5417", "Scientific research and development services"),
    ("5418", "Advertising, public relations and related services"),
    ("5419", "Other professional, scientific and technical services"),
    ("55", "Management of companies and enterprises"),
    ("551", "Management of companies and enterprises"),
    ("56", "Administrative, support and waste management services"),
    ("561", "Administrative and support services"),
    ("5613", "Employment services"),
    ("5615", "Travel arrangement and reservation services"),
    ("5616", "Investigation and security services"),
    ("562", "Waste management and remediation services"),
    ("61", "Educational services"),
    ("611", "Educational services"),
    ("6113", "Colleges, universities and professional schools"),
    ("62", "Health care and social assistance"),
    ("621", "Ambulatory health care services"),
    ("6211", "Offices of physicians"),
    ("6212", "Offices of dentists"),
    ("6215", "Medical and diagnostic laboratories"),
    ("622", "Hospitals"),
    ("623", "Nursing and residential care facilities"),
    ("624", "Social assistance"),
    ("71", "Arts, entertainment and recreation"),
    ("711", "Performing arts, spectator sports and related industries"),
    ("712", "Museums, historical sites and similar institutions"),
    ("713", "Amusement, gambling and recreation industries"),
    ("72", "Accommodation and food services"),
    ("721", "Accommodation"),
    ("722", "Food services and drinking places"),
    ("81", "Other services (except public administration)"),
    ("811", "Repair and maintenance"),
    ("812", "Personal and laundry services"),
    ("813", "Religious, grantmaking, civic and professional organizations"),
    ("92", "Public administration"),
)

# ── SIC ───────────────────────────────────────────────────────────────────────
# Apollo takes SIC as exactly four digits, so there is no broader level to fall
# back on the way NAICS prefixes allow. Common codes across every division.
SIC = (
    ("0111", "Wheat farming"),
    ("0211", "Beef cattle feedlots"),
    ("1311", "Crude petroleum and natural gas"),
    ("1531", "Operative builders"),
    ("1541", "General contractors, industrial buildings"),
    ("1731", "Electrical work"),
    ("2011", "Meat packing plants"),
    ("2086", "Bottled and canned soft drinks"),
    ("2111", "Cigarettes"),
    ("2311", "Men's and boys' suits and coats"),
    ("2421", "Sawmills and planing mills"),
    ("2621", "Paper mills"),
    ("2711", "Newspapers: publishing"),
    ("2721", "Periodicals: publishing"),
    ("2731", "Book publishing"),
    ("2812", "Alkalies and chlorine"),
    ("2821", "Plastics materials and resins"),
    ("2834", "Pharmaceutical preparations"),
    ("2836", "Biological products"),
    ("2844", "Perfumes, cosmetics and toilet preparations"),
    ("2911", "Petroleum refining"),
    ("3011", "Tires and inner tubes"),
    ("3089", "Plastics products"),
    ("3241", "Cement, hydraulic"),
    ("3312", "Steel works and blast furnaces"),
    ("3441", "Fabricated structural metal"),
    ("3511", "Turbines and turbine generator sets"),
    ("3559", "Special industry machinery"),
    ("3571", "Electronic computers"),
    ("3572", "Computer storage devices"),
    ("3576", "Computer communications equipment"),
    ("3577", "Computer peripheral equipment"),
    ("3661", "Telephone and telegraph apparatus"),
    ("3663", "Radio and TV broadcasting and communications equipment"),
    ("3674", "Semiconductors and related devices"),
    ("3711", "Motor vehicles and passenger car bodies"),
    ("3721", "Aircraft"),
    ("3812", "Search, detection, navigation and guidance systems"),
    ("3821", "Laboratory apparatus and furniture"),
    ("3841", "Surgical and medical instruments"),
    ("3843", "Dental equipment and supplies"),
    ("3845", "Electromedical apparatus"),
    ("3861", "Photographic equipment and supplies"),
    ("4011", "Railroads, line-haul operating"),
    ("4213", "Trucking, except local"),
    ("4512", "Air transportation, scheduled"),
    ("4513", "Air courier services"),
    ("4731", "Arrangement of transportation of freight and cargo"),
    ("4813", "Telephone communications"),
    ("4832", "Radio broadcasting stations"),
    ("4833", "Television broadcasting stations"),
    ("4841", "Cable and other pay television services"),
    ("4899", "Communications services"),
    ("4911", "Electric services"),
    ("4924", "Natural gas distribution"),
    ("4941", "Water supply"),
    ("5045", "Computers, peripherals and software (wholesale)"),
    ("5065", "Electronic parts and equipment (wholesale)"),
    ("5122", "Drugs and druggists' sundries (wholesale)"),
    ("5411", "Grocery stores"),
    ("5511", "Motor vehicle dealers, new and used"),
    ("5651", "Family clothing stores"),
    ("5812", "Eating places"),
    ("5912", "Drug stores and proprietary stores"),
    ("5944", "Jewelry stores"),
    ("5961", "Catalog and mail-order houses"),
    ("6021", "National commercial banks"),
    ("6022", "State commercial banks"),
    ("6035", "Savings institutions, federally chartered"),
    ("6141", "Personal credit institutions"),
    ("6153", "Short-term business credit institutions"),
    ("6199", "Finance services"),
    ("6211", "Security brokers and dealers"),
    ("6282", "Investment advice"),
    ("6311", "Life insurance"),
    ("6321", "Accident and health insurance"),
    ("6331", "Fire, marine and casualty insurance"),
    ("6411", "Insurance agents, brokers and service"),
    ("6512", "Operators of nonresidential buildings"),
    ("6531", "Real estate agents and managers"),
    ("6552", "Land subdividers and developers"),
    ("6798", "Real estate investment trusts"),
    ("7011", "Hotels and motels"),
    ("7311", "Advertising agencies"),
    ("7319", "Advertising services"),
    ("7331", "Direct mail advertising services"),
    ("7349", "Building cleaning and maintenance services"),
    ("7361", "Employment agencies"),
    ("7363", "Help supply services"),
    ("7371", "Custom computer programming services"),
    ("7372", "Prepackaged software"),
    ("7373", "Computer integrated systems design"),
    ("7374", "Data processing and preparation"),
    ("7375", "Information retrieval services"),
    ("7376", "Computer facilities management services"),
    ("7377", "Computer rental and leasing"),
    ("7378", "Computer maintenance and repair"),
    ("7379", "Computer related services"),
    ("7381", "Detective, guard and armored car services"),
    ("7389", "Business services"),
    ("7812", "Motion picture and video production"),
    ("7832", "Motion picture theaters"),
    ("7929", "Entertainers and entertainment groups"),
    ("7991", "Physical fitness facilities"),
    ("7997", "Membership sports and recreation clubs"),
    ("8011", "Offices and clinics of doctors of medicine"),
    ("8021", "Offices and clinics of dentists"),
    ("8051", "Skilled nursing care facilities"),
    ("8062", "General medical and surgical hospitals"),
    ("8071", "Medical laboratories"),
    ("8082", "Home health care services"),
    ("8093", "Specialty outpatient facilities"),
    ("8111", "Legal services"),
    ("8211", "Elementary and secondary schools"),
    ("8221", "Colleges, universities and professional schools"),
    ("8249", "Vocational schools"),
    ("8299", "Schools and educational services"),
    ("8322", "Individual and family social services"),
    ("8351", "Child day care services"),
    ("8399", "Social services"),
    ("8611", "Business associations"),
    ("8621", "Professional membership organizations"),
    ("8641", "Civic, social and fraternal associations"),
    ("8661", "Religious organizations"),
    ("8711", "Engineering services"),
    ("8712", "Architectural services"),
    ("8721", "Accounting, auditing and bookkeeping services"),
    ("8731", "Commercial physical and biological research"),
    ("8732", "Commercial economic and sociological research"),
    ("8741", "Management services"),
    ("8742", "Management consulting services"),
    ("8744", "Facilities support management services"),
    ("9199", "General government"),
    ("9411", "Administration of educational programs"),
    ("9431", "Administration of public health programs"),
)

# ── Technologies ──────────────────────────────────────────────────────────────
# Display names, because Apollo accepts them and normalizes to its own uid
# spelling itself (measured: identical result counts either way), and because
# Apollo RETURNS display names, so a learned value lands in the same shape as a
# seeded one instead of arriving as a second spelling of a value already here.
TECHNOLOGIES = (
    # Analytics and tag management
    "Google Analytics", "Google Tag Manager", "Adobe Analytics", "Mixpanel",
    "Amplitude", "Heap", "Segment", "Hotjar", "FullStory", "Looker",
    "Tableau", "Power BI", "Snowflake", "Databricks", "Google BigQuery",
    # CRM and sales
    "Salesforce", "HubSpot", "Microsoft Dynamics", "Zoho CRM", "Pipedrive",
    "Outreach", "Salesloft", "Gong", "Clari", "ZoomInfo", "Apollo",
    "LinkedIn Sales Navigator", "Drift", "Intercom", "Zendesk", "Freshdesk",
    # Marketing automation and email
    "Marketo", "Pardot", "Eloqua", "Mailchimp", "Klaviyo", "Braze",
    "Iterable", "Customer.io", "SendGrid", "Mandrill", "Constant Contact",
    "ActiveCampaign", "Marketo Engage",
    # Advertising and tracking pixels
    "Google Ads", "Facebook Pixel", "LinkedIn Ads", "Microsoft Advertising",
    "The Trade Desk", "DoubleClick", "Criteo", "AdRoll", "Taboola", "Outbrain",
    # CMS and ecommerce
    "WordPress.org", "WordPress.com", "Drupal", "Joomla", "Contentful",
    "Sitecore", "Adobe Experience Manager", "Webflow", "Squarespace", "Wix",
    "Shopify", "Shopify Plus", "Magento", "WooCommerce", "BigCommerce",
    "Salesforce Commerce Cloud", "Stripe", "PayPal", "Braintree", "Adyen",
    "Klarna", "Recurly", "Chargebee", "Zuora",
    # Cloud and infrastructure
    "Amazon AWS", "Microsoft Azure", "Google Cloud", "Heroku", "DigitalOcean",
    "Cloudflare", "Akamai", "Fastly", "Nginx", "Apache", "Kubernetes",
    "Docker", "Terraform", "Amazon S3", "Amazon CloudFront",
    # Engineering and DevOps
    "GitHub", "GitLab", "Bitbucket", "Jira", "Jenkins", "CircleCI",
    "Datadog", "New Relic", "Splunk", "Sentry", "PagerDuty", "Grafana",
    # Productivity and collaboration
    "Microsoft Office 365", "Google Workspace", "Slack", "Microsoft Teams",
    "Zoom", "Asana", "Monday.com", "Notion", "Trello", "Confluence",
    "Smartsheet", "Airtable", "Miro", "Figma", "Dropbox", "Box",
    "DocuSign", "Adobe Sign",
    # Finance, HR and operations
    "NetSuite", "SAP", "Oracle", "Workday", "QuickBooks", "Xero", "Sage",
    "ADP", "Gusto", "BambooHR", "Greenhouse", "Lever", "Workable",
    "Expensify", "Coupa", "Bill.com", "Avalara",
    # Security and identity
    "Okta", "Auth0", "OneLogin", "Duo Security", "CrowdStrike",
    "Palo Alto Networks", "Fortinet", "Proofpoint", "Mimecast", "KnowBe4",
    "LastPass", "1Password",
    # Databases and languages
    "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch",
    "Microsoft SQL Server", "Oracle Database", "React", "Angular", "Vue.js",
    "Node.js", "Python", "Ruby on Rails", "Java", "PHP", ".NET",
)

# ── Locations ─────────────────────────────────────────────────────────────────
# Countries, US states and the metros most often asked for. Apollo accepts
# either "Texas" or "TX" (measured: 1,914 either way), and the full name is the
# one written down because it is unambiguous across countries: "CA" is both
# California and Canada.
_COUNTRIES = (
    "Argentina", "Australia", "Austria", "Bangladesh", "Belgium", "Brazil",
    "Bulgaria", "Canada", "Chile", "China", "Colombia", "Costa Rica",
    "Croatia", "Czech Republic", "Denmark", "Ecuador", "Egypt", "Estonia",
    "Finland", "France", "Germany", "Ghana", "Greece", "Hong Kong",
    "Hungary", "Iceland", "India", "Indonesia", "Ireland", "Israel", "Italy",
    "Japan", "Jordan", "Kenya", "Kuwait", "Latvia", "Lithuania",
    "Luxembourg", "Malaysia", "Mexico", "Morocco", "Netherlands",
    "New Zealand", "Nigeria", "Norway", "Pakistan", "Panama", "Peru",
    "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Saudi Arabia",
    "Serbia", "Singapore", "Slovakia", "Slovenia", "South Africa",
    "South Korea", "Spain", "Sri Lanka", "Sweden", "Switzerland", "Taiwan",
    "Thailand", "Turkey", "Ukraine", "United Arab Emirates",
    "United Kingdom", "United States", "Uruguay", "Vietnam",
)

_US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin",
    "Wyoming",
)

_METROS = (
    "San Francisco, California", "San Jose, California",
    "Los Angeles, California", "San Diego, California",
    "Sacramento, California", "Seattle, Washington", "Portland, Oregon",
    "Denver, Colorado", "Austin, Texas", "Dallas, Texas", "Houston, Texas",
    "San Antonio, Texas", "Phoenix, Arizona", "Salt Lake City, Utah",
    "Las Vegas, Nevada", "Chicago, Illinois", "Minneapolis, Minnesota",
    "Detroit, Michigan", "Columbus, Ohio", "Cleveland, Ohio",
    "Indianapolis, Indiana", "Kansas City, Missouri", "St. Louis, Missouri",
    "Nashville, Tennessee", "Atlanta, Georgia", "Charlotte, North Carolina",
    "Raleigh, North Carolina", "Miami, Florida", "Orlando, Florida",
    "Tampa, Florida", "Jacksonville, Florida", "New York, New York",
    "Brooklyn, New York", "Boston, Massachusetts",
    "Philadelphia, Pennsylvania", "Pittsburgh, Pennsylvania",
    "Baltimore, Maryland", "Washington, District of Columbia",
    "Richmond, Virginia", "Toronto, Canada", "Vancouver, Canada",
    "Montreal, Canada", "London, United Kingdom",
    "Manchester, United Kingdom", "Dublin, Ireland", "Paris, France",
    "Berlin, Germany", "Munich, Germany", "Amsterdam, Netherlands",
    "Madrid, Spain", "Barcelona, Spain", "Milan, Italy", "Zurich, Switzerland",
    "Stockholm, Sweden", "Copenhagen, Denmark", "Warsaw, Poland",
    "Lisbon, Portugal", "Tel Aviv, Israel", "Dubai, United Arab Emirates",
    "Bengaluru, India", "Mumbai, India", "Delhi, India", "Hyderabad, India",
    "Pune, India", "Chennai, India", "Singapore", "Tokyo, Japan",
    "Seoul, South Korea", "Shanghai, China", "Beijing, China",
    "Sydney, Australia", "Melbourne, Australia", "Auckland, New Zealand",
    "Sao Paulo, Brazil", "Mexico City, Mexico", "Buenos Aires, Argentina",
    "Cape Town, South Africa", "Johannesburg, South Africa",
    "Nairobi, Kenya", "Lagos, Nigeria",
)

# Country first, then state, then metro. Someone typing "united" wants the
# country, and someone typing "san" wants a city; ranking the broad buckets ahead
# of the narrow ones matches how these filters are actually used.
LOCATIONS = _COUNTRIES + _US_STATES + _METROS

# What each kind's picker is made of, and how strictly a typed value is judged.
#   codes    a fixed digit format Apollo will reject outright, so validate it
#   learned  Apollo returns this on records, so the vocabulary grows by itself
_KINDS = {
    "naics": {"pattern": r"^[0-9]{2,5}$", "labelled": True, "learned": True,
              "hint": "NAICS codes are 2 to 5 digits here. Official codes are 6 "
                      "digits, so drop the last one or two: 541511 becomes 54151."},
    "sic": {"pattern": r"^[0-9]{4}$", "labelled": True, "learned": True,
            "hint": "SIC codes are exactly 4 digits."},
    "technology": {"pattern": "", "labelled": False, "learned": True, "hint": ""},
    "location": {"pattern": "", "labelled": False, "learned": True, "hint": ""},
}

_SEEDS = {"naics": NAICS, "sic": SIC,
          "technology": TECHNOLOGIES, "location": LOCATIONS}

# Official code titles use the government's words, not the ones people type.
# Nothing in NAICS is titled "software": the code is 5132, filed under
# "publishing industries", and 5415 is "computer systems design". So a search for
# the ordinary word found nothing at all, which is the same dead end the industry
# families were added to fix, one taxonomy over.
#
# These only REORDER: an alias promotes codes that already exist above, and every
# other match still appears underneath. Same discipline as the industry families,
# for the same reason: a shortcut may not invent a value.
_CODE_ALIASES = {
    "naics": {
        "software": ("5132", "5415", "518"),
        "saas": ("5132", "518"),
        "technology": ("5415", "5132", "518", "334"),
        "tech": ("5415", "5132", "518", "334"),
        "it services": ("5415", "518"),
        "computer": ("5415", "3341", "518"),
        "internet": ("518", "519"),
        "hosting": ("518",),
        "data": ("518",),
        "telecom": ("517",),
        "semiconductor": ("3344",),
        "healthcare": ("62", "621", "622", "3254", "3391"),
        "health": ("62", "621", "622"),
        "hospital": ("622",),
        "pharma": ("3254",),
        "biotech": ("5417", "3254"),
        "medical devices": ("3391",),
        "finance": ("52", "522", "523"),
        "fintech": ("52", "522", "5415"),
        "bank": ("5221", "522"),
        "insurance": ("524", "5241", "5242"),
        "consulting": ("5416", "5419"),
        "marketing": ("5418",),
        "advertising": ("5418",),
        "legal": ("5411",),
        "accounting": ("5412",),
        "engineering": ("5413",),
        "design": ("5414",),
        "research": ("5417",),
        "staffing": ("5613",),
        "recruiting": ("5613",),
        "manufacturing": ("31", "32", "33"),
        "automotive": ("3361",),
        "aerospace": ("3364",),
        "retail": ("44", "45"),
        "ecommerce": ("44", "45"),
        "logistics": ("48", "49", "493", "488"),
        "education": ("61", "611", "6113"),
        "real estate": ("531",),
        "construction": ("23", "236", "237", "238"),
        "energy": ("21", "211", "22", "221"),
        "utilities": ("221",),
        "media": ("512", "513", "516"),
        "hospitality": ("721", "722"),
        "restaurant": ("722",),
        "nonprofit": ("813",),
        "government": ("92",),
        "agriculture": ("11", "111", "112"),
        "security": ("5616",),
    },
    "sic": {
        "software": ("7372", "7371", "7373"),
        "saas": ("7372", "7371"),
        "technology": ("7372", "7371", "7379", "3674"),
        "tech": ("7372", "7371", "7379"),
        "it services": ("7379", "7376", "7373"),
        "computer": ("7372", "7371", "3571", "5045"),
        "internet": ("7375", "7379"),
        "data": ("7374", "7375"),
        "telecom": ("4813", "3661"),
        "semiconductor": ("3674",),
        "healthcare": ("8011", "8062", "2834", "3841"),
        "health": ("8011", "8062", "8082"),
        "hospital": ("8062",),
        "pharma": ("2834", "2836"),
        "biotech": ("2836", "8731"),
        "medical devices": ("3841", "3845"),
        "finance": ("6021", "6022", "6199", "6211"),
        "fintech": ("6199", "7372"),
        "bank": ("6021", "6022", "6035"),
        "insurance": ("6311", "6321", "6331", "6411"),
        "consulting": ("8742", "8741"),
        "marketing": ("7311", "7319"),
        "advertising": ("7311", "7319", "7331"),
        "legal": ("8111",),
        "accounting": ("8721",),
        "engineering": ("8711",),
        "architecture": ("8712",),
        "research": ("8731", "8732"),
        "staffing": ("7361", "7363"),
        "recruiting": ("7361", "7363"),
        "automotive": ("3711", "5511"),
        "aerospace": ("3721", "3812"),
        "retail": ("5411", "5651", "5912", "5944", "5961"),
        "ecommerce": ("5961",),
        "logistics": ("4213", "4731"),
        "education": ("8211", "8221", "8249", "8299"),
        "real estate": ("6531", "6512", "6798"),
        "construction": ("1531", "1541", "1731"),
        "energy": ("1311", "2911", "4911", "4924"),
        "utilities": ("4911", "4924", "4941"),
        "media": ("2711", "2721", "2731", "4832", "4833", "7812"),
        "hospitality": ("7011", "5812"),
        "restaurant": ("5812",),
        "nonprofit": ("8611", "8621", "8641", "8661"),
        "government": ("9199", "9411", "9431"),
        "security": ("7381",),
        "fitness": ("7991",),
    },
}


def _alias_codes(kind: str, query: str) -> tuple:
    """({code: position}, {codes}) for exact and partial alias hits.

    The two are kept apart because conflating them ranks nonsense first. "hospital"
    is a partial hit on "hospitality", so pooling them put eating places and hotels
    above 8062, general medical and surgical hospitals, whose own title contains
    the word. Exact hits outrank a title match; partial hits rank below one.

    Position is carried through so an alias lists its codes best-first rather than
    having them re-sorted into numeric order.
    """
    q = norm(query)
    if not q:
        return {}, set()
    exact: dict = {}
    loose: set = set()
    for word, codes in (_CODE_ALIASES.get(kind) or {}).items():
        w = norm(word)
        if w == q:
            for i, c in enumerate(codes):
                exact.setdefault(c, i)
        elif q in w or w in q:
            loose.update(codes)
    return exact, loose


def kinds() -> tuple:
    """The vocabulary names this module can serve, for callers that validate a
    request before dispatching on it."""
    return tuple(sorted(_KINDS))


def norm(s: str) -> str:
    """Lowercased with punctuation and spacing removed, matching
    apollo_taxonomy.norm so both modules agree on when two values are the same
    value. "WordPress.org", "wordpress org" and "wordpress_org" all collapse
    together, which is the whole point given Apollo returns one spelling and
    people type another."""
    s = str(s or "").strip().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", s)


def hint(kind: str) -> str:
    """The format rule for this kind in plain words, or "" if it has none. Shown
    when a typed value is rejected, so the message says what to do rather than
    only that something is wrong."""
    return (_KINDS.get(kind) or {}).get("hint", "")


def validate(kind: str, value) -> bool:
    """Whether this value is even the right shape for Apollo to consider.

    Only the code kinds have a shape. Technologies and locations are free strings
    to Apollo, and a wrong one fails by matching nothing rather than by being
    malformed, so there is nothing to check here and everything is accepted: a
    guess about which technology names exist is exactly the guess the picker is
    there to remove, not something to enforce against.
    """
    spec = _KINDS.get(kind)
    if not spec:
        return False
    pattern = spec["pattern"]
    if not pattern:
        return bool(str(value or "").strip())
    return bool(re.match(pattern, str(value or "").strip()))


def split_valid(kind: str, values) -> tuple:
    """(accepted, rejected) for a list of typed values.

    Rejected values are handed back rather than dropped so the caller can tell
    someone their 6-digit NAICS code was not sent, instead of running a search
    that silently ignored half of what they asked for.
    """
    ok, bad = [], []
    for raw in (values or []):
        v = str(raw or "").strip()
        if not v:
            continue
        (ok if validate(kind, v) else bad).append(v)
    return ok, bad


# Deliberately the same number as apollo_taxonomy.PICKER_LIMIT (a test pins them
# equal) rather than imported from it: these two modules hold different
# vocabularies and neither depends on the other, and one shared widget renders
# both, so the two lists must behave identically without being coupled.
#
# It used to be 40, which was smaller than every vocabulary here, so the picker
# was an alphabetical dead end: the location list stopped at "Czech Republic"
# and 163 of the 203 places it offers could not be browsed to at all. Every
# seed list below fits (location 204 written down, 203 distinct once
# "Singapore" is counted once; technology 168, sic 135, naics 121), and `meta`
# reports a cap that is hit anyway, since learned values can push past it.
PICKER_LIMIT = 300


def suggest(kind: str, query: str, learned=None, limit: int = PICKER_LIMIT,
            meta=None) -> list:
    """Ranked picker entries for a partly-typed query.

    Same entry shape as apollo_taxonomy.suggest so one widget renders both:
      value      what gets sent as the filter
      kind       this vocabulary's name
      confirmed  this exact value has been seen on a real Apollo record
      covers     always empty here; only industry families cover other values
      note       the code's official title, for the kinds that have one

    A code kind matches on its digits AND on its title, which is the point of
    the picker: nobody knows that computer systems design is 5415, but everybody
    can type "software" or "consulting".

    `meta`, when given a dict, reports {"total", "truncated"} the same way
    apollo_taxonomy.suggest does. See PICKER_LIMIT for why that matters.
    """
    spec = _KINDS.get(kind)
    if not spec:
        return []
    q = str(query or "").strip().lower()
    qn = norm(query)
    labelled = spec["labelled"]
    learned_norm = {norm(v): str(v).strip()
                    for v in (learned or []) if str(v or "").strip()}

    seed = _SEEDS[kind]
    alias_exact, alias_loose = (_alias_codes(kind, query) if labelled else ({}, set()))
    seen, out = set(), []

    def add(value, note, confirmed, rank, within=0):
        key = norm(value)
        if key in seen:
            return
        seen.add(key)
        out.append({"value": value, "kind": kind, "confirmed": confirmed,
                    "covers": [], "note": note,
                    "_rank": (rank, within, value.lower())})

    for item in seed:
        value, note = item if labelled else (item, "")
        if q:
            # A code is matched on its digits by prefix, so typing "54" offers
            # 54 and 5415 but not 6154, which contains those digits by accident.
            hay = norm(note) if labelled else norm(value)
            code_hit = labelled and value.lower().startswith(q.replace(" ", ""))
            title_hit = qn in hay
            if not code_hit and value not in alias_exact and not title_hit \
                    and value not in alias_loose:
                continue
            # Digits typed directly first, then the codes an exact alias names,
            # then titles, and only then codes reached by a partial alias word.
            if code_hit:
                rank, within = 0, 0
            elif value in alias_exact:
                rank, within = 1, alias_exact[value]
            elif title_hit:
                rank, within = (2 if hay.startswith(qn) else 3), 0
            else:
                rank, within = 4, 0
        else:
            rank, within = 0, 0
        add(value, note, norm(value) in learned_norm, rank, within)

    # Values Apollo has really returned that are not written down here. Offered
    # rather than hidden: Apollo using it is stronger evidence than this file.
    for key, original in sorted(learned_norm.items(), key=lambda kv: kv[1].lower()):
        if q and qn not in key:
            continue
        add(original, "", True, 2)

    out.sort(key=lambda e: e["_rank"])
    for e in out:
        e.pop("_rank", None)
    if meta is not None:
        meta["total"] = len(out)
        meta["truncated"] = len(out) > limit
    return out[:limit]
