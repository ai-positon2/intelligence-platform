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
