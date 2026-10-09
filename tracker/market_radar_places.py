"""Market Radar: businesses near a point, from Overture Maps' open places data.

Overture publishes every place it knows (about 70 million, worldwide) as
Parquet files on a public S3 bucket, a new release each month. DuckDB reads
only the row groups whose bounding box overlaps the area asked for, so no
download and no account are needed. Measured from the Mac on 2026-10-09
(release 2026-09-23.1), for a box about 6.6 km across:

    Austin    77 dental places              25 s (the first query reads file footers)
    Berlin    494 dental, 327 with a website  37 s (first query on that connection)
    London    511 dental, 495 with a website   5 s
    Bangalore 155 dental, 100 with a website   4 s

Each place carries its name, category (basic_category plus a taxonomy),
websites, address, confidence and operating status, which is what a local
competitor list needs: the website is how a place becomes a company record
(entities are keyed by domain), and "permanently_closed" is dropped.

Licences: Overture places are CDLA Permissive 2.0 (with some sources under
other open licences, listed per record in `sources`).
"""
from __future__ import annotations

import logging
import math
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

BUCKET = "overturemaps-us-west-2"
LIST_URL = "https://%s.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/" % BUCKET
FALLBACK_RELEASE = "2026-09-23.1"
RELEASE_TTL = 6 * 3600
MAX_RADIUS_KM = 25
MAX_ROWS = 5000
EXTENSION_DIR = os.path.join(tempfile.gettempdir(), "duckdb_extensions")
SELF_RADIUS_KM = 0.4     # the client's own listing is within this of its address

_lock = threading.Lock()
_state = {"con": None, "release": None, "release_at": 0.0}


class PlacesUnavailable(RuntimeError):
    pass


def latest_release(fetch_text=None):
    """The newest release folder on the bucket (cached for a few hours)."""
    now = time.time()
    if _state["release"] and now - _state["release_at"] < RELEASE_TTL:
        return _state["release"]
    if fetch_text is None:
        from .market_radar_site import fetch_text
    text = fetch_text(LIST_URL, limit=500_000)
    releases = []
    try:
        root = ET.fromstring(text or "")
        for el in root.iter():
            if el.tag.endswith("Prefix") and el.text and re.fullmatch(r"release/[\d\-.]+/", el.text):
                releases.append(el.text.split("/")[1])
    except ET.ParseError:
        pass
    release = max(releases) if releases else (_state["release"] or FALLBACK_RELEASE)
    _state.update(release=release, release_at=now)
    return release


def _connection():
    # One connection per process: DuckDB caches the files' metadata, which is
    # what makes every query after the first one fast.
    if _state["con"] is None:
        try:
            import duckdb
        except ImportError as e:
            raise PlacesUnavailable("duckdb is not installed") from e
        con = duckdb.connect()
        # The httpfs extension is downloaded on first use; a temp directory
        # is writable on any host, the default (~/.duckdb) may not be.
        con.execute("SET extension_directory='%s'; SET enable_progress_bar=false;"
                    % EXTENSION_DIR)
        con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'; "
                    "SET enable_object_cache=true; SET memory_limit='768MB'; SET threads=4;")
        _state["con"] = con
    return _state["con"]


def box(lat, lon, radius_km):
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.05))
    return lon - dlon, lon + dlon, lat - dlat, lat + dlat


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, a)))


COLUMNS = """names.primary, basic_category, taxonomy.primary, taxonomy.hierarchy, websites,
             operating_status, confidence, bbox.xmin, bbox.ymin, addresses[1].freeform,
             addresses[1].locality, addresses[1].country, brand.names.primary"""
KEYS = ("name", "category", "taxonomy", "hierarchy", "websites", "status", "confidence",
        "lon", "lat", "street", "city", "country", "brand")


def query(lat, lon, radius_km, *, categories=(), terms=(), release=None, runner=None):
    """Places within radius_km of (lat, lon), nearest first, each with its
    distance. `categories` are exact Overture category names (matched against
    basic_category, the taxonomy's primary category and its hierarchy);
    `terms` are lowercase substrings matched against the same. With neither,
    every place in the area is returned (up to MAX_ROWS)."""
    radius_km = max(0.1, min(float(radius_km), MAX_RADIUS_KM))
    xmin, xmax, ymin, ymax = box(lat, lon, radius_km)
    release = release or latest_release()
    src = "s3://%s/release/%s/theme=places/type=place/*" % (BUCKET, release)
    where = ["bbox.xmin BETWEEN ? AND ?", "bbox.ymin BETWEEN ? AND ?"]
    params = [xmin, xmax, ymin, ymax]
    match = []
    cats = sorted({c.strip().lower() for c in categories if c and c.strip()})
    if cats:
        match.append("basic_category IN (%s)" % ",".join("?" * len(cats)))
        match.append("taxonomy.primary IN (%s)" % ",".join("?" * len(cats)))
        match.append("list_has_any(taxonomy.hierarchy, [%s])" % ",".join("?" * len(cats)))
        params += cats * 3
    for t in sorted({t.strip().lower() for t in terms if t and t.strip()}):
        match.append("basic_category LIKE ? OR taxonomy.primary LIKE ?")
        params += ["%" + t + "%"] * 2
    if match:
        where.append("(" + " OR ".join(match) + ")")
    sql = "SELECT %s FROM read_parquet('%s', hive_partitioning=1) WHERE %s LIMIT %d" % (
        COLUMNS, src, " AND ".join(where), MAX_ROWS)
    started = time.monotonic()
    if runner is None:
        with _lock:
            try:
                rows = _connection().execute(sql, params).fetchall()
            except PlacesUnavailable:
                raise
            except Exception as e:
                raise PlacesUnavailable("%s: %s" % (type(e).__name__, str(e)[:300])) from e
    else:
        rows = runner(sql, params)
    out = []
    for row in rows:
        place = dict(zip(KEYS, row))
        if place["lat"] is None or place["lon"] is None:
            continue
        place["distance_km"] = round(distance_km(lat, lon, place["lat"], place["lon"]), 2)
        if place["distance_km"] > radius_km:
            continue                      # the box's corners are outside the circle
        place["websites"] = [w for w in (place["websites"] or []) if w]
        place["hierarchy"] = list(place["hierarchy"] or [])
        out.append(place)
    out.sort(key=lambda p: p["distance_km"])
    logger.info("market_radar_places: %d places within %.1f km in %.1fs (release %s)",
                len(out), radius_km, time.monotonic() - started, release)
    return out


def categories_of(place):
    """Every category name a place carries, most specific first."""
    names = [place.get("taxonomy"), place.get("category")] + list(reversed(place.get("hierarchy") or []))
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out
