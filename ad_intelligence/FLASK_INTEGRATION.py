# ─────────────────────────────────────────────────────────────
# AD INTELLIGENCE — Flask Integration
# Add these snippets to company-signal-tracker/app.py
# ─────────────────────────────────────────────────────────────

# ── 1. ADD TO IMPORTS (top of app.py) ────────────────────────

from flask import send_from_directory
import requests, json, re

# ── 2. ADD THESE ROUTES ──────────────────────────────────────
#
# These replace the old iframe embed route for /ppc/ad-intelligence.
# Flask now serves the built React app directly — no iframe needed.

@app.route('/ppc/ad-intelligence')
@app.route('/ppc/ad-intelligence/')
@login_required
def ad_intelligence():
    return send_from_directory('ad_intelligence', 'index.html')

# Assets don't need login — they're static JS/CSS files
@app.route('/ppc/ad-intelligence/assets/<path:filename>')
def ad_intelligence_assets(filename):
    return send_from_directory('ad_intelligence/assets', filename)

@app.route('/ppc/ad-intelligence/favicon.svg')
def ad_intelligence_favicon():
    return send_from_directory('ad_intelligence', 'favicon.svg')

@app.route('/ppc/ad-intelligence/icons.svg')
def ad_intelligence_icons():
    return send_from_directory('ad_intelligence', 'icons.svg')


# ── 3. CHATBOT FUNCTION ───────────────────────────────────────
#
# Add this to your FUNCTION_MAP for the chatbot.
# It fetches the same Google Sheet the React app reads —
# no API key needed (uses the public gviz endpoint).

AD_INTEL_SHEET_ID = '16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s'

def get_ad_intelligence_data(competitor=None, format=None, status=None,
                              keyword=None, limit=50):
    """
    Fetch competitor ad data from Ad Intelligence Google Sheet.

    Args:
        competitor: Filter by competitor name or domain
                    (e.g. 'Inspire Aesthetics', 'sonobello', 'drdanamd')
        format:     Filter by ad format — 'image', 'text', or 'video'
        status:     Filter by status — 'active' or 'inactive'
        keyword:    Filter ads whose headline/description contains this word
        limit:      Max rows to return (default 50)
    """
    url = f"https://docs.google.com/spreadsheets/d/{AD_INTEL_SHEET_ID}/gviz/tq?tqx=out:json"
    try:
        res = requests.get(url, timeout=10)
        text = res.text

        # Strip JSONP wrapper: /*O_o*/ google.visualization.Query.setResponse({...});
        json_str = re.sub(r'^[^{]*', '', text)
        json_str = re.sub(r'\);?\s*$', '', json_str)
        parsed = json.loads(json_str)

        table = parsed.get('table', {})
        headers = [c.get('label', '') for c in table.get('cols', [])]
        ads = []
        for row in table.get('rows', []):
            obj = {}
            cells = row.get('c', [])
            for i, h in enumerate(headers):
                cell = cells[i] if i < len(cells) else None
                obj[h] = str(cell['v']) if cell and cell.get('v') is not None else ''
            if obj.get('Domain') and obj.get('Domain') != 'Domain':
                ads.append(obj)

    except Exception as e:
        return {"error": str(e), "ads": []}

    # Apply filters
    if competitor:
        c = competitor.lower()
        ads = [a for a in ads if c in a.get('Domain', '').lower()
               or c in a.get('Advertiser Name', '').lower()]
    if format:
        ads = [a for a in ads if a.get('Format', '').lower() == format.lower()]
    if status:
        ads = [a for a in ads if a.get('Status', '').lower() == status.lower()]
    if keyword:
        kw = keyword.lower()
        ads = [a for a in ads if kw in a.get('Headline', '').lower()
               or kw in a.get('Description', '').lower()
               or kw in a.get('Full Ad Text', '').lower()
               or kw in a.get('Keywords', '').lower()]

    # Return a summary-friendly shape
    result = {
        "total": len(ads),
        "competitors": list({a.get('Domain') for a in ads}),
        "ads": [
            {
                "competitor":      a.get('Advertiser Name') or a.get('Domain'),
                "domain":          a.get('Domain'),
                "format":          a.get('Format'),
                "status":          a.get('Status'),
                "headline":        a.get('Headline'),
                "description":     a.get('Description'),
                "cta":             a.get('CTA'),
                "keywords":        a.get('Keywords'),
                "messaging_angle": a.get('Messaging Angle'),
                "first_shown":     a.get('First Shown'),
                "last_shown":      a.get('Last Shown'),
                "platform":        a.get('Platform'),
            }
            for a in ads[:limit]
        ]
    }
    return result


# ── 4. ADD TO OPENAI FUNCTIONS LIST ──────────────────────────
#
# Add this dict to your FUNCTIONS list for the chatbot:

AD_INTEL_FUNCTION_DEF = {
    "name": "get_ad_intelligence_data",
    "description": (
        "Fetch competitor ad intelligence data — ads tracked across competitors "
        "including headlines, CTAs, formats, keywords, messaging angles, and activity dates. "
        "Current competitors tracked: Inspire Aesthetics (inspireaesthetics.com), "
        "Dr. Dana MD (drdanamd.com), Sono Bello (sonobello.com). "
        "Use for questions about: what ads competitors are running, which CTAs they use, "
        "active vs inactive ads, ad formats (image/text/video), keywords targeted, "
        "messaging themes, when ads were last seen."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "competitor": {
                "type": "string",
                "description": "Filter by competitor name or domain. "
                               "E.g. 'Inspire Aesthetics', 'sonobello', 'drdanamd'"
            },
            "format": {
                "type": "string",
                "enum": ["image", "text", "video"],
                "description": "Filter by ad format"
            },
            "status": {
                "type": "string",
                "enum": ["active", "inactive"],
                "description": "Filter by ad status"
            },
            "keyword": {
                "type": "string",
                "description": "Search for a word in headline, description, or keywords"
            },
            "limit": {
                "type": "integer",
                "description": "Max ads to return (default 50)"
            }
        }
    }
}

# Add AD_INTEL_FUNCTION_DEF to your FUNCTIONS list
# Add "get_ad_intelligence_data": get_ad_intelligence_data to your FUNCTION_MAP
