"""Paid Marketing Agents: Location Conflicts, what to fix and in what order (no network).

One step per rule that found something, the ones that stop a campaign from showing where it should first (a place targeted and excluded, an ad that names another place than its ad
group), then the ones that let it show to the wrong people, then the settings that are worth a look. Each step says how many things it covers, says what to do about it and points at the
first campaigns to open, so a person can start without reading the whole list.
"""
from __future__ import annotations

WHERE = 3                         # campaigns named under a step
EXAMPLES = 3

TITLES = {
    "tgt_conflict": "Fix the places that are both targeted and excluded in %s",
    "group_copy": "Make the ads say the place of their ad group in %s",
    "name_place": "Rename or retarget what is named for a place that is not targeted in %s",
    "copy_place": "Change the ad text that names a place outside the locations in %s",
    "kw_place": "Move the keywords that name a place outside the locations in %s",
    "no_target": "Set a location for %s",
    "setting": "Choose the location option (presence or interest) for %s",
    "overlap": "Check the campaigns that share a place (%s)",
    "device": "Check the device adjustments of %s",
    "schedule": "Check the ad schedule of %s",
}


def _n(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def build(a):
    """{steps: [...]} for one analysed account (see tracker/paid_geoconf_math.py); steps is empty when nothing was found."""
    steps = []
    for b in a["byRule"]:
        rule = b["rule"]
        where, examples, items = [], [], 0
        for it in a["items"]:
            hit = [f for f in it["findings"] if f["rule"] == rule]
            if not hit:
                continue
            items += 1
            if len(where) < WHERE:
                where.append(it["name"])
            for f in hit:
                if len(examples) < EXAMPLES:
                    examples.append(f["detail"])
        steps.append({"id": rule, "title": TITLES[rule] % _n(items, "campaign"), "level": b["level"], "campaigns": items, "findings": b["findings"], "how": b["fix"], "where": where,
                      "examples": examples})
    if steps:
        steps.append({"id": "recheck", "title": "Check again the next day", "level": "info", "campaigns": 0, "findings": 0,
                      "how": "Press Refresh to read the settings again, or let the next daily run do it. A change in Google Ads can take a few minutes to show.", "where": [], "examples": []})
    return {"steps": steps}
