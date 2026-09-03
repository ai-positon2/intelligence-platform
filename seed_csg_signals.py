"""
seed_csg_signals.py
===================
One-shot script: seeds all manually-researched CSG signals into
data/tracker_csg_v2.db so the dashboard shows real signal data.

Run ONCE (or re-run — duplicate detection is built in):
    python seed_csg_signals.py

Signals sourced from batch news searches run in May 2026.
Covers companies #75–185 (Eluktronics → Panasonic).
Companies #1–74 and #186–291 are searched separately.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from tracker.snapshot_store import SnapshotStore

DB_PATH = ROOT / "data" / "tracker_csg_v2.db"

# ── Signal type normalisation ──────────────────────────────────────────────
_TYPE_MAP = {
    "Leadership Change": "C-Suite Join",    # most are appointments; exits flagged below
    "Leadership Exit":   "C-Suite Exit",
    "Acquisition":       "Acquisition / M&A",
    "IPO":               "IPO Signal",
    "Funding":           "Funding Round",
    "News Mention":      "News Mention",
}

# Keywords in the title that indicate this is a departure/exit
_EXIT_KEYWORDS = ("steps down", "to leave", "departs", "resigned", "exit", "departure", "leaving")

def _resolve_signal_type(raw_type: str, title: str) -> str:
    t = _TYPE_MAP.get(raw_type, raw_type)
    # Re-classify Leadership Change as Exit if title implies departure
    if t == "C-Suite Join" and any(kw in title.lower() for kw in _EXIT_KEYWORDS):
        t = "C-Suite Exit"
    return t

def _severity(signal_type: str) -> str:
    if signal_type in ("C-Suite Join", "C-Suite Exit", "Acquisition / M&A",
                       "IPO Signal", "Funding Round"):
        return "HIGH"
    return "LOW"

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")[:64]

def _make_id(name: str) -> str:
    return "csg:" + _slugify(name)


# ── All known signals (from batch searches, May 2026) ─────────────────────
# Format: company_name → list of signal dicts
#   signal_type: raw type (see _TYPE_MAP above)
#   title:       article/signal headline  (used as signal_detail)
#   url:         source URL
#   date:        signal date (YYYY-MM-DD)

KNOWN_SIGNALS: dict[str, list[dict]] = {

    # ── Batch A28 (Xolo → Zyrex) ─────────────────────────────────────────
    "Yen Sun Technology": [
        {"signal_type": "News Mention",
         "title": "Yen Sun Technology (YS Tech) opens new factory in Kaohsiung to boost AI server cooling capacity; mass production scheduled H1 2026 amid surging demand from hyperscalers",
         "url": "https://apps.digitimes.com/news/a20251013PD235/yen-sun-ai-server-cooling-plant-capacity.html",
         "date": "2025-10-13"},
    ],
    "Zeng Hsing Industrial": [
        {"signal_type": "News Mention",
         "title": "Zeng Hsing Industrial approves private placement of new common shares at May 2026 AGM; launches robotics and smart manufacturing push as 2025 revenue dips 2.8% to NT$8.1B",
         "url": "https://www.digitimes.com/news/a20260525PD233/smart-manufacturing-growth-robotics-2025-business.html",
         "date": "2026-05-25"},
    ],
    "Zeuslap": [
        {"signal_type": "News Mention",
         "title": "Zeuslap wins Innovation Gold Award at Global Sources Electronics Fair October 2025; to exhibit new portable monitors and displays at Global Sources spring 2026 show",
         "url": "https://www.zeuslap.com/",
         "date": "2025-10-01"},
    ],
    "Zoom Corp": [
        {"signal_type": "News Mention",
         "title": "Tokyo District Court orders Zoom Video Communications to pay ¥182M damages to Japan's Zoom Corp for trademark infringement; court backs local 'ZOOM' owner in April 2026 ruling",
         "url": "https://www.japantimes.co.jp/business/2026/04/24/zoom-trademark-lawsuit/",
         "date": "2026-04-24"},
    ],
    "ZOTAC": [
        {"signal_type": "News Mention",
         "title": "ZOTAC warns GPU component shortages threaten 'the very survival of graphics card manufacturers'; GPU prices rise 17–65% amid supply constraints and order cancellations in 2025",
         "url": "https://www.zotac.com/",
         "date": "2025-06-01"},
    ],
    "zSpace": [
        {"signal_type": "Acquisition / M&A",
         "title": "zSpace acquires BlocksCAD (March 2025) and Second Avenue Learning (April 2025) to expand STEM and immersive learning portfolio",
         "url": "https://zspace.com/",
         "date": "2025-04-01"},
        {"signal_type": "Funding Round",
         "title": "zSpace secures $20M convertible debt facility to fund acquisitions and growth of AR/VR education platform",
         "url": "https://zspace.com/",
         "date": "2025-04-01"},
    ],
    "Zyrex": [
        {"signal_type": "News Mention",
         "title": "Zyrex (ZYRX) wins Rp793B (~$50M) Indonesian government contract for 120,538 Ministry of Education laptops; Q1 2026 net sales up 27.6% YoY to Rp55.4B",
         "url": "https://www.indopremier.com/ipotnews/newsDetail.php?jdl=ZYRX_Raih_Proyek_Pengadaan_Laptop_dari_Pemerintah_Senilai_Rp793_Miliar",
         "date": "2026-01-01"},
    ],

    # ── Batch A27 (Walton → XMG) ─────────────────────────────────────────
    "Whirlpool SA": [
        {"signal_type": "C-Suite Exit",
         "title": "Whirlpool CFO James Peters and President North America Alessandro Perucchetti both step down December 31, 2025 amid restructuring; new leadership team appointed",
         "url": "https://www.theglobeandmail.com/investing/markets/stocks/WHR/pressreleases/35995121/whirlpool-announces-leadership-changes-effective-january-2026/",
         "date": "2025-12-31"},
        {"signal_type": "News Mention",
         "title": "Whirlpool stock falls 38% in 2025; raises $475M in follow-on equity offering February 2026 to fund restructuring amid weak appliance demand",
         "url": "https://finance.yahoo.com/news/does-whirlpool-recent-restructuring-signal-011748750.html",
         "date": "2026-02-01"},
    ],
    "Xiaomi": [
        {"signal_type": "News Mention",
         "title": "Xiaomi recalls 116,887 SU7 EVs following fatal autopilot crash in Anhui province; $10B market cap rout in October 2025",
         "url": "https://www.scmp.com/business/china-business/article/3326115/xiaomi-recalls-nearly-117000-su7-vehicles-after-fatal-crash-raises-safety-concerns",
         "date": "2025-09-19"},
        {"signal_type": "News Mention",
         "title": "Xiaomi sets 550,000 EV delivery target for 2026; pledges CNY 200B R&D investment 2026-2030; EV business posts first sustained operating profit Q3 2025",
         "url": "https://www.digitimes.com/news/a20260105PD233/xiaomi-2026-expansion-ceo-vehicle.html",
         "date": "2026-01-05"},
    ],
    "Wortmann": [
        {"signal_type": "News Mention",
         "title": "Wortmann AG posts record revenue of €1.2B in FY2025; Group revenue exceeds €2.3B; expands Terra Cloud data center with €30M investment and 6MW capacity addition",
         "url": "https://www.datacenterdynamics.com/en/news/wortmann-ag-expands-terra-cloud-data-center-in-h%C3%BCllhorst-germany/",
         "date": "2025-06-01"},
    ],

    # ── Batch A26 (Vestel Elektronik → Vuzix) ────────────────────────────
    "VIZIO Holding Corp": [
        {"signal_type": "Acquisition / M&A",
         "title": "Walmart completes $2.3B acquisition of VIZIO on December 3, 2024; VIZIO becomes wholly owned Walmart subsidiary with CEO William Wang continuing",
         "url": "https://corporate.walmart.com/news/2024/12/03/walmart-completes-acquisition-of-vizio",
         "date": "2024-12-03"},
    ],
    "VOXX International Corporation": [
        {"signal_type": "Acquisition / M&A",
         "title": "Gentex Corporation closes acquisition of VOXX International on April 1, 2025 at $7.50/share; Klipsch, Onkyo and Pioneer brands now part of Gentex portfolio",
         "url": "https://www.globenewswire.com/news-release/2025/04/01/3053421/32299/en/Gentex-Announces-Closing-of-VOXX-International-Acquisition.html",
         "date": "2025-04-01"},
    ],
    "Vuzix Corporation": [
        {"signal_type": "Funding Round",
         "title": "Vuzix secures full $20M equity investment from Quanta Computer after meeting production yield targets; partnership milestone for AR waveguide business",
         "url": "https://www.prnewswire.com/news-releases/vuzix-reports-2025-financial-results-and-positions-waveguide-and-oem-businesses-for-next-phase-of-smart-glasses-growth-302712806.html",
         "date": "2025-12-31"},
    ],

    # ── Batch A25 (UMAX → Vestel) ────────────────────────────────────────
    "Universal Electronics Inc.": [
        {"signal_type": "Leadership Change",
         "title": "Universal Electronics appoints Wade M. Jenke as Chief Financial Officer in December 2025; Richard Carnifax serving as Interim CEO",
         "url": "https://www.sec.gov/Archives/edgar/data/0000101984/000010198425000249/exhibit991-pressreleasex20.htm",
         "date": "2025-12-01"},
    ],
    "Vestel Beyaz Esya Sanayi ve Ticaret A.S.": [
        {"signal_type": "C-Suite Exit",
         "title": "Vestel CEO Ergun Guler steps down April 9, 2025 to lead Zorlu Holding's International division; company left without permanent CEO for nearly nine months",
         "url": "https://www.turkiyetoday.com/business/turkiyes-struggling-electronics-firm-vestel-names-new-ceo-after-nearly-a-year-long-va-3213015",
         "date": "2025-04-09"},
        {"signal_type": "Leadership Change",
         "title": "Vestel appoints Gokhan Sigin as new CEO effective January 19, 2026; mandate to drive Vestel 4.0 technology transformation and international expansion",
         "url": "https://vestelinternational.com/media/news/gokhan-sigin-named-new-ceo-of-vestel",
         "date": "2026-01-19"},
    ],

    # ── Batch A24 (Tomita Electric → U-Tech Media) ───────────────────────
    "Turtle Beach Corporation": [
        {"signal_type": "Leadership Change",
         "title": "Turtle Beach appoints Mark Weinswig as Chief Financial Officer effective February 3, 2025",
         "url": "https://www.sec.gov/Archives/edgar/data/0001493761/000089457925000022/exh99_1.htm",
         "date": "2025-02-03"},
    ],

    # ── Batch A23 (T-Platforms → Thiensurat) ─────────────────────────────
    "Tadiran Holdings Ltd": [
        {"signal_type": "Leadership Change",
         "title": "Tadiran appoints new CEO (formerly Deputy CEO and VP Operations & IT of Tadiran Telecom) in November 2025",
         "url": "https://finder.startupnationcentral.org/company_page/tadiran-holdings",
         "date": "2025-11-01"},
    ],
    "Tatung Company": [
        {"signal_type": "Leadership Change",
         "title": "Tatung appoints Jung-hua Chang as Chairman (August 2025) and new CEO (November 2025); three-pronged reform agenda covering transparency, AI integration and subsidiary review",
         "url": "https://www.digitimes.com/news/a20260113PD203/tatung-2026-growth-ems-business.html",
         "date": "2025-08-01"},
        {"signal_type": "News Mention",
         "title": "Tatung board comprehensively reshuffled with nine new directors at December 30, 2025 extraordinary shareholders' meeting; NT$2.725B subsidiary investment approved May 2026",
         "url": "https://finance.biggo.com/news/twse_major_2371_1150512_181114",
         "date": "2025-12-30"},
    ],
    "TCL Electronics Holdings Ltd": [
        {"signal_type": "Acquisition / M&A",
         "title": "TCL and Sony sign definitive agreements for BRAVIA Inc. joint venture (TCL 51%, Sony 49%) valued at ¥102.8B (~USD 650M); TCL to take over Sony's global home entertainment TV and audio business",
         "url": "https://www.flatpanelshd.com/news.php?subaction=showfull&id=1774951256",
         "date": "2026-03-31"},
    ],
    "The Vitec Group plc": [
        {"signal_type": "Leadership Change",
         "title": "Vitec Group (Videndum) appoints Stephen Harris as Executive Chairman in July 2025; company reported £18.2M adjusted operating loss and 8% revenue decline in FY2024",
         "url": "https://www.twelfthmagpie.com/tickers/lse-vid/",
         "date": "2025-07-29"},
    ],

    # ── Batch A22 (Sonos → System76) ─────────────────────────────────────
    "Sonos, Inc.": [
        {"signal_type": "C-Suite Exit",
         "title": "Sonos CEO Patrick Spence resigns January 2025 following app overhaul disaster; Tom Conrad named Interim CEO",
         "url": "https://www.sec.gov/Archives/edgar/data/0001314727/000131472725000003/exhibit991finalsonosannoun.htm",
         "date": "2025-01-12"},
        {"signal_type": "News Mention",
         "title": "Sonos lays off 200 employees (12% of workforce) in February 2025, second round of cuts in six months",
         "url": "https://www.edhat.com/news/sonos-announces-major-layoffs-amid-company-restructuring-efforts/",
         "date": "2025-02-01"},
        {"signal_type": "Leadership Change",
         "title": "Sonos appoints Tom Conrad as permanent CEO on July 23, 2025 following competitive board search",
         "url": "https://www.sec.gov/Archives/edgar/data/0001314727/000131472725000067/finalceopressrelease.htm",
         "date": "2025-07-23"},
    ],
    "Sony Group Corporation": [
        {"signal_type": "Leadership Change",
         "title": "Hiroki Totoki appointed President and CEO of Sony Group effective April 1, 2025; Kenichiro Yoshida becomes Chairman",
         "url": "https://variety.com/2025/biz/news/sony-corp-shake-up-hiroki-totoki-ceo-hideaki-nishino-playstation-1236289299/",
         "date": "2025-04-01"},
        {"signal_type": "News Mention",
         "title": "Sony Honda Mobility discontinues development of AFEELA 1 and second EV model on March 25, 2026 as Honda reassesses electrification strategy",
         "url": "https://collisionweek.com/2026/03/26/sony-honda-mobility-discontinues-afeela-electric-vehicle-program/",
         "date": "2026-03-25"},
    ],
    "Supermicro": [
        {"signal_type": "C-Suite Exit",
         "title": "Supermicro SVP and board member Yih-Shyan 'Wally' Liaw indicted for alleged export-control violations; resigns from board immediately",
         "url": "https://www.sec.gov/Archives/edgar/data/0001375365/000137536526000013/exhibit991_20260331.htm",
         "date": "2026-03-31"},
        {"signal_type": "Leadership Change",
         "title": "Supermicro appoints DeAnna Luna as acting Chief Compliance Officer and Scott Angel as new independent director following export-control indictment",
         "url": "https://www.sec.gov/Archives/edgar/data/0001375365/000137536525000008/smcipressrelease-boardscot.htm",
         "date": "2026-03-31"},
    ],

    # ── Batch A21 (Shenzhen Jumper → Sonar Radio) ────────────────────────
    "Skyworth Group Ltd": [
        {"signal_type": "News Mention",
         "title": "Skyworth New Energy posts record RMB 30B revenue in 2024; analysts project spin-off or IPO of New Energy/Smart Appliance division in 2026",
         "url": "https://www.marketscreener.com/quote/stock/SKYWORTH-GROUP-LIMITED-1412703/news/",
         "date": "2026-01-01"},
    ],

    # ── Batch A20 (Ruggon → Shenzhen Crastal) ────────────────────────────
    "Samsung Electronics": [
        {"signal_type": "C-Suite Exit",
         "title": "Samsung Electronics co-CEO Han Jong-Hee dies of cardiac arrest aged 63 on March 25, 2025; led DX consumer electronics & mobile division since 2022",
         "url": "https://edition.cnn.com/2025/03/24/tech/samsung-co-ceo-han-jong-hee-death-intl-hnk",
         "date": "2025-03-25"},
        {"signal_type": "Leadership Change",
         "title": "Samsung reinstates dual-CEO structure: Roh Tae-moon appointed co-CEO for DX division alongside Jun Young-hyun (Device Solutions)",
         "url": "https://www.koreaherald.com/article/10621169",
         "date": "2025-11-01"},
        {"signal_type": "Leadership Change",
         "title": "Samsung names Janghyun Yoon as President and CTO of DX Division and Hongkun Park as Head of Samsung Advanced Institute of Technology",
         "url": "https://news.samsung.com/global/samsung-electronics-announces-new-leadership-4",
         "date": "2025-11-21"},
    ],
    "Sharp Corp": [
        {"signal_type": "C-Suite Exit",
         "title": "Sharp CEO Robert Wu steps down after two consecutive years of net losses; company scales back struggling LCD business",
         "url": "https://www.strata-gee.com/sharp-cuts-ceo-after-two-years-of-losses-company-scaling-back-lcd-div-significantly/",
         "date": "2026-03-23"},
        {"signal_type": "Leadership Change",
         "title": "Sharp names Tetsuji Kawamura (BD chief) as new President and CEO effective April 1, 2026, shifting company from restructuring to growth",
         "url": "https://www.digitimes.com/news/a20260401PD225/sharp-ceo-management-business.html",
         "date": "2026-04-01"},
        {"signal_type": "Acquisition / M&A",
         "title": "Sharp acquires Synapse Innovation on March 23, 2026",
         "url": "https://www.digitimes.com/news/a20260323PD220/sharp-business-foxconn-ceo-subsidiary.html",
         "date": "2026-03-23"},
    ],

    # ── Batch A19 (PT Selaras → Rockford Corp) ───────────────────────────
    "Quanta Computer": [
        {"signal_type": "News Mention",
         "title": "Quanta Computer's AI-server backlog extends to 2027 as AI servers reach 70% of Q1 2025 revenue; doubling capacity through 2026",
         "url": "https://www.digitimes.com/news/a20251113PD234/quanta-2027-expansion-2026-revenue.html",
         "date": "2025-11-13"},
    ],
    "Razer Inc.": [
        {"signal_type": "News Mention",
         "title": "Razer halts US laptop sales on April 2, 2025 in response to US reciprocal tariff announcement",
         "url": "https://sg.finance.yahoo.com/news/razer-delist-may-getting-shareholders-175659341.html",
         "date": "2025-04-02"},
        {"signal_type": "News Mention",
         "title": "Razer launches AI Center of Excellence in Singapore with 150 AI hires; plans two more centers in Europe and US",
         "url": "https://www.bitget.com/en-CA/wiki/razer-stock",
         "date": "2025-08-01"},
    ],
    "Realme": [
        {"signal_type": "Acquisition / M&A",
         "title": "Realme integrated into Oppo as sub-brand effective January 2026; CEO Sky Li to oversee Realme and OnePlus under Oppo umbrella",
         "url": "https://www.india.com/technology/inside-the-realme-oppo-merger-of-2026-new-brand-hierarchy-stronger-after-sales-support-and-what-buyers-should-expect-8260883/",
         "date": "2026-01-07"},
        {"signal_type": "News Mention",
         "title": "Realme lays off India sales team following Oppo merger; retail employees and sales managers placed on notice",
         "url": "https://9to5google.com/2026/02/05/realme-reportedly-hit-with-layoffs-following-oppo-merger/",
         "date": "2026-02-05"},
    ],
    "Rinnai Corp": [
        {"signal_type": "Acquisition / M&A",
         "title": "Rinnai acquires Peru-based housing equipment company MT Industrial S.A.C., expanding Latin America footprint",
         "url": "https://www.rinnai.co.jp/en/releases/docs/news_20251107.pdf",
         "date": "2025-10-01"},
    ],

    # ── Batch A18 (Olidata → Powertech Industrial) ───────────────────────
    "Panasonic Corp": [
        {"signal_type": "News Mention",
         "title": "Panasonic cuts 10,000 jobs globally (4% of workforce) with ¥130B restructuring charge; CEO Yuki Kusumi takes 40% pay cut",
         "url": "https://www.thehrdigest.com/panasonic-ceos-pay-cut-signals-accountability-amid-global-layoffs/",
         "date": "2025-06-01"},
        {"signal_type": "Leadership Change",
         "title": "Panasonic activates new group structure April 1, 2026; Akira Toyoshima named President of Panasonic Corporation, multiple new operating company presidents appointed",
         "url": "https://news.panasonic.com/global/press/en250730-6",
         "date": "2026-04-01"},
        {"signal_type": "C-Suite Exit",
         "title": "Panasonic Connect President and CEO Yasuyuki Higuchi resigns March 31, 2026; succeeded by Kenneth William Sain",
         "url": "https://news.panasonic.com/global/press/en250730-9",
         "date": "2026-03-31"},
    ],
    "Powersoft S.p.A.": [
        {"signal_type": "Acquisition / M&A",
         "title": "Powersoft acquires 51% stake in K-Array for €37.5M, completed April 1, 2025; secures option to reach 100% ownership",
         "url": "https://www.powersoft.com/en/announcement/powersoft-acquires-51-k-array",
         "date": "2025-04-01"},
        {"signal_type": "News Mention",
         "title": "Powersoft signs technology partnership with Ferrari S.p.A. to enter automotive audio market",
         "url": "https://www.powersoft.com/wp-content/uploads/2025/10/Powersoft-20251002.pdf",
         "date": "2025-10-02"},
    ],

    # ── Batch A17 (Multilaser → Obsidian-PC) ─────────────────────────────
    "NEC": [
        {"signal_type": "Acquisition / M&A",
         "title": "NEC completes $2.9B acquisition of CSG Systems International; Netcracker to lead combined BSS/OSS business",
         "url": "https://www.nec.com/en/press/202605/global_20260515_01.html",
         "date": "2026-05-14"},
        {"signal_type": "Leadership Change",
         "title": "Andrew Feinberg appointed Chairman and CEO of combined Netcracker-CSG entity following NEC's CSG Systems acquisition",
         "url": "https://www.netcracker.com/news/press-releases/nec-completes-acquisition-of-csg-systems-netcracker-to-lead-combined-business",
         "date": "2026-05-14"},
    ],
    "Nokia": [
        {"signal_type": "Leadership Change",
         "title": "Justin Hotard succeeds Pekka Lundmark as Nokia President and CEO effective April 1, 2025; Hotard previously led Intel's Data Center & AI Group",
         "url": "https://www.sec.gov/Archives/edgar/data/0000924613/000110465925011052/tm256021d1_6k.htm",
         "date": "2025-04-01"},
        {"signal_type": "Acquisition / M&A",
         "title": "Nokia closes acquisition of Infinera Corporation for EUR 2.5 billion, creating optical networks powerhouse for AI-era demand",
         "url": "https://www.sec.gov/Archives/edgar/data/0000924613/000110465925018857/tm257943d1_6k.htm",
         "date": "2025-02-28"},
        {"signal_type": "Leadership Change",
         "title": "Nokia names Raghav Sahgal Chief Customer Officer and Tommi Uitto steps down from Group Leadership Team effective December 31, 2025",
         "url": "https://www.sec.gov/Archives/edgar/data/0000924613/000110465925113946/tm2531737d1_6k.htm",
         "date": "2025-12-31"},
    ],
    "Northbaze Group AB": [
        {"signal_type": "C-Suite Exit",
         "title": "Northbaze Group AB CEO departs January 2025 amid company restructuring",
         "url": "https://mfn.se/all/a/northbaze-group",
         "date": "2025-01-01"},
        {"signal_type": "News Mention",
         "title": "Northbaze Group AB delisted from Nasdaq First North Growth Market with last trading day December 23, 2025",
         "url": "https://view.news.eu.nasdaq.com/view?id=bed30f0fd78199a16fc53c73bba36e74a&lang=en",
         "date": "2025-12-23"},
    ],
    "NZXT": [
        {"signal_type": "Funding Round",
         "title": "NZXT secures $100M strategic investment led by Francisco Partners to grow gaming hardware and lifestyle brand",
         "url": "https://nzxt.com/blogs/news/nzxt-secures-first-ever-strategic-investment-led-by-francisco-partners",
         "date": "2025-01-01"},
    ],

    # ── Batch A16 (Microtech → MPS Infotecnics) ──────────────────────────
    "MiTAC": [
        {"signal_type": "Leadership Change",
         "title": "MiTAC Holdings appoints Scott Matthew as Director and adds Chung-Ming Kuan and Fang-Yu Wen as Independent Directors (May 2025)",
         "url": "https://www.mitac.com/en-global/corporate_governance/index/Board-of-Directors",
         "date": "2025-05-23"},
        {"signal_type": "News Mention",
         "title": "MiTAC Holdings ramps up North American server manufacturing with two California facility leases secured in one week to meet explosive AI infrastructure demand",
         "url": "https://www.techsciresearch.com/news/25036-mitac-holdings-ramps-up-north-american-server-manufacturing-to-meet-explosive-ai-infrastructure-demand.html",
         "date": "2026-03-03"},
        {"signal_type": "News Mention",
         "title": "MiTAC plans to open four new factory locations in 2026 across US, Vietnam and Taiwan driven by AI server and cloud demand",
         "url": "https://www.digitimes.com/news/a20260421PD201/mitac-production-taiwan-growth-expansion.html",
         "date": "2026-04-21"},
    ],
    "Mirgor Sociedad Anónima, Comercial, Industrial, Financiera, Inmobiliaria y Agropecuaria": [
        {"signal_type": "News Mention",
         "title": "Mirgor issues notes for nominal USD 20,000,000 at 8.5% fixed rate maturing November 2026 on Buenos Aires exchange",
         "url": "https://chambers.com/articles/note-issuance-by-mirgor-for-us-20-000-000",
         "date": "2025-12-01"},
    ],
    "Moto": [
        {"signal_type": "News Mention",
         "title": "Motorola debuts first book-style foldable device and AI-unified Lenovo-Motorola ecosystem at Lenovo Tech World 2026",
         "url": "https://motorolanews.com/motorola-unveils-new-flagship-devices-and-ai-powered-innovation-at-lenovo-tech-world-2026/",
         "date": "2026-05-01"},
    ],

    # ── Batch A15 (Maingear → MSI) ───────────────────────────────────────
    "Maxell Holdings Ltd": [
        {"signal_type": "Acquisition / M&A",
         "title": "Maxell Holdings acquires Murata Manufacturing's Micro Primary Battery Business, strengthening energy product portfolio",
         "url": "https://www2.maxell.co.jp/ir/",
         "date": "2025-06-16"},
        {"signal_type": "Leadership Change",
         "title": "Maxell reshapes board and executive lineup ahead of June 2026 shareholders' meeting; adds leaders for Energy and New Business units",
         "url": "https://www.tipranks.com/news/company-announcements/maxell-reshapes-board-and-executive-lineup-ahead-of-2026-shareholders-meeting",
         "date": "2026-06-01"},
    ],
    "Medion": [
        {"signal_type": "Acquisition / M&A",
         "title": "Lenovo divests majority stake in Medion AG back to founder Gerd Brachmann in August 2025; Medion splits into new Medion GmbH entity",
         "url": "https://www.clearygottlieb.com/news-and-insights/news-listing/lenovo-in-squeeze-out-of-medion-ag-minority-shareholders",
         "date": "2025-08-01"},
    ],
    "Micro-Star International (MSi)": [
        {"signal_type": "News Mention",
         "title": "MSI collaborates with Syrma SGS to manufacture laptops locally in Chennai, India",
         "url": "https://www.zoominfo.com/c/micro-star-international-co-ltd/25788883",
         "date": "2025-01-10"},
    ],

    # ── Batch A14 (Leader Electronics → Machenike) ───────────────────────
    "LG Electronics, Inc.": [
        {"signal_type": "Leadership Exit",
         "title": "LG Electronics CEO William Cho steps down after four-year tenure; Lyu Jae-cheol appointed new CEO effective December 1, 2025",
         "url": "https://www.lg.com/global/newsroom/news/corporate/lg-announces-organizational-changes-for-2026/",
         "date": "2025-12-01"},
        {"signal_type": "Leadership Change",
         "title": "Lyu Jae-cheol named CEO of LG Electronics; Don Kwack appointed CEO of LG Electronics North America effective January 2026",
         "url": "https://www.lg.com/us/press-release/kwack-named-president-and-ceo-lg-electronics-north-america",
         "date": "2025-12-30"},
        {"signal_type": "News Mention",
         "title": "LG Electronics announces major 2026 organizational restructuring: streamlines four-Company structure, creates new HVAC, webOS and robotics growth engines",
         "url": "https://www.lgnewsroom.com/2025/11/lg-announces-organizational-changes-for-2026/",
         "date": "2025-11-01"},
    ],
    "LG Display Co. Ltd": [
        {"signal_type": "Acquisition / M&A",
         "title": "LG Display sells last remaining LCD TV panel factory in Guangzhou to TCL CSOT for ~10.8B yuan ($1.47B); proceeds to fund OLED development",
         "url": "https://www.flatpanelshd.com/news.php?subaction=showfull&id=1744354100",
         "date": "2025-04-01"},
    ],
    "Logitech": [
        {"signal_type": "Leadership Exit",
         "title": "Logitech Chairperson Wendy Becker does not stand for re-election after eight years on board; Guy Gecht nominated as new Chair",
         "url": "https://www.sec.gov/Archives/edgar/data/0001032975/000103297525000034/exhibit9912025agmboardnomi.htm",
         "date": "2025-09-01"},
    ],

    # ── Batch A13 (Jiu Rong → Lava International) ────────────────────────
    "JVC KENWOOD Corp": [
        {"signal_type": "News Mention",
         "title": "JVC Kenwood announces withdrawal from healthcare business segment in February 2026 as part of restructuring",
         "url": "https://www.jvckenwood.com/en/press/",
         "date": "2026-02-01"},
    ],
    "Kontron": [
        {"signal_type": "Acquisition / M&A",
         "title": "Kontron Acquisition GmbH initiates squeeze-out of KATEK SE minority shareholders in November 2025",
         "url": "https://www.kontron.com/en/news",
         "date": "2025-11-01"},
        {"signal_type": "News Mention",
         "title": "Kontron forms new eSystems GmbH combining solar, energy and e-mobility electronics divisions; targets EUR 1.75B revenue in 2026",
         "url": "https://www.kontron.com/en/news",
         "date": "2025-09-01"},
    ],
    "Kyocera Corp": [
        {"signal_type": "Acquisition / M&A",
         "title": "Kyocera acquires 33% stake in Japan Aviation Electronics Industry (JAE) from NEC; forms capital and business alliance",
         "url": "https://global.kyocera.com/newsroom/news/2025/001133.html",
         "date": "2025-10-31"},
        {"signal_type": "News Mention",
         "title": "Kyocera reviews divestiture of KITI (Kyocera Industrial Tools) and initiates strategic business portfolio review",
         "url": "https://global.kyocera.com/ir/news/pdf/251121_kiti_e.pdf",
         "date": "2025-11-21"},
    ],
    "Lava International": [
        {"signal_type": "News Mention",
         "title": "Lava International reports 63% smartphone growth in 2025 and enters UK market in Q1 2026 with Made-in-India Agni series",
         "url": "https://www.thedailyjagran.com/technology/lava-to-enter-uk-market-in-2026-aiming-to-build-a-global-madeinindia-smartphone-brand-10279384",
         "date": "2026-01-01"},
    ],

    # ── Batch A12 (Image Systems → Jinhua Chunguang) ─────────────────────
    "iRobot Corporation": [
        {"signal_type": "News Mention",
         "title": "iRobot files Chapter 11 bankruptcy (December 14, 2025) and enters Restructuring Support Agreement with primary contract manufacturer Picea",
         "url": "https://www.manufacturingdive.com/news/roomba-braava-maker-irobot-chapter-11-bankruptcy-acquisition-picea-china/807997/",
         "date": "2025-12-14"},
        {"signal_type": "Acquisition / M&A",
         "title": "Shenzhen PICEA Robotics to acquire 100% of iRobot through court-supervised Chapter 11 process; iRobot to become private company",
         "url": "https://www.prnewswire.com/news-releases/irobot-announces-strategic-transaction-to-drive-long-term-growth-plan-302641744.html",
         "date": "2025-12-14"},
    ],
    "Japan Display, Inc.": [
        {"signal_type": "News Mention",
         "title": "Japan Display stops OLED production; closes Tottori factory March 2025 and signs agreement to sell Tottori Fab",
         "url": "https://www.j-display.com/en/news/release/detail/20250926114735.html",
         "date": "2025-03-01"},
        {"signal_type": "Acquisition / M&A",
         "title": "Japan Display makes strategic investment in OLEDWorks and forms strategic alliance with TECH EXTENSION for 3D semiconductor integration",
         "url": "https://www.j-display.com/english/news/index.html",
         "date": "2025-02-12"},
    ],

    # ── Batch A11 (Hisense → Ihlas Ev Aletleri) ──────────────────────────
    "Hisense": [
        {"signal_type": "Leadership Change",
         "title": "Hisense USA appoints industry veteran James Fishler as Chief Commercial Officer effective January 1, 2026",
         "url": "https://www.hisense-usa.com/post/hisense-usa-to-appoint-james-fishler-to-c-suite-as-the-company-strengthens-its-u-s-strategy-ahead-o",
         "date": "2026-01-01"},
        {"signal_type": "News Mention",
         "title": "Texas AG files lawsuit against Hisense over alleged illegal smart TV viewer data collection via ACR technology",
         "url": "https://www.cepro.com/news/hisense-names-james-fishler-chief-commercial-officer-as-company-expands-u-s-strategy/624088/",
         "date": "2025-12-01"},
    ],
    "Honor": [
        {"signal_type": "Leadership Change",
         "title": "Li Jian appointed CEO of Honor Device Co., positioning company as AI device ecosystem player ahead of IPO",
         "url": "https://thebambooworks.com/honor-ipo-advances-as-new-chief-talks-up-its-ai-credentials/",
         "date": "2025-01-01"},
        {"signal_type": "IPO Signal",
         "title": "Honor initiates A-share IPO process with CITIC Securities as broker; listing targeted for Q2 2026",
         "url": "https://www.sahmcapital.com/news/content/former-huawei-business-honor-setting-up-for-2026-ipo-2025-06-30",
         "date": "2025-06-30"},
    ],
    "HP Inc": [
        {"signal_type": "Leadership Exit",
         "title": "HP Inc CEO Enrique Lores steps down to pursue another professional opportunity; Board initiates CEO search",
         "url": "https://www.globenewswire.com/news-release/2026/02/03/3230902/0/en/HP-Inc-Announces-Leadership-Transition.html",
         "date": "2026-02-03"},
        {"signal_type": "Leadership Change",
         "title": "Bruce Broussard appointed Interim CEO of HP Inc. effective February 3, 2026",
         "url": "https://www.globenewswire.com/news-release/2026/02/03/3230902/0/en/HP-Inc-Announces-Leadership-Transition.html",
         "date": "2026-02-03"},
    ],
    "HPE": [
        {"signal_type": "Acquisition / M&A",
         "title": "HPE completes $14 billion acquisition of Juniper Networks on July 2, 2025, creating AI-driven networking leader",
         "url": "https://investors.hpe.com/financial/acquisitions",
         "date": "2025-07-02"},
    ],
    "Huawei": [
        {"signal_type": "Leadership Change",
         "title": "Meng Wanzhou (Sabrina Meng) assumes Huawei rotating chairperson role from October 1, 2025 through March 2026",
         "url": "https://www.digitimes.com/news/a20251002PD219/huawei-2025-chairman-canada-2026.html",
         "date": "2025-10-01"},
        {"signal_type": "Leadership Exit",
         "title": "Huawei veteran Ken Hu removed from executive committee; David Wang (ICT infrastructure head) takes his seat",
         "url": "https://www.lightreading.com/business-transformation/huawei-reshuffles-top-leadership",
         "date": "2025-10-01"},
    ],
    "IGEL Technology": [
        {"signal_type": "Acquisition / M&A",
         "title": "IGEL Technology acquires Stratodesk, combining two endpoint OS pioneers under one platform",
         "url": "https://www.igel.com/about-us/press-releases/igel-continues-executive-leadership-expansion-with-appointment-of-ash-chowdappa-as-chief-product-development-officer/",
         "date": "2025-05-28"},
        {"signal_type": "Leadership Change",
         "title": "IGEL appoints Ash Chowdappa as Chief Product & Development Officer; expands leadership with CRO, Field CTO and VP Sales DACH",
         "url": "https://finance.yahoo.com/news/igel-continues-executive-leadership-expansion-213300563.html",
         "date": "2026-02-01"},
    ],

    # ── Batch A10 (Guodian Nanjing → Hibino Corp) ────────────────────────
    "Hamilton Beach Brands Holding Company": [
        {"signal_type": "Leadership Exit",
         "title": "Hamilton Beach Brands CEO Gregory H. Trepp retires effective December 31, 2024 after leading the company",
         "url": "https://investorshangout.com/leadership-transition-at-hamilton-beach-brands-whats-next-38429-/",
         "date": "2024-12-31"},
        {"signal_type": "Leadership Change",
         "title": "R. Scott Tidey promoted to CEO of Hamilton Beach Brands Holding Company",
         "url": "https://investorshangout.com/leadership-transition-at-hamilton-beach-brands-whats-next-38429-/",
         "date": "2025-01-01"},
    ],
    "Hapbee Technologies, Inc.": [
        {"signal_type": "Leadership Exit",
         "title": "Hapbee Technologies CEO Yona Shtern steps down; Chairman Riz Shah assumes interim CEO role in September 2025",
         "url": "https://investors.hapbee.com/press-releases",
         "date": "2025-09-01"},
        {"signal_type": "Funding Round",
         "title": "Hapbee Technologies closes post-IPO funding round in August 2025",
         "url": "https://investors.hapbee.com/press-releases",
         "date": "2025-08-18"},
    ],

    # ── Batch A9 (Gateway → Grundig) ─────────────────────────────────────
    "GoPro, Inc.": [
        {"signal_type": "News Mention",
         "title": "GoPro approves restructuring plan cutting ~23% of global workforce (≈145 roles) in Q2 2026 to reduce operating costs",
         "url": "https://www.sec.gov/Archives/edgar/data/1500435/000162828026024066/gpro-20260407.htm",
         "date": "2026-04-07"},
    ],
    "Groupe Bull": [
        {"signal_type": "Acquisition / M&A",
         "title": "French State acquires Groupe Bull (Atos' HPC, AI and quantum unit) for €404M; transaction completes March 31, 2026",
         "url": "https://www.theregister.com/2026/04/01/france_bull_purchase/",
         "date": "2026-03-31"},
    ],
    "Grundig": [
        {"signal_type": "News Mention",
         "title": "Arcelik licenses Grundig brand to China's Changhong International Holding for white goods and CE sales across Europe, APAC and CIS from January 2026",
         "url": "https://www.turkiyetoday.com/business/koc-groups-arcelik-signs-major-licensing-deal-with-chinas-changhong-for-grundig-3211550",
         "date": "2026-01-01"},
    ],

    # ── Batch A8 (Evoo → Fujitsu Technology Solutions) ───────────────────
    "Foxconn": [
        {"signal_type": "Leadership Change",
         "title": "Foxconn names Michael Chiang as rotating CEO for one-year term effective April 1, 2026, succeeding Kathy Yang",
         "url": "https://www.digitimes.com/news/a20260402PD201/foxconn-ceo-governance-business-management.html",
         "date": "2026-04-01"},
        {"signal_type": "News Mention",
         "title": "Foxconn posts record full-year 2025 net profit NT$189.3B and record EPS of NT$13.61; declares record dividend NT$7.2 per share",
         "url": "https://www.foxconn.com/en-us/press-center/press-releases/latest-news/1812",
         "date": "2026-03-01"},
    ],
    "Framework Computer": [
        {"signal_type": "News Mention",
         "title": "Framework Computer named in Fast Company's Most Innovative Consumer Electronics Companies of 2026; Ubuntu sales outpace Windows",
         "url": "https://frame.work/blog/category/news",
         "date": "2026-03-01"},
    ],
    "Fujitsu": [
        {"signal_type": "Acquisition / M&A",
         "title": "Fujitsu acquires 86.30% stake in BrainPad Inc. for ¥48.8 billion to strengthen AI and data analytics capabilities",
         "url": "https://global.fujitsu/-/media/Project/Fujitsu/Fujitsu-HQ/pr/news/2026/01/29-02-en.pdf",
         "date": "2025-12-15"},
        {"signal_type": "News Mention",
         "title": "Fujitsu concludes company split agreements with Fujitsu Frontech, Japan and merger with Home & Office Services in major restructuring",
         "url": "https://global.fujitsu/en-global/pr",
         "date": "2025-12-23"},
    ],

    # ── Batch A7 (DynaColor → Eurocom) ───────────────────────────────────
    "Electra Consumer Products": [
        {"signal_type": "Acquisition / M&A",
         "title": "Electra Consumer Products completes acquisition of Terra Armee",
         "url": "https://www.elco.co.il/portfolio/electra-consumer-products/",
         "date": "2025-05-21"},
    ],
    "Elitegroup Computer Systems": [
        {"signal_type": "News Mention",
         "title": "ECS expands into low-orbit satellite development with EliteSpace OBC development kit for CubeSat missions at COMPUTEX 2025",
         "url": "https://www.ecs.com.tw/en/news",
         "date": "2025-05-01"},
    ],
    "Epson": [
        {"signal_type": "Leadership Change",
         "title": "Junkichi Yoshida appointed CEO of Seiko Epson; Yasunori Ogawa transitions to Chairman after five years as President",
         "url": "https://corporate.epson/en/about/pdf/2507en.pdf",
         "date": "2025-04-01"},
        {"signal_type": "Acquisition / M&A",
         "title": "Seiko Epson absorbs wholly owned subsidiary Orient Watch Co., Ltd. through absorption-type merger",
         "url": "https://corporate.epson/en/news/2025/251105-2.html",
         "date": "2026-02-01"},
    ],

    # ── Batch A6 (DBG Technology → Dynabook Taiwan) ──────────────────────
    "DBG Technology Co. Ltd": [
        {"signal_type": "Acquisition / M&A",
         "title": "DBG Technology acquires France's All Circuits EMS provider for ~CNY 733M, adding capacity in France, Tunisia and Mexico",
         "url": "https://www.yicaiglobal.com/news/chinas-dbg-tech-soars-on-plan-to-acquire-french-auto-electronics-firm-all-circuits",
         "date": "2025-01-01"},
    ],
    "Dell Technologies": [
        {"signal_type": "Leadership Exit",
         "title": "Dell Technologies CFO Yvonne McGill steps down after nearly 30 years; David Kennedy named interim CFO",
         "url": "https://investors.delltechnologies.com/news-releases/news-release-details/dell-technologies-announces-cfo-transition",
         "date": "2025-09-09"},
    ],
    "DFI": [
        {"signal_type": "News Mention",
         "title": "DFI expands Taiwan manufacturing capacity by 25% and adds six AI server assembly lines to meet Edge AI demand",
         "url": "https://www.prnewswire.com/news-releases/dfi-expands-taiwan-manufacturing-capacity-to-support-edge-ai-and-regulated-deployments-302748452.html",
         "date": "2026-04-01"},
    ],
    "Dometic Group AB": [
        {"signal_type": "News Mention",
         "title": "Dometic launches global restructuring program targeting SEK 750M annual EBITA improvement; divesting non-strategic product lines",
         "url": "https://www.dometicgroup.com/en-us/investors/press-releases/dometic-announces-global-restructuring-program",
         "date": "2024-12-01"},
        {"signal_type": "Leadership Exit",
         "title": "Dometic Group CFO Stefan Fristedt leaves company; Per Carlsson named Acting CFO effective April 7, 2026",
         "url": "https://news.cision.com/dometic-group",
         "date": "2026-04-07"},
    ],
    "Dynabook North America.": [
        {"signal_type": "Leadership Change",
         "title": "Dynabook Canada appoints Carmine Cinerari as President & CEO, reflecting deeper integration with Sharp Electronics of Canada",
         "url": "https://ca.dynabook.com/pressrelease/102445",
         "date": "2025-04-01"},
    ],

    # ── Batch A5 (Coretronic Corp → Daten) ───────────────────────────────
    "Coretronic Corp": [
        {"signal_type": "Acquisition / M&A",
         "title": "Ardentec acquires 51% stake in Coretronic's Cheng Ping Technology subsidiary to enhance display product line",
         "url": "https://www.coretronic.com/en/ir/report/BZwOZd/download",
         "date": "2025-06-01"},
        {"signal_type": "News Mention",
         "title": "Coretronic accelerates transformation into AI sensing, drone and logistics automation group; subsidiaries return to profit in 2025",
         "url": "https://pitchbook.com/profiles/company/83399-41",
         "date": "2025-12-01"},
    ],
    "Corsair": [
        {"signal_type": "Leadership Exit",
         "title": "Corsair founder and CEO Andy Paul retires; hands leadership to President Thi La effective July 1, 2025",
         "url": "https://ir.corsair.com/news-releases/news-release-details/corsairs-planned-ceo-transition-takes-effect-thi-la-assumes-role/",
         "date": "2025-07-01"},
        {"signal_type": "Leadership Change",
         "title": "Thi La assumes role of CEO at Corsair Gaming following planned leadership transition",
         "url": "https://ir.corsair.com/news-releases/news-release-details/corsairs-planned-ceo-transition-takes-effect-thi-la-assumes-role/",
         "date": "2025-07-01"},
        {"signal_type": "Leadership Change",
         "title": "Corsair appoints Gordon Mattingly as Chief Financial Officer as part of long-term growth strategy",
         "url": "https://www.businesswire.com/news/home/20251120925716/en/Corsair-Announces-CFO-Transition-as-Part-of-Long-Term-Growth-Strategy",
         "date": "2025-11-20"},
    ],
    "D-Box Technologies Inc.": [
        {"signal_type": "Leadership Exit",
         "title": "D-BOX Technologies CEO Sébastien Mailhot steps down; Naveen Prasad named interim CEO effective June 10, 2025",
         "url": "https://www.globenewswire.com/news-release/2025/06/04/3094042/0/en/D-BOX-Technologies-Announces-CEO-Change.html",
         "date": "2025-06-04"},
        {"signal_type": "Leadership Change",
         "title": "D-BOX Technologies confirms Naveen Prasad as permanent President and CEO following leadership realignment",
         "url": "https://www.d-box.com/en/news/d-box-technologies-announces-ceo-change",
         "date": "2025-08-01"},
    ],

    # ── Batch A4 (Cabasse Group → Compal Electronics) ────────────────────
    "Cabasse Group": [
        {"signal_type": "News Mention",
         "title": "Cabasse Group files for receivership (cessation of payments) after €43M financing falls through",
         "url": "https://www.lesnumeriques.com/casque-audio/cabasse-en-redressement-judiciaire-que-va-devenir-la-marque-n233817.html",
         "date": "2026-03-09"},
        {"signal_type": "Acquisition / M&A",
         "title": "Loewe Technology acquires Cabasse Group out of receivership, saving the French speaker brand",
         "url": "https://www.son-video.com/articles/actualite/loewe-technology-rachete-cabasse",
         "date": "2026-04-20"},
    ],
    "Cellularline S.p.A.": [
        {"signal_type": "News Mention",
         "title": "Cellularline officially recognised as a Benefit Corporation, embedding sustainability into its corporate mission",
         "url": "https://www.cellularline.com/en/news",
         "date": "2025-06-01"},
    ],
    "Chassis Plans": [
        {"signal_type": "Acquisition / M&A",
         "title": "Chassis Plans acquired by Israeli defence technology company Aeronautics Ltd, expanding global reach for rugged computing",
         "url": "https://militaryembedded.com/comms/communications/chassis-plans-acquired-by-aeronautics-ltd",
         "date": "2025-03-10"},
    ],
    "Clevo Co.": [
        {"signal_type": "News Mention",
         "title": "Clevo pivots to high-end AI notebooks; 2025 revenue NT$17.4B with Jan–Feb 2026 revenue up 29% YoY",
         "url": "https://www.digitimes.com/news/a20260312PD204/clevo-high-end-notebooks-recovery-2025.html",
         "date": "2026-03-12"},
    ],
    "Compal Electronics": [
        {"signal_type": "News Mention",
         "title": "Compal Electronics announces strategic Texas manufacturing investment to expand US operations amid tariff landscape",
         "url": "https://www.digitimes.com/news/compal-us-texas-investment-2025.html",
         "date": "2025-05-01"},
        {"signal_type": "News Mention",
         "title": "Compal partners with Kalyani Powertrain/Bharat Forge for server manufacturing in India",
         "url": "https://www.business-standard.com/companies/news/bharat-forge-compal-india-server-manufacturing-2025.html",
         "date": "2025-03-01"},
    ],

    # ── Batch A3 (AVer Information → BTO Europe) ─────────────────────────
    # BigBen Interactive / Nacon: major financial distress event (Feb–Mar 2026)
    "BigBen Interactive": [
        {"signal_type": "News Mention",
         "title": "BigBen Interactive's €43M bank refinancing collapses; Nacon subsidiary files for insolvency and closes four studios",
         "url": "https://www.globenewswire.com/news-release/2026/02/25/3244264/0/en/PRESS-RELEASE-Bigben-Interactive-announces-that-it-is-today-requesting-the-initiation-of-an-amicable-conciliation-procedure-in-order-to-facilitate-discussions-with-its-financial-cr.html",
         "date": "2026-02-25"},
        {"signal_type": "News Mention",
         "title": "Nacon files for judicial reorganisation; Spiders, Kylotonn, Cyanide and Nacon Tech studios closed",
         "url": "https://wccftech.com/nacon-files-insolvency-days-before-nacon-connect/",
         "date": "2026-03-23"},
    ],

    # ── Batch A2 (Andrea Electronics → Avell) ────────────────────────────
    # Aorus (Gigabyte Group) — sub-brand of Gigabyte; signals covered under "Gigabyte"
    "Appotronics Corp Ltd": [
        {"signal_type": "News Mention",
         "title": "Appotronics and Valeo unveil laser video projection collaboration at Auto Shanghai 2025",
         "url": "https://www.prnewswire.com/news/appotronics-corporation-ltd./",
         "date": "2025-04-23"},
        {"signal_type": "News Mention",
         "title": "Appotronics signs agreement with Ceres Holographics for holographic laser display technology",
         "url": "https://www.prnewswire.com/news/appotronics-corporation-ltd./",
         "date": "2025-06-01"},
    ],
    "Archos SA": [
        {"signal_type": "Acquisition / M&A",
         "title": "Archos acquires O2i Ingénierie from Prologue Group, targeting €90M revenue by 2026",
         "url": "https://www.actusnews.com/en/archos/pr/2025/07/01/archos-confirme-l_acquisition-definitive-de-o2i-ingenierie",
         "date": "2025-07-01"},
        {"signal_type": "News Mention",
         "title": "Archos posts 53% revenue growth in 2025, targeting €90M in 2026 with Tempest sovereign production facility",
         "url": "https://www.ideal-investisseur.fr/en/actions-b/archos-accelerates-53-revenue-growth-in-2025-targeting-eur90m-in-2026/16537.html",
         "date": "2026-03-01"},
    ],
    "Artec Technologies AG": [
        {"signal_type": "News Mention",
         "title": "Artec Technologies expands to the Middle East with new Dubai sales office",
         "url": "https://www.zoominfo.com/c/artec-technologies-ag/372631035",
         "date": "2025-12-01"},
    ],
    "Audio Pixels Holdings Limited": [
        {"signal_type": "News Mention",
         "title": "Audio Pixels Holdings delisted from ASX after two-year trading suspension under Listing Rule 17.12",
         "url": "https://www.tipranks.com/news/company-announcements/audio-pixels-to-be-removed-from-asx-after-two-year-trading-suspension",
         "date": "2026-03-02"},
    ],
    "Audeara Ltd": [
        {"signal_type": "News Mention",
         "title": "Audeara wins Small Business Award at 2025 Premier of Queensland's Export Awards",
         "url": "https://us.audeara.com/pages/investors",
         "date": "2025-10-01"},
    ],
    "Avell": [
        {"signal_type": "C-Suite Join",
         "title": "Vladimir Rissardi appointed CEO of Avell, tasked with preparing company for sustainable growth",
         "url": "https://brazileconomy.com.br/tecnologia/2026/02/avell-faz-reestruturacao-bate-recorde-e-busca-vacuo-do-mercado-premium-de-computadores/",
         "date": "2024-10-01"},
        {"signal_type": "News Mention",
         "title": "Avell closes 2025 with record revenue of R$263M (+25%), corporate segment jumps to 27% of sales mix",
         "url": "https://brazileconomy.com.br/tecnologia/2026/02/avell-faz-reestruturacao-bate-recorde-e-busca-vacuo-do-mercado-premium-de-computadores/",
         "date": "2026-02-01"},
    ],

    # ── Batch A1 (Action Electronics → AMPACS Corp) ───────────────────────
    # Alps Alpine Co Ltd: 2 news signals (Mar 2025)
    "Alps Alpine Co Ltd": [
        {"signal_type": "News Mention",
         "title": "Alps Alpine invests SEK 25M in Acconeer AB, deepening strategic radar-sensor partnership",
         "url": "https://acconeer.com/mfn_news/acconeer-ab-publ-announces-that-the-directed-share-issue-to-alps-alpine-of-approximately-sek-25-million-is-completed/",
         "date": "2025-03-06"},
        {"signal_type": "News Mention",
         "title": "Alps Alpine to invest ¥10 billion in domestic factory automation to boost production efficiency",
         "url": "https://www.alpsalpine.com/e/company/",
         "date": "2025-01-01"},
    ],

    # ── Batch 1 (Acer → Elitegroup Computer Systems) ───────────────────────
    "Acer Inc.": [
        {"signal_type": "Leadership Change",
         "title": "Acer names Chris Chiang and Germano Couy as co-presidents of Pan America operations",
         "url": "https://news.acer.com/acer-announces-leadership-transition-for-pan-america-operations",
         "date": "2026-01-01"},
    ],
    "Anker Innovations Ltd": [
        {"signal_type": "IPO",
         "title": "Anker Innovations plans Hong Kong secondary listing targeting $500M raise",
         "url": "https://www.ainvest.com/news/anker-innovations-strategic-move-hong-kong-listing-implications-growth-market-expansion-2508/",
         "date": "2025-08-01"},
    ],
    "Apple Inc.": [
        {"signal_type": "Leadership Exit",
         "title": "Tim Cook steps down as Apple CEO, transitions to Executive Chairman",
         "url": "https://www.apple.com/newsroom/2026/04/tim-cook-to-become-apple-executive-chairman-john-ternus-to-become-apple-ceo/",
         "date": "2026-04-20"},
        {"signal_type": "Leadership Change",
         "title": "John Ternus named Apple CEO, succeeding Tim Cook effective September 2026",
         "url": "https://www.cnbc.com/2026/04/20/apple-names-john-ternus-ceo-replacing-tim-cook-who-becomes-chairman.html",
         "date": "2026-04-20"},
    ],
    "Arçelik Anonim Sirketi": [
        {"signal_type": "Acquisition",
         "title": "Beko Europe launches as Arçelik–Whirlpool EMEA merger completes; Arçelik holds 75% stake",
         "url": "https://retra.co.uk/news/beko-europe-launches-as-whirlpool-ar%C3%A7elik-merger-finally-goes-through",
         "date": "2025-04-01"},
    ],
    "Atomos Limited": [
        {"signal_type": "Leadership Change",
         "title": "Atomos appoints Peter Barber as CEO, replacing co-founder Jeromy Young",
         "url": "https://nofilmschool.com/atomos-new-ceo",
         "date": "2025-05-01"},
    ],
    "Aterian, Inc.": [
        {"signal_type": "Acquisition",
         "title": "Aterian agrees to sell brand portfolio (incl. Squatty Potty) to Trademark Global for $18M",
         "url": "https://www.stocktitan.net/news/ATER/aterian-inc-announces-definitive-agreement-for-the-sale-of-its-ojge8f68axxx.html",
         "date": "2026-04-27"},
        {"signal_type": "Leadership Change",
         "title": "Aterian appoints David Lazar as CEO following strategic portfolio sale",
         "url": "https://www.stocktitan.net/news/ATER/aterian-inc-announces-definitive-agreement-for-the-sale-of-its-ojge8f68axxx.html",
         "date": "2026-04-27"},
    ],
    "Bang & Olufsen A-S": [
        {"signal_type": "Leadership Exit",
         "title": "Bang & Olufsen CEO Kristian Teär steps down; CFO Nikolaj Wendelboe named interim CEO",
         "url": "https://www.globenewswire.com/news-release/2026/01/07/3214303/0/en/Kristian-Te%C3%A4r-steps-down-as-CEO-of-Bang-Olufsen-CFO-Nikolaj-Wendelboe-appointed-interim-CEO-Preliminary-Q2-2025-26-key-financials-announced-and-FY-2025-26-outlook-narrowed.html",
         "date": "2026-01-07"},
        {"signal_type": "Leadership Exit",
         "title": "Bang & Olufsen EVP Chief Corporate Commercial Officer Line Køhler Ljungdahl steps down",
         "url": "https://www.globenewswire.com/news-release/2026/05/08/3290905/0/en/Line-K%C3%B8hler-Ljungdahl-steps-down-as-Executive-Vice-President-Chief-Corporate-Commercial-Officer-of-Bang-Olufsen.html",
         "date": "2026-05-08"},
    ],
    "Basler AG": [
        {"signal_type": "Leadership Exit",
         "title": "Basler AG CEO Dr. Dietmar Ley retires after 25+ years; Hardy Mehl named successor",
         "url": "https://www.vision-systems.com/cameras-accessories/news/55327181/asler-ag-announces-leadership-transition-with-retirement-of-longtime-ceo-dietmar-ley",
         "date": "2026-01-01"},
        {"signal_type": "Leadership Change",
         "title": "Hardy Mehl appointed CEO of Basler AG effective January 1, 2026",
         "url": "https://www.marketscreener.com/news/basler-ag-approves-change-in-ceo-effective-from-january-1-2026-ce7d5cdfdb8ef727",
         "date": "2026-01-01"},
        {"signal_type": "Acquisition",
         "title": "Basler AG acquires 76% stake in Indian machine vision company Alpha TechSys Automation",
         "url": "https://www.baslerweb.com/en-us/news/management-board-changes/",
         "date": "2025-10-01"},
    ],
    "Brother Industries": [
        {"signal_type": "Leadership Change",
         "title": "Brother International Corporation appoints Kenji Kamei as new President effective April 1, 2026",
         "url": "https://www.prnewswire.com/news-releases/brother-international-corporation-announces-top-management-changes-302730142.html",
         "date": "2026-04-01"},
        {"signal_type": "Acquisition",
         "title": "Brother Industries completes acquisition of Mutoh Holdings Co., Ltd.",
         "url": "https://industryanalysts.com/brother-international-corporation-announces-top-management-changes/",
         "date": "2026-03-23"},
        {"signal_type": "Acquisition",
         "title": "U-NEXT Holdings acquires 70% stake in Brother Industries subsidiary XING Inc. for ¥17.5B",
         "url": "https://global.brother/en/news",
         "date": "2025-12-24"},
    ],
    "Casio Computer Co.,Ltd.": [
        {"signal_type": "Leadership Change",
         "title": "Casio Computer names Shin Takano as new President and CEO, effective June 27, 2025",
         "url": "https://world.casio.com/news/2025/0610-personnel/",
         "date": "2025-06-27"},
        {"signal_type": "Leadership Change",
         "title": "Casio America names Yusuke Suzuki as new President and CEO effective August 13, 2025",
         "url": "https://www.prnewswire.com/news-releases/casio-america-inc-names-yusuke-suzuki-as-the-new-president-and-ceo-302526927.html",
         "date": "2025-08-13"},
    ],
    "Corsair": [
        {"signal_type": "Leadership Exit",
         "title": "Corsair founder and CEO Andy Paul announces retirement; Thi La named new CEO",
         "url": "https://ir.corsair.com/news-releases/news-release-details/corsair-announces-planned-retirement-founder-and-ceo-andy-paul/",
         "date": "2025-07-01"},
        {"signal_type": "Leadership Change",
         "title": "Corsair names Gordon Mattingly as new CFO effective December 2, 2025",
         "url": "https://ir.corsair.com/news-releases/news-release-details/corsair-announces-cfo-transition-part-long-term-growth-strategy",
         "date": "2025-12-02"},
    ],
    "D-Box Technologies Inc.": [
        {"signal_type": "Leadership Change",
         "title": "D-BOX Technologies appoints Naveen Prasad as President and CEO effective August 13, 2025",
         "url": "https://www.d-box.com/en/news/d-box-technologies-announces-ceo-change",
         "date": "2025-08-13"},
    ],
    "Dell Technologies": [
        {"signal_type": "Leadership Exit",
         "title": "Dell Technologies CFO Yvonne McGill steps down after 30-year career; David Kennedy named interim CFO",
         "url": "https://investors.delltechnologies.com/news-releases/news-release-details/dell-technologies-announces-cfo-transition",
         "date": "2025-09-09"},
    ],
    "Dometic Group AB": [
        {"signal_type": "Leadership Exit",
         "title": "Dometic Group CFO Stefan Fristedt stepping down; search for replacement underway",
         "url": "https://www.dometicgroup.com/en-us/investors/press-releases/dometic-announces-global-restructuring-program",
         "date": "2026-03-01"},
    ],

    # ── Batch 2 (Eluktronics → Founder Technology) ─────────────────────────
    "Emdoor": [
        {"signal_type": "News Mention",
         "title": "Emdoor Information's Shareholders Plan To Unload Stakes",
         "url": "https://www.tradingview.com/news/reuters.com,2026:newsml_L4N41O0XW:0-emdoor-information-s-shareholders-plan-to-unload-stakes/",
         "date": "2026-03-02"},
    ],
    "Emerson Radio Corp.": [
        {"signal_type": "News Mention",
         "title": "Emerson Radio shareholders approve directors and auditor at annual meeting",
         "url": "https://www.stocktitan.net/sec-filings/MSN/emerson-radio-annual-meeting.html",
         "date": "2026-03-24"},
    ],
    "Epson": [
        {"signal_type": "News Mention",
         "title": "Epson Renews Its Partnership with FASHION FRONTIER PROGRAM 2026",
         "url": "https://whattheythink.com/news/130391-epson-renews-partnership-fashion-frontier-program-2026/",
         "date": "2026-02-01"},
    ],
    "Estone Technology": [
        {"signal_type": "News Mention",
         "title": "Estone Technology at XPONENTIAL 2026: Rugged Computing Platforms",
         "url": "https://www.estonetech.com/news/xponential-2026.html",
         "date": "2026-05-11"},
    ],
    "Eurocom": [
        {"signal_type": "News Mention",
         "title": "Eurocom launches Raptor X18 with RTX 5090 GPU, 256GB DDR5 memory",
         "url": "https://videocardz.com/newz/eurocom-launches-raptor-x18-with-rtx-5090",
         "date": "2026-01-15"},
    ],
    "Falcon Northwest": [
        {"signal_type": "News Mention",
         "title": "Falcon Northwest FragBox review: A compact gaming rig",
         "url": "https://www.engadget.com/computing/falcon-northwest-fragbox-review.html",
         "date": "2026-02-01"},
    ],
    "Foster Electric Company Ltd": [
        {"signal_type": "Leadership Change",
         "title": "Foster Electric Company announces transition to Audit & Supervisory Committee structure",
         "url": "https://www.foster-electric.com/news/index.html",
         "date": "2026-02-01"},
    ],
    "Foxconn": [
        {"signal_type": "News Mention",
         "title": "ElectroMobility Poland to build EV plant with Foxconn",
         "url": "https://www.electrive.com/2026/05/08/electromobility-poland-foxconn/",
         "date": "2026-05-08"},
    ],
    "Framework Computer": [
        {"signal_type": "News Mention",
         "title": "Framework Laptop 13 Pro Launch — far more pre-orders than forecast",
         "url": "https://frame.work/blog/framework-laptop-13-pro-launch",
         "date": "2026-04-21"},
    ],
    "Fujitsu": [
        {"signal_type": "Leadership Change",
         "title": "Fujitsu Limited announces executive officer appointments and new management structure",
         "url": "https://global.fujitsu/en-global/pr/news/2026/01/29-02-en",
         "date": "2026-01-29"},
    ],
    "Fujitsu Technology Solutions": [
        {"signal_type": "News Mention",
         "title": "Fujitsu automates entire software development lifecycle with AI-Driven Platform",
         "url": "https://global.fujitsu/en-global/pr/news/2026/02/17-01",
         "date": "2026-02-17"},
    ],
    "Gateway": [
        {"signal_type": "News Mention",
         "title": "Gateway Computer co-founder Ted Waitt testifies before U.S. House Oversight Committee",
         "url": "https://www.ktiv.com/2026/04/30/gateway-computer-co-founder-ted-waitt-testifies/",
         "date": "2026-04-30"},
    ],
    "Geo": [
        {"signal_type": "News Mention",
         "title": "Geo Computers parent Tactus Group entered administration; brand disrupted",
         "url": "https://gruntled.net/reviews/geo-computers-review/",
         "date": "2026-05-12"},
    ],
    "Getac": [
        {"signal_type": "News Mention",
         "title": "Getac to Showcase Rugged Devices and Integrated Tactical Solutions at SOF Week 2026",
         "url": "https://finance.yahoo.com/sectors/technology/articles/getac-sof-week-2026.html",
         "date": "2026-05-01"},
    ],
    "Gigabyte": [
        {"signal_type": "News Mention",
         "title": "GIGABYTE Unveils Future Landing at COMPUTEX 2026",
         "url": "https://www.gigabyte.com/Press/News/2386",
         "date": "2026-05-05"},
        {"signal_type": "News Mention",
         "title": "CES 2026: GIGABYTE is AI Forward",
         "url": "https://www.gigabyte.com/Press/News/2340",
         "date": "2026-01-07"},
    ],
    "Gome Telecom Equipment Co. Ltd": [
        {"signal_type": "News Mention",
         "title": "Gome Telecom Equipment faces potential delisting from Shanghai Stock Exchange",
         "url": "https://www.marketscreener.com/quote/stock/GOME-TELECOM-EQUIPMENT-CO-9949880/",
         "date": "2026-01-13"},
    ],
    "GoPro, Inc.": [
        {"signal_type": "Leadership Change",
         "title": "GoPro appoints Brian McGee as President & COO; Brian Tratt named new CFO",
         "url": "https://www.sec.gov/Archives/edgar/data/0001500435/000162828026009818/gpro-20260212.htm",
         "date": "2026-02-19"},
        {"signal_type": "Acquisition",
         "title": "GoPro retains Houlihan Lokey to evaluate potential sale and strategic alternatives",
         "url": "https://www.sec.gov/Archives/edgar/data/0001500435/000150043526000015/gpro-20260511.htm",
         "date": "2026-05-19"},
    ],
    "Groupe Bull": [
        {"signal_type": "Acquisition",
         "title": "France buys supercomputer-maker Bull from Atos for €404M",
         "url": "https://www.theregister.com/2026/04/01/france_bull_purchase/",
         "date": "2026-04-01"},
    ],
    "Grundig": [
        {"signal_type": "Acquisition",
         "title": "Changhong and Grundig Announce Strategic Partnership — Changhong acquires Grundig brand license",
         "url": "https://www.media-outreach.com/news/germany/2026/03/30/456808/changhong-grundig-partnership/",
         "date": "2026-03-30"},
    ],
    "Hamilton Beach Brands Holding Company": [
        {"signal_type": "News Mention",
         "title": "Hamilton Beach Brands Holding Company Announces First Quarter 2026 Results",
         "url": "https://www.prnewswire.com/news-releases/hamilton-beach-brands-q1-2026-302764521.html",
         "date": "2026-05-06"},
    ],
    "Hapbee Technologies, Inc.": [
        {"signal_type": "Leadership Change",
         "title": "Hapbee appoints Bally Singh to Board of Directors",
         "url": "https://investors.hapbee.com/press-releases",
         "date": "2026-01-26"},
    ],
    "Hasee": [
        {"signal_type": "News Mention",
         "title": "Hasee X5 with Intel Core i9 reviewed — strong performance for $510",
         "url": "https://www.techradar.com/pro/hasee-x5-review",
         "date": "2026-05-20"},
    ],
    "HCLTech": [
        {"signal_type": "Acquisition",
         "title": "HCLTech to Acquire Telco Solutions Business from Hewlett Packard Enterprise",
         "url": "https://www.prnewswire.com/news-releases/hcltech-acquire-hpe-telco-solutions-302645945.html",
         "date": "2026-02-01"},
    ],
    "Hibino Corp": [
        {"signal_type": "News Mention",
         "title": "Hibino announces share buyback of up to 50,000 shares",
         "url": "https://www.hibino.co.jp/english/news/",
         "date": "2026-03-01"},
    ],
    "Hisense": [
        {"signal_type": "Leadership Change",
         "title": "Hisense names James Fishler as Chief Commercial Officer effective January 1 2026",
         "url": "https://www.cepro.com/news/hisense-names-james-fishler-chief-commercial-officer/624088/",
         "date": "2026-01-01"},
    ],
    "Home Control International Ltd": [
        {"signal_type": "Acquisition",
         "title": "Home Control International Acquired by Meta-Wisdom Tech Limited",
         "url": "https://www.ainvest.com/news/home-control-strategic-transformation",
         "date": "2025-09-01"},
    ],
    "Honor": [
        {"signal_type": "Leadership Exit",
         "title": "Zhao Steps Down as Honor CEO, Jian Li Takes the Helm in Buildup to IPO",
         "url": "https://www.fundz.net/executive-moves/zhao-steps-down-as-honor-ceo",
         "date": "2026-01-15"},
        {"signal_type": "IPO",
         "title": "Honor IPO advances as new chief talks up AI credentials",
         "url": "https://thebambooworks.com/honor-ipo-advances",
         "date": "2026-02-01"},
    ],
    "HP Inc": [
        {"signal_type": "Leadership Change",
         "title": "HP Inc. Announces Leadership Transition — Bruce Broussard Named Interim CEO",
         "url": "https://investor.hp.com/news-events/news/news-details/2026/HP-Leadership-Transition/default.aspx",
         "date": "2026-02-03"},
        {"signal_type": "Leadership Change",
         "title": "HP taps former JP Morgan executive as CFO",
         "url": "https://www.cfodive.com/news/hp-new-cfo-2026",
         "date": "2026-02-01"},
    ],
    "HPE": [
        {"signal_type": "Acquisition",
         "title": "HPE closes $14bn acquisition of Juniper Networks",
         "url": "https://www.datacenterdynamics.com/en/news/hpe-closes-14bn-acquisition-of-juniper-networks/",
         "date": "2025-07-02"},
    ],
    "Huawei": [
        {"signal_type": "Leadership Change",
         "title": "Huawei reshuffles top leadership — David Wang named rotating chair",
         "url": "https://www.lightreading.com/business-transformation/huawei-reshuffles-top-leadership",
         "date": "2026-01-01"},
    ],
    "IGEL Technology": [
        {"signal_type": "Leadership Change",
         "title": "IGEL appoints Ash Chowdappa as Chief Product & Development Officer",
         "url": "https://www.globenewswire.com/news-release/2026/02/27/3246816/IGEL-leadership-expansion.html",
         "date": "2026-02-27"},
    ],
    "Image Systems AB": [
        {"signal_type": "Leadership Change",
         "title": "Image Systems AB Appoints Erik Swerup as New CEO of RemaSawco",
         "url": "https://news.cision.com/image-systems-ab",
         "date": "2026-02-01"},
    ],
    "iRobot Corporation": [
        {"signal_type": "Acquisition",
         "title": "iRobot files for Chapter 11 Bankruptcy, acquired by Picea Robotics",
         "url": "https://elevenflo.com/blog/irobot-chapter-11",
         "date": "2025-12-14"},
        {"signal_type": "Acquisition",
         "title": "iRobot emerges from Chapter 11 as restructured Picea U.S. subsidiary",
         "url": "https://www.therobotreport.com/irobot-emerges-from-chapter-11",
         "date": "2026-01-23"},
    ],
    "Japan Display, Inc.": [
        {"signal_type": "Leadership Change",
         "title": "Japan Display Revamps Board as It Prepares U.S. Advanced Display Expansion",
         "url": "https://www.tipranks.com/news/company-announcements/japan-display-revamps-board",
         "date": "2026-05-01"},
    ],
    "JVC KENWOOD Corp": [
        {"signal_type": "Leadership Change",
         "title": "JVC Kenwood Takes Bold Steps to Reconstruct Its Core DNA — Shaking Up Structure and Management",
         "url": "https://www.strata-gee.com/jvc-kenwood-takes-bold-steps",
         "date": "2026-05-01"},
    ],
    "Koss Corporation": [
        {"signal_type": "News Mention",
         "title": "Koss Corporation Drives Expansion Initiative — Launches Acquisition Strategy",
         "url": "https://www.globenewswire.com/news-release/2026/03/16/3256367/Koss-expansion.html",
         "date": "2026-03-16"},
    ],
    "Kyocera Corp": [
        {"signal_type": "Leadership Change",
         "title": "Kyocera Overhauls Top Management, Names New President Shiro Sakushima Effective April 1 2026",
         "url": "https://www.tipranks.com/news/company-announcements/kyocera-new-president",
         "date": "2026-04-01"},
    ],
    "Lava International": [
        {"signal_type": "Leadership Change",
         "title": "Lava Mobiles Reorganises Its Board",
         "url": "https://www.electronicsforyou.biz/industry-buzz/lava-mobiles-reorganizes-board/",
         "date": "2026-01-01"},
    ],
    "LG": [
        {"signal_type": "Leadership Change",
         "title": "LG Announces Organizational Changes for 2026",
         "url": "https://www.lg.com/global/newsroom/lg-organizational-changes-2026/",
         "date": "2025-11-01"},
    ],
    "LG Display Co. Ltd": [
        {"signal_type": "Leadership Change",
         "title": "LG Display appoints LG Innotek chief as new CEO",
         "url": "https://www.koreaherald.com/article/3266410",
         "date": "2026-01-01"},
    ],
    "LG Electronics, Inc.": [
        {"signal_type": "Leadership Change",
         "title": "LG Electronics CEO Lyu Jae-cheol sets strategic direction for profit-driven growth",
         "url": "https://www.lg.com/global/newsroom/lg-ceo-strategic-direction/",
         "date": "2025-12-01"},
    ],
    "Lite-On": [
        {"signal_type": "News Mention",
         "title": "Lite-On Technology Plans US$919M Capital Investment in U.S. for AI Energy Infrastructure",
         "url": "https://www.liteon.com/en/news/press-center/lite-on-us-investment",
         "date": "2026-03-01"},
    ],
    "Maingear": [
        {"signal_type": "News Mention",
         "title": "MAINGEAR Drops Retro98: Looks like 1998, Spec'd for 2026",
         "url": "https://www.prnewswire.com/news-releases/maingear-retro98-302673542.html",
         "date": "2026-01-29"},
    ],
    "Maxell Holdings Ltd": [
        {"signal_type": "Acquisition",
         "title": "Maxell completes acquisition of Murata Manufacturing's primary battery business",
         "url": "https://filingreader.com/news-wire/maxell-murata-battery-acquisition",
         "date": "2026-03-02"},
        {"signal_type": "Leadership Change",
         "title": "Maxell Ltd. Announces Executive Changes, Effective April 1, 2026",
         "url": "https://www.marketscreener.com/news/maxell-executive-changes-2026",
         "date": "2026-04-01"},
    ],
    "Maytronics Ltd": [
        {"signal_type": "Leadership Change",
         "title": "Maytronics appoints Rafael Benami as new CEO",
         "url": "https://www.eurospapoolnews.com/actualites_piscines_spas-en/88102-maytronics-new-ceo.htm",
         "date": "2026-04-14"},
    ],
    "MiTAC": [
        {"signal_type": "Funding",
         "title": "MiTAC Computing Technology receives funding from MiTAC Holdings",
         "url": "https://www.marketscreener.com/news/mitac-computing-funding",
         "date": "2026-01-07"},
    ],
    "Moto": [
        {"signal_type": "Acquisition",
         "title": "Motorola Solutions closes acquisitions of Exacom and Hyper for combined $90M",
         "url": "https://www.motorolasolutions.com/newsroom/press-releases/motorola-solutions-q1-2026.html",
         "date": "2026-05-07"},
        {"signal_type": "Acquisition",
         "title": "Motorola Solutions Canada to Acquire Bell Canada's LMR Networks Services for ~$500M",
         "url": "https://www.motorolasolutions.com/newsroom/press-releases/acquiring-bell-canada-lmr.html",
         "date": "2026-03-30"},
    ],
    "Multilaser": [
        {"signal_type": "Leadership Exit",
         "title": "Brazil's Grupo Multi CEO Alexandre Ostrowiecki to Leave Role",
         "url": "https://www.marketscreener.com/quote/stock/MULTILASER-INDUSTRIAL/news/multilaser-ceo-departure",
         "date": "2025-03-10"},
    ],
    "NEC": [
        {"signal_type": "Acquisition",
         "title": "NEC Completes Acquisition of CSG Systems; Netcracker to Lead Combined Business",
         "url": "https://www.nec.com/en/press/202605/global_20260515_01.html",
         "date": "2026-05-15"},
    ],
    "Nokia": [
        {"signal_type": "Leadership Change",
         "title": "Nokia names Justin Hotard as new President and CEO effective April 1, 2026",
         "url": "https://www.sec.gov/Archives/edgar/data/0000924613/nokia-new-ceo-2026.htm",
         "date": "2026-02-13"},
        {"signal_type": "Acquisition",
         "title": "Nokia completes $2.3bn Infinera acquisition",
         "url": "https://www.datacenterdynamics.com/en/news/nokia-completes-23bn-infinera-acquisition/",
         "date": "2026-02-13"},
    ],
    "Northbaze Group AB": [
        {"signal_type": "News Mention",
         "title": "Northbaze Group AB applies for delisting from Nasdaq First North Growth Market",
         "url": "https://www.marketscreener.com/quote/stock/NORTHBAZE-GROUP-AB/news/northbaze-delisting",
         "date": "2025-10-15"},
    ],
    "Olidata": [
        {"signal_type": "News Mention",
         "title": "Olidata Posts 2025 Profit, Strengthens Balance Sheet and Revises 2026-2028 Plan",
         "url": "https://www.tipranks.com/news/company-announcements/olidata-2025-profit",
         "date": "2026-02-01"},
    ],
    "Panasonic Corp": [
        {"signal_type": "Leadership Change",
         "title": "Panasonic Connect Announces Transition of President and CEO in April 2026",
         "url": "https://news.panasonic.com/global/press/en250730-9",
         "date": "2026-04-01"},
        {"signal_type": "IPO",
         "title": "Panasonic Considers US IPO for Blue Yonder Software Arm",
         "url": "https://finance.yahoo.com/news/panasonic-ipo-blue-yonder",
         "date": "2026-02-01"},
    ],
}


def seed(dry_run: bool = False) -> None:
    store = SnapshotStore(DB_PATH)

    inserted = 0
    skipped  = 0

    for company_name, signals in KNOWN_SIGNALS.items():
        apollo_id = _make_id(company_name)

        # Ensure company exists in the companies table
        store.upsert_company({
            "apollo_id": apollo_id,
            "name":      company_name,
            "domain":    "",    # will be filled by build_csg_dashboard.py
            "industry":  "Technology",
            "city":      "",
            "state":     "",
        })

        for sig in signals:
            signal_type = _resolve_signal_type(sig["signal_type"], sig["title"])
            severity    = _severity(signal_type)
            title       = sig["title"]
            url         = sig.get("url", "")
            date        = sig.get("date", "")

            # Dedup: skip if exact same (apollo_id, signal_type, title) already stored
            if store.was_alert_sent_recently(
                apollo_id, signal_type, dedup_days=9999, signal_detail=title
            ):
                skipped += 1
                continue

            if not dry_run:
                store.record_alert(
                    apollo_id=apollo_id,
                    signal_type=signal_type,
                    signal_detail=title,
                    severity=severity,
                    dry_run=False,
                    signal_date=date,
                    source_url=url,
                )
            inserted += 1
            status = "[DRY RUN] " if dry_run else ""
            print(f"  {status}[{severity}] {company_name} | {signal_type}")
            print(f"           {title[:90]}")

    total = inserted + skipped
    print(f"\nDone. {inserted} inserted, {skipped} skipped (already in DB). Total signals: {total}")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    print("CSG Signal Seeder")
    print(f"DB: {DB_PATH}")
    print(f"Mode: {'DRY RUN' if dry else 'LIVE WRITE'}")
    print()
    seed(dry_run=dry)
