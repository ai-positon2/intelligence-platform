"""Paid Marketing Agents: Unknown Domains Alert, the logic.

Pinned here: which host an address has (and the ones with none to judge: placeholders, Facebook, phone numbers, bare IPs); what a person's typed domain
becomes; a subdomain covered by its parent's entry but not the reverse; two-part public suffixes; who an entry is for; the three verdicts (fine, wrong
account, unknown); look-alikes (a letter off, swapped glyphs, short names left alone); severities; a redirect through a tracking template; the takeover
burst; the fingerprint (a page change on an approved domain is nothing, a change of domain is new); and the words of the alert. Nothing here talks to
a network.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_domains as d  # noqa: E402

A = {"id": "1112223330", "name": "Acme Garden", "platform": "google", "group": "mcc-1"}
B = {"id": "4445556660", "name": "Bolt Dental", "platform": "google", "group": "mcc-2"}
M = {"id": "act_77", "name": "Acme Garden", "platform": "meta", "group": ""}
GROUPS = [{"id": "mcc-1", "name": "Acme", "accountIds": [A["id"]]}, {"id": "mcc-2", "name": "Bolt", "accountIds": [B["id"]]}]


def sight(account, url, where="ad", oid="1", redirect=False, host=d.host_of):
    return {"platform": account["platform"], "accountId": account["id"], "accountName": account["name"], "where": where, "objectId": oid,
            "objectName": "Ad " + oid, "campaign": "Camp", "adGroup": "Group", "url": url, "host": host(url), "redirect": redirect}


def test_hosts_of_addresses():
    assert d.host_of("https://www.Acme-Garden.com/path?x=1") == "www.acme-garden.com"
    assert d.host_of("acme-garden.com/shop") == "acme-garden.com"
    assert d.host_of("https://shop.acme.co.uk:8080/") == "shop.acme.co.uk"
    assert d.host_of("{lpurl}?gclid=1") is None
    assert d.host_of("https://{unescapedlpurl}") is None
    assert d.host_of("tel:+15550100") is None
    assert d.host_of("https://192.168.0.1/x") is None
    assert d.host_of("") is None and d.host_of(None) is None
    assert d.host_of("https://münchen.example/") == "xn--mnchen-3ya.example"


def test_template_host_only_when_it_starts_elsewhere():
    assert d.host_of_template("{lpurl}?gclid={gclid}") is None
    assert d.host_of_template("https://track.example.net/r?u={lpurl}&c={campaignid}") == "track.example.net"
    assert d.host_of_template("") is None


def test_ignored_hosts_are_never_judged():
    for h in ("facebook.com", "www.facebook.com", "l.instagram.com", "wa.me", "play.google.com"):
        assert d.is_ignored(h)
    assert not d.is_ignored("notfacebook.com") and not d.is_ignored("acme-garden.com")


def test_registrable_domain():
    assert d.registrable("shop.example.com") == "example.com"
    assert d.registrable("shop.example.co.uk") == "example.co.uk"
    assert d.registrable("example.com") == "example.com"


def test_clean_domain_accepts_what_people_type():
    assert d.clean_domain("https://www.Acme-Garden.com/") == "acme-garden.com"
    assert d.clean_domain("  shop.acme-garden.com ") == "shop.acme-garden.com"
    assert d.clean_domain("acme garden") is None
    assert d.clean_domain("localhost") is None
    assert d.clean_domain("") is None


def test_an_entry_covers_subdomains_not_the_other_way():
    assert d.covers("acme-garden.com", "www.acme-garden.com") and d.covers("acme-garden.com", "acme-garden.com")
    assert not d.covers("shop.acme-garden.com", "acme-garden.com")
    assert not d.covers("acme-garden.com", "notacme-garden.com")


def test_scopes():
    accounts = [A, B, M]
    assert d.clean_scope("", accounts, GROUPS) == "*" and d.clean_scope("everyone", accounts, GROUPS) == "*"
    assert d.clean_scope("111-222-3330", accounts, GROUPS) == A["id"]
    assert d.clean_scope("mcc-2", accounts, GROUPS) == "mcc-2"
    assert d.clean_scope("ACT_77", accounts, GROUPS) == "act_77"
    assert d.clean_scope("999-999-9999", accounts, GROUPS) is None
    assert d.applies("*", A) and d.applies(A["id"], A) and d.applies("mcc-1", A)
    assert not d.applies("mcc-2", A) and not d.applies(B["id"], A) and not d.applies("mcc-1", M)


def test_verdicts():
    entries = [{"domain": "acme-garden.com", "scope": "mcc-1"}, {"domain": "bolt-dental.com", "scope": B["id"]}, {"domain": "linktree.example", "scope": "*"}]
    assert d.judge("www.acme-garden.com", A, entries) == ("ok", None)
    assert d.judge("linktree.example", B, entries) == ("ok", None)
    assert d.judge("bolt-dental.com", A, entries) == ("wrong_account", [B["id"]])
    assert d.judge("evil.example", A, entries) == ("unknown", None)


def test_lookalikes():
    known = ["acme-garden.com", "bolt-dental.com"]
    assert d.lookalike("acme-gardn.com", known) == "acme-garden.com"          # a letter off
    assert d.lookalike("www.acme-garden.net", known) == "acme-garden.com"     # same name, other ending
    assert d.lookalike("bolt-denta1.com", known) == "bolt-dental.com"         # 1 for l
    assert d.lookalike("rnodern-roofing.com", ["modern-roofing.com"]) == "modern-roofing.com"   # rn for m
    assert d.lookalike("acme-garden.com", known) is None                      # the same domain is no look-alike
    assert d.lookalike("zebra-fitness.com", known) is None
    assert d.lookalike("abc.com", ["abd.com"]) is None                        # short names are not compared


def test_assess_kinds_and_severity():
    entries = [{"domain": "acme-garden.com", "scope": "mcc-1"}, {"domain": "bolt-dental.com", "scope": "mcc-2"}]
    s = [sight(A, "https://acme-garden.com/a", oid="1"), sight(A, "https://bolt-dental.com/", oid="2"), sight(A, "https://something-else.org/", oid="3"),
         sight(A, "https://acme-gardn.com/", oid="4"), sight(A, "https://facebook.com/acme", oid="5"), sight(A, "{lpurl}", oid="6")]
    out = {f["objectId"]: f for f in d.assess(s, entries, [A, B], GROUPS)}
    assert set(out) == {"2", "3", "4"}
    assert out["2"]["kind"] == "wrong_account" and out["2"]["severity"] == "medium" and out["2"]["owners"] == ["Bolt"]
    assert "Bolt" in out["2"]["why"]
    assert out["3"]["kind"] == "unknown" and out["3"]["severity"] == "high" and out["3"]["lookalike"] is None
    assert out["4"]["severity"] == "critical" and "acme-garden.com" in out["4"]["why"]
    assert [f["severity"] for f in d.assess(s, entries, [A, B], GROUPS)] == ["critical", "high", "medium"]


def test_a_redirect_is_critical_and_says_so():
    s = [sight(A, "https://track.evil.example/r?u={lpurl}", where="tracking template (ad)", redirect=True, host=lambda u: "track.evil.example")]
    f = d.assess(s, [{"domain": "acme-garden.com", "scope": "*"}], [A], GROUPS)[0]
    assert f["severity"] == "critical" and f["redirect"] and f["why"].startswith("The tracking template sends people through this host")


def test_unknown_account_is_skipped_and_one_finding_per_place():
    s = [sight(A, "https://x.example/a"), sight(A, "https://x.example/b"), sight({"id": "999", "name": "?", "platform": "google", "group": ""}, "https://y.example/")]
    out = d.assess(s, [], [A], GROUPS)
    assert len(out) == 1                                                       # same ad and host twice is one finding; an account we do not list is not judged


def test_fingerprint_follows_the_host_not_the_page():
    a, b = sight(A, "https://x.example/one"), sight(A, "https://x.example/two")
    c, e = sight(A, "https://y.example/one"), sight(B, "https://x.example/one")
    assert d.fingerprint(a) == d.fingerprint(b)
    assert len({d.fingerprint(a), d.fingerprint(c), d.fingerprint(e)}) == 3


def test_takeover_needs_a_burst_of_distinct_unknown_domains():
    s = [sight(A, "https://one.example/", oid="1"), sight(A, "https://two.example/", oid="2"), sight(A, "https://three.example/", oid="3"),
         sight(B, "https://four.example/", oid="4"), sight(B, "https://five.example/", oid="5")]
    new = d.assess(s, [], [A, B], GROUPS)
    t = d.takeovers(new)
    assert len(t) == 1 and t[0]["accountId"] == A["id"] and len(t[0]["domains"]) == 3
    same = [sight(A, "https://one.example/p%d" % i, oid=str(i)) for i in range(5)]
    assert d.takeovers(d.assess(same, [], [A], GROUPS)) == []                   # five ads, one domain, is a mistake not a burst


def test_in_use_counts_domains_per_account():
    s = [sight(A, "https://shop.acme-garden.com/", oid="1"), sight(A, "https://acme-garden.com/", oid="2"), sight(A, "https://facebook.com/x", oid="3"), sight(B, "https://b.example/")]
    assert d.in_use(s) == {A["id"]: {"acme-garden.com": 2}, B["id"]: {"b.example": 1}}


def test_the_alert_words():
    s = [sight(A, "https://one.example/", oid="1"), sight(A, "https://two.example/", oid="2"), sight(A, "https://three.example/", oid="3")]
    f = d.assess(s, [], [A], GROUPS)
    plain = d.digest(f, "https://p.example/page")
    assert plain["subject"].startswith("3 ads point to a domain that is not approved (1 account)")
    hit = d.digest(f, "https://p.example/page", d.takeovers(f))
    assert hit["subject"].startswith("POSSIBLE ACCOUNT TAKEOVER: ")
    assert "Acme Garden has 3 new unknown domains" in hit["text"] and "https://p.example/page" in hit["text"]
    assert hit["slack_blocks"][0]["type"] == "header" and any(":rotating_light:" in b["text"]["text"] for b in hit["slack_blocks"] if b["type"] == "section")
    one = d.digest(f[:1], "https://p.example/page")
    assert one["subject"].startswith("1 ad points to")
    assert "—" not in hit["text"] and "—" not in hit["html"]


def test_the_alert_escapes_what_it_shows_and_caps_the_list():
    many = [sight(A, "https://x%d.example/?a=<b>" % i, oid=str(i)) for i in range(20)]
    f = d.assess(many, [], [A], GROUPS)
    m = d.digest(f, "https://p.example/")
    assert "...and 8 more" in m["text"] and "&lt;b&gt;" in m["html"]
    assert len(m["slack_blocks"]) <= 49
