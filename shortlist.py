"""Daily apply-list: the freshest HK jobs Irmin can actually apply to, ranked.

Reads the category JSON the scraper produced (local files, or the live site with
--live) and keeps only jobs that are:
  - posted within --days (default 4: apply in the first days, before the pile-up)
  - not requiring Chinese (read from the full ad; 'Preferred' is kept but flagged)
  - not Director+ (and not Manager unless --managers; IC roles are where every
    interview so far has come from)
  - not already applied to (resume/outreach/tracker.csv, company + role)

Runs locally only. The tracker is private and gitignored; nothing here is deployed.

Usage:
    python shortlist.py                  # top 15 from local data
    python shortlist.py --live           # use what is on climbthesearches.com
    python shortlist.py --days 2 --top 25 --managers
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "frontend", "public", "data")
LIVE = "https://climbthesearches.com/hk-jobs/data"
TRACKER = os.path.join(HERE, "resume", "outreach", "tracker.csv")
HKT = timezone(timedelta(hours=8))

# Categories where his interviews actually came from get a boost.
# Data roles stay the main focus; web dev, AI agents and technical SEO widen the net.
CORE = {"Data Analyst": 12, "Business Intelligence": 12, "Fraud & Risk Analyst": 10,
        "AI Agents & Automation": 8, "Technical SEO": 7, "Web Developer": 7,
        "Product Analyst": 6, "Python & Automation": 5, "Full Stack Developer": 3,
        "Data Engineer": 2}
# Data engineering is ~10% of applications by choice (2026-10-08): browse it in the
# Data Engineer category, never let it crowd the daily top picks.
EXCLUDED_TITLES = re.compile(
    r"data scientist|research scientist|data engineer|data engineering|data platform|etl developer",
    re.IGNORECASE)


def norm(s):
    s = re.sub(r"\b(limited|ltd|hong kong|hk|company|co|inc|group|holdings?)\b", " ",
               str(s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load_jobs(live):
    """Return {url: job}, tagging each with the categories it appeared in."""
    def files():
        if live:
            def get(name):
                req = Request(f"{LIVE}/{name}?v={datetime.now().timestamp():.0f}",
                              headers={"User-Agent": "hk-shortlist"})
                with urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            for e in get("index.json")["data"]:
                yield get(e["file"])
        else:
            for f in glob.glob(os.path.join(DATA_DIR, "*_hong-kong.json")):
                with open(f, encoding="utf-8") as fh:
                    yield json.load(fh)

    jobs = {}
    for data in files():
        cat = data.get("meta", {}).get("query", "")
        for j in data.get("jobs", []):
            key = (j.get("url") or "").rstrip("/").lower()
            if not key:
                continue
            if key in jobs:
                jobs[key]["cats"].add(cat)
                jobs[key]["relevance"] = max(jobs[key]["relevance"], j.get("relevance") or 0)
            else:
                jobs[key] = dict(j, cats={cat}, relevance=j.get("relevance") or 0)
    return list(jobs.values())


def load_applied():
    """(company, role) pairs and companies from the tracker."""
    pairs, companies = set(), set()
    if not os.path.exists(TRACKER):
        print(f"  (no tracker at {TRACKER}; nothing excluded as already applied)")
        return pairs, companies
    with open(TRACKER, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            c = norm(row.get("company"))
            if c:
                companies.add(c)
                pairs.add((c, norm(row.get("role"))))
    return pairs, companies


def score(j, age):
    s = (j.get("relevance") or 0) * 0.5 + (j.get("match") or 0) * 0.4
    s += max(CORE.get(c, 0) for c in j["cats"]) if j["cats"] else 0
    s += {0: 15, 1: 12, 2: 8, 3: 5}.get(age, 2)          # apply early
    if j.get("mandarin") == "Preferred":
        s -= 8
    if j.get("ttps"):
        s += 3
    return round(s, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4, help="posted within N days (default 4)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--live", action="store_true", help="read the deployed site's data")
    ap.add_argument("--managers", action="store_true", help="include Manager-level roles")
    ap.add_argument("--csv", help="also write the list to this CSV path")
    args = ap.parse_args()

    today = datetime.now(HKT).date()
    jobs = load_jobs(args.live)
    applied_pairs, applied_cos = load_applied()

    drop = {"too old / undated": 0, "Chinese required": 0, "level": 0, "already applied": 0}
    keep = []
    for j in jobs:
        try:
            age = (today - date.fromisoformat(j.get("date", ""))).days
        except ValueError:
            age = None
        if age is None or age > args.days:
            drop["too old / undated"] += 1; continue
        if EXCLUDED_TITLES.search(j.get("title") or ""):
            drop["level"] += 1; continue
        if j.get("mandarin") == "Required":
            drop["Chinese required"] += 1; continue
        level = j.get("seniority") or ""
        if level == "Director+" or (level == "Manager" and not args.managers):
            drop["level"] += 1; continue
        c, r = norm(j.get("company")), norm(j.get("title"))
        if (c, r) in applied_pairs:
            drop["already applied"] += 1; continue
        j["age"] = max(age, 0)
        j["prior"] = c in applied_cos and c != "confidential"
        j["score"] = score(j, j["age"])
        keep.append(j)

    keep.sort(key=lambda j: j["score"], reverse=True)
    top = keep[:args.top]

    print(f"\nHK apply-list for {today} (HKT): {len(jobs)} jobs scanned, "
          f"{len(keep)} eligible, showing {len(top)}")
    print("  dropped: " + ", ".join(f"{k} {v}" for k, v in drop.items()))
    print("  flags: [pref] Chinese preferred  [prior] already applied at this company\n")
    for i, j in enumerate(top, 1):
        flags = " ".join(f for f, on in (("[pref]", j.get("mandarin") == "Preferred"),
                                          ("[prior]", j["prior"]),
                                          ("[NEW]", j.get("first_seen") == str(today))) if on)
        skills = ", ".join((j.get("skills") or [])[:4])
        posted = "today" if j["age"] == 0 else f"{j['age']}d ago"
        print(f"{i:>2}. {j['title'][:70]}  |  {j['company'][:40]}  {flags}")
        print(f"    {posted} | {j.get('seniority') or '-'} | {j.get('work_mode') or '-'} | "
              f"{j.get('salary') or '-'} | match {j.get('match')}% | {skills}")
        print(f"    {j['url']}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["rank", "posted", "title", "company", "level", "mode", "salary",
                        "match", "chinese", "prior_application", "url"])
            for i, j in enumerate(top, 1):
                w.writerow([i, j.get("date"), j["title"], j["company"], j.get("seniority"),
                            j.get("work_mode"), j.get("salary"), j.get("match"),
                            j.get("mandarin") or "", "yes" if j["prior"] else "", j["url"]])
        print(f"\nWrote {len(top)} rows to {args.csv}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
