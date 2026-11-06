"""Every HTML page gets the rule that makes dropdown options black on white."""
import gzip

import app as app_module

_app = app_module.app
if "_t_opt_html" not in _app.view_functions:
    _app.add_url_rule("/_t_opt_html", "_t_opt_html", lambda: (
        "<html><head><title>x</title></head><body><select><option>a</option></select></body></html>"))
    _app.add_url_rule("/_t_opt_json", "_t_opt_json", lambda: {"ok": True})
    _app.add_url_rule("/_t_opt_big", "_t_opt_big", lambda: (
        "<html><head></head><body>" + "x" * 5000 + "</body></html>"))


def _client():
    _app.config["TESTING"] = True
    return _app.test_client()


def test_html_pages_get_the_option_style_once_and_json_does_not():
    c = _client()
    html = c.get("/_t_opt_html").get_data(as_text=True)
    assert html.count('id="p2-option-colors"') == 1
    assert html.index("p2-option-colors") < html.index("</head>")
    assert "color:#111 !important" in html
    assert "p2-option-colors" not in c.get("/_t_opt_json").get_data(as_text=True)


def test_gzipped_pages_still_carry_the_style():
    r = _client().get("/_t_opt_big", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("Content-Encoding") == "gzip"
    assert b"p2-option-colors" in gzip.decompress(r.get_data())
