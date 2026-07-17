"""
visitor_intelligence: the real de-anonymization engine behind the platform's
Anonymous Website Visitors surface.

Multi-signal IP -> company resolution (reverse DNS + IPinfo + RDAP), connection-
type gating, calibrated confidence, Apollo firmographic + buying-committee
enrichment, and behavioural intent scoring.

Public API:
    from visitor_intelligence import resolve_visitor, resolve_ip, score_intent
"""

import os as _os

from .resolver import resolve_ip, Resolution, classify_connection, domain_from_host
from .intent import score_intent
from .pipeline import resolve_visitor, deepen_with_apollo
from .free_enrich import enrich_company_free, detect_technologies, fetch_team_page
from .identity_graph import (IdentityGraph, GraphStore, PersonMatch, sha256_email,
                            IdentityProvider, ApolloPersonProvider, CoopFileProvider)


def build_identity_graph(apollo_key=None, coop_file=None, db_path=None):
    """Assemble the identity graph with the standard provider waterfall from env:
      APOLLO_API_KEY  -> ApolloPersonProvider (email/hashed-email/name+domain)
      VI_COOP_FILE    -> CoopFileProvider (licensed graph / your own co-op CSV)
      VI_GRAPH_DB     -> SQLite path (default data/identity_graph.db)
    Providers that lack credentials are simply omitted."""
    apollo_key = apollo_key if apollo_key is not None else _os.environ.get("APOLLO_API_KEY", "")
    coop_file = coop_file if coop_file is not None else _os.environ.get("VI_COOP_FILE", "")
    providers = []
    if apollo_key:
        providers.append(ApolloPersonProvider(apollo_key))
    if coop_file and _os.path.exists(coop_file):
        providers.append(CoopFileProvider(coop_file))
    return IdentityGraph(store=GraphStore(db_path), providers=providers)


__all__ = ["resolve_visitor", "deepen_with_apollo", "resolve_ip", "Resolution",
        "classify_connection", "domain_from_host", "score_intent",
        "enrich_company_free", "detect_technologies", "fetch_team_page",
        "IdentityGraph", "GraphStore", "PersonMatch", "sha256_email",
        "IdentityProvider", "ApolloPersonProvider", "CoopFileProvider",
        "build_identity_graph"]
__version__ = "1.2.0"
