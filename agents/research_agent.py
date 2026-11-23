"""Agent 1 — Company research.

Flow (each arrow with [H] is a human approval gate):
  company name -> Exa lookup -> [H] confirm the right company
  -> Exa deep research (JSON) -> Claude insights -> [H] go / no-go
  -> People Data Labs decision makers -> SQLite
"""
import json
from pathlib import Path

import requests

import db
from agents.ui import ask_choice, ask_yes_no, bullets, hr, wrap
from clients import exa_client, pdl_client
from clients.llm import ask_json
from config import PDL_MAX_PEOPLE, SENDER_COMPANY, SENDER_OFFERING, USE_PDL

DATA_DIR = Path("data")

INSIGHTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "company_snapshot", "what_they_do", "key_facts", "recent_developments",
        "growth_signals", "pain_points", "opportunities_for_us", "risks_red_flags",
        "fit_score", "recommendation", "recommendation_reason", "outreach_angles", "sources",
    ],
    "properties": {
        "company_snapshot": {"type": "string", "description": "2-3 sentence executive summary"},
        "what_they_do": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"},
                      "description": "size, HQ, founded, funding, revenue, leadership - only if found"},
        "recent_developments": {"type": "array", "items": {"type": "string"}},
        "growth_signals": {"type": "array", "items": {"type": "string"}},
        "pain_points": {"type": "array", "items": {"type": "string"}},
        "opportunities_for_us": {"type": "array", "items": {"type": "string"}},
        "risks_red_flags": {"type": "array", "items": {"type": "string"}},
        "fit_score": {"type": "integer", "description": "1 (poor fit) to 10 (excellent fit)"},
        "recommendation": {"type": "string", "enum": ["pursue", "maybe", "skip"]},
        "recommendation_reason": {"type": "string"},
        "outreach_angles": {"type": "array", "items": {"type": "string"},
                            "description": "specific hooks to open a cold email with"},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "most useful URLs"},
    },
}

INSIGHTS_SYSTEM = """You are a senior B2B sales research analyst.
You receive raw web research about a target company as JSON. Produce sharp, factual insights
that help a salesperson decide whether to pursue the account and how to open the conversation.
Rules:
- Use only facts present in the research. If something is unknown, leave it out; never invent numbers.
- Prefer recent information; mention dates when available.
- Be specific and concrete, not generic.
- fit_score and recommendation must reflect how well the company matches the seller's offering."""


def _confirm_company(company_name: str) -> dict | None:
    """Show Exa candidates and let the human pick the right one."""
    while True:
        print(f"\nLooking up '{company_name}' ...")
        candidates = exa_client.find_company_candidates(company_name)
        if not candidates:
            print("No matches found.")
        else:
            hr(f"Which '{company_name}' do you mean?")
            for i, c in enumerate(candidates, 1):
                print(f"\n[{i}] {c['title']}  —  {c['domain']}")
                print(wrap((c.get("summary") or c.get("text") or "")[:400]))
        print("\nEnter a number to pick, 'r' to retype the name, or 'q' to quit.")
        valid = {str(i) for i in range(1, len(candidates) + 1)} | {"r", "q"}
        choice = ask_choice("Your choice", valid)
        if choice == "q":
            return None
        if choice == "r":
            company_name = input("Company name: ").strip()
            continue

        picked = candidates[int(choice) - 1]

        # Firmographics from PDL so the human can be sure (and for an exact people search later).
        pdl_company = None
        if USE_PDL:
            try:
                pdl_company = pdl_client.enrich_company(company_name, website=picked["domain"])
            except (requests.RequestException, SystemExit) as exc:
                print(f"(PDL company lookup skipped: {exc})")

        hr("Please confirm this is the company")
        print(f"  Name     : {(pdl_company or {}).get('display_name') or picked['title']}")
        print(f"  Website  : {picked['domain']}")
        if pdl_company:
            loc = (pdl_company.get("location") or {}).get("name")
            print(f"  Industry : {pdl_company.get('industry')}")
            print(f"  Size     : {pdl_company.get('size')}  (employees ~{pdl_company.get('employee_count')})")
            print(f"  Founded  : {pdl_company.get('founded')}")
            print(f"  HQ       : {loc}")
            print(f"  LinkedIn : {pdl_company.get('linkedin_url')}")
        print("\n  About:")
        print(wrap((picked.get("summary") or picked.get("text") or "")[:600], indent="    "))

        if ask_yes_no("\nIs this the company you are searching for?"):
            return {
                "name": (pdl_company or {}).get("display_name") or company_name,
                "domain": picked["domain"],
                "website": picked["url"],
                "summary": picked.get("summary"),
                "pdl_company": pdl_company,
            }
        print("OK, let's try again.")


def _show_insights(name: str, ins: dict) -> None:
    hr(f"AI INSIGHTS — {name}")
    print("\nSnapshot:")
    print(wrap(ins["company_snapshot"]))
    print("\nWhat they do:")
    print(wrap(ins["what_they_do"]))
    bullets("Key facts", ins["key_facts"])
    bullets("Recent developments", ins["recent_developments"])
    bullets("Growth signals", ins["growth_signals"])
    bullets("Likely pain points", ins["pain_points"])
    bullets("Opportunities for us", ins["opportunities_for_us"])
    bullets("Risks / red flags", ins["risks_red_flags"])
    bullets("Outreach angles", ins["outreach_angles"])
    print(f"\nFit score      : {ins['fit_score']}/10")
    print(f"Recommendation : {ins['recommendation'].upper()}")
    print(wrap(ins["recommendation_reason"]))


def run(company_name: str) -> int | None:
    """Run Agent 1. Returns company_id when people were fetched, else None."""
    db.init_db()

    # Step 1: confirm the company with the human
    picked = _confirm_company(company_name)
    if not picked:
        print("Cancelled.")
        return None
    company_id = db.add_company(picked["name"], picked["domain"], picked["website"], picked["summary"])
    print(f"\nSaved company #{company_id}.")

    # Step 2: in-depth research with Exa -> JSON
    print("\nRunning in-depth research with Exa ...")
    research = exa_client.deep_research(picked["name"], picked["domain"])
    research["pdl_company"] = picked["pdl_company"]
    DATA_DIR.mkdir(exist_ok=True)
    out_file = DATA_DIR / f"company_{company_id}_{picked['domain']}.json"
    out_file.write_text(json.dumps(research, indent=2, ensure_ascii=False), encoding="utf-8")
    db.update_company(company_id, research_json=research, status="researched")
    print(f"Research saved to {out_file}")

    # Step 3: Claude insights
    print("\nAsking Claude for insights (this can take a minute) ...")
    seller = f"Seller: {SENDER_COMPANY or 'our company'}. Offering: {SENDER_OFFERING or 'not specified'}."
    insights = ask_json(
        INSIGHTS_SYSTEM,
        f"{seller}\n\nResearch JSON about the target company:\n{json.dumps(research, ensure_ascii=False)}",
        INSIGHTS_SCHEMA,
        effort="high",
    )
    db.update_company(company_id, insights_json=insights)
    _show_insights(picked["name"], insights)

    # Step 4: human go / no-go
    if not ask_yes_no("\nShould we go with this company?"):
        db.update_company(company_id, status="rejected")
        print("Marked as rejected. Done.")
        return None
    db.update_company(company_id, status="approved")

    if not USE_PDL:
        hr("Approved — next step: LinkedIn")
        print(f"""  1. Start the local API:      python main.py serve
  2. In Chrome, open the extension side panel and pick company #{company_id} ({picked['name']}).
  3. Open the company's LinkedIn About, Posts and People pages and click "Read this page" on each.
  4. Click "Suggest who to connect", then "Write message" for the people you like.""")
        return None

    # Step 5: decision makers from People Data Labs
    print(f"\nFetching up to {PDL_MAX_PEOPLE} decision makers from People Data Labs ...")
    pdl_id = (picked["pdl_company"] or {}).get("id")
    people = pdl_client.find_decision_makers(picked["domain"], picked["name"],
                                             size=PDL_MAX_PEOPLE, pdl_company_id=pdl_id)
    added = db.add_employees(company_id, people)
    db.update_company(company_id, status="people_fetched")

    hr(f"{added} decision makers saved")
    for e in db.get_employees(company_id):
        print(f"  #{e['employee_id']:<4} {e['full_name'] or '-':<28} {e['job_title'] or '-':<40} "
              f"{e['work_email'] or '(no email)'}")
    return company_id
