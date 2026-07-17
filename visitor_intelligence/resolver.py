"""
Multi-signal IP -> organization resolver for the Intelligence platform.

This is the real engine behind the "Anonymous Website Visitors" surface. It
replaces the old bare `_ip_company()` (a single IPinfo org string) with a fused,
confidence-scored resolution over several independent signals:

  1. IPinfo (IPINFO_TOKEN)  -- org, ASN, hostname(rDNS), city/country, and on
                               paid plans a `privacy` block (vpn/proxy/hosting)
                               and a `company` object. Primary signal.
  2. Reverse DNS (PTR)      -- socket.gethostbyaddr; a corp self-host PTR
                               ("mail.acme.com") is the single strongest domain
                               signal. Falls back here if IPinfo lacks hostname.
  3. RDAP (RIR)             -- ARIN/RIPE/APNIC authoritative netblock owner +
                               allocation size, via rdap.org bootstrap.

The connection-type classification is a HARD GATE. These are marked
not-identifiable because "resolving" them yields infrastructure, not the
visitor's employer:
  - residential ISP / mobile carrier (the carrier, not a lead)
  - hosting / cloud / CDN, incl. the hyperscalers by name (a hosted service/bot)
  - security proxy / SASE / secure-web-gateway / commercial VPN, e.g. Zscaler,
    Netskope, Cato -- a whole company egresses through the VENDOR's IPs, so the
    IP names the security vendor, never the company browsing
This is the #1 false-positive control and the reason honest match rates sit at
~20-40% of traffic.

Two more precision controls, added after real ISP/proxy/maintainer names leaked
to the UI as "companies":
  - Name sanitizer: RDAP exposes bookkeeping objects (maintainers like
    "*-MNT"/"*-MAINT", netnames like "MSFT", role handles). These are rejected;
    the resolver picks the real owning organisation or returns nothing.
  - Confidence floor: a company is only claimed when we're sure -- a real domain
    from reverse-DNS/IPinfo-Company, OR >=2 agreeing signals, OR a clean
    registrant org on a dedicated block. A lone org-name->domain guess does not
    qualify. Better to under-report than to mislabel and erode trust.

No third-party deps: pure stdlib (urllib, socket, ipaddress) so it drops into the
platform without touching requirements.txt. IPinfo/RDAP calls are best-effort and
time-boxed; everything degrades gracefully to "unknown" on failure.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #
@dataclass
class Resolution:
    ip: str
    domain: Optional[str] = None          # the join key for enrichment
    company: Optional[str] = None         # display org name
    asn: Optional[int] = None
    asn_org: Optional[str] = None
    rdns: Optional[str] = None
    netblock: Optional[str] = None
    netblock_size: Optional[int] = None
    connection_type: str = "unknown"      # business|education|government|isp|mobile|hosting|unknown
    country: Optional[str] = None
    city: Optional[str] = None
    is_vpn: bool = False
    is_proxy: bool = False
    is_hosting: bool = False
    identifiable: bool = False            # passed the gate + has a domain/name
    confidence: float = 0.0               # 0..1
    method: Optional[str] = None          # winning method
    methods: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Connection-type classification (the gate)
# --------------------------------------------------------------------------- #
_ISP_HINTS = ("comcast", "verizon", "spectrum", "charter", "at&t", "att-",
              "cox communications", "centurylink", "telecom", "telecommunications",
              "teleservices", "telecom services", "broadband", "broadband services",
              "fibernet", "cable", "cable network", "dsl", " isp", "isp ltd",
              "internet services", "internet service provider", "residential",
              "bell canada", "telus", "rogers", "sky broadband", "virgin media",
              "bt group", "deutsche telekom", "vodafone", "vodafone idea",
              "vi (vodafone idea)", "jio", "reliance jio", "bharti airtel",
              "airtel", "frontier communications", "frontier",
              # India-specific ISPs/telcos: the platform's own traffic is
              # India-heavy, and a naive US/EU-centric keyword list quietly
              # promotes these to "business" and hands a rep the carrier's
              # name as if it were the visitor's employer.
              "tata teleservices", "tata docomo", "tata play fiber",
              "tata communications", "vsnl", "bsnl", "mtnl", "hathway",
              "act fibernet", "atria convergence", "you broadband", "railwire",
              "excitel", "tikona", "netplus", "gtpl", "alliance broadband",
              "spectra", "den networks", "siti networks", "connect broadband",
              "ortel communications",
              # More US/Canada consumer + regional ISPs (the platform is US- and
              # India-heavy, so both markets need real coverage).
              "wideopenwest", "wow internet", "mediacom", "cable one",
              "sparklight", "windstream", "consolidated communications",
              "cincinnati bell", "grande communications", "rcn", "optimum",
              "altice", "cablevision", "suddenlink", "google fiber", "starlink",
              "hughesnet", "viasat", "shaw communications", "videotron",
              "cogeco", "beanfield", "distributel", "teksavvy", "eastlink",
              "sasktel", "xplornet",
              # Backbone/transit carriers: a company buying transit shows the
              # carrier here, not itself, so these are not a lead either.
              "cogent", "lumen", "level 3", "level3", "gtt communications",
              "zayo", "he.net", "hurricane electric", "ntt communications",
              "tata communications", "sify")
_MOBILE_HINTS = ("t-mobile", "sprint", "cellular", "wireless", " mobile",
                 "mobile ", "orange s.a", "telefonica", "vivo", "claro",
                 "mtn ", "airtel", "jio", "vodafone idea", "verizon wireless",
                 "at&t mobility", "us cellular", "boost mobile", "cricket wireless",
                 "metropcs")
_HOSTING_HINTS = ("amazon", "aws", "azure", "microsoft azure", "google cloud",
                  "gcp", "digitalocean", "linode", "ovh", "hetzner", "hosting",
                  "datacenter", "data center", "data centre", "cloud", "vpn",
                  "colo", "colocation", "leaseweb", "vultr", "cloudflare",
                  "akamai", "fastly", "oracle cloud", "server", "dedicated server",
                  "m247", "choopa", "contabo",
                  # Shared/web hosts + registrars whose IPs front thousands of
                  # unrelated small sites -- the host is never the visitor.
                  "hostpapa", "godaddy", "namecheap", "bluehost", "hostgator",
                  "dreamhost", "siteground", "ionos", "1&1", "rackspace",
                  "webair", "internet development", "web hosting", "webhosting",
                  "hosting services", "vps", "wpengine", "wp engine", "kinsta",
                  "flywheel", "digital ocean", "namesilo", "hostinger",
                  "a2 hosting", "inmotion", "liquid web", "scaleway", "upcloud",
                  "gcore", "g-core",
                  # Hyperscalers, by their own org names. Their IP space is
                  # overwhelmingly cloud tenancy (GCP/Azure/OCI/etc), so a hit
                  # is almost always a hosted service/bot, not an employee of
                  # the hyperscaler. Amazon is already covered above; treat the
                  # rest the same way, prioritising precision (never show
                  # "Google LLC" for what is really a GCP-hosted crawler).
                  "google llc", "google inc", "google, inc", "google, llc",
                  "microsoft corporation", "microsoft corp", "oracle corporation",
                  "alibaba", "aliyun", "tencent", "huawei", "yandex",
                  # India-heavy user base: common Indian hosts / data centres
                  # whose names don't all carry a telecom word.
                  "e2e networks", "ctrls", "netmagic", "esds", "web werks",
                  "bigrock", "znetlive", "hostgator india", "milesweb", "bluehost india",
                  # Dedicated-server / VPS / colo resellers verified as hosting
                  # (their org names carry no generic hosting word, so they must
                  # be named explicitly -- see the operator override below for
                  # the long tail a static list can never fully cover).
                  "aventice", "bare metal", "colocrossing", "quadranet",
                  "hivelocity", "reliablesite", "servermania", "hostkey",
                  "melbicom", "servers.com", "datacamp", "psychz", "gigenet",
                  "hostwinds", "interserver", "buyvm", "frantech", "nocix")
_EDU_HINTS = ("university", "college", "institute of technology", " school",
              "univ.", "univ ", ".edu", "education", "académ", "polytechnic")
_GOV_HINTS = ("government", " gov ", "ministry", "department of", "county of",
              "city of", "military", "gov.", "state of", "u.s. ", "national ")
# Security proxies / SASE / secure-web-gateway / commercial VPN vendors. This is
# a DISTINCT false-positive class from hosting: a company routes ALL its
# outbound traffic through these vendors' cloud, so the egress IP resolves to
# the security vendor (Zscaler, Netskope, ...), never to the company whose
# employee is actually browsing. Left ungated, every customer of Zscaler shows
# up as "Zscaler, Inc." Only unambiguous vendor names/phrases go here -- no bare
# "proxy"/"vpn"/"gateway" tokens that would false-match real company names.
_PROXY_HINTS = ("zscaler", "netskope", "cato networks", "iboss", "forcepoint",
                "menlo security", "perimeter 81", "perimeter81", "twingate",
                "tailscale", "prisma access", "cisco umbrella", "bitglass",
                "lookout cloud", "versa networks", "netfoundry", "secure web gateway",
                "sase", "zensor",
                # commercial consumer VPN egress
                "nordvpn", "expressvpn", "surfshark", "mullvad", "protonvpn",
                "private internet access", "cyberghost", "ipvanish", "windscribe",
                "hide.me", "purevpn", "tunnelbear", "vpn service", "vpn provider")
# A weaker, broader net than _ISP_HINTS: words that show up in telecom/ISP
# legal names generally (not just the specific carriers hardcoded above). Not
# strong enough on its own to assert "isp", but strong enough to veto the
# risky "small netblock -> business" inference below -- carriers routinely
# register small per-city/per-exchange sub-blocks under their own name, so a
# small allocation does not imply a single corporate tenant when the org name
# itself reads as a telecom operator our hardcoded list doesn't happen to name.
_TELECOM_SOFT_HINTS = ("tele", "telecom", "communications", "networks", "network",
                       "cellular", "wireless", "cable", "broadband", "fiber",
                       "fibre", "connectivity", "internet service")

# Operator-maintainable exclusion list. No static keyword list can ever cover
# the long tail of obscure hosting/VPN/reseller org names (e.g. "Aventice LLC",
# a dedicated-server host whose name carries no hosting word). This env var lets
# the team add such names as they're spotted, WITHOUT a code change/deploy --
# comma-separated, case-insensitive substring match. Any hit is gated exactly
# like hosting (never shown as a company).
_EXTRA_NONBUSINESS = tuple(
    s.strip().lower() for s in os.environ.get("VI_EXCLUDE_ORG_NAMES", "").split(",")
    if s.strip())


def classify_connection(asn_org: Optional[str], domain: Optional[str],
                        rdns: Optional[str], netblock_size: Optional[int],
                        ipinfo_type: Optional[str] = None,
                        privacy: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """Return (connection_type, reasons). Prefers explicit provider fields, then
    keyword heuristics, then netblock-size fallback."""
    reasons: List[str] = []
    privacy = privacy or {}

    # 0) Operator-configured exclusions win outright (the long-tail knob).
    hay0 = " ".join(x for x in [asn_org, domain, rdns] if x).lower()
    if hay0 and _EXTRA_NONBUSINESS and any(x in hay0 for x in _EXTRA_NONBUSINESS):
        reasons.append("operator exclusion (VI_EXCLUDE_ORG_NAMES)")
        return "hosting", reasons

    # 1) Explicit privacy flags from IPinfo (paid) beat everything.
    if privacy.get("hosting") or privacy.get("vpn") or privacy.get("proxy") or privacy.get("tor"):
        reasons.append("ipinfo.privacy flags hosting/vpn/proxy")
        return "hosting", reasons

    hay = " ".join(x for x in [asn_org, domain, rdns] if x).lower()

    # 2) Our own keyword evidence is checked before trusting an external
    # ipinfo.asn.type "business"/"education"/"government" claim. A regional
    # carrier mistagged upstream (or one whose ASN type data is stale/coarse)
    # would otherwise short-circuit past every other signal below and hand a
    # rep the carrier's name as if it were the visitor's employer -- so a
    # clear carrier-name match in our own list can veto that claim.
    keyword_type: Optional[str] = None
    if hay:
        # proxy/SASE first: it's the most specific and the highest-risk false
        # positive (a whole company's traffic egresses through the vendor).
        if any(h in hay for h in _PROXY_HINTS): keyword_type = "proxy"
        elif any(h in hay for h in _EDU_HINTS): keyword_type = "education"
        elif any(h in hay for h in _GOV_HINTS): keyword_type = "government"
        elif any(h in hay for h in _MOBILE_HINTS): keyword_type = "mobile"
        elif any(h in hay for h in _HOSTING_HINTS): keyword_type = "hosting"
        elif any(h in hay for h in _ISP_HINTS): keyword_type = "isp"

    if keyword_type in ("isp", "mobile", "hosting", "proxy"):
        reasons.append("keyword: %s" % keyword_type); return keyword_type, reasons

    # 3) Explicit ASN type from IPinfo (paid): isp|hosting|education|government|business
    if ipinfo_type:
        t = ipinfo_type.strip().lower()
        if t in ("isp", "hosting"):
            reasons.append("ipinfo.asn.type=%s" % t)
            return t, reasons
        if t in ("education", "government", "business"):
            reasons.append("ipinfo.asn.type=%s" % t)
            return t, reasons

    if keyword_type:
        reasons.append("keyword: %s" % keyword_type); return keyword_type, reasons

    if not hay:
        reasons.append("no signals -> unknown")
        return "unknown", reasons

    # 4) Netblock-size fallback: a small named allocation is usually a single
    # corporate tenant -- UNLESS the org name itself reads as a telecom/ISP,
    # since carriers commonly register small per-region sub-blocks under their
    # own name. A soft telecom-word match blocks the "business" inference even
    # when the hardcoded carrier list above doesn't happen to name this one.
    telecom_soft = any(h in hay for h in _TELECOM_SOFT_HINTS)
    if telecom_soft:
        reasons.append("org name reads as telecom/ISP (unrecognized carrier) -> unknown, not treated as a business")
        return "unknown", reasons
    if netblock_size is not None and netblock_size <= 65536:
        reasons.append("small named netblock (<=/16) -> business")
        return "business", reasons
    if domain and not any(h in hay for h in _ISP_HINTS + _HOSTING_HINTS):
        reasons.append("named org domain, no isp/hosting markers -> business")
        return "business", reasons
    reasons.append("insufficient evidence -> unknown")
    return "unknown", reasons


# --------------------------------------------------------------------------- #
# Reverse DNS
# --------------------------------------------------------------------------- #
_GENERIC_RDNS = ("comcast.net", "rr.com", "cox.net", "amazonaws.com",
                 "googleusercontent.com", "1e100.net", "azure.com", "spectrum.com",
                 "your-server.de", "ip-", "-dsl", ".dyn", "pool-", ".cust",
                 "broadband", "res.", "static.", "dynamic.", "cable.",
                 "t-mobile.com", "verizon.net", "charter.com", "sbcglobal",
                 "hinet.net", "telecom", "teleservices", "compute.amazonaws",
                 "bc.googleusercontent",
                 # Indian ISP/telecom PTR domains -- same false-positive class
                 # as the US-centric entries above (a carrier's own hostname,
                 # not a lead's corporate domain).
                 "ttsl.co.in", "tatateleservices", "vsnl.net.in", "vsnl.co.in",
                 "airtel.in", "airtelbroadband", "jio.com", "relianceada.com",
                 "bsnl.co.in", "bsnl.in", "hathway.com", "actcorp.in",
                 "youbroadband.co.in", "railwire.co.in", "tikona.in",
                 "gtpl.net.in", "spectra.co", "dennetworks.com",
                 "sitinetworks.com", "vodafoneidea.com", "myvi.in")
_TWO_LABEL_TLDS = {"co.uk", "com.au", "co.jp", "co.in", "com.br", "co.nz",
                   "co.za", "com.sg", "com.mx", "co.kr", "com.tr"}


def reverse_dns(ip: str, timeout: float = 1.5) -> Optional[str]:
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        host, _, _ = socket.gethostbyaddr(ip)
        return host.lower().rstrip(".")
    except (socket.herror, socket.gaierror, OSError):
        return None
    finally:
        socket.setdefaulttimeout(old)


def domain_from_host(host: Optional[str]) -> Optional[str]:
    """Extract a plausible company eTLD+1 from a PTR/hostname, or None for
    generic ISP/hosting hostnames that name the carrier, not a lead."""
    if not host:
        return None
    low = host.lower().strip()
    if any(g in low for g in _GENERIC_RDNS):
        return None
    parts = [p for p in low.split(".") if p]
    if len(parts) < 2:
        return None
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LABEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# --------------------------------------------------------------------------- #
# IPinfo (their existing token)
# --------------------------------------------------------------------------- #
def ipinfo_lookup(ip: str, token: str, timeout: float = 2.5) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    url = "https://ipinfo.io/%s/json?token=%s" % (ip, token)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vi/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, ValueError):
        return None


def _parse_ipinfo_org(org: str) -> Tuple[Optional[int], Optional[str]]:
    """'AS13335 Cloudflare, Inc.' -> (13335, 'Cloudflare, Inc.')"""
    if not org:
        return None, None
    m = re.match(r"^AS(\d+)\s+(.*)$", org.strip())
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, org.strip()


# --------------------------------------------------------------------------- #
# RDAP (authoritative RIR lookup)
# --------------------------------------------------------------------------- #
# Handle/maintainer patterns. RDAP entities include RIR bookkeeping objects
# (maintainers, netnames, role handles) that are NOT company names. Left
# unfiltered, the resolver picked whichever came last and displayed e.g.
# "RIPE-NCC-HM-MNT", "BLUEWINNET-MNT", "NS1212-mnt", "MICROSOFT-MAINT" or the
# netname "MSFT" as the visitor's "company". These filters reject them.
_MAINTAINER_RE = re.compile(
    r"(?i)(-(mnt|maint|noc|adm|admin|abuse|hm|ipadmin))$"      # RIPE/APNIC mntner suffixes
    r"|^(ripe|apnic|arin|lacnic|afrinic)\b"                    # RIR bookkeeping objects
    r"|^(net|org|as|mnt|maint|auto)-[a-z0-9-]+$"               # NET-.../ORG-.../AS-... handles
    r"|hostmaster|ip[\s-]?admin|abuse|\bnoc\b")


def _looks_like_handle(s: Optional[str]) -> bool:
    """True if `s` is an RIR handle/netname/maintainer string rather than a
    real company name. Real company names have spaces and mixed case; handles
    are single ALL-CAPS/hyphen/digit tokens or match a maintainer pattern."""
    if not s or not s.strip():
        return True
    s = s.strip()
    if _MAINTAINER_RE.search(s):
        return True
    if " " not in s:
        # single token: MSFT / ZSCALER-WAS1 / NS1212-MNT / IE-FACEBOOK-20140612
        if s.isupper():
            return True
        if "-" in s and re.search(r"\d", s):
            return True
    return False


def _clean_org_name(s: Optional[str]) -> Optional[str]:
    """Return a displayable company name, or None if `s` is a handle/netname/
    maintainer string. Final safety net before any name reaches the UI."""
    if not s:
        return None
    s = s.strip()
    return s if s and not _looks_like_handle(s) else None


def rdap_lookup(ip: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    url = "https://rdap.org/ip/%s" % ip
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vi/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, ValueError):
        return None
    org = _best_rdap_org(data)
    size = None
    start, end = data.get("startAddress"), data.get("endAddress")
    if start and end:
        try:
            size = int(ipaddress.ip_address(end)) - int(ipaddress.ip_address(start)) + 1
        except ValueError:
            pass
    cidr = None
    for c in data.get("cidr0_cidrs", []) or []:
        pfx = c.get("v4prefix") or c.get("v6prefix")
        length = c.get("length")
        if pfx and length is not None:
            cidr = "%s/%s" % (pfx, length)
            break
    # NOTE: deliberately do NOT fall back to data["name"] for org -- that field
    # is the netname ("MSFT", "IE-FACEBOOK-20140612", "ZSCALER-WAS1"), never a
    # company name. An honest empty org beats a wrong/garbage one.
    return {"org": org, "netblock": cidr or data.get("handle"),
            "netblock_size": size, "country": data.get("country")}


def _flatten_entities(entities: List[Dict[str, Any]], depth: int = 0) -> List[Dict[str, Any]]:
    """RDAP nests entities (an org can contain admin/tech sub-entities). Walk
    up to 2 levels so a real registrant nested under a maintainer is seen."""
    out: List[Dict[str, Any]] = []
    for e in entities or []:
        if isinstance(e, dict):
            out.append(e)
            if depth < 2 and e.get("entities"):
                out.extend(_flatten_entities(e["entities"], depth + 1))
    return out


def _best_rdap_org(data: Dict[str, Any]) -> Optional[str]:
    """Pick the entity most likely to be the real owning organisation, skipping
    maintainer/handle objects entirely. Ranks: registrant role, organisation
    vcard kind, an ORG-* handle, a multi-word / mixed-case (i.e. human-readable)
    name. Returns None when nothing clean remains -- which is the correct,
    honest answer for blocks that only expose bookkeeping objects."""
    best_name, best_score = None, 0
    for ent in _flatten_entities(data.get("entities", [])):
        name = _vcard_org(ent)
        if not name or _looks_like_handle(name):
            continue
        handle = ent.get("handle") or ""
        if _looks_like_handle(handle) and " " not in name:
            # a clean multi-word name under an ORG- handle is fine; but a
            # single-token name under a handle-y handle is itself suspect
            continue
        roles = [r.lower() for r in (ent.get("roles") or [])]
        score = 1
        if "registrant" in roles: score += 4
        elif "administrative" in roles: score += 1
        if _vcard_kind(ent) == "org": score += 3
        if handle.upper().startswith("ORG-"): score += 3
        if " " in name: score += 2
        if any(c.islower() for c in name) and any(c.isupper() for c in name): score += 1
        if score > best_score:
            best_score, best_name = score, name
    return best_name


def _vcard_kind(entity: Dict[str, Any]) -> Optional[str]:
    vcard = entity.get("vcardArray")
    if not vcard or len(vcard) < 2:
        return None
    for item in vcard[1]:
        if item and item[0] == "kind":
            return str(item[3]).lower() if len(item) > 3 else None
    return None


def _vcard_org(entity: Dict[str, Any]) -> Optional[str]:
    vcard = entity.get("vcardArray")
    if not vcard or len(vcard) < 2:
        return None
    # prefer an explicit 'org' field over 'fn' (fn on a person entity is a human
    # name, not the company); fall back to fn only if no org present.
    fn = None
    for item in vcard[1]:
        if not item:
            continue
        if item[0] == "org":
            val = item[3]
            if isinstance(val, list):
                val = " ".join(str(v) for v in val)
            if val:
                return str(val)
        if item[0] == "fn" and fn is None:
            val = item[3]
            if isinstance(val, list):
                val = " ".join(str(v) for v in val)
            fn = str(val) if val else None
    return fn


# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #
_METHOD_STRENGTH = {
    "reverse_dns": 0.80,      # PTR -> corp domain
    "ipinfo_company": 0.78,   # IPinfo Company dataset (paid) direct hit
    "ipinfo_org": 0.50,       # IPinfo ASN org
    "rdap_netblock": 0.55,    # RIR registrant
}
# Minimum confidence to CLAIM an identification via a domain-backed/corroborated
# match. Set so a single weak guessed-domain signal (ipinfo_org, 0.50, even +0.05
# for a small block) does NOT clear the bar on its own; a strong domain signal
# (reverse_dns 0.80, ipinfo_company 0.78) or two agreeing methods does.
_MIN_IDENTIFY_CONF = 0.6


def _score(connection_type: str, candidates: List[Tuple[str, str]],
        netblock_size: Optional[int]) -> Tuple[float, Optional[str], Optional[str], List[str], List[str]]:
    """Return (confidence, winning_domain, winning_method, methods, reasons)."""
    reasons: List[str] = []
    if connection_type in ("isp", "mobile", "hosting", "proxy"):
        reasons.append("GATE: connection_type=%s -> not identifiable" % connection_type)
        return 0.0, None, None, [], reasons
    if not candidates:
        reasons.append("no domain candidates")
        return 0.0, None, None, [], reasons

    per_domain: Dict[str, List[Tuple[str, float]]] = {}
    for method, domain in candidates:
        if not domain:
            continue
        per_domain.setdefault(domain.lower(), []).append(
            (method, _METHOD_STRENGTH.get(method, 0.3)))

    best_domain, best_conf = None, 0.0
    for domain, evs in per_domain.items():
        prob_absent = 1.0
        for _, s in evs:
            prob_absent *= (1.0 - s)
        conf = 1.0 - prob_absent
        if conf > best_conf:
            best_conf, best_domain = conf, domain

    win_evs = per_domain[best_domain]
    methods = sorted({m for m, _ in win_evs})
    reasons.append("winning domain %s (noisy-OR %.2f)" % (best_domain, best_conf))
    if len(methods) >= 2:
        best_conf = min(1.0, best_conf + 0.05 * (len(methods) - 1))
        reasons.append("corroboration: %d methods agree" % len(methods))
    if netblock_size:
        if netblock_size <= 4096:
            best_conf = min(1.0, best_conf + 0.05); reasons.append("small netblock (+)")
        elif netblock_size >= 1_048_576:
            best_conf *= 0.7; reasons.append("very large netblock (-)")
    if connection_type in ("education", "government"):
        best_conf = min(best_conf, 0.85); reasons.append("capped: org-level only")
    win_method = max(win_evs, key=lambda x: x[1])[0]
    return round(best_conf, 3), best_domain, win_method, methods, reasons


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def resolve_ip(ip: str, ipinfo_token: Optional[str] = None,
            online: bool = True) -> Resolution:
    """Resolve one IP into a confidence-scored Resolution. `online` toggles the
    live rDNS/RDAP/IPinfo calls (off => offline/testable)."""
    r = Resolution(ip=ip)
    if not ip or ip in ("127.0.0.1", "::1", "localhost", ""):
        r.reasons.append("local/empty ip")
        return r
    if ipinfo_token is None:
        ipinfo_token = os.environ.get("IPINFO_TOKEN", "")

    signals: Dict[str, Any] = {}
    ipinfo_type = None
    privacy = None

    # 1) IPinfo
    if online and ipinfo_token:
        info = ipinfo_lookup(ip, ipinfo_token)
        if info:
            signals["ipinfo"] = True
            asn, asn_org = _parse_ipinfo_org(info.get("org", ""))
            r.asn, r.asn_org = asn, asn_org
            r.country = info.get("country") or r.country
            r.city = info.get("city") or r.city
            r.rdns = (info.get("hostname") or "").lower() or r.rdns
            # paid enrichers, if present
            asnobj = info.get("asn") or {}
            if isinstance(asnobj, dict):
                ipinfo_type = asnobj.get("type")
                r.asn = r.asn or asnobj.get("asn") and _digits(asnobj.get("asn"))
                r.asn_org = r.asn_org or asnobj.get("name")
            privacy = info.get("privacy") if isinstance(info.get("privacy"), dict) else None
            companyobj = info.get("company") if isinstance(info.get("company"), dict) else None
            if companyobj and companyobj.get("domain"):
                signals["ipinfo_company"] = companyobj.get("domain")

    # 2) Reverse DNS (fallback / corroboration)
    if online and not r.rdns:
        r.rdns = reverse_dns(ip)
    signals["rdns"] = r.rdns

    # 3) RDAP (authoritative netblock + size)
    if online:
        rd = rdap_lookup(ip)
        if rd:
            signals["rdap"] = True
            r.netblock = rd.get("netblock") or r.netblock
            r.netblock_size = rd.get("netblock_size") or r.netblock_size
            r.country = r.country or rd.get("country")
            if not r.asn_org and rd.get("org"):
                r.asn_org = rd.get("org")

    # Classify (the gate)
    dom_from_asn = None  # ASN org domain only from IPinfo company/hostname, handled below
    r.connection_type, cls_reasons = classify_connection(
        r.asn_org, None, r.rdns, r.netblock_size, ipinfo_type, privacy)
    r.reasons.extend(cls_reasons)
    if privacy:
        r.is_vpn = bool(privacy.get("vpn"))
        r.is_proxy = bool(privacy.get("proxy"))
        r.is_hosting = bool(privacy.get("hosting"))
    r.is_hosting = r.is_hosting or r.connection_type == "hosting"

    # Company name: explicit allowlist (business/education/government only) AND
    # run through the handle/netname sanitizer, so a maintainer/netname string
    # ("MICROSOFT-MAINT", "MSFT", ...) never reaches the UI as a company. The
    # sanitized name is only committed to r.company once we're actually sure
    # (identifiable), decided by the policy below.
    clean_company = (_clean_org_name(r.asn_org)
                    if r.connection_type in ("business", "education", "government")
                    else None)

    # Build domain candidates (real domains only). A guessed domain from the
    # org name is a weak, secondary signal used for the enrichment join key --
    # it does NOT by itself make us "sure" who this is (see the policy below).
    candidates: List[Tuple[str, str]] = []
    rdns_domain = domain_from_host(r.rdns)
    if rdns_domain:
        candidates.append(("reverse_dns", rdns_domain))
    if signals.get("ipinfo_company"):
        candidates.append(("ipinfo_company", signals["ipinfo_company"]))
    if clean_company:
        guessed = _org_to_domain(clean_company)
        if guessed:
            candidates.append(("ipinfo_org", guessed))

    conf, win_domain, win_method, methods, score_reasons = _score(
        r.connection_type, candidates, r.netblock_size)
    r.confidence = conf
    r.domain = win_domain
    r.method = win_method
    r.methods = methods
    r.reasons.extend(score_reasons)

    # Identification policy -- "only claim a company when we're sure".
    # Three tiers of trust:
    #   1. Domain-backed: a real corporate domain from reverse-DNS PTR or the
    #      IPinfo Company dataset. Definitive; identify.
    #   2. Corroborated: >=2 independent methods agree on the same domain.
    #   3. Registrant-backed: a clean (non-handle, non-ISP/host/proxy) RDAP/ASN
    #      registrant org on a DEDICATED (small) block. The NAME is trustworthy
    #      (that block belongs to that org); we identify off the name even
    #      though the guessed domain is only best-effort.
    # A single guessed-domain signal (org-name -> domain) on a non-dedicated
    # block is NOT enough -- that is exactly the weak match that surfaced
    # "Zscaler, Inc." and friends, and it erodes trust in the whole feature.
    r.identifiable = False
    if r.connection_type in ("business", "education", "government") and win_domain:
        strong_domain = ("reverse_dns" in methods) or ("ipinfo_company" in methods)
        corroborated = len(methods) >= 2
        dedicated_block = (r.netblock_size is not None and r.netblock_size <= 65536)
        if (strong_domain or corroborated) and conf >= _MIN_IDENTIFY_CONF:
            r.identifiable = True
        elif clean_company and dedicated_block:
            r.identifiable = True
            r.confidence = max(conf, 0.6)
            if "rdap_registrant" not in r.methods:
                r.methods = sorted(set(r.methods + ["rdap_registrant"]))
            r.method = r.method or "rdap_registrant"
            r.reasons.append("registrant-backed: clean org on a dedicated block")
        else:
            r.reasons.append("weak single guessed-domain signal on a "
                            "non-dedicated block -> not sure, left unidentified")
    # Only surface a company name when we actually identified one. This keeps
    # r.company and r.identifiable in lockstep, so no consumer can read a name
    # we weren't sure enough to stand behind.
    r.company = clean_company if r.identifiable else None
    r.signals = signals
    return r


def _digits(s: Any) -> Optional[int]:
    m = re.search(r"(\d+)", str(s or ""))
    return int(m.group(1)) if m else None


_ORG_SUFFIXES = re.compile(
    r"\b(inc|inc\.|llc|ltd|ltd\.|corp|corporation|co|company|gmbh|s\.a|sa|plc|"
    r"limited|technologies|technology|holdings|group|the)\b", re.I)


def _org_to_domain(org: str) -> Optional[str]:
    """Best-effort org name -> domain guess ('Stripe, Inc.' -> 'stripe.com').
    Only used as a weak candidate; Apollo enrichment corrects it downstream."""
    if not org:
        return None
    name = _ORG_SUFFIXES.sub("", org.lower())
    name = re.sub(r"[^a-z0-9]+", "", name)
    if len(name) < 2:
        return None
    return name + ".com"
