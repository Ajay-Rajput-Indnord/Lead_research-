"""SQLite storage: `companies` and `employees`, linked by company_id."""
import json
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    company_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    domain            TEXT,
    website           TEXT,
    summary           TEXT,
    research_json     TEXT,     -- raw Exa research (JSON)
    insights_json     TEXT,     -- Claude insights (JSON)
    status            TEXT NOT NULL DEFAULT 'confirmed',
                                -- confirmed | researched | approved | rejected | people_fetched
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS employees (
    employee_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id        INTEGER NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    pdl_id            TEXT,
    full_name         TEXT,
    job_title         TEXT,
    job_title_levels  TEXT,     -- JSON list e.g. ["cxo"]
    job_title_role    TEXT,
    work_email        TEXT,
    personal_emails   TEXT,     -- JSON list
    linkedin_url      TEXT,
    location          TEXT,
    raw_json          TEXT,     -- full PDL record
    email_subject     TEXT,
    email_body        TEXT,
    email_status      TEXT NOT NULL DEFAULT 'none',  -- none | drafted | approved | sent | skipped
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, pdl_id)
);

CREATE INDEX IF NOT EXISTS idx_employees_company ON employees(company_id);
"""

# Columns added for the LinkedIn extension flow. Added with ALTER TABLE so existing DBs upgrade.
EXTRA_COLUMNS = {
    "companies": {
        "linkedin_url": "TEXT",          # https://www.linkedin.com/company/<slug>
        "linkedin_json": "TEXT",         # {"about": {...}, "posts": [...]} read via the extension
    },
    "employees": {
        "source": "TEXT DEFAULT 'pdl'",  # pdl | linkedin
        "linkedin_headline": "TEXT",
        "linkedin_profile_json": "TEXT", # details read from the person's profile page
        "connect_score": "INTEGER",      # 1-10, from Claude
        "connect_reason": "TEXT",
        "connect_angle": "TEXT",
        "connection_note": "TEXT",       # <=300 chars, for the LinkedIn connect request
        "followup_message": "TEXT",      # message to send after they accept
        "outreach_status": "TEXT DEFAULT 'none'",  # none | suggested | drafted | sent | skipped
        "email_sent_at": "TEXT",
    },
}


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, cols in EXTRA_COLUMNS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# ---------- companies ----------

def add_company(name: str, domain: str | None, website: str | None, summary: str | None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO companies (name, domain, website, summary) VALUES (?, ?, ?, ?)",
            (name, domain, website, summary),
        )
        return cur.lastrowid


def update_company(company_id: int, **fields) -> None:
    for key in ("research_json", "insights_json", "linkedin_json"):
        if key in fields and fields[key] is not None and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE companies SET {cols}, updated_at = datetime('now') WHERE company_id = ?",
            (*fields.values(), company_id),
        )


def get_company(company_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM companies WHERE company_id = ?", (company_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    for key in ("research_json", "insights_json", "linkedin_json"):
        data[key] = json.loads(data[key]) if data.get(key) else None
    return data


def list_companies() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.company_id, c.name, c.domain, c.status,
                      COUNT(e.employee_id) AS people
               FROM companies c LEFT JOIN employees e ON e.company_id = c.company_id
               GROUP BY c.company_id ORDER BY c.company_id"""
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- employees ----------

def add_employees(company_id: int, people: list[dict]) -> int:
    """Insert PDL person records; returns number of new rows."""
    inserted = 0
    with connect() as conn:
        for p in people:
            work_email = p.get("work_email")
            cur = conn.execute(
                """INSERT OR IGNORE INTO employees
                   (company_id, pdl_id, full_name, job_title, job_title_levels, job_title_role,
                    work_email, personal_emails, linkedin_url, location, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    company_id,
                    p.get("id"),
                    p.get("full_name"),
                    p.get("job_title"),
                    json.dumps(p.get("job_title_levels") or []),
                    p.get("job_title_role"),
                    work_email if isinstance(work_email, str) else None,
                    json.dumps(p.get("personal_emails") if isinstance(p.get("personal_emails"), list) else []),
                    p.get("linkedin_url"),
                    p.get("location_name"),
                    json.dumps(p, ensure_ascii=False),
                ),
            )
            inserted += cur.rowcount
    return inserted


def get_employees(company_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM employees WHERE company_id = ? ORDER BY employee_id", (company_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_employee(employee_id: int, **fields) -> None:
    if "linkedin_profile_json" in fields and not isinstance(fields["linkedin_profile_json"], str):
        fields["linkedin_profile_json"] = json.dumps(fields["linkedin_profile_json"], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE employees SET {cols} WHERE employee_id = ?", (*fields.values(), employee_id))


def get_employee(employee_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,)).fetchone()
    return dict(row) if row else None


def upsert_linkedin_person(company_id: int, name: str, headline: str | None,
                           profile_url: str, location: str | None = None) -> tuple[int, bool]:
    """Insert or update a person seen on LinkedIn, keyed by profile URL. Returns (id, created)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT employee_id FROM employees WHERE company_id = ? AND linkedin_url = ?",
            (company_id, profile_url),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE employees SET full_name = COALESCE(?, full_name),
                       linkedin_headline = COALESCE(?, linkedin_headline),
                       location = COALESCE(?, location)
                   WHERE employee_id = ?""",
                (name, headline, location, row["employee_id"]),
            )
            return row["employee_id"], False
        cur = conn.execute(
            """INSERT INTO employees (company_id, pdl_id, full_name, job_title, linkedin_headline,
                                      linkedin_url, location, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'linkedin')""",
            (company_id, f"li:{profile_url}", name, headline, headline, profile_url, location),
        )
        return cur.lastrowid, True
