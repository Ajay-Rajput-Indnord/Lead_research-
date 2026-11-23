"""Local API used by the Chrome extension. Run with:  python main.py serve"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import db
from agents import linkedin_agent as la
from clients import mailer
from clients.llm import LLMError

db.init_db()
app = FastAPI(title="Lead Research API")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class Link(BaseModel):
    href: str
    text: str = ""


class CaptureIn(BaseModel):
    url: str
    text: str = Field(max_length=60000)
    links: list[Link] = Field(default_factory=list, max_length=1000)


class LinkIn(BaseModel):
    url: str


class MessageIn(BaseModel):
    feedback: str | None = None


class StatusIn(BaseModel):
    status: str


def _run(fn, *args):
    try:
        return fn(*args)
    except la.CaptureError as exc:
        raise HTTPException(400, str(exc))
    except LLMError as exc:
        raise HTTPException(502, str(exc))


EMPLOYEE_FIELDS = ("employee_id", "full_name", "job_title", "linkedin_headline", "linkedin_url",
                   "location", "source", "connect_score", "connect_reason", "connect_angle",
                   "connection_note", "followup_message", "outreach_status",
                   "work_email", "email_subject", "email_body", "email_status", "email_sent_at")


class EmailAddressIn(BaseModel):
    email: str


class SendEmailIn(BaseModel):
    subject: str = Field(max_length=300)
    body: str = Field(max_length=20000)


@app.get("/api/health")
def health():
    return {"ok": True, "email_configured": mailer.is_configured(),
            "email_from": mailer.SMTP_FROM if mailer.is_configured() else None}


@app.post("/api/employees/{employee_id}/email-address")
def email_address(employee_id: int, body: EmailAddressIn):
    return {"email": _run(la.set_email_address, employee_id, body.email)}


@app.post("/api/employees/{employee_id}/email-draft")
def email_draft(employee_id: int, body: MessageIn):
    return _run(la.draft_email, employee_id, body.feedback)


@app.post("/api/employees/{employee_id}/send-email")
def send_email(employee_id: int, body: SendEmailIn):
    return _run(la.send_email, employee_id, body.subject, body.body)


@app.get("/api/companies")
def companies():
    rows = []
    for c in db.list_companies():
        if c["status"] in ("approved", "people_fetched"):
            full = db.get_company(c["company_id"])
            rows.append({**c, "linkedin_url": full["linkedin_url"]})
    return rows


@app.get("/api/companies/{company_id}")
def company(company_id: int):
    c = db.get_company(company_id)
    if not c:
        raise HTTPException(404, "Company not found")
    li = c["linkedin_json"] or {}
    people = [{k: e[k] for k in EMPLOYEE_FIELDS} for e in db.get_employees(company_id)]
    people.sort(key=lambda e: -(e["connect_score"] or 0))
    return {
        "company_id": c["company_id"], "name": c["name"], "domain": c["domain"],
        "status": c["status"], "linkedin_url": c["linkedin_url"],
        "has_about": bool(li.get("about")), "posts_count": len(li.get("posts") or []),
        "people": people,
    }


@app.post("/api/companies/{company_id}/link")
def link(company_id: int, body: LinkIn):
    return {"linkedin_url": _run(la.link_company, company_id, body.url)}


@app.post("/api/companies/{company_id}/capture")
def capture(company_id: int, body: CaptureIn):
    links = [l.model_dump() for l in body.links]
    return _run(la.capture, company_id, body.url, body.text, links)


@app.post("/api/companies/{company_id}/suggest")
def suggest(company_id: int):
    return _run(la.suggest, company_id)


@app.post("/api/employees/{employee_id}/message")
def message(employee_id: int, body: MessageIn):
    return _run(la.draft_message, employee_id, body.feedback)


@app.post("/api/employees/{employee_id}/status")
def status(employee_id: int, body: StatusIn):
    if body.status not in ("sent", "skipped", "suggested", "drafted", "none"):
        raise HTTPException(400, "Invalid status")
    if not db.get_employee(employee_id):
        raise HTTPException(404, "Person not found")
    db.update_employee(employee_id, outreach_status=body.status)
    return {"ok": True}
