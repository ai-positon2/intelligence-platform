"""Market Radar, Phase 3: what a competitor's own website says, read the
same way every week so the difference is the news. No model here.

Six detectors, each split into a READ (fetch and reduce to a payload) and a
COMPARE (this week's payload against last week's, giving typed events):

    locations   location pages in the sitemaps, addresses in structured data
    catalog     every product with its price (Shopify, WooCommerce, or the
                product sitemap when neither answers)
    promotions  offer lines on the homepage ("20% off", "free shipping over")
    pages       the readable text of the home, pricing and offer pages
    reviews     public review counts stated in structured data
    newsroom    the company's own news, press or blog feed

Rules every detector keeps:

* A read that failed returns a non-ok status and NO payload. The caller
  stores no snapshot for it, so next week's comparison is against the last
  GOOD read, never against a hole that would make everything look new.
* A payload says whether it is complete. Something "removed" is only
  reported when both reads were complete: a sitemap cut at 6 files, or a
  catalog cut at 2,000 products, cannot tell a closed store from one we
  did not reach.
* The first read of a company is a baseline, not a week of news. Only
  facts that carry their own date (a product's creation date, a dated feed
  item) become events on a first read.
"""
from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from . import market_radar_site as site

BASELINE_DAYS = 90
MAX_EVENTS_PER_KIND = 25          # beyond this, one summary event instead of more cards


def _h(text, n=12):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def _event(key, type, title, *, status="unknown", date=None, summary=None, url=None,
           location=None):
    return {"key": key, "type": type, "title": title[:300], "status": status, "date": date,
            "summary": summary, "url": url, "location": location}


def _fail(status, note):
    return {"status": status, "note": note, "payload": None, "items": 0, "complete": False}


def _date(value):
    """An ISO-ish date or datetime string as YYYY-MM-DD, or None."""
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(value or ""))
    return m.group(1) if m else None


def _recent(day, now, days=BASELINE_DAYS):
    if not day:
        return False
    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return timedelta(0) <= now - d <= timedelta(days=days)


def _capped(events, kind_title, key, *, date=None):
    """At most MAX_EVENTS_PER_KIND individual events, then one that counts
    the rest, so a 300-product restock is one line, not 300 cards."""
    if len(events) <= MAX_EVENTS_PER_KIND:
        return events
    rest = len(events) - MAX_EVENTS_PER_KIND
    return events[:MAX_EVENTS_PER_KIND] + [
        _event(key, events[0]["type"], "%d more %s" % (rest, kind_title), date=date)]


# == sitemaps ======================================================================

LOC_RE = re.compile(r"<url>(.*?)</url>", re.S)


def read_sitemaps(get, sitemap_urls, *, prefer, keep, max_files=8, max_urls=60_000,
                  parallel=6, avoid=None):
    """Every (url, lastmod) the sitemaps list whose path `keep` accepts.

    A sitemap index is followed, children whose address matches `prefer`
    first, up to `parallel` files at a time (orangetheory.com keeps one
    sitemap per country: 30 files). Returns {"entries", "files_read",
    "complete", "failed"}. complete is False when a file that could hold
    what we want was left unread or failed, so a later comparison knows a
    missing page may only be unread. A refused file whose address does not
    look like what we want (aspendental.com's appointment-booking sitemap)
    does not make the read incomplete once a file that does was read."""
    import concurrent.futures
    queue = list(dict.fromkeys(sitemap_urls))
    seen, entries, files, failed, refused = set(), {}, 0, [], []
    complete, wanted_read, unwanted_failed = True, False, False
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        while queue:
            if files >= max_files or len(entries) >= max_urls:
                complete = False
                break
            batch = []
            while queue and len(batch) < min(parallel, max_files - files):
                sm = queue.pop(0)
                if sm not in seen:
                    seen.add(sm)
                    batch.append(sm)
            reads = list(pool.map(get, batch))
            files += len(batch)
            for sm, read in zip(batch, reads):
                if read["status"] != "ok":
                    failed.append("%s: %s" % (sm, read["note"] or read["status"]))
                    if read["status"] != "not_found":
                        refused.append(sm)
                    if prefer.search(sm) or sm in sitemap_urls:
                        complete = False
                    else:
                        unwanted_failed = True
                    continue
                body = read["body"]
                if read.get("truncated"):
                    complete = False
                if "<sitemapindex" in body[:3000]:
                    children = [c for c in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
                                if site.parseable(c)]
                    wanted = [c for c in children if prefer.search(c)]
                    # Only the children that look like what we want, when any
                    # do; otherwise every child (sitemap-1.xml, sitemap-2.xml)
                    # except those plainly about something else.
                    rest = [c for c in children if not (avoid and avoid.search(c))]
                    queue.extend(c for c in (wanted or rest) if c not in seen)
                    continue
                if prefer.search(sm):
                    wanted_read = True
                for block in LOC_RE.findall(body):
                    m = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
                    if not m:
                        continue
                    url = m.group(1).replace("&amp;", "&")
                    if not site.parseable(url):
                        continue
                    if keep(urlsplit(url).path or "/"):
                        lm = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block)
                        entries[url] = _date(lm.group(1)) if lm else None
                        if len(entries) >= max_urls:
                            complete = False
                            break
    if unwanted_failed and not wanted_read:
        complete = False
    return {"entries": entries, "files_read": files, "complete": complete, "failed": failed,
            "refused": refused}


# == locations =====================================================================

LOCATION_FILES = 40               # orangetheory.com: an index and 30 country sitemaps
# Shopify names its sitemaps sitemap_products_1.xml, sitemap_blogs_1.xml ...:
# gymshark.com spent 17 s reading 4,872 product URLs looking for 6 stores.
NOT_LOCATION_SITEMAP = re.compile(r"product|collection|blog|article|post|image|video|news|"
                                  r"categor|tag|author", re.I)
LOCATION_SITEMAP = re.compile(r"locat|store|clinic|office|dentist|practice|branch|studio|gym|"
                              r"centre|center|loja|unidade|standort|filial|tienda|club|salon",
                              re.I)


def _page_key(url):
    p = urlsplit(url)
    return ((p.hostname or "").lower().removeprefix("www.") + (p.path or "/").rstrip("/").lower()
            or "/")


def _leaves(keys):
    keys = sorted(set(keys))
    out = []
    for i, k in enumerate(keys):
        nxt = keys[i + 1] if i + 1 < len(keys) else ""
        if not nxt.startswith(k + "/"):
            out.append(k)
    return out


def leaves(keys):
    """The places among location pages.

    A hub for other location pages is not a place: with /dentist/ca and
    /dentist/ca/merced listed, only Merced is. Neither is a tab of a place:
    aspendental.com lists 16 service pages under each of its ~1,100 offices
    (/dentist/ia/ankeny/2409-se-delaware-ave/dentures), which counted as
    17,492 "locations" (2026-10-09). A last segment that recurs under at
    least 3 different parents and 30% of all parents is such a tab, and
    folds into its parent."""
    found = _leaves(keys)
    parents = {}
    for k in found:
        head, _, tail = k.rpartition("/")
        parents.setdefault(tail, set()).add(head)
    n_parents = len({k.rpartition("/")[0] for k in found})
    tabs = {t for t, ps in parents.items() if len(ps) >= 3 and len(ps) >= 0.3 * n_parents}
    if not tabs:
        return found
    folded = {k.rpartition("/")[0] if k.rpartition("/")[2] in tabs else k for k in found}
    # A hub can now be a leaf again (its only children were tabs), so look again.
    return _leaves(folded)


def place_label(key):
    """/dentist/ca/merced -> "Merced, CA"; /locations/london-soho -> "London Soho"."""
    segs = [s for s in key.split("/")[1:] if s]
    if not segs:
        return key
    name = re.sub(r"[-_]+", " ", segs[-1]).strip().title()
    if len(segs) >= 2 and re.fullmatch(r"[a-z]{2}", segs[-2]):
        return "%s, %s" % (name, segs[-2].upper())
    return name


def _location_hosts(s, domain):
    """The company's own hosts that serve location pages: the main one, plus
    a locations.brand.com style host its links point at (Yext and similar
    locator vendors publish there)."""
    root = ".".join(domain.split(".")[-2:])
    hosts = [domain]
    for u in (s.get("location_links") or {}).get("sample", []):
        h = (urlsplit(u).hostname or "").lower().removeprefix("www.")
        if h and h not in hosts and (h == root or h.endswith("." + root)):
            hosts.append(h)
    return hosts[:3]


def read_locations(ctx):
    rs, get = ctx["site"], ctx["get"]
    s = rs.get("signals") or {}
    domain = ctx["entity"]["domain"]
    addresses = sorted({
        ", ".join(x for x in (a.get("streetAddress"), a.get("addressLocality"),
                              a.get("postalCode"), a.get("country")) if x)
        for o in s.get("organizations") or [] for a in [o.get("address") or {}]
        if a.get("streetAddress") and a.get("addressLocality")})[:200]
    pages, complete, files, failed, refused = [], True, 0, [], []
    for host in _location_hosts(s, domain):
        if host == domain:
            sitemaps = ((rs.get("robots") or {}).get("sitemaps") or
                        [urljoin(rs["home_url"], "/sitemap.xml")])
        else:
            robots = get("https://%s/robots.txt" % host, limit=500_000)
            _, sitemaps = site.parse_robots(robots["body"] if robots["status"] == "ok" else "")
            sitemaps = sitemaps or ["https://%s/sitemap.xml" % host]
        got = read_sitemaps(get, sitemaps, prefer=LOCATION_SITEMAP, max_files=LOCATION_FILES,
                            avoid=NOT_LOCATION_SITEMAP,
                            keep=lambda p: bool(site.LOCATION_PATH.search(p)))
        files += got["files_read"]
        complete = complete and got["complete"]
        failed.extend(got["failed"])
        refused.extend(got["refused"])
        pages.extend(_page_key(u) for u in got["entries"])
    places = leaves(pages)
    if not places and not addresses:
        if refused and len(failed) >= files:
            # Not one sitemap file answered and at least one refused us: we
            # did not look, so we cannot say there are no locations. A
            # sitemap that answers 404 does not exist (nwhillsdentist.com,
            # veja-store.com, 2026-10-09): that is "none", said below.
            return _fail("failed", "the sitemap could not be read (%s)" % failed[0])
        where = "no sitemap" if failed and len(failed) >= files else \
            "no location pages in the sitemap"
        return {"status": "none", "note": where + " and no address in structured data",
                "payload": None, "items": 0, "complete": complete}
    vendors = s.get("locator_vendors") or []
    note = "%d location pages, %d addresses" % (len(places), len(addresses))
    if vendors:
        note += "; store locator by %s (read through its pages, not its own data feed)" % \
            ", ".join(vendors)
    if not complete:
        note += "; the sitemap was only partly read, so closures are not reported"
    payload = {"places": places[:20_000], "hubs": len(set(pages)) - len(places),
               "addresses": addresses, "complete": complete and len(places) <= 20_000}
    return {"status": "ok", "note": note, "payload": payload,
            "items": len(places) + len(addresses), "complete": payload["complete"]}


def compare_locations(prev, cur, now):
    day = now.strftime("%Y-%m-%d")
    before, after = set(prev.get("places") or []), set(cur.get("places") or [])
    added, removed = sorted(after - before), sorted(before - after)
    trust_removed = prev.get("complete") and cur.get("complete")
    events = []
    # A site that renamed its location URLs (every page "removed", as many
    # "added") has restructured, not opened and closed half its branches.
    # A small chain swapping one branch for another is not that: it takes
    # five pages each way.
    floor = max(5, 0.3 * len(before))
    if len(added) >= floor and len(removed) >= floor:
        return [_event("loc-reshaped:%s" % day, "site_restructured",
                       "Location pages were renamed or reorganised (%d removed, %d added)"
                       % (len(removed), len(added)), date=day,
                       summary="Not read as openings or closures: the address scheme changed.")]
    # A list that lost many pages and gained none is far more often a
    # sitemap that came back short than a wave of closures: one line, so a
    # person checks, instead of dozens of "closed" cards.
    if trust_removed and len(removed) >= max(10, 0.2 * len(before)) and len(added) < 5:
        return [_event("loc-shrank:%s" % day, "location_list_shrank",
                       "%d location pages are no longer listed (of %d)" % (len(removed),
                                                                          len(before)),
                       date=day, summary="Not reported as closures one by one: a drop this "
                       "large is usually a sitemap that came back short. Examples: " +
                       ", ".join(place_label(k) for k in removed[:8]))]
    opened = [_event("loc+:" + k, "new_location", "New location page: " + place_label(k),
                     date=day, url="https://" + k, location={"label": place_label(k)})
              for k in added]
    events += _capped(opened, "new location pages", "loc+many:%s" % day, date=day)
    if trust_removed:
        closed = [_event("loc-:" + k, "closed_location", "Location page removed: " + place_label(k),
                         status="closed", date=day, url="https://" + k,
                         location={"label": place_label(k)}) for k in removed]
        events += _capped(closed, "location pages removed", "loc-many:%s" % day, date=day)
    a_before, a_after = set(prev.get("addresses") or []), set(cur.get("addresses") or [])
    for a in sorted(a_after - a_before)[:MAX_EVENTS_PER_KIND]:
        events.append(_event("addr+:" + _h(a), "new_location", "New address listed: " + a,
                             date=day, location={"address": a}))
    if trust_removed:
        for a in sorted(a_before - a_after)[:MAX_EVENTS_PER_KIND]:
            events.append(_event("addr-:" + _h(a), "closed_location", "Address no longer listed: "
                                 + a, status="closed", date=day, location={"address": a}))
    return events


# == catalog =======================================================================

SHOPIFY_PAGE = 250
SHOPIFY_MAX_PAGES = 8              # 2,000 products
WOO_PAGE = 100
WOO_MAX_PAGES = 10
PRODUCT_SITEMAP = re.compile(r"product|produto|produkt|producto|item|shop", re.I)


def _price(value):
    try:
        return round(float(str(value).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def shopify_product(p):
    """One products.json entry as [title, path, min price, max price,
    highest compare-at price, any variant available, variants, created
    date, product type, published date]."""
    vs = [v for v in p.get("variants") or [] if isinstance(v, dict)]
    prices = [x for x in (_price(v.get("price")) for v in vs) if x is not None]
    compare = [x for x in (_price(v.get("compare_at_price")) for v in vs) if x]
    return [str(p.get("title") or "")[:160], "/products/%s" % p.get("handle", ""),
            min(prices) if prices else None, max(prices) if prices else None,
            max(compare) if compare else None,
            any(bool(v.get("available")) for v in vs), len(vs),
            _date(p.get("created_at")), str(p.get("product_type") or "")[:60],
            _date(p.get("published_at"))]


def woo_product(p):
    pr = p.get("prices") or {}
    unit = int(pr.get("currency_minor_unit") or 0)

    def money(v):
        x = _price(v)
        return None if x is None else round(x / (10 ** unit), 2)

    price, regular = money(pr.get("price")), money(pr.get("regular_price"))
    path = urlsplit(p.get("permalink") or "").path or ""
    # The store API sends names HTML-escaped ("Turbo &#038; Chill Bundle",
    # nutribullet.com, 2026-10-09).
    return [html.unescape(str(p.get("name") or ""))[:160], path, price, price,
            regular if regular and price is not None and regular > price else None,
            bool(p.get("is_in_stock")), len(p.get("variations") or []) or 1, None,
            ", ".join(c.get("name", "") for c in (p.get("categories") or [])[:2])[:60]]


def _read_shopify(ctx):
    home, get_json = ctx["site"]["home_url"], ctx["get_json"]
    products, complete, note = {}, True, ""
    for page in range(1, SHOPIFY_MAX_PAGES + 1):
        data, read = get_json(urljoin(home, "/products.json?limit=%d&page=%d" % (SHOPIFY_PAGE, page)))
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            if page == 1:
                return None, read["note"] or "products.json did not answer"
            complete, note = False, "page %d failed (%s)" % (page, read["note"] or read["status"])
            break
        for p in data["products"]:
            if isinstance(p, dict) and p.get("id") is not None:
                products[str(p["id"])] = shopify_product(p)
        if len(data["products"]) < SHOPIFY_PAGE:
            break
    else:
        complete, note = False, "stopped at %d products" % len(products)
    return {"source": "shopify", "products": products, "complete": complete}, note


def _read_woo(ctx):
    home, get_json = ctx["site"]["home_url"], ctx["get_json"]
    products, complete, note = {}, True, ""
    for page in range(1, WOO_MAX_PAGES + 1):
        data, read = get_json(urljoin(home, "/wp-json/wc/store/v1/products?per_page=%d&page=%d"
                                      % (WOO_PAGE, page)))
        if not isinstance(data, list):
            if page == 1:
                return None, read["note"] or "the WooCommerce store API did not answer"
            complete, note = False, "page %d failed (%s)" % (page, read["note"] or read["status"])
            break
        for p in data:
            if isinstance(p, dict) and p.get("id") is not None:
                products[str(p["id"])] = woo_product(p)
        if len(data) < WOO_PAGE:
            break
    else:
        complete, note = False, "stopped at %d products" % len(products)
    cur = next((str((p.get("prices") or {}).get("currency_code") or "") for p in data
                if isinstance(p, dict) and (p.get("prices") or {}).get("currency_code")), "") \
        if isinstance(data, list) else ""
    return {"source": "woocommerce", "products": products, "complete": complete,
            "currency": cur[:3].upper()}, note


def read_catalog(ctx):
    rs = ctx["site"]
    s = rs.get("signals") or {}
    names = {p["name"] for p in s.get("platforms") or []}
    sm = s.get("sitemap") or {}
    tried = []
    for platform, reader in (("shopify", _read_shopify), ("woocommerce", _read_woo)):
        if platform == "shopify" and not ("shopify" in names or rs.get("shopify_store")):
            continue
        if platform == "woocommerce" and "woocommerce" not in names:
            continue
        payload, note = reader(ctx)
        # WooCommerce is a plugin many sites install and never sell through:
        # three Austin dental practices answered 404 or 403 to its store API
        # (2026-10-09). With no sign of products elsewhere that is not a
        # shop, not a failure. (The API is still asked first: nutribullet.com
        # sells 180 products through it with no product markup on its pages.)
        if payload is None and platform == "woocommerce" and \
                sm.get("product_like", 0) < 20 and not s.get("product_schema"):
            continue
        if payload is not None:
            # Prices mean nothing without their currency ("Priced 22.8" on a
            # UK shop, 2026-10-10). Shopify's products.json carries none: the
            # store's own record (/meta.json) does, then the site's prices.
            if not payload.get("currency"):
                payload["currency"] = shop_currency(rs)
            prices = [p[2] for p in payload["products"].values() if p[2] is not None]
            on_sale = sum(1 for p in payload["products"].values() if p[4] and p[2] and p[4] > p[2])
            text = "%d products from %s" % (len(payload["products"]), platform)
            if prices:
                text += ", prices %s to %s%s, %d on sale" % (
                    money(min(prices), payload["currency"]),
                    money(max(prices), payload["currency"]),
                    no_currency(payload["currency"]), on_sale)
            if note:
                text += "; " + note + ", so removals are not reported"
            return {"status": "ok" if payload["products"] else "empty", "note": text,
                    "payload": payload, "items": len(payload["products"]),
                    "complete": payload["complete"]}
        tried.append("%s: %s" % (platform, note))
    if sm.get("product_like", 0) >= 20:
        got = read_sitemaps(ctx["get"], (rs.get("robots") or {}).get("sitemaps") or
                            [urljoin(rs["home_url"], "/sitemap.xml")], prefer=PRODUCT_SITEMAP,
                            keep=lambda p: bool(site.PRODUCT_PATH.search(p)))
        if got["entries"]:
            products = {_page_key(u): [None, urlsplit(u).path, None, None, None, None, None,
                                       lm, ""] for u, lm in got["entries"].items()}
            payload = {"source": "sitemap", "products": products, "complete": got["complete"]}
            note = "%d product pages from the sitemap (no prices: the shop has no public " \
                   "product feed)" % len(products)
            if tried:
                note += "; " + "; ".join(tried)
            return {"status": "ok", "note": note, "payload": payload, "items": len(products),
                    "complete": got["complete"]}
    if tried:
        return _fail("failed", "; ".join(tried))
    return {"status": "none", "note": "not a shop: no shop platform and no product pages",
            "payload": None, "items": 0, "complete": False}


def shop_currency(rs):
    """The currency a shop prices in: its Shopify store record, else the
    most frequent currency on its pages, else "" (unknown)."""
    cur = ((rs.get("shopify_store") or {}).get("currency") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", cur):
        return cur
    seen = ((rs.get("signals") or {}).get("currencies") or [])
    return seen[0] if seen and re.fullmatch(r"[A-Z]{3}", str(seen[0])) else ""


def money(x, currency):
    """22.8, "GBP" -> "GBP 22.80"; with no currency just "22.80", and the
    line carries NO_CURRENCY once (see no_currency) so no reader takes the
    number for dollars."""
    if x is None:
        return "?"
    amount = ("%.2f" % x) if isinstance(x, (int, float)) else str(x)
    return "%s %s" % (currency, amount) if currency else amount


NO_CURRENCY = " (currency not stated)"


def no_currency(currency):
    return "" if currency else NO_CURRENCY


def _family(title):
    """Colourways are one product: "Tree Runner - Dusty Pink" and
    "Tree Runner - Navy" launch as one line."""
    return re.split(r"\s+[-–|/]\s*|\s*[-–|/]\s+", title or "", maxsplit=1)[0].strip() \
        or title or "?"


def _launch_events(rows, day, currency=""):
    """New products grouped by family, one event each."""
    fam = {}
    for pid, p in rows:
        fam.setdefault(_family(p[0]).lower(), []).append((pid, p))
    events = []
    for name, items in sorted(fam.items(), key=lambda kv: -len(kv[1])):
        first = items[0][1]
        prices = sorted(x for _, p in items for x in (p[2], p[3]) if x is not None)
        dates = sorted(_launched(p) for _, p in items if _launched(p))
        summary = None
        if prices:
            summary = "Priced %s%s" % (money(prices[0], currency),
                                       " to %s" % money(prices[-1], currency)
                                       if prices[-1] != prices[0] else "") + no_currency(currency)
        title = "New product: " + _family(first[0])
        if len(items) > 1:
            title += " (%d versions)" % len(items)
        events.append(_event("prod+:" + _h(name), "product_launch", title,
                             status="announced", date=dates[0] if dates else day, summary=summary,
                             url=first[1] or None))
    return events


def _launched(p):
    """When a product went on sale: its publish date when the shop gives one
    (shops draft products months ahead and publish at launch: an Allbirds
    flip flop created in the spring was published on 2026-09-25), else its
    creation date."""
    return (p[9] if len(p) > 9 else None) or p[7]


def baseline_catalog(cur, now):
    """On a first read, products the shop itself says launched within 90
    days. Published recently but created more than a year ago is an old
    product put back on sale (a seasonal return), not a launch."""
    rows = [(pid, p) for pid, p in (cur.get("products") or {}).items()
            if p[0] and _recent(_launched(p), now)
            and (not p[7] or _recent(p[7], now, days=365))]
    day = now.strftime("%Y-%m-%d")
    return _capped(_launch_events(rows, day, cur.get("currency") or ""), "new products",
                   "prod+many:baseline", date=day)


def compare_catalog(prev, cur, now):
    day = now.strftime("%Y-%m-%d")
    a, b = prev.get("products") or {}, cur.get("products") or {}
    if prev.get("source") != cur.get("source"):
        return []       # read a different way; the two lists are not comparable
    currency = cur.get("currency") or prev.get("currency") or ""
    events = _capped(_launch_events([(k, b[k]) for k in b if k not in a and b[k][0]], day,
                                    currency),
                     "new products", "prod+many:%s" % day, date=day)
    if prev.get("complete") and cur.get("complete"):
        gone = [_event("prod-:%s:%s" % (k, day), "product_removed",
                       "Product no longer listed: " + (a[k][0] or a[k][1]), status="closed",
                       date=day, url=a[k][1] or None) for k in a if k not in b]
        events += _capped(gone, "products removed", "prod-many:%s" % day, date=day)
    ups, downs, sales, soldout = [], [], [], []
    for k in a.keys() & b.keys():
        old, new = a[k], b[k]
        name = new[0] or new[1]
        if old[2] and new[2] and abs(new[2] - old[2]) >= max(0.01, 0.01 * old[2]):
            pct = round(100 * (new[2] - old[2]) / old[2], 1)
            ev = _event("price:%s:%s" % (k, new[2]), "price_increase" if pct > 0 else "price_cut",
                        "%s: price %s from %s to %s (%+.1f%%)" % (
                            name, "up" if pct > 0 else "down", money(old[2], currency),
                            money(new[2], currency), pct) + no_currency(currency),
                        status="completed", date=day, url=new[1] or None)
            (ups if pct > 0 else downs).append(ev)
        on_sale_now = bool(new[4] and new[2] and new[4] > new[2])
        on_sale_before = bool(old[4] and old[2] and old[4] > old[2])
        if on_sale_now and not on_sale_before:
            sales.append(_event("sale:%s:%s" % (k, new[2]), "sale_started",
                                "%s on sale: %s, was %s%s" % (name, money(new[2], currency),
                                                             money(new[4], currency),
                                                             no_currency(currency)),
                                status="announced", date=day, url=new[1] or None))
        if old[5] is True and new[5] is False:
            soldout.append(_event("soldout:%s:%s" % (k, day), "sold_out", "Sold out: " + name,
                                  date=day, url=new[1] or None))
    both = len(a.keys() & b.keys())
    for group, word in ((ups, "price increases"), (downs, "price cuts")):
        if both >= 10 and len(group) >= 0.3 * both:
            # One move across the shop is the news, not 400 product lines.
            events.append(_event("price-wide:%s:%s" % (word, day),
                                 group[0]["type"], "Shop-wide %s: %d of %d products" % (
                                     word, len(group), both), status="completed", date=day))
            group[:] = group[:5]
    for group, word, key in ((ups, "price increases", "up"), (downs, "price cuts", "down"),
                             (sales, "products on sale", "sale"),
                             (soldout, "products sold out", "soldout")):
        events += _capped(group, word, "%s-many:%s" % (key, day), date=day)
    return events


# == promotions ====================================================================

PROMO = re.compile(
    r"(\d{1,2}\s?%\s?(off|rabatt|de desconto|desconto|descuento|de r[ée]duction|sconto)|"
    r"\b(sale|clearance|black friday|cyber monday|promo(tion)? code|use code|discount|"
    r"free (shipping|delivery|returns|gift)|bogo|buy one|limited time|offer|"
    r"versandkostenfrei|gratis versand|angebot|rabatt|aktion|"
    r"frete gr[aá]tis|promo[cç][aã]o|desconto|oferta|liquida[cç][aã]o|"
    r"env[ií]o gratis|rebajas|descuento|"
    r"livraison gratuite|soldes|r[ée]duction|"
    r"セール|送料無料|割引)\b)", re.I)
LINK_SUFFIX = re.compile(r"\s*\[(https?:|mailto:|tel:|/)[^\]]*\]")


def clean_lines(text, *, max_lines=250, max_len=200):
    """Readable lines of a page without the link addresses the text keeps."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        line = " ".join(LINK_SUFFIX.sub("", line).split())
        if len(line) < 3 or line in seen:
            continue
        seen.add(line)
        out.append(line[:max_len])
        if len(out) >= max_lines:
            break
    return out


def _page_text(rs, kind):
    """The text the site reader kept for the first readable page of a kind."""
    for p in rs.get("pages") or []:
        if p["kind"] == kind and p["status"] == "ok":
            t = (rs.get("texts") or {}).get(p["url"])
            if t:
                return p["url"], t
    return None, None


def read_promotions(ctx):
    rs = ctx["site"]
    url, text = _page_text(rs, "home")
    if not text:
        return _fail("failed", "the homepage was not readable")
    lines = sorted({l for l in clean_lines(text, max_lines=600) if 4 <= len(l) <= 160
                    and PROMO.search(l)})[:30]
    note = "%d offer lines on the homepage" % len(lines) if lines else \
        "no offer on the homepage today"
    if rs.get("needs_browser"):
        note += " (the page is built in the browser, so a banner drawn there is not seen)"
    return {"status": "ok" if lines else "empty", "note": note,
            "payload": {"url": url, "lines": lines}, "items": len(lines), "complete": True}


def compare_promotions(prev, cur, now):
    day = now.strftime("%Y-%m-%d")
    before, after = set(prev.get("lines") or []), set(cur.get("lines") or [])
    events = [_event("promo+:" + _h(l), "promotion", "New offer on the homepage: " + l,
                     status="announced", date=day, url=cur.get("url")) for l in sorted(after - before)]
    events += [_event("promo-:%s:%s" % (_h(l), day), "promotion_ended",
                      "Offer taken off the homepage: " + l, status="completed", date=day,
                      url=cur.get("url")) for l in sorted(before - after)]
    return events[:2 * MAX_EVENTS_PER_KIND]


# == key pages =====================================================================

KEY_PAGES = ("home", "pricing", "offerings")
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"


def read_pages(ctx):
    rs = ctx["site"]
    pages = {}
    for kind in KEY_PAGES:
        url, text = _page_text(rs, kind)
        if text:
            lines = clean_lines(text)
            pages[kind] = {"url": url, "hash": _h("\n".join(lines), 16), "lines": lines}
    if not pages:
        return _fail("failed", "none of the key pages was readable")
    note = "read %s" % ", ".join(sorted(pages))
    return {"status": "ok", "note": note, "payload": {"pages": pages}, "items": len(pages),
            "complete": True}


def compare_pages(prev, cur, now):
    day = now.strftime("%Y-%m-%d")
    events = []
    for kind, page in (cur.get("pages") or {}).items():
        old = (prev.get("pages") or {}).get(kind)
        if not old or old["hash"] == page["hash"]:
            continue
        added = [l for l in page["lines"] if l not in set(old["lines"])]
        removed = [l for l in old["lines"] if l not in set(page["lines"])]
        if len(added) + len(removed) < 3:
            continue        # a date or a counter moved, not the message
        ratio = difflib.SequenceMatcher(None, old["lines"], page["lines"]).ratio()
        summary = "Added: " + " | ".join(added[:6]) if added else ""
        if removed:
            summary += ("\n" if summary else "") + "Removed: " + " | ".join(removed[:6])
        events.append(_event("page:%s:%s" % (kind, page["hash"]), "page_changed",
                             "%s page changed (%d lines added, %d removed, %d%% the same)" % (
                                 kind.capitalize(), len(added), len(removed), round(100 * ratio)),
                             status="completed", date=day, summary=summary[:1500],
                             url=page["url"]))
    return events


def page_history(get, url, now, days=BASELINE_DAYS):
    """How many different versions of a page the Wayback Machine holds from
    the last `days` days, or None when it could not be asked. Free history
    on a first read: "the pricing page changed 3 times in 90 days"."""
    parts = urlsplit(url)
    since = (now - timedelta(days=days)).strftime("%Y%m%d")
    read = get("%s?url=%s&from=%s&output=json&fl=timestamp,digest&filter=statuscode:200"
               "&collapse=digest&limit=200" % (WAYBACK_CDX, parts.netloc + (parts.path or "/"),
                                              since), limit=200_000, timeout=12)
    if read["status"] != "ok":
        return None
    try:
        rows = json.loads(read["body"] or "[]")
    except ValueError:
        return None
    return max(0, len(rows) - 1)        # the first row is the header


# == reviews =======================================================================

TRACKED_PRODUCTS = 3


def read_reviews(ctx):
    """Review counts from structured data: what the site reader already saw,
    plus a few product pages, the same ones every week so growth compares
    like with like."""
    rs, prev = ctx["site"], ctx["prev"].get("reviews") or {}
    scores = {}
    for r in (rs.get("signals") or {}).get("ratings") or []:
        scores.setdefault(r["url"], r)
    tracked = list(prev.get("tracked") or [])
    if not tracked:
        catalog = (ctx["results"].get("catalog") or {}).get("payload") or {}
        products = sorted(((p[7] or "9999", p[1]) for p in (catalog.get("products") or {}).values()
                           if p[1] and catalog.get("source") in ("shopify", "woocommerce")))
        # The oldest products: the ones with the most reviews to grow.
        tracked = [urljoin(rs["home_url"], path) for _, path in products[:TRACKED_PRODUCTS]]
    failed = []
    for url in tracked:
        page = ctx["fetch"](url)
        if page["status"] != "ok":
            failed.append(url)
            continue
        for r in site.ratings_from_html(page["html"], url)[:1]:
            scores[url] = r
    if not scores:
        if failed:
            return _fail("failed", "%d product pages could not be read" % len(failed))
        return {"status": "none", "note": "no public review count on the site",
                "payload": None, "items": 0, "complete": False}
    total = sum(r["count"] for r in scores.values())
    note = "%d review counts, %d reviews in all" % (len(scores), total)
    if failed:
        note += "; %d tracked pages could not be read this time" % len(failed)
    return {"status": "ok", "note": note,
            "payload": {"tracked": tracked, "scores": {u: [r["item"], r["rating"], r["count"]]
                                                       for u, r in sorted(scores.items())}},
            "items": len(scores), "complete": not failed}


def compare_reviews(prev, cur, now):
    a, b = prev.get("scores") or {}, cur.get("scores") or {}
    both = a.keys() & b.keys()
    before = sum(a[u][2] for u in both)
    after = sum(b[u][2] for u in both)
    growth = after - before
    if not both or growth < max(20, 0.02 * max(before, 1)):
        return []
    day = now.strftime("%Y-%m-%d")
    return [_event("reviews:%s" % day, "review_growth",
                   "%d new reviews on %d tracked pages (+%.1f%%)" % (
                       growth, len(both), 100.0 * growth / max(before, 1)),
                   status="completed", date=day,
                   summary="From %d to %d reviews." % (before, after))]


# == newsroom ======================================================================

FEEDISH = re.compile(r"news|press|presse|blog|media|imprensa|noticias|actualites|stories", re.I)
POSTISH = re.compile(r"/(news|press|presse|blog|media|stories|articles?|imprensa|noticias|"
                     r"newsroom|insights|updates)/", re.I)
MAX_POSTS = 40
# Feeds that are not news: comment feeds, and the product feeds Shopify
# announces on every store (wildling.shoes' /collections/all.atom listed 26
# shoes as "announcements", 2026-10-09).
NOT_NEWS_FEED = re.compile(r"/comments/|/collections/|/products?[./]|\.atom\?|/shop/feed", re.I)
NOT_NEWS = re.compile(r"newsletter|subscribe|sign-?up|unsubscribe|preferences", re.I)


def parse_feed(xml):
    """RSS or Atom items as [{"title", "url", "date"}], newest first as listed."""
    items = []
    for block in re.findall(r"<item[\s>](.*?)</item>|<entry[\s>](.*?)</entry>", xml or "", re.S):
        body = block[0] or block[1]
        title = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
        link = re.search(r"<link>\s*([^<\s]+)\s*</link>", body) or \
            re.search(r"<link[^>]*href=\"([^\"]+)\"", body)
        date = re.search(r"<(pubDate|published|updated|dc:date)>(.*?)</\1>", body, re.S)
        if not title or not link:
            continue
        t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title.group(1), flags=re.S)
        t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())
        items.append({"title": t[:300], "url": link.group(1).replace("&amp;", "&"),
                      "date": _feed_date(date.group(2) if date else None)})
    return items


def _feed_date(value):
    if not value:
        return None
    d = _date(value)
    if d:
        return d
    try:
        import email.utils
        return email.utils.parsedate_to_datetime(value.strip()).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return None


def links_under(html, page_url):
    """Article-like links on a news page: the site's own, with a real title."""
    doc = site.parse_html(html)
    base = urlsplit(page_url)
    out, seen = [], set()
    for href, text in doc.links:
        url = urljoin(page_url, href).split("#", 1)[0]
        p = urlsplit(url)
        if p.hostname != base.hostname or url.rstrip("/") == page_url.rstrip("/") or url in seen:
            continue
        under = p.path.startswith(base.path.rstrip("/") + "/") and base.path not in ("", "/")
        if len(text) < 25 or not (under or POSTISH.search(p.path)):
            continue
        seen.add(url)
        out.append({"title": text[:300], "url": url, "date": None})
    return out[:MAX_POSTS]


def read_newsroom(ctx):
    rs, get = ctx["site"], ctx["get"]
    s = rs.get("signals") or {}
    feeds = sorted((f for f in s.get("feeds") or [] if not NOT_NEWS_FEED.search(f)),
                   key=lambda u: 0 if FEEDISH.search(u) else 1)
    if not feeds and any(p["name"] == "wordpress" for p in s.get("platforms") or []):
        feeds = [urljoin(rs["home_url"], "/feed/")]
    notes, answered_empty = [], None
    for f in feeds[:3]:
        read = get(f, limit=2_000_000)
        items = parse_feed(read["body"]) if read["status"] == "ok" else []
        if read["status"] == "ok" and not items and re.search(r"<(rss|feed|channel)[\s>]",
                                                              read["body"][:3000]):
            answered_empty = answered_empty or f
            continue
        if read["status"] == "not_found" or (read["status"] == "ok" and not items):
            # A guessed /feed/ that does not exist, or that answers with a
            # page instead of a feed, means there is no feed: not a failure.
            continue
        # A feed whose items are mostly product pages is a catalogue, not news.
        if items and sum(1 for i in items if site.PRODUCT_PATH.search(urlsplit(i["url"]).path
                                                                    or "")) > len(items) / 2:
            continue
        if items:
            items = items[:MAX_POSTS]
            return {"status": "ok", "note": "%d posts from the site's feed" % len(items),
                    "payload": {"source": f, "kind": "feed",
                                "items": [dict(i, id=_h(i["url"])) for i in items]},
                    "items": len(items), "complete": True}
        notes.append("%s: %s" % (f, read["note"] or "no items"))
    # "news" also matches a newsletter sign-up (gymshark.com's
    # /pages/sign-up-to-our-newsletter, 2026-10-09): not a news page.
    press = next((p for p in rs.get("pages") or [] if p["kind"] == "press" and p["status"] == "ok"
                  and not NOT_NEWS.search(p["url"])), None)
    if press:
        page = ctx["fetch"](press["url"])
        if page["status"] == "ok":
            items = links_under(page["html"], page["final_url"])
            if items:
                return {"status": "ok", "note": "%d posts listed on %s" % (
                    len(items), urlsplit(page["final_url"]).path or "/"),
                        "payload": {"source": page["final_url"], "kind": "page",
                                    "items": [dict(i, id=_h(i["url"])) for i in items]},
                        "items": len(items), "complete": True}
            return {"status": "empty", "note": "the news page lists no posts we can read",
                    "payload": {"source": page["final_url"], "kind": "page", "items": []},
                    "items": 0, "complete": True}
        notes.append("news page: " + (page["note"] or page["status"]))
    if answered_empty:
        return {"status": "empty", "note": "the site's feed has no posts",
                "payload": {"source": answered_empty, "kind": "feed", "items": []},
                "items": 0, "complete": True}
    if notes:
        return _fail("failed", "; ".join(notes[:2]))
    return {"status": "none", "note": "no news page or feed on the site", "payload": None,
            "items": 0, "complete": False}


def _post_events(items, day, source):
    return [_event("post:" + i["id"], "announcement", i["title"], status="announced",
                   date=i.get("date") or day, url=i["url"],
                   summary="From the company's own %s." % ("feed" if source == "feed" else
                                                          "news page")) for i in items]


def baseline_newsroom(cur, now):
    dated = [i for i in cur.get("items") or [] if _recent(i.get("date"), now)]
    return _post_events(dated[:10], now.strftime("%Y-%m-%d"), cur.get("kind"))


def compare_newsroom(prev, cur, now):
    old = {i["id"] for i in prev.get("items") or []}
    new = [i for i in cur.get("items") or [] if i["id"] not in old]
    if prev.get("source") != cur.get("source") and len(new) > 5:
        return []       # a different feed or page: its back catalogue is not news
    return _post_events(new[:MAX_EVENTS_PER_KIND], now.strftime("%Y-%m-%d"), cur.get("kind"))
