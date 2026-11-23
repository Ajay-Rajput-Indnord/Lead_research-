"""People Data Labs wrapper (https://docs.peopledatalabs.com)."""
import requests

from config import PDL_API_KEY, require

BASE = "https://api.peopledatalabs.com/v5"

# Seniority levels PDL uses; these are the decision makers.
DECISION_LEVELS = ["owner", "partner", "cxo", "vp", "director"]


def _headers() -> dict:
    return {"X-Api-Key": require("PDL_API_KEY", PDL_API_KEY), "Content-Type": "application/json"}


def enrich_company(name: str, website: str | None = None) -> dict | None:
    params = {"name": name}
    if website:
        params["website"] = website
    resp = requests.get(f"{BASE}/company/enrich", headers=_headers(), params=params, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def find_decision_makers(domain: str | None, company_name: str, size: int = 10,
                         pdl_company_id: str | None = None) -> list[dict]:
    """Search current employees of the company at decision-making seniority."""
    if pdl_company_id:
        company_filter = {"term": {"job_company_id": pdl_company_id}}
    elif domain:
        company_filter = {"term": {"job_company_website": domain}}
    else:
        company_filter = {"match": {"job_company_name": company_name}}

    query = {
        "bool": {
            "must": [
                company_filter,
                {"terms": {"job_title_levels": DECISION_LEVELS}},
            ]
        }
    }
    resp = requests.post(
        f"{BASE}/person/search",
        headers=_headers(),
        json={"query": query, "size": size, "dataset": "all"},
        timeout=60,
    )
    if resp.status_code == 404:  # PDL returns 404 when nothing matches
        return []
    resp.raise_for_status()
    people = resp.json().get("data", [])

    # Sort: cxo/owner first, then vp, then director.
    rank = {lvl: i for i, lvl in enumerate(DECISION_LEVELS)}
    people.sort(key=lambda p: min((rank.get(l, 99) for l in p.get("job_title_levels") or []), default=99))
    return people
