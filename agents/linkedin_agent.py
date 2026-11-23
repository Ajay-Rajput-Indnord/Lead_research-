"""LinkedIn outreach agent.

The Chrome extension sends the visible text of a LinkedIn page the human has opened for the
selected company (About, Posts, People, or one person's profile). This module:
  1. turns that text into structured data with Claude and stores it,
  2. suggests which decision makers to connect with (and why),
  3. writes a humanised connection note + follow-up message for the human to send.
Nothing is sent automatically: the human copies the message and sends it on LinkedIn.
"""
import json
from datetime import datetime
import re

import db
from agents.email_agent import EMAIL_SCHEMA, EMAIL_SYSTEM
from clients import mailer
from clients.llm import ask_json
from config import SENDER_COMPANY, SENDER_NAME, SENDER_OFFERING, SENDER_TITLE

CONNECTION_NOTE_LIMIT = 300  # LinkedIn's limit for a connection request note


class CaptureError(ValueError):
    """Raised when a capture is for the wrong page/company; the message is shown to the human."""


# ---------- URLs ----------

_COMPANY_RE = re.compile(r"linkedin\.com/(?:company|showcase|school)/([^/?#]+)", re.I)
_PROFILE_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)


def company_slug(url: str | None) -> str | None:
    m = _COMPANY_RE.search(url or "")
    return m.group(1).lower() if m else None


def company_url(slug: str) -> str:
    return f"https://www.linkedin.com/company/{slug}"


def profile_url(url: str | None) -> str | None:
    m = _PROFILE_RE.search(url or "")
    return f"https://www.linkedin.com/in/{m.group(1).lower()}" if m else None


def page_kind(url: str) -> str | None:
    """about | posts | people | profile | None"""
    if profile_url(url):
        return "profile"
    if not company_slug(url):
        return None
    path = url.split("?")[0].rstrip("/").lower()
    if path.endswith("/posts") or "/posts/" in path:
        return "posts"
    if path.endswith("/people") or "/people/" in path:
        return "people"
    return "about"  # /about and the company home page both carry the overview


# ---------- extraction schemas ----------

def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


STR = {"type": "string"}
STR_LIST = {"type": "array", "items": STR}

ABOUT_SCHEMA = _obj({
    "name": STR, "tagline": STR, "overview": STR, "website": STR, "industry": STR,
    "company_size": STR, "headquarters": STR, "founded": STR, "specialties": STR_LIST,
    "followers": STR,
})

POSTS_SCHEMA = _obj({
    "posts": {"type": "array", "items": _obj({
        "posted": {"type": "string", "description": "relative date as shown, e.g. '2w'"},
        "text": STR,
        "topic": {"type": "string", "description": "one-line summary of what the post is about"},
        "engagement": {"type": "string", "description": "reactions/comments if shown, else ''"},
    })},
})

PEOPLE_SCHEMA = _obj({
    "people": {"type": "array", "items": _obj({
        "name": STR,
        "headline": STR,
        "profile_url": {"type": "string", "description": "must be one of the provided links"},
        "location": STR,
    })},
})

PROFILE_SCHEMA = _obj({
    "name": STR, "headline": STR, "location": STR, "about": STR,
    "current_title": STR, "current_company": STR,
    "currently_works_at_target": {"type": "boolean"},
    "time_in_role": STR,
    "past_roles": STR_LIST, "education": STR_LIST, "skills": STR_LIST,
    "recent_activity": {"type": "array", "items": STR,
                        "description": "topics of their recent posts/comments if visible"},
})

EXTRACT_SYSTEM = """You extract structured data from the visible text of a LinkedIn page.
Use only what is in the text. Use '' or [] for anything not present. Never guess."""


def _extract_about(text: str) -> dict:
    return ask_json(EXTRACT_SYSTEM, f"LinkedIn company About page text:\n\n{text}",
                    ABOUT_SCHEMA, effort="low", max_tokens=8000)


def _extract_posts(text: str) -> list[dict]:
    out = ask_json(EXTRACT_SYSTEM,
                   "LinkedIn company Posts page text. Return the 3 most recent posts by the "
                   f"company itself (ignore ads and suggested content), newest first:\n\n{text}",
                   POSTS_SCHEMA, effort="low", max_tokens=8000)
    return out["posts"][:3]


def _extract_people(text: str, links: list[dict], company_name: str) -> list[dict]:
    allowed = {}
    for link in links:
        url = profile_url(link.get("href"))
        if url:
            allowed.setdefault(url, (link.get("text") or "").strip()[:200])
    if not allowed:
        return []
    link_lines = "\n".join(f"- {u}  ({t})" for u, t in allowed.items())
    out = ask_json(
        EXTRACT_SYSTEM,
        f"LinkedIn People page of {company_name}. List every employee card shown, with the "
        f"profile_url copied exactly from the links list.\n\nLinks:\n{link_lines}\n\n"
        f"Page text:\n{text}",
        PEOPLE_SCHEMA, effort="low", max_tokens=16000,
    )
    people = []
    for p in out["people"]:
        url = profile_url(p["profile_url"])
        if url in allowed and p["name"] and p["name"].lower() != "linkedin member":
            people.append({**p, "profile_url": url})
    return people


def _extract_profile(text: str, company_name: str) -> dict:
    return ask_json(EXTRACT_SYSTEM,
                    f"LinkedIn personal profile page. Target company: {company_name}.\n\n{text}",
                    PROFILE_SCHEMA, effort="low", max_tokens=8000)


# ---------- capture ----------

def link_company(company_id: int, url: str) -> str:
    slug = company_slug(url)
    if not slug:
        raise CaptureError("Open the company's LinkedIn page (linkedin.com/company/...) first.")
    db.update_company(company_id, linkedin_url=company_url(slug))
    return company_url(slug)


def capture(company_id: int, url: str, text: str, links: list[dict]) -> dict:
    company = db.get_company(company_id)
    if not company:
        raise CaptureError(f"Company #{company_id} not found.")
    if company["status"] not in ("approved", "people_fetched"):
        raise CaptureError("This company is not approved yet. Approve it in the research agent first.")

    kind = page_kind(url)
    if not kind:
        raise CaptureError("This is not a LinkedIn company page or profile.")
    if not text.strip():
        raise CaptureError("The page looks empty. Wait for it to load and try again.")

    linked_slug = company_slug(company["linkedin_url"])
    li = company["linkedin_json"] or {}

    if kind in ("about", "posts", "people"):
        if not linked_slug:
            raise CaptureError("Link this company's LinkedIn page first.")
        if company_slug(url) != linked_slug:
            raise CaptureError(f"This page is not {company['name']}'s LinkedIn page "
                               f"({company['linkedin_url']}). Only the selected company is read.")

    if kind == "about":
        li["about"] = _extract_about(text)
        db.update_company(company_id, linkedin_json=li)
        return {"kind": kind, "message": "Company About saved.", "about": li["about"]}

    if kind == "posts":
        li["posts"] = _extract_posts(text)
        db.update_company(company_id, linkedin_json=li)
        return {"kind": kind, "message": f"{len(li['posts'])} latest posts saved.", "posts": li["posts"]}

    if kind == "people":
        people = _extract_people(text, links, company["name"])
        created = 0
        for p in people:
            _, is_new = db.upsert_linkedin_person(company_id, p["name"], p["headline"],
                                                  p["profile_url"], p["location"] or None)
            created += is_new
        if people:
            db.update_company(company_id, status="people_fetched")
        return {"kind": kind, "message": f"{len(people)} people read ({created} new). "
                                         "Scroll for more and read again if needed."}

    # profile
    purl = profile_url(url)
    existing = next((e for e in db.get_employees(company_id) if e["linkedin_url"] == purl), None)
    prof = _extract_profile(text, company["name"])
    if not existing and not prof["currently_works_at_target"]:
        raise CaptureError(f"This person doesn't appear to work at {company['name']}, so it was not saved.")
    emp_id, _ = db.upsert_linkedin_person(company_id, prof["name"] or None, prof["headline"] or None,
                                          purl, prof["location"] or None)
    db.update_employee(emp_id, linkedin_profile_json=prof,
                       job_title=prof["current_title"] or prof["headline"] or None)
    db.update_company(company_id, status="people_fetched")
    return {"kind": kind, "message": f"Profile of {prof['name']} saved.", "employee_id": emp_id}


# ---------- suggestions ----------

SUGGEST_SCHEMA = _obj({
    "overall_strategy": {"type": "string", "description": "2-3 sentences on how to approach this account"},
    "suggestions": {"type": "array", "items": _obj({
        "employee_id": {"type": "integer"},
        "should_connect": {"type": "boolean"},
        "connect_score": {"type": "integer", "description": "1 (low priority) to 10 (connect first)"},
        "reason": {"type": "string", "description": "why this person, in one or two sentences"},
        "angle": {"type": "string", "description": "the specific hook to use with this person"},
    })},
})

SUGGEST_SYSTEM = """You are a senior B2B sales strategist.
Given research about a target company and the people visible on its LinkedIn page, decide who the
seller should connect with. Favour people who own the budget or the problem the seller solves.
Rank by likely decision-making power and relevance to the seller's offering. Skip people who are
clearly irrelevant (interns, unrelated functions) by setting should_connect=false.
Base every reason on the data given; do not invent facts about anyone."""


def _company_context(company: dict) -> dict:
    ins = company["insights_json"] or {}
    li = company["linkedin_json"] or {}
    return {
        "name": company["name"],
        "website": company["domain"],
        "research_insights": {k: ins.get(k) for k in (
            "company_snapshot", "what_they_do", "key_facts", "recent_developments",
            "growth_signals", "pain_points", "opportunities_for_us", "outreach_angles")},
        "linkedin_about": li.get("about"),
        "linkedin_latest_posts": li.get("posts"),
    }


def _sender() -> dict:
    return {"name": SENDER_NAME, "title": SENDER_TITLE, "company": SENDER_COMPANY,
            "offering": SENDER_OFFERING}


def _person_context(emp: dict) -> dict:
    prof = json.loads(emp["linkedin_profile_json"]) if emp.get("linkedin_profile_json") else None
    return {"employee_id": emp["employee_id"], "name": emp["full_name"],
            "headline": emp["linkedin_headline"] or emp["job_title"],
            "location": emp["location"], "profile_details": prof}


def suggest(company_id: int) -> dict:
    company = db.get_company(company_id)
    people = [e for e in db.get_employees(company_id) if e["outreach_status"] not in ("sent", "skipped")]
    if not people:
        raise CaptureError("No people saved yet. Open the company's People page and click 'Read this page'.")

    out = ask_json(
        SUGGEST_SYSTEM,
        json.dumps({"seller": _sender(), "target_company": _company_context(company),
                    "people": [_person_context(e) for e in people]}, ensure_ascii=False),
        SUGGEST_SCHEMA, effort="high", max_tokens=32000,
    )
    valid_ids = {e["employee_id"] for e in people}
    for s in out["suggestions"]:
        if s["employee_id"] in valid_ids:
            db.update_employee(s["employee_id"], connect_score=s["connect_score"],
                               connect_reason=s["reason"], connect_angle=s["angle"],
                               outreach_status="suggested" if s["should_connect"] else "none")
    out["suggestions"] = sorted((s for s in out["suggestions"] if s["employee_id"] in valid_ids),
                                key=lambda s: (not s["should_connect"], -s["connect_score"]))
    return out


# ---------- messages ----------

MESSAGE_SCHEMA = _obj({
    "connection_note": {"type": "string",
                        "description": f"connection request note, max {CONNECTION_NOTE_LIMIT} characters"},
    "followup_message": {"type": "string", "description": "message to send after they accept"},
    "why_this_works": {"type": "string", "description": "one line for the human reviewer"},
})

MESSAGE_SYSTEM = f"""You write LinkedIn messages that read like a real person wrote them.
Connection note (max {CONNECTION_NOTE_LIMIT} characters, count carefully):
- Mention one specific, true thing about them or their company (a recent post, a growth signal,
  their role focus). No pitch, no links, no "I'd love to pick your brain".
- Warm and brief, first name only, no sign-off needed.
Follow-up message (60-120 words, sent after they accept):
- Thank them briefly, connect the specific observation to one relevant way the seller can help,
  and end with an easy question. No hype words, no bullet lists, no emojis, no fake familiarity.
Use only the facts provided. Never invent metrics, mutual connections or past conversations."""


def draft_message(employee_id: int, feedback: str | None = None) -> dict:
    emp = db.get_employee(employee_id)
    if not emp:
        raise CaptureError(f"Person #{employee_id} not found.")
    company = db.get_company(emp["company_id"])
    context = {
        "seller": _sender(),
        "target_company": _company_context(company),
        "recipient": {**_person_context(emp), "why_connect": emp["connect_reason"],
                      "suggested_angle": emp["connect_angle"]},
    }
    prompt = f"Write the messages using this context:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    if feedback:
        prompt += f"\n\nReviewer feedback on the previous draft: {feedback}"

    out = ask_json(MESSAGE_SYSTEM, prompt, MESSAGE_SCHEMA, effort="medium", max_tokens=16000)
    if len(out["connection_note"]) > CONNECTION_NOTE_LIMIT:
        out = ask_json(MESSAGE_SYSTEM, prompt + f"\n\nYour connection note was {len(out['connection_note'])} "
                       f"characters. Rewrite it under {CONNECTION_NOTE_LIMIT - 20} characters.",
                       MESSAGE_SCHEMA, effort="medium", max_tokens=16000)
    out["connection_note_chars"] = len(out["connection_note"])
    db.update_employee(employee_id, connection_note=out["connection_note"],
                       followup_message=out["followup_message"], outreach_status="drafted")
    return out


# ---------- email ----------

def set_email_address(employee_id: int, email: str) -> str:
    email = (email or "").strip()
    if not mailer.valid_email(email):
        raise CaptureError(f"'{email}' doesn't look like a valid email address.")
    if not db.get_employee(employee_id):
        raise CaptureError(f"Person #{employee_id} not found.")
    db.update_employee(employee_id, work_email=email)
    return email


def draft_email(employee_id: int, feedback: str | None = None) -> dict:
    """Write a cold email using the same company + LinkedIn + person context as the messages."""
    emp = db.get_employee(employee_id)
    if not emp:
        raise CaptureError(f"Person #{employee_id} not found.")
    company = db.get_company(emp["company_id"])
    context = {
        "sender": _sender(),
        "target_company": _company_context(company),
        "recipient": {**_person_context(emp), "why_contact": emp["connect_reason"],
                      "suggested_angle": emp["connect_angle"]},
    }
    prompt = f"Write the email using this context:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    if feedback:
        prompt += f"\n\nReviewer feedback on the previous draft: {feedback}"
    out = ask_json(EMAIL_SYSTEM, prompt, EMAIL_SCHEMA, effort="medium", max_tokens=16000)
    db.update_employee(employee_id, email_subject=out["subject"], email_body=out["body"],
                       email_status="drafted")
    return out


def send_email(employee_id: int, subject: str, body: str) -> dict:
    """Send the (possibly human-edited) email. Called only when the human clicks Send."""
    emp = db.get_employee(employee_id)
    if not emp:
        raise CaptureError(f"Person #{employee_id} not found.")
    if not emp["work_email"]:
        raise CaptureError("Add this person's email address first.")
    if emp["email_status"] == "sent":
        raise CaptureError(f"An email was already sent to {emp['work_email']} on {emp['email_sent_at']}.")
    try:
        mailer.send(emp["work_email"], subject, body)
    except mailer.MailError as exc:
        raise CaptureError(str(exc)) from exc
    db.update_employee(employee_id, email_subject=subject, email_body=body, email_status="sent",
                       email_sent_at=datetime.now().isoformat(timespec="seconds"))
    return {"message": f"Email sent to {emp['work_email']}."}
