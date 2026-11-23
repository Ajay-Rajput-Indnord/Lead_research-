"""Agent 2 — Humanised outreach emails.

Uses the company insights from Agent 1 plus each decision maker's profile to write a short,
personal cold email. The human approves / regenerates / skips each one; approved emails are
sent over SMTP if configured, otherwise kept as drafts (DB + outbox/ folder).
"""
import json
from pathlib import Path

import db
from agents.ui import ask_choice, ask_yes_no, hr
from clients.llm import ask_json
from config import SENDER_COMPANY, SENDER_NAME, SENDER_OFFERING, SENDER_TITLE, SMTP_HOST, SMTP_USER

OUTBOX = Path("outbox")

EMAIL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "body", "personalization_notes"],
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string", "description": "plain-text email body including sign-off"},
        "personalization_notes": {"type": "string",
                                  "description": "one line on why this angle fits this person"},
    },
}

EMAIL_SYSTEM = """You write cold B2B emails that sound like a thoughtful human wrote them, not a bot.
Style rules:
- 70-130 words. Short paragraphs. Plain text, no markdown, no emojis, no bullet lists.
- Open with something specific about THEIR company or role (a recent development, growth signal,
  or challenge from the insights). Never open with "I hope this email finds you well" or "My name is".
- Connect that to one clear, relevant way the sender can help. One idea only.
- Speak to the person's actual responsibilities given their title.
- Confident, warm, conversational. No hype words (revolutionary, cutting-edge, synergy, leverage).
- End with a low-friction question (e.g. open to a 15-minute chat next week?), not a hard pitch.
- Subject: under 7 words, lowercase-friendly, specific, no clickbait.
- Use only facts given to you. Never invent metrics, mutual connections, or past conversations.
- Sign off with the sender's name, title, and company exactly as given."""


def _person_profile(emp: dict) -> dict:
    raw = json.loads(emp["raw_json"] or "{}")
    return {
        "name": emp["full_name"],
        "first_name": raw.get("first_name"),
        "job_title": emp["job_title"],
        "seniority": json.loads(emp["job_title_levels"] or "[]"),
        "department": emp["job_title_role"],
        "location": emp["location"],
        "time_in_role_since": raw.get("job_start_date"),
        "headline": raw.get("headline"),
        "skills": (raw.get("skills") or [])[:10],
        "past_companies": [
            (x.get("company") or {}).get("name")
            for x in (raw.get("experience") or [])[:5]
            if (x.get("company") or {}).get("name")
        ],
    }


def _draft(company: dict, emp: dict, feedback: str | None = None) -> dict:
    ins = company["insights_json"] or {}
    context = {
        "sender": {"name": SENDER_NAME, "title": SENDER_TITLE, "company": SENDER_COMPANY,
                   "offering": SENDER_OFFERING},
        "target_company": {
            "name": company["name"],
            "website": company["domain"],
            "snapshot": ins.get("company_snapshot"),
            "recent_developments": ins.get("recent_developments"),
            "growth_signals": ins.get("growth_signals"),
            "pain_points": ins.get("pain_points"),
            "opportunities_for_us": ins.get("opportunities_for_us"),
            "outreach_angles": ins.get("outreach_angles"),
        },
        "recipient": _person_profile(emp),
    }
    prompt = f"Write the email using this context:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    if feedback:
        prompt += f"\n\nThe reviewer asked for these changes to the previous draft: {feedback}"
    return ask_json(EMAIL_SYSTEM, prompt, EMAIL_SCHEMA, effort="medium", max_tokens=16000)


def _send(to_addr: str, subject: str, body: str) -> None:
    from clients import mailer  # local import: mailer -> config only, avoids cycles
    mailer.send(to_addr, subject, body)


def _save_outbox(company: dict, emp: dict, subject: str, body: str) -> Path:
    OUTBOX.mkdir(exist_ok=True)
    path = OUTBOX / f"company_{company['company_id']}_emp_{emp['employee_id']}.txt"
    path.write_text(f"To: {emp['work_email'] or '(unknown)'}\nSubject: {subject}\n\n{body}\n",
                    encoding="utf-8")
    return path


def run(company_id: int, redo: bool = False) -> None:
    db.init_db()
    company = db.get_company(company_id)
    if not company:
        raise SystemExit(f"No company with id {company_id}.")
    if company["status"] != "people_fetched":
        raise SystemExit(f"Company #{company_id} is '{company['status']}'. Run Agent 1 first.")

    employees = [e for e in db.get_employees(company_id)
                 if redo or e["email_status"] in ("none", "drafted")]
    if not employees:
        print("Nothing to do — every person already has an approved/sent/skipped email.")
        return
    can_send = bool(SMTP_HOST and SMTP_USER)
    if not can_send:
        print("SMTP not configured: approved emails will be saved as drafts in outbox/.")

    for emp in employees:
        feedback = None
        while True:
            print(f"\nWriting email for {emp['full_name']} ({emp['job_title']}) ...")
            draft = _draft(company, emp, feedback)
            db.update_employee(emp["employee_id"], email_subject=draft["subject"],
                               email_body=draft["body"], email_status="drafted")

            hr(f"To: {emp['full_name']} <{emp['work_email'] or 'no email on file'}>")
            print(f"Subject: {draft['subject']}\n")
            print(draft["body"])
            print(f"\n(why this angle: {draft['personalization_notes']})")

            choice = ask_choice("\n[a]pprove  [r]egenerate with feedback  [s]kip  [q]uit",
                                {"a", "r", "s", "q"})
            if choice == "r":
                feedback = input("What should change? ").strip()
                continue
            if choice == "s":
                db.update_employee(emp["employee_id"], email_status="skipped")
            elif choice == "q":
                print("Stopped. Drafts so far are saved in the database.")
                return
            else:  # approve
                path = _save_outbox(company, emp, draft["subject"], draft["body"])
                db.update_employee(emp["employee_id"], email_status="approved")
                print(f"Saved to {path}")
                if can_send and emp["work_email"] and ask_yes_no(f"Send it now to {emp['work_email']}?"):
                    try:
                        _send(emp["work_email"], draft["subject"], draft["body"])
                        db.update_employee(emp["employee_id"], email_status="sent")
                        print("Sent.")
                    except Exception as exc:  # MailError or network error
                        print(f"Send failed ({exc}); kept as approved draft.")
            break

    print("\nEmail agent finished.")
