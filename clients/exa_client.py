"""Exa search API wrapper (https://docs.exa.ai)."""
from urllib.parse import urlparse

import requests

from config import EXA_API_KEY, require

EXA_SEARCH_URL = "https://api.exa.ai/search"


def _search(query: str, num_results: int = 5, category: str | None = None,
            include_domains: list[str] | None = None, text_chars: int = 3000) -> list[dict]:
    body = {
        "query": query,
        "type": "auto",
        "numResults": num_results,
        "contents": {
            "text": {"maxCharacters": text_chars},
            "summary": {"query": query},
        },
    }
    if category:
        body["category"] = category
    if include_domains:
        body["includeDomains"] = include_domains
    resp = requests.post(
        EXA_SEARCH_URL,
        headers={"x-api-key": require("EXA_API_KEY", EXA_API_KEY), "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    return [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "published_date": r.get("publishedDate"),
            "summary": r.get("summary"),
            "text": r.get("text"),
        }
        for r in resp.json().get("results", [])
    ]


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.") or None


def find_company_candidates(company_name: str, num_results: int = 5) -> list[dict]:
    """Quick lookup so the human can confirm which company they mean."""
    results = _search(f"{company_name} company official website", num_results=num_results,
                      category="company", text_chars=800)
    seen, candidates = set(), []
    for r in results:
        d = domain_of(r["url"])
        if d and d not in seen:
            seen.add(d)
            candidates.append({**r, "domain": d})
    return candidates


# Topics pulled for the in-depth research JSON.
RESEARCH_TOPICS = {
    "overview": "{name} ({domain}) company overview, what they do, products and services",
    "business_model": "{name} business model, customers, pricing, target market",
    "funding_financials": "{name} funding rounds, investors, revenue, valuation",
    "leadership": "{name} leadership team, CEO, founders, executives",
    "recent_news": "{name} latest news announcements 2026",
    "growth_signals": "{name} hiring, expansion, new offices, partnerships, acquisitions",
    "technology": "{name} technology stack, platforms, engineering, digital transformation",
    "challenges": "{name} challenges, problems, layoffs, competition, customer complaints",
    "competitors": "{name} competitors and alternatives",
}


def deep_research(name: str, domain: str | None, results_per_topic: int = 5) -> dict:
    """Run one Exa search per topic and return everything as a single JSON-able dict."""
    research = {"company": name, "domain": domain, "topics": {}}
    if domain:
        research["official_site"] = _search(f"{name} about us", num_results=3,
                                            include_domains=[domain], text_chars=4000)
    for key, template in RESEARCH_TOPICS.items():
        query = template.format(name=name, domain=domain or "")
        try:
            research["topics"][key] = _search(query, num_results=results_per_topic)
        except requests.HTTPError as exc:
            research["topics"][key] = {"error": str(exc)}
        print(f"  - {key}: done")
    return research
