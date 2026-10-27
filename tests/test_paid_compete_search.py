"""Competitor Analysis: finding a competitor from its name (tracker/paid_compete_search.py). No network."""
import os
import sys

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_compete_search as se  # noqa: E402

TANDEM = {"id": "1", "name": "Tandem Diabetes Care", "primary_domain": "tandemdiabetes.com", "logo_url": "https://logo.example/tandem.png", "industry": "medical devices", "city": "San Diego", "state": "California",
          "country": "United States", "short_description": "Insulin pumps.", "facebook_url": "https://www.facebook.com/TandemDiabetes"}
INSULET = {"id": "2", "name": "Insulet", "primary_domain": "insulet.com", "industry": "medical devices", "city": "Acton", "state": "Massachusetts", "country": "United States"}
OTHER = {"id": "3", "name": "Tandem Bank", "primary_domain": "tandem.co.uk", "country": "United Kingdom"}
NOSITE = {"id": "4", "name": "Ghost Co", "industry": "x"}


def fake(*orgs):
    def run(q):
        return list(orgs)
    return run


def test_a_name_gives_the_companies_with_a_similar_name_and_what_is_needed_to_pick_one():
    out = se.search("tandem", search_fn=fake(TANDEM, OTHER, NOSITE))
    assert out["error"] is None and [c["name"] for c in out["companies"]] == ["Tandem Diabetes Care", "Tandem Bank"]                  # a company with nowhere to look is not offered
    c = out["companies"][0]
    assert c == {"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com", "website": "https://tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "fbSource": "apollo", "logo": "https://logo.example/tandem.png",
                 "industry": "medical devices", "location": "San Diego, California, United States", "description": "Insulin pumps.", "added": False}
    assert out["companies"][1]["facebook"] == "" and out["companies"][1]["fbSource"] == "" and out["companies"][1]["logo"] == ""


def test_names_that_match_equally_keep_the_order_the_search_gave():
    assert [c["name"] for c in se.search("tandem", search_fn=fake(OTHER, TANDEM))["companies"]] == ["Tandem Bank", "Tandem Diabetes Care"]


def test_an_exact_name_comes_first_then_one_that_starts_with_it():
    out = se.search("Insulet", search_fn=fake({"name": "Insulet Europe", "primary_domain": "insulet.eu"}, {"name": "The Insulet Group", "primary_domain": "group.com"}, INSULET))
    assert [c["name"] for c in out["companies"]] == ["Insulet", "Insulet Europe", "The Insulet Group"]


def test_a_company_already_among_the_account_competitors_says_so():
    saved = [{"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com", "facebook": "", "googleAdvertiser": ""}]
    out = se.search("tandem", saved=saved, search_fn=fake(TANDEM, INSULET))
    assert {c["name"]: c["added"] for c in out["companies"]} == {"Tandem Diabetes Care": True, "Insulet": False}
    renamed = se.search("tandem", saved=[{"name": "tandem diabetes care", "domain": "other.com", "facebook": "", "googleAdvertiser": ""}], search_fn=fake(TANDEM))
    assert renamed["companies"][0]["added"] is True


def test_the_same_company_twice_is_shown_once_and_at_most_eight_are_shown():
    out = se.search("tandem", search_fn=fake(TANDEM, dict(TANDEM, id="9", name="Tandem Diabetes Care Inc")))
    assert len(out["companies"]) == 1
    many = [{"name": "Co %d" % i, "primary_domain": "co%d.com" % i} for i in range(12)]
    assert len(se.search("co", search_fn=fake(*many))["companies"]) == 8


def test_a_short_or_empty_search_asks_nothing():
    def boom(q):
        raise AssertionError("asked")
    for q in ("", " ", "a", None):
        assert se.search(q, search_fn=boom) == {"companies": [], "error": None}


def test_a_website_typed_is_offered_as_a_company_of_its_own_before_the_names():
    out = se.search("https://www.Omnipod.com/pumps", search_fn=fake({"name": "Omnipod", "primary_domain": "omnipod.com", "industry": "medical devices"}))
    assert out["companies"][0]["name"] == "Omnipod" and len(out["companies"]) == 1 and out["companies"][0]["industry"] == "medical devices"        # the same company is not listed twice
    typed = se.search("new-rival.com", search_fn=fake())
    assert [(c["name"], c["domain"], c["description"]) for c in typed["companies"]] == [("New Rival", "new-rival.com", "The website you typed.")]
    assert se.looks_like_site("tandemdiabetes.com") and se.looks_like_site("https://x.com/a") and not se.looks_like_site("tandem diabetes") and not se.looks_like_site("tandem") and not se.looks_like_site("")


def test_a_search_that_fails_says_why_and_a_website_typed_still_works():
    def down(q):
        raise requests.Timeout("slow")
    out = se.search("new-rival.com", search_fn=down)
    assert out["error"]["kind"] == "timeout" and out["error"]["retryable"] is True and [c["domain"] for c in out["companies"]] == ["new-rival.com"]
    def rejected(q):
        r = requests.Response()
        r.status_code = 401
        raise requests.HTTPError(response=r)
    err = se.search("tandem", search_fn=rejected)["error"]
    assert err["kind"] == "http_status" and "renewed" in err["message"] and err["retryable"] is False
    assert se.search("tandem", search_fn=lambda q: (_ for _ in ()).throw(ValueError("odd")))["error"]["kind"] == "unparsable"


def test_without_the_company_search_key_it_says_so_and_a_website_still_works(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    out = se.search("tandem")
    assert out["companies"] == [] and out["error"]["kind"] == "not_configured" and "APOLLO_API_KEY" in out["error"]["message"] and out["error"]["retryable"] is False
    assert [c["domain"] for c in se.search("tandemdiabetes.com")["companies"]] == ["tandemdiabetes.com"]


def test_the_search_is_made_with_the_apollo_company_search_and_the_name(monkeypatch):
    from tracker import apollo_client
    seen = {}

    def fake_search(filters, key, page=1, per_page=25, strict=False, meta=None):
        seen.update(filters=filters, key=key, per_page=per_page, strict=strict)
        return [TANDEM]
    monkeypatch.setattr(apollo_client, "search_companies", fake_search)
    monkeypatch.setenv("APOLLO_API_KEY", "k123")
    out = se.search("tandem")
    assert seen == {"filters": {"name": "tandem", "max_companies": 8}, "key": "k123", "per_page": 8, "strict": True} and out["companies"][0]["name"] == "Tandem Diabetes Care"


# ── the Facebook page ───────────────────────────────────────────────────────

def ask(**got):
    calls = []

    def run(name, domain):
        calls.append((name, domain))
        return got
    run.calls = calls
    return run


def test_a_page_the_answer_is_sure_of_is_kept_as_the_address_the_ad_library_is_asked_for():
    a = ask(profile_url="https://m.facebook.com/TandemDiabetes/", page_name="Tandem Diabetes Care", confidence="high", reasoning="Its site links to it.")
    out = se.find_facebook("Tandem", "tandemdiabetes.com", ask_fn=a)
    assert out["facebook"] == "https://www.facebook.com/TandemDiabetes" and out["confidence"] == "high" and out["pageName"] == "Tandem Diabetes Care" and out["error"] == "" and a.calls == [("Tandem", "tandemdiabetes.com")]
    assert se.find_facebook("T", "t.com", ask_fn=ask(profile_url="https://www.facebook.com/TandemDiabetes", confidence="medium"))["facebook"] == "https://www.facebook.com/TandemDiabetes"


@pytest.mark.parametrize("conf", ["low", "none", "", "certain", None])
def test_a_page_the_answer_is_not_sure_of_is_never_kept(conf):
    out = se.find_facebook("T", "t.com", ask_fn=ask(profile_url="https://www.facebook.com/TandemDiabetes", confidence=conf, reasoning="a fan page"))
    assert out["facebook"] == "" and out["reasoning"] == "a fan page"


def test_an_answer_that_is_not_a_facebook_page_is_not_kept_even_when_sure():
    for bad in ("https://example.com/tandem", "tandemdiabetes.com", "https://www.facebook.com/login", None, ""):
        out = se.find_facebook("T", "t.com", ask_fn=ask(profile_url=bad, confidence="high"))
        assert out["facebook"] == "" and out["confidence"] == "none" and "not a Facebook page address" in out["reasoning"]


def test_a_search_that_could_not_be_made_is_said_and_never_read_as_no_page():
    out = se.find_facebook("T", "t.com", ask_fn=ask(error="ANTHROPIC_API_KEY is not configured on this deployment."))
    assert out["facebook"] == "" and out["error"] == "ANTHROPIC_API_KEY is not configured on this deployment." and out["confidence"] == "none"
    assert se.find_facebook("T", "t.com", ask_fn=lambda n, d: None)["facebook"] == ""
    both = se.find_facebook("T", "t.com", ask_fn=ask(error="rate limited", profile_url="https://www.facebook.com/TandemDiabetes", confidence="high"))
    assert both["facebook"] == "" and both["confidence"] == "none" and both["error"] == "rate limited"           # an error wins over whatever else came with it


def test_claude_is_asked_once_with_the_name_and_website_and_the_answer_is_read_from_all_the_text_blocks(monkeypatch):
    class Block:
        def __init__(self, text):
            self.type, self.text = "text", text

    class Stream:
        def __init__(self, calls):
            self.calls = calls

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            class R:
                content = [Block('Here is what I found: {"profile_url": "https://www.facebook.com/Tandem'), Block('Diabetes", "page_name": "Tandem Diabetes Care", "confidence": "high", "reasoning": "linked from tandemdiabetes.com"}')]
            return R()

    class Client:
        calls = []

        class messages:
            @staticmethod
            def stream(**kw):
                Client.calls.append(kw)
                return Stream(Client.calls)
    monkeypatch.setattr(se, "_client", lambda: Client)
    got = se.find_facebook("Tandem", "tandemdiabetes.com")
    assert got["facebook"] == "https://www.facebook.com/TandemDiabetes" and got["confidence"] == "high"
    kw = Client.calls[0]
    assert kw["messages"] == [{"role": "user", "content": "Company: Tandem\nWebsite: tandemdiabetes.com"}] and kw["tools"][0]["name"] == "web_search" and kw["tools"][0]["max_uses"] == 4 and "official Facebook page" in kw["system"]


def test_claude_without_a_key_or_with_an_unreadable_answer_gives_an_error_not_a_page(monkeypatch):
    monkeypatch.setattr(se, "_client", lambda: None)
    assert "ANTHROPIC_API_KEY" in se.ask_claude("T", "t.com")["error"]

    class Block:
        type, text = "text", "I could not find it, sorry."

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            class R:
                content = [Block()]
            return R()

    class Client:
        class messages:
            @staticmethod
            def stream(**kw):
                return Stream()
    monkeypatch.setattr(se, "_client", lambda: Client)
    assert "could not be read" in se.ask_claude("T", "t.com")["error"]
    out = se.find_facebook("T", "t.com")
    assert out["facebook"] == "" and "could not be read" in out["error"]


def test_a_search_tool_version_the_api_refuses_falls_through_to_the_next(monkeypatch):
    tried = []

    class Block:
        type, text = "text", '{"profile_url": "https://www.facebook.com/Tandem", "confidence": "high", "reasoning": "r"}'

    class Stream:
        def __init__(self, version):
            self.version = version

        def __enter__(self):
            if self.version != se._WEB_SEARCH_VERSIONS[-1]:
                raise RuntimeError("invalid tool: unsupported")
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            class R:
                content = [Block()]
            return R()

    class Client:
        class messages:
            @staticmethod
            def stream(**kw):
                tried.append(kw["tools"][0]["type"])
                return Stream(kw["tools"][0]["type"])
    monkeypatch.setattr(se, "_client", lambda: Client)
    assert se.find_facebook("T", "t.com")["facebook"] == "https://www.facebook.com/Tandem" and tried == list(se._WEB_SEARCH_VERSIONS)
    tried.clear()

    class Down(Stream):
        def __enter__(self):
            raise ConnectionError("network down")
    Client.messages.stream = staticmethod(lambda **kw: (tried.append(1), Down(""))[1])
    out = se.find_facebook("T", "t.com")
    assert tried == [1] and "network down" in out["error"] and out["facebook"] == ""                         # a network failure is not retried on every tool version
