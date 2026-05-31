"""
enrich_csg_from_apollo.py — apply Apollo enrichment data to tracker_csg_v2.db

Updates each company's latest snapshot row with:
  - employees
  - annual_revenue
  - latest_funding_type

Also updates the parent `companies` table with:
  - logo_url   (so the dashboard avatar grid shows the real brand logo)

Re-runnable. Reads enrichments from apollo_enrichments.json if present in the
same folder (override), otherwise uses the built-in DEFAULT_ENRICHMENTS dict.

Usage (PowerShell, local):
  cd C:\\Users\\krishna.l\\company-signal-tracker
  python enrich_csg_from_apollo.py
  python build_csg_dashboard.py
  git add -A && git commit -m "Apollo enrichment: top 10 by signals (with logos)" && git push
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tracker_csg_v2.db"
OVERRIDE_JSON = Path(__file__).parent / "apollo_enrichments.json"

# Initial batch: top 10 CSG companies by signal count.
# All 10 are publicly traded (Apollo confirmed publicly_traded_symbol for 7;
# Brother / Sharp / BigBen verified manually via stock listings TSE:6448,
# TSE:6753, Euronext:NACON respectively).
#
# Apollo returned Brother under primary_domain "brother-usa.com" and BigBen
# under "bigben.eu" — that's why earlier extraction said they were missing.
# We now have employees + revenue for all 10.
DEFAULT_ENRICHMENTS: dict[str, dict] = {
    # ── Batch 1: top 10 CSG companies by signal count (Apollo 2026-05-28) ──
    # domain -> {employees, annual_revenue, latest_funding_type, logo_url}
    "baslerweb.com":     {"employees": 890,    "annual_revenue": 263_183_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a965620ccc4f0001cef321/picture"},
    "brother.com":       {"employees": 2200,   "annual_revenue": 3_000_000_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b39c8dc7f5cc0001471e76/picture"},
    "samsung.com":       {"employees": 127000, "annual_revenue": 230_084_404_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6a06550fae34cf0001bd103f/picture"},
    "sharp.com":         {"employees": 19000,  "annual_revenue": 2_400_000_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69aeb37f150740000187a929/picture"},
    "alpsalpine.com":    {"employees": 29000,  "annual_revenue": 6_644_560_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a3c8e801f31000011fb7ec/picture"},
    "apple.com":         {"employees": 164000, "annual_revenue": 416_161_000_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69afbb99ba3131000126d493/picture"},
    "bigben.fr":         {"employees": 490,    "annual_revenue": 312_000_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ad89d8e0c9cd0001e78e21/picture"},
    "casio.com":         {"employees": 590,    "annual_revenue": 1_776_947_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a529a146d4870001a75528/picture"},
    "compal.com":        {"employees": 44000,  "annual_revenue": 27_722_035_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a41c681c0fd700017ae0d4/picture"},
    "corsair.com":       {"employees": 2600,   "annual_revenue": 1_472_480_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ae8cd16138a6000139a662/picture"},

    # ── Batch 2: rows 11-30 (Apollo 2026-05-29) ──
    # Kontron AG + Acer verified public manually (Apollo flag missing).
    # Honor / Framework / Emdoor / Geo are private — latest_funding_type left None.
    "gigabyte.com":      {"employees": 6500,   "annual_revenue": 8_338_701_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a6ba32dd3ccc0001bd2c88/picture"},
    "hp.com":            {"employees": 58000,  "annual_revenue": 55_295_000_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69c470f4ca51920001367f8c/picture"},
    "kontron.com":       {"employees": 6900,   "annual_revenue": None,            "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ad7932c1310b00011e3c51/picture"},
    "motorola.com":      {"employees": 25000,  "annual_revenue": 2_100_000_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69c42ea82f7d460001e14c72/picture"},
    "nokia.com":         {"employees": 79000,  "annual_revenue": 23_069_553_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b29de1574c8f000100a62d/picture"},
    "sony.com":          {"employees": 113000, "annual_revenue": 89_155_541_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69aab40769fedc00010a5014/picture"},
    "whirlpool.com.br":  {"employees": 20000,  "annual_revenue": 2_289_795_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/60f45c74e2469d000148aeb7/picture"},
    "acer.com":          {"employees": 9100,   "annual_revenue": 8_723_000_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69aaa139c1853b0001dd959b/picture"},
    "cellularline.com":  {"employees": None,   "annual_revenue": 125_215_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6203b6785e8a340001d22bae/picture"},
    "dell.com":          {"employees": 108000, "annual_revenue": 113_538_000_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/681f833e71dab200011b0477/picture"},
    "emdoor.com":        {"employees": 88,     "annual_revenue": None,            "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/68e653d0588b090001309984/picture"},
    "epson.com":         {"employees": 930,    "annual_revenue": 9_129_812_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b4d8438fe2750001cf64a7/picture"},
    "foxconn.com":       {"employees": 633000, "annual_revenue": 258_491_666_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b281f7848480000185734b/picture"},
    "frame.work":        {"employees": 63,     "annual_revenue": 33_300_000,      "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69acea3436f7d000015bb308/picture"},
    "fujitsu.com":       {"employees": 124000, "annual_revenue": 21_951_259_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6a03ec75aab5dc0001c558e0/picture"},
    "geo.co.uk":         {"employees": 7,      "annual_revenue": None,            "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/66eba5ca503cb5000103fd35/picture"},
    "hamiltonbeach.com": {"employees": 660,    "annual_revenue": 612_500_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b3bc62d1be6b0001fa3864/picture"},
    "hcltech.com":       {"employees": 226000, "annual_revenue": 14_349_613_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/66d5cf75b1074b00019df202/picture"},
    "honor.com":         {"employees": 5600,   "annual_revenue": 12_714_000_000,  "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a904a7a3533e0001fd216c/picture"},
    "hpe.com":           {"employees": 61000,  "annual_revenue": 34_296_000_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69abfdbffc976200014662fb/picture"},

    # ── Batch 3: rows 31-40 (Apollo 2026-05-29) ──
    # MiTAC verified public manually (TWSE:3706 — Apollo's flag missing).
    # Huawei / IGEL / Lava / MAINGEAR / NZXT are private.
    "huawei.com":        {"employees": 208000, "annual_revenue": 118_103_000_000, "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6963ef6bc3dc3000014b36e2/picture"},
    "igel.com":          {"employees": 380,    "annual_revenue": 150_000_000,     "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b4ed98d9d24f0001e4b60c/picture"},
    "koss.com":          {"employees": 28,     "annual_revenue": 12_624_000,      "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b261671adc540001444be9/picture"},
    "lavamobiles.com":   {"employees": 5000,   "annual_revenue": 279_470_000,     "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a4fb47647a8e00017b89d2/picture"},
    "lge.com":           {"employees": 35000,  "annual_revenue": 61_520_883_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a8122a622d2b00012d0168/picture"},
    "logitech.com":      {"employees": 7300,   "annual_revenue": 4_554_900_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ac32d445172b00019ef26f/picture"},
    "maingear.com":      {"employees": 30,     "annual_revenue": 7_000_000,       "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/67153758f2ead900017557db/picture"},
    "mitac.com":         {"employees": 7200,   "annual_revenue": None,            "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a3b78a42b29e0001d88831/picture"},
    "nec.com":           {"employees": 105000, "annual_revenue": 22_451_085_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a48164e5f58c00018640d3/picture"},
    "nzxt.com":          {"employees": 280,    "annual_revenue": 191_000_000,     "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69aced8ba259920001efdc44/picture"},

    # ── Batch 4: rows 41-46 (Apollo 2026-05-29) ──
    # Realme is private (no publicly_traded_symbol from Apollo).
    # Turtle Beach (HEAR) verified public — Apollo market_cap present but ticker missing (stale).
    "quantatw.com":      {"employees": 57000, "annual_revenue": 67_601_128_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/66f9ce4b2fbf000001524166/picture"},
    "razer.com":         {"employees": 1600,  "annual_revenue": 1_619_590_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69abfb6713601100018de8c3/picture"},
    "realme.com":        {"employees": 4100,  "annual_revenue": 16_153_000,      "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69b2751b0959e80001c1c1d0/picture"},
    "turtlebeach.com":   {"employees": 300,   "annual_revenue": 319_914_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6a057df43c3d550001701576/picture"},
    "uei.com":           {"employees": 3900,  "annual_revenue": 368_288_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a968216ee59100017c8f05/picture"},
    "vuzix.com":         {"employees": 76,    "annual_revenue": 6_280_000,       "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6959e692063e300001b8e972/picture"},

    # ── Batch 5: rows 47-51 (Apollo 2026-05-29) ──
    # Zoom Corp: Apollo returned US subsidiary (zoomcorp.com, 17 emp) — parent is Zoom Corp Japan (TSE:6770). Revenue/emp are sub-only.
    # ZOTAC: Apollo returned US subsidiary — parent PC Partner Ltd is HKSE-listed; sub treated as Private.
    "wortmann.de":       {"employees": 270,   "annual_revenue": 1_042_000_000,   "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6959b865eb51f100018977cb/picture"},
    "mi.com":            {"employees": 57000, "annual_revenue": 54_992_238_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ace7b94ef6d600015e585b/picture"},
    "zoom.co.jp":        {"employees": 17,    "annual_revenue": 14_200_000,      "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/670b1f99cc2d4b000118354f/picture"},
    "zotac.com":         {"employees": 42,    "annual_revenue": 210_000_000,     "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6910d6eb110ba2000159c84d/picture"},
    "zspace.com":        {"employees": 78,    "annual_revenue": 27_858_000,      "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/695b91989831320001ad4fa0/picture"},

    # ── Batch 6: rows 52-56 (Apollo 2026-05-29) ──
    # AVer: Apollo returned US sub (averusa.com); parent ticker TWSE:3669 retained.
    # Casper: Apollo returned $370K revenue — suspiciously low for a 400-person Turkish OEM; logo/emp used, revenue flagged.
    "asrock.com":        {"employees": 600,   "annual_revenue": 1_522_795_000,   "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6877592d5b97cd0001ea335d/picture"},
    "aver.com":          {"employees": 140,   "annual_revenue": 83_653_000,      "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69083dd46e6d940001d56c20/picture"},
    "casper.com.tr":     {"employees": 400,   "annual_revenue": 370_000,         "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6844452949b7070001e554a7/picture"},
    "cyberpowerpc.com":  {"employees": 92,    "annual_revenue": 100_000_000,     "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/695b3f10c8a4d50001cfbd8a/picture"},
    "ecs.com.tw":        {"employees": 81,    "annual_revenue": 752_127_000,     "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69afa5c209bcea0001b4f080/picture"},

    # ── Batch 7: rows 57-62 (Apollo 2026-05-29) ──
    # Gateway: Apollo returned wrong entity (Pakistani fashion brand) — skipped entirely.
    # Getac: revenue null from Apollo; MiTAC subsidiary, not independently listed.
    # Hasee: Apollo emp (40) contradicts own description (3,000+) — logo only.
    # Hyundai Technology: revenue null; private US consumer electronics brand.
    # iRobot: Amazon acquisition blocked by EU 2024; restructured; no current ticker in Apollo.
    "getac.com":              {"employees": 710,   "annual_revenue": None,           "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a42e4f2613c90001227559/picture"},
    "hasee.com":              {"employees": None,  "annual_revenue": None,           "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/67134566c8afdb0001da30cf/picture"},
    "hyundaitechnology.com":  {"employees": 34,    "annual_revenue": None,           "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69d69101e3f31d0001526053/picture"},
    "inventec.com":           {"employees": 29000, "annual_revenue": 22_001_920_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6a01184f26452f00017dd126/picture"},
    "irobot.com":             {"employees": 550,   "annual_revenue": 546_998_000,    "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a4edf027d3a30001354b5b/picture"},

    # ── Batch 8: rows 63-65 (Apollo 2026-05-29) ──
    # LG (lg.com): same Apollo entity as lge.com (already in Batch 3) — identical data applied to the second CSG entry.
    "j-display.com":     {"employees": 4600,  "annual_revenue": 1_261_357_000,  "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/695c944753c3e90001ae4431/picture"},
    "kyocera.com":       {"employees": 80000, "annual_revenue": 13_514_809_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69ad4de05ce3bd0001b47ab7/picture"},
    "lg.com":            {"employees": 35000, "annual_revenue": 61_520_883_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69a8122a622d2b00012d0168/picture"},

    # ── Batch 9: rows 66-71 (Apollo 2026-05-29) ──
    # Machenike: emp=6 from Apollo is wrong (gaming laptop brand); logo only.
    # Olidata: Apollo returned empty — skipped entirely.
    # OverPowered (walmart.com): Walmart-exclusive gaming brand, no standalone entity; parent Walmart data used.
    # Panasonic Toughbook (panasonic.com/toughbook): product line; Panasonic parent data used.
    "machenike.com":          {"employees": None,    "annual_revenue": None,           "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/6709ed0bffc4bd00016d4c73/picture"},
    "microcenter.com":        {"employees": 2600,    "annual_revenue": 2_400_000_000,  "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/69aeb54955cc3600010135db/picture"},
    "multilaser.com.br":      {"employees": 5000,    "annual_revenue": 549_000_000,    "latest_funding_type": None,     "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/68739c2578281600010483a8/picture"},
    "walmart.com":            {"employees": 2100000, "annual_revenue": 648_100_000_000,"latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/683a93ae8ea32a00017f4e6c/picture"},
    "panasonic.com/toughbook": {"employees": 208000, "annual_revenue": 65_700_000_000, "latest_funding_type": "Public", "logo_url": "https://zenprospect-production.s3.amazonaws.com/uploads/pictures/66e733ffcf04820001040ede/picture"},
}


def load_enrichments() -> dict[str, dict]:
    if OVERRIDE_JSON.exists():
        print(f"Reading enrichments from {OVERRIDE_JSON.name}")
        return json.loads(OVERRIDE_JSON.read_text(encoding="utf-8"))
    print("Using built-in DEFAULT_ENRICHMENTS")
    return DEFAULT_ENRICHMENTS


def column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def main() -> None:
    enrich = load_enrichments()
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    # CIFS-safe: never use WAL on the CIFS-mounted DB
    conn.execute("PRAGMA journal_mode=DELETE")
    cur = conn.cursor()

    # Auto-add logo_url column if missing (idempotent)
    if not column_exists(cur, "companies", "logo_url"):
        cur.execute("ALTER TABLE companies ADD COLUMN logo_url TEXT")
        print("  + Added companies.logo_url column")

    today = datetime.now().strftime("%Y-%m-%d")
    n_snap_updated = 0
    n_snap_inserted = 0
    n_logo_updated = 0
    n_missing = 0

    for domain, data in enrich.items():
        cur.execute(
            "SELECT apollo_id, name FROM companies WHERE LOWER(domain) = LOWER(?)",
            (domain,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"  - {domain!r}: NOT FOUND in companies table")
            n_missing += 1
            continue

        for apollo_id, name in rows:
            # 1. companies.logo_url
            if data.get("logo_url"):
                cur.execute(
                    "UPDATE companies SET logo_url = ? WHERE apollo_id = ?",
                    (data["logo_url"], apollo_id),
                )
                n_logo_updated += 1

            # 2. latest snapshot row: employees / annual_revenue / latest_funding_type
            cur.execute(
                "SELECT id FROM snapshots WHERE apollo_id = ? "
                "ORDER BY snapshot_date DESC, id DESC LIMIT 1",
                (apollo_id,),
            )
            snap = cur.fetchone()

            set_parts: list[str] = []
            params: list = []
            if data.get("employees") is not None:
                set_parts.append("employees = ?")
                params.append(data["employees"])
            if data.get("annual_revenue") is not None:
                set_parts.append("annual_revenue = ?")
                params.append(data["annual_revenue"])
            if data.get("latest_funding_type") is not None:
                set_parts.append("latest_funding_type = ?")
                params.append(data["latest_funding_type"])

            if set_parts:
                if snap:
                    params.append(snap[0])
                    cur.execute(
                        f"UPDATE snapshots SET {', '.join(set_parts)} WHERE id = ?",
                        params,
                    )
                    print(f"  ✓ {name} ({domain}): updated snapshot id={snap[0]}"
                          + (" + logo" if data.get("logo_url") else ""))
                    n_snap_updated += 1
                else:
                    cur.execute(
                        "INSERT INTO snapshots (apollo_id, snapshot_date, employees, annual_revenue, latest_funding_type) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            apollo_id,
                            today,
                            data.get("employees"),
                            data.get("annual_revenue"),
                            data.get("latest_funding_type"),
                        ),
                    )
                    print(f"  + {name} ({domain}): inserted new snapshot dated {today}"
                          + (" + logo" if data.get("logo_url") else ""))
                    n_snap_inserted += 1
            elif data.get("logo_url"):
                print(f"  ✓ {name} ({domain}): logo only")

    conn.commit()
    conn.close()

    print()
    print(
        f"Done. Snapshots: {n_snap_updated} updated, {n_snap_inserted} inserted.  "
        f"Logos updated: {n_logo_updated}.  Domains missing in DB: {n_missing}."
    )
    print()
    print("Next steps:")
    print("  1. python build_csg_dashboard.py")
    print("  2. git add -A")
    print("  3. git commit -m 'Apollo enrichment: top 10 by signals (logos + Brother/BigBen recovered)'")
    print("  4. git push")


if __name__ == "__main__":
    main()
