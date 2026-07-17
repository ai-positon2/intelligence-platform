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

The connection-type classification is a HARD GATE: residential ISP, mobile
carrier, and hosting/VPN/proxy IPs are marked not-identifiable, because
"resolving" them yields the carrier, not a company. This is the #1 false-positive
control and the reason honest match rates sit at ~20-40% of traffic.

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
              "cox communications", "centurylink", "telecom", "broadband",
              "fibernet", "cable", "dsl", " isp", "residential", "bell canada",
              "telus", "rogers", "sky broadband", "virgin media", "bt group",
              "deutsche telekom", "vodafone", "jio", "airtel b, ", "frontier")
_MOBILE_HINTS = ("t-mobile", "sprint", "cellular", "wireless", " mobile",
                 "mobile ", "orange s.a", "telefonica", "vivo", "claro",
                 "mtn ", "airtel")
_HOSTING_HINTS = ("amazon", "aws", "azure", "microsoft azure", "google cloud",
                  "gcp", "digitalocean", "linode", "ovh", "hetzner", "hosting",
                  "datacenter", "data center", "cloud", "vpn", "colo",
                  "leaseweb", "vultr", "cloudflare", "akamai", "fastly",
                  "oracle cloud", "server", "m247", "choopa", "contabo")
_EDU_HINTS = ("university", "college", "institute of technology", " school",
              "univ.", "univ ", ".edu", "education", "académ", "polytechnic")
_GOV_HINTS = ("government", " gov ", "ministry", "department of", "county of",
              "city of", "military", "gov.", "state of", "u.s. ", "national ")


def classify_connection(asn_org: Optional[str], domain: Optional[str],
                        rdns: Optional[str], netblock_size: Optional[int],
                        ipinfo_type: Optional[str] = None,
                        privacy: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """Return (connection_type, reasons). Prefers explicit provider fields, then
    keyword heuristics, then netblock-size fallback."""
    reasons: List[str] = []
    privacy = privacy or {}

    # 1) Explicit privacy flags from IPinfo (paid) beat everything.
    if privacy.get("hosting") or privacy.get("vpn") or privacy.get("proxy") or privacy.get("tor"):
        reasons.append("ipinfo.privacy flags hosting/vpn/proxy")
        return "hosting", reasons

    # 2) Explicit ASN type from IPinfo (paid): isp|hosting|education|government|business
    if ipinfo_type:
        t = ipinfo_type.strip().lower()
        if t in ("isp", "hosting", "education", "government", "business"):
            reasons.append("ipinfo.asn.type=%s" % t)
            return t, reasons

    hay = " ".join(x for x in [asn_org, domain, rdns] if x).lower()
    if not hay:
        reasons.append("no signals -> unknown")
        return "unknown", reasons

    if any(h in hay for h in _EDU_HINTS):
        reasons.append("keyword: education"); return "education", reasons
    if any(h in hay for h in _GOV_HINTS):
        reasons.append("keyword: government"); return "government", reasons
    if any(h in hay for h in _MOBILE_HINTS):
        reasons.append("keyword: mobile carrier"); return "mobile", reasons
    if any(h in hay for h in _HOSTING_HINTS):
        reasons.append("keyword: hosting/cloud/vpn"); return "hosting", reasons
    if any(h in hay for h in _ISP_HINTS):
        reasons.append("keyword: residential ISP"); return "isp", reasons

    # 3) Netblock-size fallback: a small named allocation is almost always a
    # single corporate tenant; a huge one is an ISP pool.
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
                 "hinet.net", "telecom", "compute.amazonaws", "bc.googleusercontent")
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
def rdap_lookup(ip: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    url = "https://rdap.org/ip/%s" % ip
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vi/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, ValueError):
        return None
    org = None
    for ent in data.get("entities", []) or []:
        roles = ent.get("roles", []) or []
        if "registrant" in roles or "administrative" in roles:
            org = _vcard_org(ent) or org
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
    return {"org": org or data.get("name"), "netblock": cidr or data.get("handle"),
            "netblock_size": size, "country": data.get("country")}


def _vcard_org(entity: Dict[str, Any]) -> Optional[str]:
    vcard = entity.get("vcardArray")
    if not vcard or len(vcard) < 2:
        return None
    for item in vcard[1]:
        if item and item[0] in ("fn", "org"):
            val = item[3]
            if isinstance(val, list):
                val = " ".join(str(v) for v in val)
            if val:
                return str(val)
    return None


# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #
_METHOD_STRENGTH = {
    "reverse_dns": 0.80,      # PTR -> corp domain
    "ipinfo_company": 0.78,   # IPinfo Company dataset (paid) direct hit
    "ipinfo_org": 0.50,       # IPinfo ASN org
    "rdap_netblock": 0.55,    # RIR registrant
}


def _score(connection_type: str, candidates: List[Tuple[str, str]],
        netblock_size: Optional[int]) -> Tuple[float, Optional[str], Optional[str], List[str], List[str]]:
    """Return (confidence, winning_domain, winning_method, methods, reasons)."""
    reasons: List[str] = []
    if connection_type in ("isp", "mobile", "hosting"):
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

    # Build candidates
    candidates: List[Tuple[str, str]] = []
    rdns_domain = domain_from_host(r.rdns)
    if rdns_domain:
        candidates.append(("reverse_dns", rdns_domain))
    if signals.get("ipinfo_company"):
        candidates.append(("ipinfo_company", signals["ipinfo_company"]))
    # IPinfo ASN org -> domain only when it looks corporate (not isp/hosting)
    if r.connection_type in ("business", "education", "government") and r.asn_org:
        guessed = _org_to_domain(r.asn_org)
        if guessed:
            candidates.append(("ipinfo_org", guessed))

    conf, win_domain, win_method, methods, score_reasons = _score(
        r.connection_type, candidates, r.netblock_size)
    r.confidence = conf
    r.domain = win_domain
    r.method = win_method
    r.methods = methods
    r.reasons.extend(score_reasons)
    r.company = r.asn_org if r.connection_type not in ("isp", "mobile", "hosting") else None
    r.identifiable = bool(win_domain) and conf > 0
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
