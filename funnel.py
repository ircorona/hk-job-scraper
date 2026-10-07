"""Job-search funnel: are we on track for 3 interviews a month?

Reads resume/outreach/tracker.csv (private, gitignored) and reports, per week:
applications, outreach emails, replies, interviews, against the targets set on
2026-10-08: 15 applications a week, 3 interviews a month. Local only; never deployed.

Usage:
    python funnel.py                      # report (last 8 weeks + this month)
    python funnel.py --weeks 12
    python funnel.py add --company "OOCL" --role "Operations Analyst / Data Analyst" \
        --url https://hk.jobsdb.com/job/95104958          # log an application today
    python funnel.py add --company X --role Y --status INTERVIEW --notes "1st round, Teams"

Statuses the report understands (case-insensitive, matched by keyword):
    APPLIED / SENT (an outreach email) / REPLIED / ENGAGED / SCREEN / INTERVIEW /
    OFFER / REJECTED / GHOSTED_AFTER_SCREEN; BUILT / TAILORED / PACKAGE = not sent yet.
Log an interview as its own row (status INTERVIEW, date = interview day) so the
month count is right even when the application was months earlier.
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKER = os.path.join(HERE, "resume", "outreach", "tracker.csv")
FIELDS = ["date", "company", "role", "contact_name", "contact_email", "contact_position",
          "contact_location", "status", "notes"]

APPS_PER_WEEK = 15
INTERVIEWS_PER_MONTH = 3
FOLLOW_UP_DAY = 7          # outreach rule: exactly one follow-up, on day 7


def today() -> date:
    """Date from an HTTP Date header: the machine clock has misdated this tracker before."""
    try:
        with urlopen(Request("https://www.google.com", method="HEAD"), timeout=8) as r:
            return parsedate_to_datetime(r.headers["Date"]).date()
    except Exception:
        print("  (could not read the HTTP date; using the machine clock - check it)")
        return date.today()


def stage(row) -> str:
    """Map a free-text status onto one funnel stage."""
    s = f"{row.get('status', '')}".lower()
    notes = f"{row.get('notes', '')}".lower()
    if "offer" in s:
        return "offer"
    if "interview" in s or "screen" in s or re.search(r"interview (done|completed)|screening call completed", notes):
        return "interview"
    if "repl" in s or "engaged" in s:
        return "reply"
    if "reject" in s:
        return "rejected"
    if re.search(r"built|tailored|package|profile|skipped|not applied|froze", s):
        return "not_sent"
    if "applied" in s:
        return "applied"
    if "sent" in s:
        return "outreach"
    return "other"


def parse_date(v):
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def load():
    if not os.path.exists(TRACKER):
        sys.exit(f"No tracker at {TRACKER}")
    with open(TRACKER, encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def report(weeks: int):
    rows = load()
    now = today()
    staged = [(parse_date(r["date"]), stage(r), r) for r in rows]
    staged = [x for x in staged if x[0]]

    # Weekly table
    per = defaultdict(lambda: defaultdict(int))
    for d, st, _ in staged:
        per[week_start(d)][st] += 1
    print(f"\nJob-search funnel, {now} | targets: {APPS_PER_WEEK} applications/week, "
          f"{INTERVIEWS_PER_MONTH} interviews/month\n")
    print(f"{'week of':<11} {'applied':>7} {'emails':>6} {'replies':>7} {'interv.':>7}  vs target")
    first = week_start(now) - timedelta(weeks=weeks - 1)
    wk = first
    while wk <= week_start(now):
        p = per[wk]
        apps = p["applied"] + p["reply"] + p["interview"] + p["rejected"] + p["offer"]
        bar = "#" * min(apps, APPS_PER_WEEK) + "." * max(0, APPS_PER_WEEK - apps)
        print(f"{wk.isoformat():<11} {apps:>7} {p['outreach']:>6} {p['reply']:>7} "
              f"{p['interview']:>7}  {bar} {apps}/{APPS_PER_WEEK}")
        wk += timedelta(weeks=1)

    # This month
    m0 = now.replace(day=1)
    month = [x for x in staged if x[0] >= m0]
    m_int = sum(1 for _, st, _ in month if st in ("interview", "offer"))
    m_apps = sum(1 for _, st, _ in month if st not in ("not_sent", "outreach", "other"))
    days_left = ((m0.replace(year=m0.year + (m0.month == 12), month=m0.month % 12 + 1)) - now).days
    print(f"\nThis month ({m0:%B}): {m_apps} applications, {m_int}/{INTERVIEWS_PER_MONTH} interviews, "
          f"{days_left} days left")

    # All-time conversion
    sent = sum(1 for _, st, _ in staged if st not in ("not_sent", "other", "outreach"))
    ints = sum(1 for _, st, _ in staged if st in ("interview", "offer"))
    if sent:
        rate = ints / sent
        need = round(INTERVIEWS_PER_MONTH / rate) if rate else None
        print(f"All time: {sent} applications -> {ints} interviews logged ({rate:.1%}). "
              + (f"At this rate {INTERVIEWS_PER_MONTH}/month needs ~{need} applications/month "
                 f"(~{round(need / 4.3)}/week)." if need else ""))

    # Wasted effort and follow-ups
    backlog = [r for d, st, r in staged if st == "not_sent"]
    if backlog:
        print(f"\nBuilt but never sent ({len(backlog)}): apply within 48h or mark SKIPPED")
        for r in backlog[-6:]:
            print(f"  {r['date']}  {r['company'][:35]} | {r['role'][:45]}")

    due = [(d, r) for d, st, r in staged
           if st in ("applied", "outreach") and (now - d).days == FOLLOW_UP_DAY]
    overdue = [(d, r) for d, st, r in staged
               if st in ("applied", "outreach") and FOLLOW_UP_DAY < (now - d).days <= 14
               and "follow" not in (r.get("notes") or "").lower()]
    if due or overdue:
        print(f"\nFollow-ups (one only, day {FOLLOW_UP_DAY}):")
        for d, r in due:
            print(f"  DUE TODAY  {r['company'][:35]} | {r['role'][:45]}")
        for d, r in overdue:
            print(f"  overdue {(now - d).days - FOLLOW_UP_DAY}d  {r['company'][:35]} | {r['role'][:45]}")

    print("\nInterviews logged:")
    for d, st, r in staged:
        if st in ("interview", "offer"):
            print(f"  {d}  {r['company'][:35]} | {r['role'][:45]} | {r['status']}")
    print("  (If one is missing, add it: python funnel.py add --status INTERVIEW ...)")


def add(args):
    d = today().isoformat()
    row = {k: "" for k in FIELDS}
    row.update(date=d, company=args.company, role=args.role, status=args.status.upper(),
               contact_name=args.contact or "",
               notes=" ".join(x for x in [args.url or "", args.notes or ""] if x))
    with open(TRACKER, "a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writerow(row)
    print(f"Logged {d}: {row['status']} | {row['company']} | {row['role']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=8)
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add", help="append a row to the tracker with today's date")
    a.add_argument("--company", required=True)
    a.add_argument("--role", required=True)
    a.add_argument("--status", default="APPLIED")
    a.add_argument("--url")
    a.add_argument("--contact")
    a.add_argument("--notes")
    args = ap.parse_args()
    if args.cmd == "add":
        add(args)
    else:
        report(args.weeks)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
