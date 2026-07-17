"""
Persistent identity graph + person resolution.

This is the machine that turns an anonymous browser (a `p2_vid` cookie) into a
named person. The matching logic is the easy part; the honest constraint is that
resolving a *cold* anonymous cookie to a stranger requires an external identity
graph you either license or build as a publisher co-op. So this module is built
in two clean halves:

  1. The graph + resolver (fully ours, works today). Nodes are identifiers
     (vid / email / email_sha256 / crm_id / ip / device); edges are observed
     co-occurrences. Deterministic edges (a login, a form fill, a hashed-email
     match) are auto-merged via union-find into one person cluster; probabilistic
     edges (shared IP+time) are kept as weak links, NOT auto-merged, to avoid the
     classic "one bad edge fuses two people" failure.

  2. Providers (pluggable). resolve_person() runs a waterfall:
        a. first-party deterministic cluster (login / form)         [today]
        b. Apollo person enrichment on any known email in cluster    [today]
        c. an external IdentityProvider, if configured               [plug]
     (a) and (b) need data you already own. (c) is the slot for a licensed
     graph or your own co-op feed (CoopFileProvider ingests a hashed-email ->
     identity CSV). Nothing here invents a person: no provider, no email, no
     match => no person, with the reason recorded.

Storage is SQLite (stdlib), path from VI_GRAPH_DB (default data/identity_graph.db),
consistent with the platform's other sqlite stores. Swap for Postgres by
reimplementing GraphStore with the same methods.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_email(email: str) -> str:
    return hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass
class PersonMatch:
    resolved: bool = False
    full_name: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    linkedin_url: Optional[str] = None
    confidence: float = 0.0
    method: Optional[str] = None          # first_party|apollo|provider:<name>
    lawful_basis: Optional[str] = None    # first_party|crm|coop|provider
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Provider interface (the plug for external graphs / co-ops)
# --------------------------------------------------------------------------- #
class IdentityProvider:
    name = "base"
    def resolve(self, signals: Dict[str, Any]) -> Optional[PersonMatch]:
        """signals may include: vid, email, hashed_email, ip, device, domain,
        name. Return a PersonMatch or None. MUST NOT fabricate."""
        raise NotImplementedError


class ApolloPersonProvider(IdentityProvider):
    """Resolves a person from an email / hashed_email / (name+domain) via Apollo
    people match. This is deterministic-ish and uses data you own (a captured
    email) or a hash you were given. Costs 1 Apollo credit per successful match."""
    name = "apollo"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def resolve(self, signals: Dict[str, Any]) -> Optional[PersonMatch]:
        if not self.api_key:
            return None
        email = signals.get("email")
        hashed = signals.get("hashed_email")
        name = signals.get("name")
        domain = signals.get("domain")
        if not (email or hashed or (name and domain)):
            return None
        try:
            from .enrich import enrich_person
        except Exception:
            return None
        p = enrich_person(self.api_key, email=email, hashed_email=hashed,
                        name=name, domain=domain)
        if not p:
            return None
        return PersonMatch(
            resolved=True, full_name=p.get("full_name"), email=p.get("email") or email,
            title=p.get("title"), company=p.get("company"),
            linkedin_url=p.get("linkedin_url"), confidence=0.9,
            method="apollo", lawful_basis="first_party" if email else "coop",
            reasons=["apollo people match"])


class CoopFileProvider(IdentityProvider):
    """Reference third-party graph: a hashed-email -> identity map you license or
    build as a co-op, loaded from a CSV/JSON file. This is exactly the shape a
    real co-op feed takes; drop a real file in and cold hashed-email resolution
    works. Columns/keys: hashed_email, full_name, email, title, company,
    linkedin_url."""
    name = "coop_file"

    def __init__(self, path: str):
        self.path = path
        self._map: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            if self.path.endswith(".json"):
                with open(self.path, encoding="utf-8") as f:
                    rows = json.load(f)
            else:
                import csv
                with open(self.path, newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
            for r in rows:
                he = (r.get("hashed_email") or "").strip().lower()
                if he:
                    self._map[he] = r
        except Exception:
            pass

    def resolve(self, signals: Dict[str, Any]) -> Optional[PersonMatch]:
        he = signals.get("hashed_email")
        if not he and signals.get("email"):
            he = sha256_email(signals["email"])
        if not he:
            return None
        r = self._map.get(he.lower())
        if not r:
            return None
        return PersonMatch(
            resolved=True, full_name=r.get("full_name"), email=r.get("email"),
            title=r.get("title"), company=r.get("company"),
            linkedin_url=r.get("linkedin_url"), confidence=0.95,
            method="provider:coop_file", lawful_basis="coop",
            reasons=["hashed-email co-op match"])


# --------------------------------------------------------------------------- #
# Graph store (SQLite)
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    ident TEXT PRIMARY KEY,       -- e.g. 'vid:abc', 'email:a@b.com', 'hem:<sha>'
    kind  TEXT,                   -- vid|email|email_sha256|crm_id|ip|device|person
    attrs TEXT,                   -- json (name/title/company/... for identity nodes)
    first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT, dst TEXT,
    kind TEXT,                    -- deterministic|co_occurrence
    confidence REAL, source TEXT,
    first_seen TEXT, last_seen TEXT, observations INTEGER DEFAULT 1,
    PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
"""


class GraphStore:
    def __init__(self, path: Optional[str] = None):
        path = path or os.environ.get("VI_GRAPH_DB", "data/identity_graph.db")
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # timeout>0 + WAL so the 2 gunicorn workers can share the file without
        # "database is locked" under concurrent reads/writes.
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def upsert_node(self, ident: str, kind: str, attrs: Optional[Dict] = None) -> None:
        now = _now()
        with self._lock:
            row = self.conn.execute("SELECT attrs FROM nodes WHERE ident=?", (ident,)).fetchone()
            merged = {}
            if row and row["attrs"]:
                try:
                    merged = json.loads(row["attrs"])
                except Exception:
                    merged = {}
            if attrs:
                merged.update({k: v for k, v in attrs.items() if v})
            if row:
                self.conn.execute("UPDATE nodes SET last_seen=?, attrs=? WHERE ident=?",
                                (now, json.dumps(merged), ident))
            else:
                self.conn.execute(
                    "INSERT INTO nodes (ident,kind,attrs,first_seen,last_seen) VALUES (?,?,?,?,?)",
                    (ident, kind, json.dumps(merged), now, now))
            self.conn.commit()

    def add_edge(self, src: str, dst: str, kind: str, confidence: float,
                source: str) -> None:
        now = _now()
        with self._lock:
            self.conn.execute(
                """INSERT INTO edges (src,dst,kind,confidence,source,first_seen,last_seen,observations)
                   VALUES (?,?,?,?,?,?,?,1)
                   ON CONFLICT(src,dst,kind) DO UPDATE SET
                     last_seen=excluded.last_seen,
                     observations=edges.observations+1,
                     confidence=MAX(edges.confidence, excluded.confidence)""",
                (src, dst, kind, confidence, source, now, now))
            self.conn.commit()

    def neighbors(self, ident: str, kinds: Optional[List[str]] = None) -> List[sqlite3.Row]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM edges WHERE src=? OR dst=?", (ident, ident)).fetchall()
        out = []
        for r in rows:
            if kinds and r["kind"] not in kinds:
                continue
            out.append(r)
        return out

    def get_node(self, ident: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM nodes WHERE ident=?", (ident,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["attrs"] = json.loads(d.get("attrs") or "{}")
        except Exception:
            d["attrs"] = {}
        return d

    def stats(self) -> Dict[str, int]:
        with self._lock:
            n = self.conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
            e = self.conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
            p = self.conn.execute(
                "SELECT COUNT(*) c FROM nodes WHERE kind IN ('email','crm_id')").fetchone()["c"]
        return {"nodes": n, "edges": e, "person_anchors": p}


# --------------------------------------------------------------------------- #
# The identity graph API
# --------------------------------------------------------------------------- #
class IdentityGraph:
    def __init__(self, store: Optional[GraphStore] = None,
                providers: Optional[List[IdentityProvider]] = None):
        self.store = store or GraphStore()
        self.providers = providers or []

    # ---- ingestion --------------------------------------------------------- #
    def observe(self, vid: str, ip: Optional[str] = None,
                device: Optional[str] = None) -> None:
        """Record an anonymous session touch. Builds weak co-occurrence edges
        (never auto-merged) so probabilistic signals are available but safe."""
        if not vid:
            return
        vnode = "vid:%s" % vid
        self.store.upsert_node(vnode, "vid")
        if ip:
            self.store.upsert_node("ip:%s" % ip, "ip")
            self.store.add_edge(vnode, "ip:%s" % ip, "co_occurrence", 0.3, "tag")
        if device:
            self.store.upsert_node("device:%s" % device, "device")
            self.store.add_edge(vnode, "device:%s" % device, "co_occurrence", 0.4, "tag")

    def identify(self, vid: str, email: Optional[str] = None,
                name: Optional[str] = None, title: Optional[str] = None,
                company: Optional[str] = None, crm_id: Optional[str] = None,
                source: str = "first_party") -> None:
        """A DETERMINISTIC anchor: the visitor told us who they are (login, form,
        provider webhook). Links vid <-> person identifiers with a strong edge,
        which retro-stitches every prior anonymous session of this vid."""
        if not vid or not (email or crm_id or name):
            return
        vnode = "vid:%s" % vid
        self.store.upsert_node(vnode, "vid")
        attrs = {"full_name": name, "title": title, "company": company,
                "email": email, "source": source}
        if email:
            enode = "email:%s" % email.strip().lower()
            self.store.upsert_node(enode, "email", attrs)
            self.store.upsert_node("hem:%s" % sha256_email(email), "email_sha256",
                                {"email": email.strip().lower()})
            self.store.add_edge(vnode, enode, "deterministic", 1.0, source)
        if crm_id:
            cnode = "crm_id:%s" % crm_id
            self.store.upsert_node(cnode, "crm_id", attrs)
            self.store.add_edge(vnode, cnode, "deterministic", 1.0, source)
        if name and not email and not crm_id:
            # a name alone is weak; store on the vid node, do not create a person anchor
            self.store.upsert_node(vnode, "vid", {"observed_name": name})

    # ---- resolution -------------------------------------------------------- #
    def _deterministic_cluster(self, vid: str) -> List[Dict[str, Any]]:
        """Union-find over deterministic edges only, starting from the vid.
        Returns the identity nodes (email/crm_id) in the vid's person cluster."""
        start = "vid:%s" % vid
        seen = set([start])
        frontier = [start]
        identity_nodes = []
        while frontier:
            cur = frontier.pop()
            for e in self.store.neighbors(cur, kinds=["deterministic"]):
                for other in (e["src"], e["dst"]):
                    if other not in seen:
                        seen.add(other)
                        frontier.append(other)
                        if other.startswith(("email:", "crm_id:")):
                            node = self.store.get_node(other)
                            if node:
                                identity_nodes.append(node)
        return identity_nodes

    def resolve_person(self, vid: str, extra_signals: Optional[Dict] = None
                    ) -> PersonMatch:
        """Waterfall: first-party deterministic cluster -> Apollo -> providers.
        Never fabricates: returns resolved=False with a reason when nothing hits."""
        pm = PersonMatch()
        signals = dict(extra_signals or {})
        signals.setdefault("vid", vid)

        # (a) first-party deterministic cluster
        cluster = self._deterministic_cluster(vid) if vid else []
        if cluster:
            # survivorship: most recently seen identity node wins
            best = sorted(cluster, key=lambda n: n.get("last_seen") or "", reverse=True)[0]
            a = best.get("attrs", {})
            pm = PersonMatch(
                resolved=True, full_name=a.get("full_name"),
                email=a.get("email") or (best["ident"].split("email:", 1)[-1]
                                        if best["ident"].startswith("email:") else None),
                title=a.get("title"), company=a.get("company"),
                confidence=1.0, method="first_party",
                lawful_basis=a.get("source") or "first_party",
                reasons=["deterministic cluster from login/form"])
            # enrich thin first-party record via Apollo if we have a key + email
            if pm.email and (not pm.title or not pm.full_name):
                signals["email"] = pm.email
                for p in self.providers:
                    if p.name == "apollo":
                        got = p.resolve(signals)
                        if got and got.resolved:
                            pm.full_name = pm.full_name or got.full_name
                            pm.title = pm.title or got.title
                            pm.company = pm.company or got.company
                            pm.linkedin_url = got.linkedin_url
                            pm.reasons.append("apollo enriched")
                        break
            return pm

        # feed cluster emails into signals for providers (none here, but general)
        # (b)+(c) providers waterfall (Apollo person match, then external graphs)
        for p in self.providers:
            got = p.resolve(signals)
            if got and got.resolved:
                got.reasons.insert(0, "resolved via %s" % p.name)
                return got

        pm.reasons.append("no deterministic anchor, no provider hit -> anonymous")
        return pm
