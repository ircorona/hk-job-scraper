# Hong Kong Job Market Intelligence

## Project
- **Live URL**: https://climbthesearches.com/hk-jobs/
- **GitHub**: https://github.com/ircorona/hk-job-scraper (public, CI green)
- **Hosting**: Hostinger shared hosting, FTP deploy
- **Architecture**: Python scrapers → Pydantic models → salary estimation → Astro.js frontend → GitHub Actions daily
- **Purpose**: Personal job hunting tool + portfolio piece for TTPS visa holder (Irmin Corona)

## Data Sources (7 working)
- **JobSpy** — wraps LinkedIn + Indeed + Google Jobs, no API keys
- **JobsDB** — search API `hk.jobsdb.com/api/jobsearch/v5/search` (keywords, `dateRange=14`) + GraphQL `jobDetails` for the full ad
- **Lever API** — Binance (138 HK jobs), Animoca Brands, Lalamove, Crypto.com
- **Greenhouse API** — OKX (131 HK jobs), Agoda
- **LinkedIn** (via JobSpy) — rate-limits after page 10
- **Indeed HK** (via JobSpy) — `country_indeed="Hong Kong"`
- **Google Jobs** (via JobSpy)

## Scrapers
- `scrapers/jobspy_scraper.py` — Multi-source wrapper (LinkedIn + Indeed + Google)
- `scrapers/jobsdb.py` — JobsDB search API + budgeted full-ad fetch (see Freshness section)
- `scrapers/lever.py` — Generic multi-company Lever ATS scraper
- `scrapers/greenhouse.py` — Generic multi-company Greenhouse ATS scraper
- `scrapers/base.py` — Abstract base class
- Dead scrapers deleted: adzuna.py, binance.py, linkedin.py

## Salary Estimation Engine
- Calibrated from verified sources (March 2026):
  - JobsDB HK salary pages (median by role)
  - FastLane HR 2025 HK salary benchmarks
  - Robert Half 2026 HK Salary Guide
- 5 signals: seniority level, company tier, role category, TTPS status, premium skills
- Confidence: High (3+ signals), Medium (1-2), Low (baseline only)
- Code: `estimate_salary()` in `generate_data.py`

## Category Relevance
- 12 categories with title-based relevance scoring
- Each category shows distinct, relevant results (not the same Binance jobs everywhere)
- `CATEGORY_TITLE_KEYWORDS` maps category → title keywords
- Sort: relevance first, match score as tiebreaker

## Frontend (Astro.js)
- Located in `frontend/` — Astro 5 + Tailwind CSS 3
- **Theme**: Orange/black dark theme
- **CRITICAL**: `astro.config.mjs` has `applyBaseStyles: false` — global.css must be imported in Layout.astro
- **Layout**: Header → Stats (5 KPI cards) → Charts (open by default) → FilterBar (sticky) → SkillTags → JobTable → TaxCalculator → Methodology → Footer (builder banner)
- **Table**: Fit dots (green/yellow/gray) + Match % bar + title + company + salary + level + mode + source
- **Mobile**: Cards only (no table), 2x2 stats grid, full-width search, view toggle hidden
- **Inter font**: Loaded from Google Fonts in Layout.astro
- Charts: Chart.js (salary brackets, salary by seniority, top employers)
- Export: XLSX.js for Excel download

## Tests
- 165 pytest tests in `tests/` (incl. `test_freshness.py`, `test_jobsdb_guardrails.py`, `test_language_filter.py`)
- `test_models.py` — JobListing Pydantic model validation
- `test_dedup.py` — 3-layer deduplication logic
- `test_scoring.py` — Skill matching, TTPS detection, work mode, seniority

## Resume Pipeline (python-docx — CURRENT as of Aug 2026)
- **Master DOCX**: `resume/master/Irmin_Corona_Resume.docx` — the single source every
  tailored build reads. Backup of the pre-ATS version:
  `resume/master/backup_Irmin_Corona_Resume_pre-ATS_20260803.docx`
- **How tailoring works now**: each package has its own `build_resume.py` that loads the
  master and rewrites paragraph *runs* in place, so the master's formatting survives.
  Pattern: `resume/tailored/<company>_<role>_<YYYYMMDD>/build_resume.py`
- **ALWAYS resolve paragraphs via `resume/tailored/_master_layout.py` → `slots(doc)`.**
  NEVER hardcode paragraph indices. Removing one paragraph from the master on 2026-08-03
  shifted every index below it and three build scripts silently wrote the summary on top
  of the SKILLS heading with no error. `slots()` anchors on section headings and raises
  SystemExit if the layout changes. It expects exactly **5 skill lines** — adding a 6th
  means updating `slots()` too.
- **Page count**: verify with Word COM `ComputeStatistics(2)`. Word count is NOT a proxy —
  a build came out 2 pages with *fewer* words than the 1-page master, because bullets that
  spill one character past a line break cost a whole line.
- **PDF export is broken on this machine**: no pywin32, no LibreOffice, no docx2pdf, and
  Word COM `ExportAsFixedFormat`/`SaveAs2` HANGS (kill WINWORD if it does). Export PDFs by
  hand from Word. `ComputeStatistics` works fine.
- **Legacy (mostly unused)**: `resume/tailor.py` (JD URL → YAML + PDF) and RenderCV v2.3
  + Typst margin patch. `tailor.py` auto-injects crypto claims — always scrub.
- **Outreach script**: `resume/outreach.py` — Hunter.io + banner + email draft
- **Tracker**: `resume/outreach/tracker.csv` — logs all contacts, emails, statuses.
  KEEP IT CURRENT. A stale "not yet applied" note caused a wrong recommendation on
  2026-08-03; always confirm against his JobsDB "applied" list.

## ATS Rules (master audited + hardened 2026-08-03)
The master is clean and must stay that way. Audit result: 0 tables, 0 text boxes,
0 images, nothing in headers/footers, single column, single section, Calibri.
- **Section headings must stay standard**: `PROFESSIONAL SUMMARY`, `SKILLS`,
  `WORK EXPERIENCE`, `EDUCATION`, `CERTIFICATIONS`. Parsers are trained on these;
  creative names ("Core Competencies") map to nothing.
- **Banned characters** (older parsers, Taleo especially, strip these or emit `?`):
  `·` → `|`, em/en dash → `-`, `→` → `to`, curly quotes/apostrophes → straight.
  The round bullet `•` is KEPT — it is explicitly ATS-safe.
- **No duplicate TTPS.** One line only, in the header block:
  `Hong Kong | TTPS Activated (no sponsorship needed) | Available immediately`
- Submit **DOCX** wherever allowed (safest across Workday/Taleo). Some ads demand Word only.
- Re-run `resume/master/make_ats_safe.py` if the master is ever edited by hand.
- **Known keyword gaps in the master** (all TRUE of him, just not worded that way):
  stakeholder management, requirements gathering, KPI, reconciliation, machine learning,
  data analysis (exact phrase), cross-functional, mentoring, process improvement,
  data governance/quality, Google Analytics. Tailored builds add these per role.
  **Do NOT add**: Tableau, JIRA, Agile/Scrum, Lean Six Sigma, AML — he cannot defend them.

## Resume Key Facts
- 2 pages is acceptable (user's call, 2026-08-03). Don't cut real content to force 1 page.
- No blockchain/Web3/LangChain/Claude API claims (user's explicit decision)
- "Beyond Copilot" AI training series included under Stellantis — it is the proof for
  mentoring, change management AND "AI prompting" requirements that most BAs can't evidence
- TTPS in middle of email (not top) — lead with qualifications
- Phone: +66822855985
- **Ling growth = 1,000 to 1 million monthly visits in 6 MONTHS** (not 18 — Claude wrote 18
  into two resumes that were sent on 2026-08-03). Verify numbers against the master.
- Education is a **Bachelor's in Business Management (UNAM)** — not IS/CS, and no Master's.
  Roles stating either as a hard requirement are a real gap, not a soft one.
- climbthesearches.com only in Portfolio section (not duplicated)

## Installed Claude Skills (job-search related)
- `resume-ats-optimizer` + `application-form-filler` (paramchoudhary/resumeskills, Aug 3)
- `cold-email` + `humanize-writing` (Jul 22)
- Treat all of them as ADVISORY. They generate claims he may not be able to defend, and
  must never edit the master directly. Skill search tip: query "resume", not "ats"
  (which matches "bats-testing", "caveman-stats", "whatsapp").

## Outreach Rules (non-negotiable)
1. ONE email per person, ever
2. ONE contact per company
3. Follow-up: exactly 1, at Day 7, only if no reply. Then stop.
4. HK-verified only: confirm location on LinkedIn before sending
5. Never email generic inboxes (info@, careers@, hello@, hr@)
6. Draft email → show user → wait for approval → send
7. Send from Gmail (irminorta@gmail.com), not business domain
8. No blockchain/crypto claims in emails unless user approves

## Job Search Status (March 30, 2026)
- **14 applications** across Binance (4), OKX (3), Lalamove (2), ConnectedSolutions (1), Argyll Scott (1), Accenture (1), others (2)
- **5 emails sent** to verified HK contacts
- **2 warm leads**: Lalamove (Carmen replied, Kitty Lee reaching out), ConnectedSolutions (Dalvinder connected on LinkedIn)
- **Follow-ups due**: Binance (Apr 5), OKX (Apr 5)
- **Skip banks**: User prefers tech/crypto/fintech culture (not conservative banking)
- **Target salary**: HK$50-55K/month

## FTP Deploy
- **ALWAYS use** `python deploy.py` (ftplib FTP_TLS). Plain FTP stopped working
  2026-10-06: the server resets the session after login.
- **Daily workflow was auto-disabled by GitHub** (60 days without commits) and froze the
  live data on 2026-05-29. A keep-alive step now re-enables it each run. If the live
  `index.json` "generated" date is old, check `gh workflow list --all` first.
- Hostinger CDN edges can serve the old `data/index.json` for a few minutes after a deploy;
  verify by fetching it several times, not once.
- **NEVER use** `lftp`, `ftps://` protocol, or `SamKirkland/FTP-Deploy-Action`
- Hostinger uses explicit FTPS (AUTH TLS on port 21)
- All credentials in `.env` (gitignored) and GitHub Secrets

## Credentials & Secrets
All in `.env` (gitignored) and GitHub Secrets:
- `FTP_HOST` — Hostinger FTP server IP
- `FTP_USER` — Hostinger FTP username
- `FTP_PASS` — Hostinger FTP password
- `HUNTER_API_KEY` — Hunter.io API key

## Freshness pipeline (2026-10-07)
- **Why**: JobsDB used to scan job IDs down from a hardcoded 90,000,000. Live IDs passed
  that in Jan 2026, so CI shipped ~100 January ads for 8 months, and the UI never showed
  a posted date, so nobody could tell. Live IDs were ~95.1M on 2026-10-07.
- **JobsDB** now uses keyword search (`sortmode=KeywordRelevance`, `dateRange=14`, 100 per
  query), keeps an ad only if every query word is in title + full text, and fetches the full
  ad via GraphQL (3 concurrent, 0.3s, cap `MAX_DETAIL_FETCHES=2500`/run, falls back to the
  teaser past the cap or after 5 straight RATE_LIMITED). ~500 fetches per 9 queries, no throttling.
- `generate_data.py`: `clean_posted_date()` turns 0001-01-01 / future dates into unknown;
  ads older than `MAX_AGE_DAYS=45` are dropped (Lever/Greenhouse evergreen reqs from 2022).
- `first_seen` per job is carried over from the LIVE site (`load_first_seen()`), because CI
  starts from an empty checkout. This powers the NEW badge and "new today".
- `index.json` `generated` now has a +08:00 offset; the header shows "Updated HH:MM HKT".
- Frontend defaults: posted within 7 days, Chinese-required hidden, newest first.
- `calc_skill_match` saturates at `SKILL_SCORE_FULL=15` weighted points (it used to divide by
  the whole profile, so every job scored 0-10%). Tableau/JIRA/Agile removed from the profile.
- Google Jobs via JobSpy returns 0; known dead, not a regression.

## Daily apply-list (local only)
- `python shortlist.py` (or `--live` for the deployed data): posted <=4 days, no Chinese
  required, IC level (`--managers` to include), excludes company+role pairs already in
  `resume/outreach/tracker.csv`, flags [pref] Chinese preferred and [prior] company.
  Prints the top 15 with real apply URLs; `--csv path` writes them out. Never run in CI:
  the tracker is private.

## Dashboard job pipeline (2026-10-08)
- Every job: 1 Read JD (opens ad) -> 2 Customize (copies `/tailor-application title | company | url`)
  -> 3 Applied (copies the `python funnel.py add ...` line). State is per-browser
  localStorage (`hkjobs.stage.v1`, `hkjobs.applied.v1`); the tracker is the cross-device truth.
- Status tabs (2026-10-08) above the job list: To apply / In progress (read or customized) /
  Applied / Removed. "x Remove" (not for me, `hkjobs.removed.v1`) hides a job from Apply first
  and the other tabs; the Removed tab restores it. Applied jobs cannot be removed. Filters narrow To apply only; the other two list every job with that status across
  all categories. Colours: amber stripe = read, orange = customizing, green tint = applied, red
  chip = day-7 follow-up due. A clicked job stays in place 1.5s, then moves (toast has Undo);
  Apply first refills to 8. Page opens on In progress when it is non-empty. Header shows
  "Applied this week N / 15" from the localStorage marks. Back up / Restore writes the marks to
  a JSON file (merge on restore, earliest date wins). Tailwind purges class names built by
  concatenation, so status classes are spelled out in full (`ROW_CLS`).
- Find contact: LinkedIn people-search links, Hong Kong only (geoUrn 103291313): People at
  company, Recruiter, Hiring manager (or Agency consultant for staffing agencies). Format
  TESTED in his session 2026-10-07: `company=<name>` filters CURRENT employer (putting the
  company in keywords matched past jobs too); `titleFreeText` is silently dropped, so role
  words go in `keywords`; long quoted OR-groups returned nothing for small firms. Some firms
  (e.g. Reefan) have no LinkedIn presence at all: no link can fix that, just apply.
- Frontend-only fixes can ship without the 55-min CI run: build, then curl-FTPS upload the new
  `_assets/*.css` FIRST and `index.html` second (Python ftplib data channel timed out here).

## Funnel tracking (local only, 2026-10-08)
- Goal: 3 interviews/month (was 3 in 6 months). Target 15 applications/week; at the
  measured ~4% application-to-interview rate that is the volume the goal needs.
- `python funnel.py` reads tracker.csv: weekly applications vs 15, emails, replies,
  interviews, this month vs 3, built-but-never-sent backlog, day-7 follow-ups.
- `python funnel.py add --company X --role Y --url U` logs an application with the HTTP
  date (machine clock is unreliable). Log interviews as their own row, status INTERVIEW.
- NOT on the website: the tracker holds contacts and statuses and the site is public.
  The dashboard's "Mark applied" is per-browser localStorage and does not sync to it.
- Data Engineer assessment (2026-10-08, 11 JobsDB DE ads): 82% want a CS/IT degree,
  45% Spark, 36% Databricks/Kafka. Keep DE to ~10% of applications, only roles that lead
  with SQL/Python/Power BI/Snowflake; data roles stay the main focus.

## Usage
```bash
# Full scrape (all 12 categories, 7 sources)
python generate_data.py --force

# Single category
python generate_data.py --query "data analyst" --location "Hong Kong" --force

# Resume tailoring
python resume/tailor.py --url "https://jobs.lever.co/binance/..." --render

# Outreach (dry run)
python resume/outreach.py --company binance --role "BI Data Analyst" --dry-run

# Tests
python -m pytest tests/ -v

# Frontend
cd frontend && npm run dev       # localhost:4321/hk-jobs/
cd frontend && npm run build     # production build

# Deploy
python deploy.py                 # FTP to Hostinger
```

## Session hygiene (from the /insights report, 2026-09-23)
- **Dates:** the machine clock misdated tracker rows and follow-ups twice in Aug 2026.
  Use the authoritative HTTP date printed at session start for every tracker row,
  follow-up date and package folder name. If it is missing, run
  `curl -sI https://www.google.com | grep -i ^date` before writing any date.
- **Triage in a subagent.** Scrapes return 1,400-2,300 rows; triaging them inline
  burned the context needed for tailoring, and one session ended before the third
  resume. Hand scrape + shortlist to a subagent that returns the top 8 with a
  one-line fit reason and JD link. Keep this context for the packages.
- **LinkedIn people search is auth-walled** and cannot be worked around. Ask Irmin to
  paste candidate profiles (name, title, location) up front; verify the email with
  one Hunter credit before sending. Never guess a pattern.
- **Hunter free tier** returns at most 10 results per domain-search and is useless for
  large multinational domains; find the person first, then verify.
- **`/tailor-application`** (`.claude/skills/tailor-application/`) is the checklist for
  building a package: JD language check, slots-anchored build script, typst cover,
  Word COM page count, ATS character check, tracker row. Use it instead of
  re-specifying the standard.
