# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Checks which Wikipedia articles exist, 50 titles per request, with caching."""
from __future__ import annotations

import logging
from urllib.parse import quote

from platesolver.core import net
from platesolver.core.cache import JsonCache

log = logging.getLogger(__name__)

API = "https://{lang}.wikipedia.org/w/api.php"
ARTICLE = "https://{lang}.wikipedia.org/wiki/{title}"

_cache: JsonCache | None = None


def _get_cache() -> JsonCache:
    global _cache
    if _cache is None:
        _cache = JsonCache("wikipedia", max_age_days=30)
    return _cache


def article_url(lang: str, title: str) -> str:
    page, _, fragment = title.partition("#")
    url = ARTICLE.format(lang=lang, title=quote(page.replace(" ", "_"), safe="_(),'-.:"))
    return url + ("#" + quote(fragment.replace(" ", "_")) if fragment else "")


def resolve_titles(lang: str, titles: list[str], check_cancel=lambda: None) -> dict[str, str]:
    """For each title, the article it leads to (after redirects), or '' if none / disambiguation page."""
    result: dict[str, str] = {}
    todo = []
    cache = _get_cache()
    for t in dict.fromkeys(titles):
        cached = cache.get(f"{lang}|{t}")
        if cached is not None:
            result[t] = cached
        else:
            todo.append(t)
    for i in range(0, len(todo), 50):
        check_cancel()
        batch = todo[i:i + 50]
        data = net.get_json(API.format(lang=lang), {
            "action": "query", "format": "json", "formatversion": "2", "redirects": "1",
            "prop": "pageprops", "ppprop": "disambiguation", "titles": "|".join(batch)}, timeout=30)
        q = data.get("query", {})
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {r["from"]: (r["to"], r.get("tofragment", "")) for r in q.get("redirects", [])}
        pages = {p["title"]: p for p in q.get("pages", [])}
        for t in batch:
            title = norm.get(t, t)
            fragment = ""
            for _ in range(5):  # follow redirect chains
                if title in redir:
                    title, frag = redir[title]
                    fragment = frag or fragment
                else:
                    break
            page = pages.get(title, {})
            ok = page and not page.get("missing") and not page.get("invalid") \
                and "disambiguation" not in page.get("pageprops", {})
            final = (title + ("#" + fragment if fragment else "")) if ok else ""
            result[t] = final
            cache.set(f"{lang}|{t}", final)
    return result
