"""Step 4 for Social Media Intelligence: mechanical pattern
classification across a run's already-analyzed posts. No vendor calls --
this is pure aggregation over the creative_analysis(_openai) + metrics that
sci_pipeline's Step 3 already wrote onto each sci_posts row, pooling BOTH
Claude's and ChatGPT's independent reads of each post (see _post_keywords)
so a theme both vendors land on ranks higher than one only one of them saw.
Runs once after every platform finishes collecting and analyzing;
sci_synthesize.py (Step 5) turns this into the cited narrative.
"""

from __future__ import annotations

from collections import Counter, defaultdict


def _engagement_score(metrics: dict) -> float:
    """A single comparable number for ranking posts WITHIN one platform,
    where the metric set is consistent -- not meant to be normalized across
    platforms with different metric sets (likes vs. views vs. shares)."""
    if not metrics:
        return 0.0
    return sum(v for v in metrics.values() if isinstance(v, (int, float)))


def _analysis_keywords(analysis: dict | None) -> list[str]:
    """Recurring-theme words from ONE vendor's analysis dict -- covers both
    the image/carousel shape (sci_vision.analyze_image's own fields) and the
    video shape (sci_vision.summarize_frames's folded lists). tone and
    format_technique use the SAME key name in both shapes (video folds them
    into one dedupe-joined string, see summarize_frames), so they only need
    the singular loop -- no plural variant to also check."""
    if not analysis or "error" in analysis:
        return []
    words = []
    for key in ("subject", "setting", "style", "tone", "format_technique"):
        v = analysis.get(key)
        if isinstance(v, str) and v.strip():
            words.append(v.strip().lower())
    for key in ("subjects", "settings"):
        v = analysis.get(key)
        if isinstance(v, list):
            words.extend(str(x).strip().lower() for x in v if str(x).strip())
    return words


def _post_keywords(post: dict) -> list[str]:
    """Pools BOTH vendors' independent reads of one post -- Claude's
    creative_analysis and ChatGPT's creative_analysis_openai, each counted
    only when THAT vendor's own status is 'ok' (a post where one vendor
    succeeded and the other failed/was never run still contributes the
    successful one's words). Deliberately not deduplicated PER post: when
    both vendors independently land on the same word (e.g. both call the
    tone "urgent"), that word earns two votes in the run-wide theme_counter
    below rather than one -- agreement between two independent models is
    itself a stronger signal than either alone, and top_themes should rank
    it accordingly."""
    words = []
    if post.get("creative_analysis_status") == "ok":
        words.extend(_analysis_keywords(post.get("creative_analysis")))
    if post.get("creative_analysis_openai_status") == "ok":
        words.extend(_analysis_keywords(post.get("creative_analysis_openai")))
    return words


def _group_patterns(posts: list[dict]) -> dict:
    # "Analyzed" means at least one vendor produced a real read -- a post
    # ChatGPT could describe but Claude's call failed on (or vice versa) is
    # still real evidence, not a gap.
    analyzed = [p for p in posts if p.get("creative_analysis_status") == "ok"
               or p.get("creative_analysis_openai_status") == "ok"]
    format_mix = Counter(p.get("post_type") or "unknown" for p in posts)
    theme_counter = Counter()
    for p in analyzed:
        theme_counter.update(_post_keywords(p))
    scored = sorted(posts, key=lambda p: _engagement_score(p.get("metrics") or {}), reverse=True)
    return {
        "post_count": len(posts),
        "analyzed_count": len(analyzed),
        "format_mix": dict(format_mix),
        "top_themes": [t for t, _ in theme_counter.most_common(8)],
        "top_engaging_post_ids": [p["id"] for p in scored[:3]],
    }


def classify_patterns(run_id: int) -> dict:
    """Per-platform pattern summary keyed by platform name, plus a pooled
    "_all" entry across every analyzed post for cross-platform context.
    Never raises -- a run with no posts yet, or a platform with nothing
    analyzed, just yields an empty-but-well-formed pattern dict rather than
    an exception, so a partial run still classifies whatever it has."""
    from tracker import sci_store

    posts = sci_store.get_posts(run_id)
    by_platform = defaultdict(list)
    for p in posts:
        by_platform[p.get("platform")].append(p)

    result = {platform: _group_patterns(plist) for platform, plist in by_platform.items()}
    result["_all"] = _group_patterns(posts)
    return result
