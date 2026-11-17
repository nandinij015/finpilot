"""News retrieval with date verification. Provider: Google News RSS (no API key).

"Verified date" here means: the feed item has a parseable publication timestamp, it is not in the future, and
it falls inside the requested window. Swap `fetch_news` for NewsAPI/GDELT/etc. behind the same return shape.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import requests

_SUFFIX = re.compile(r"\b(inc|corp|corporation|company|co|ltd|plc|holdings|etf|the)\b\.?,?", re.I)


def _aliases(holdings: list[dict]) -> dict[str, list[re.Pattern]]:
    out = {}
    for h in holdings:
        pats = [re.compile(rf"\b{re.escape(h['ticker'])}\b")]  # case-sensitive ticker
        name = re.sub(r"[&.,]", " ", _SUFFIX.sub("", h.get("name", ""))).strip()
        name = re.sub(r"\s+", " ", name)
        if len(name) >= 4:
            pats.append(re.compile(rf"\b{re.escape(name)}\b", re.I))
        out[h["ticker"]] = pats
    return out


def fetch_news(query: str, days: int, max_items: int, holdings: list[dict]) -> dict:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}+when:{days}d&hl=en-US&gl=US&ceid=US:en"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 FinPilot/1.0"})
    resp.raise_for_status()
    now = datetime.now(timezone.utc)
    earliest = now - timedelta(days=days)
    aliases = _aliases(holdings)
    items, seen, rejected = [], set(), 0
    for node in ET.fromstring(resp.content).iterfind(".//item"):
        title = (node.findtext("title") or "").strip()
        key = re.sub(r"\W+", " ", title.lower())
        if not title or key in seen:
            continue
        seen.add(key)
        try:
            pub = parsedate_to_datetime(node.findtext("pubDate") or "")
            pub = pub if pub.tzinfo else pub.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            rejected += 1
            continue
        if not (earliest <= pub <= now + timedelta(hours=1)):
            rejected += 1
            continue
        src = node.find("source")
        link = node.findtext("link") or ""
        matched = [t for t, pats in aliases.items() if any(p.search(title) for p in pats)]
        items.append({
            "index": len(items) + 1,
            "title": title,
            "source": (src.text if src is not None else urlparse(link).netloc),
            "published_utc": pub.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "age_days": round((now - pub).total_seconds() / 86400, 1),
            "url": link,
            "date_verified": True,
            "matched_holdings": matched,
            "link_type": "direct_mention" if matched else "unmapped_requires_hypothesis",
        })
        if len(items) >= max_items:
            break
    return {
        "query": query, "window_days": days, "retrieved_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "provider": "google_news_rss", "items": items, "rejected_unverifiable_or_out_of_window": rejected,
    }
