"""Command line entry point.

  python main.py research "Company Name"   # Agent 1 (then optionally Agent 2)
  python main.py email <company_id>        # Agent 2 only (needs PDL people with emails)
  python main.py serve                     # local API for the LinkedIn Chrome extension
  python main.py list                      # show companies in the DB
"""
import argparse

import db
from agents import email_agent, research_agent
from agents.ui import ask_yes_no


def main() -> None:
    parser = argparse.ArgumentParser(description="Lead research + outreach agents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_research = sub.add_parser("research", help="Agent 1: research a company")
    p_research.add_argument("company", nargs="?", help="company name")

    p_email = sub.add_parser("email", help="Agent 2: write outreach emails")
    p_email.add_argument("company_id", type=int)
    p_email.add_argument("--redo", action="store_true", help="rewrite emails already approved/skipped")

    sub.add_parser("list", help="list companies")
    sub.add_parser("serve", help="start the local API for the Chrome extension")
    p_test = sub.add_parser("test-email", help="send a test email to check your SMTP settings")
    p_test.add_argument("to", help="address to send the test to (e.g. your own)")
    args = parser.parse_args()

    if args.cmd == "test-email":
        from clients import mailer
        try:
            mailer.send(args.to, "Lead Research: SMTP test", "If you can read this, email sending works.")
            print(f"Test email sent to {args.to}.")
        except mailer.MailError as exc:
            raise SystemExit(f"Failed: {exc}")
        return

    if args.cmd == "serve":
        import uvicorn

        from config import API_HOST, API_PORT
        print(f"API running on http://{API_HOST}:{API_PORT}  (Ctrl+C to stop)")
        uvicorn.run("api:app", host=API_HOST, port=API_PORT)
        return

    if args.cmd == "research":
        name = args.company or input("Company name: ").strip()
        company_id = research_agent.run(name)
        if company_id and ask_yes_no("\nRun the email agent for these people now?"):
            email_agent.run(company_id)
    elif args.cmd == "email":
        email_agent.run(args.company_id, redo=args.redo)
    else:
        db.init_db()
        rows = db.list_companies()
        if not rows:
            print("No companies yet.")
        for r in rows:
            print(f"#{r['company_id']:<4} {r['name']:<35} {r['domain'] or '-':<28} "
                  f"{r['status']:<15} {r['people']} people")


if __name__ == "__main__":
    main()
