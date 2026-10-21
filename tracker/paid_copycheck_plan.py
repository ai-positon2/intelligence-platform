"""Paid Marketing Agents: Ad Copy Checker, what to fix and in what order (no network).

One step per rule that found something, the ones that stop an ad from doing its job first (an unfilled placeholder, a wrong phone number), then the slips a reader notices, then the
matters of style. Each step names how many ads it covers, says what to do about it, and points at the first ads to open, so a person can start without reading the whole list.
"""
from __future__ import annotations

WHERE = 3                         # ads named under a step

TITLES = {
    "placeholder": "Fill in or remove the placeholders in %s",
    "phone": "Put the right phone number in %s",
    "garbled": "Retype the damaged characters in %s",
    "spelling": "Correct the misspelt words in %s",
    "repeat": "Delete the repeated words in %s",
    "gap": "Fix the spacing around punctuation in %s",
    "punct": "Remove the doubled punctuation in %s",
    "caps_line": "Rewrite the lines in capitals in %s",
    "lower_start": "Start the lines with a capital in %s",
    "place": "Check the places named in %s",
    "brand": "Write the brand the same way in %s",
    "caps_word": "Write the shouted words normally in %s",
    "odd_caps": "Check the odd capitals inside words in %s",
    "ending": "Make the endings of the descriptions agree in %s",
    "style": "Use one capitalisation style for the headlines of %s",
    "vocab": "Tell the checker which unknown words are right (%s)",
}
HOW = {
    "placeholder": "Open each ad, replace the placeholder with the real text (or delete the line) and save. An ad that is live with a placeholder shows it to people.",
    "phone": "Open each ad and use a number from the account's list. If the number is right, add it to the account's phone numbers in the settings below.",
    "garbled": "Retype the apostrophe, quote or accent by hand. Copying from a document is how they are damaged.",
    "spelling": "Correct the word shown. If a word is right (a product, a person), mark it right so it is never doubted again.",
    "repeat": "Delete the second copy of the word.",
    "gap": "One space after a comma, colon or full stop, and none before one.",
    "punct": "Keep a single mark. Google Ads refuses repeated punctuation in some places.",
    "caps_line": "Rewrite in sentence or title case. Google Ads limits capitals used for emphasis.",
    "lower_start": "Start each headline and description with a capital, unless the brand is written with a small letter.",
    "place": "Check the line belongs in this account. If the place is right for it, add the place to this account's list below.",
    "brand": "Write the name the way the client writes it, in every headline, description and path.",
    "caps_word": "Normal case unless it is an acronym or the brand.",
    "odd_caps": "Fix the word, or add it to the allowed words if the brand is written that way.",
    "ending": "Either every description ends with a full stop or none does.",
    "style": "Title Case for every headline of the ad or sentence case for every one.",
    "vocab": "These words are not in the dictionary and the account uses them often. If they are right, add them to the allowed words below.",
}


def _where(items, rule):
    out = []
    for i in items:
        if any(f["rule"] == rule for f in i["findings"]):
            out.append("%s%s" % (i["campaign"] or "Whole account", (" / " + i["adGroup"]) if i["adGroup"] else ""))
        if len(out) == WHERE:
            break
    return out


def build(a):
    """{steps: [...]} for one analysed account (see tracker/paid_copycheck_analyse.py); steps is empty when nothing was found."""
    steps = []
    for b in a["byRule"]:
        rule = b["rule"]
        n = "%d ad%s" % (b["ads"], "" if b["ads"] == 1 else "s")
        steps.append({"id": rule, "title": TITLES[rule] % n, "level": b["level"], "ads": b["ads"], "findings": b["findings"], "how": HOW[rule], "where": _where(a["items"], rule),
                      "examples": [e["excerpt"] for e in b["examples"]]})
    if steps:
        steps.append({"id": "recheck", "title": "Check again the next day", "level": "info", "ads": 0, "findings": 0,
                      "how": "An edited responsive ad is a new version and is reviewed again. Press Refresh to read the live ads again, or let the next run of the monitor do it.", "where": [], "examples": []})
    return {"steps": steps}
