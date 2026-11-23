# Lead Research Agents

## Flow

**Agent 1 – Research** (`python main.py research "Company"`)
1. Exa finds matching companies → **you confirm** the right one → saved to `companies`.
2. Exa runs ~10 targeted searches → one JSON file in `data/` + `companies.research_json`.
3. Claude turns it into insights (pain points, growth signals, outreach angles, fit score) → **you decide** go / no-go.

**Agent 2 – LinkedIn outreach** (Chrome extension + `python main.py serve`)
4. In Chrome, open the side panel, pick the approved company and link its LinkedIn page.
5. Open its **About**, **Posts** and **People** pages (and any person's profile) and click **Read this page**.
   The extension reads only the page you have open, only when you click, and only for the linked company.
6. **Suggest who to connect** → Claude ranks the people with a score, reason and angle.
7. **Write message** → a connection note (≤300 chars) and a follow-up message, using the Exa
   insights, LinkedIn About, the latest 3 posts and the person's profile. Edit, regenerate with feedback,
   copy, **send it yourself on LinkedIn**, then click **I sent it**.

**Email** (same side panel, bottom of each person's card)
8. Paste the person's email address → **Save** → **Write email** (Claude uses the same research).
9. Edit the subject/body or **Regenerate** with feedback → **Send email** → confirm. It is sent from
   your SMTP account in `.env`, and the person is marked as emailed so it can't be sent twice.
   Check your SMTP settings first with `python main.py test-email you@yourcompany.com`.

People Data Labs + the command-line email agent (`agents/email_agent.py`, `python main.py email <id>`) still exist;
set `USE_PDL=true` to use them.

## Database (SQLite)

```
companies (company_id PK, name, domain, website, summary, research_json, insights_json,
           linkedin_url, linkedin_json, status)
employees (employee_id PK, company_id FK -> companies.company_id, full_name, job_title,
           linkedin_url, linkedin_headline, linkedin_profile_json, source,
           connect_score, connect_reason, connect_angle,
           connection_note, followup_message, outreach_status,
           work_email, email_subject, email_body, email_status)
```
Existing databases are upgraded automatically (new columns are added on start).

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env   # fill ANTHROPIC_API_KEY, EXA_API_KEY and the SENDER_* fields
```

Load the extension: Chrome → `chrome://extensions` → turn on **Developer mode** →
**Load unpacked** → choose the `extension` folder. Click its toolbar icon to open the side panel.

## Run

```powershell
python main.py research "Acme Corp"
python main.py serve
python main.py list
```

## Notes
- The extension never navigates, scrolls, connects or messages on its own; you do every step.
  Even so, LinkedIn's User Agreement restricts tools that copy data from its site, so keep usage
  light and personal.
- Each "Read this page" makes one Claude call to structure the page text.
