"""
Generate JSON data files for the static frontend (Hong Kong).
Targeted for TTPS visa holder profile: data analyst, web dev, technical SEO.

Usage:
    python generate_data.py                    # all categories
    python generate_data.py --query "data analyst" --location "Hong Kong"
    python generate_data.py --force            # ignore cache
"""

import asyncio
import json
import os
import sys
import re
import argparse
from collections import Counter
from datetime import datetime, date, timedelta, timezone
from urllib.request import Request, urlopen

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers.jobspy_scraper import JobSpyScraper
from scrapers.jobsdb import JobsDBScraper
from scrapers.lever import LeverScraper
from scrapers.greenhouse import GreenhouseScraper
from merge import deduplicate
from utils.analysis import extract_keywords, detect_mandarin, extract_max_salary, clean_salary, HARD_SKILLS, SOFT_SKILLS

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "public", "data")
LIVE_DATA_URL = "https://climbthesearches.com/hk-jobs/data"
HKT = timezone(timedelta(hours=8))

# Ads older than this are dropped. Lever and Greenhouse keep "evergreen" reqs open
# for years (2022 dates were live on the site); nobody applies to those in time.
MAX_AGE_DAYS = 45

# url -> first date this pipeline saw the job, carried over from the live site.
# CI starts from an empty checkout every day, so without this every job would look
# brand new every run and "new since yesterday" would be meaningless.
FIRST_SEEN: dict = {}


def today_hk() -> date:
    return datetime.now(HKT).date()


def clean_posted_date(value, today: date = None):
    """Return a sane posted date or None.

    Sources emit junk: 0001-01-01 placeholders and dates in the future. A bad date
    must read as "unknown", never as "fresh".
    """
    today = today or today_hk()
    if not value:
        return None
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    if d.year < 2020 or d > today + timedelta(days=1):
        return None
    return d


def load_first_seen(index_url: str = None) -> dict:
    """Build url -> first_seen from the live site (falls back to local files).

    Rows deployed before first_seen existed get their posted date, or the day the
    live index was generated, so the first run after this change does not flag
    every job as new.
    """
    seen = {}

    def absorb(data, fallback):
        for j in data.get("jobs", []):
            url = j.get("url")
            if not url:
                continue
            first = j.get("first_seen") or j.get("date") or fallback
            if first and (url not in seen or first < seen[url]):
                seen[url] = first[:10]

    try:
        base = index_url or LIVE_DATA_URL
        def get(name):
            req = Request(f"{base}/{name}?v={datetime.now().timestamp():.0f}",
                          headers={"User-Agent": "hk-job-scraper/first-seen"})
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        index = get("index.json")
        fallback = (index.get("generated") or "")[:10]
        for entry in index.get("data", []):
            try:
                absorb(get(entry["file"]), fallback)
            except Exception as e:
                print(f"  first_seen: could not read live {entry.get('file')}: {e}")
        print(f"  first_seen: {len(seen)} known jobs from the live site")
    except Exception as e:
        print(f"  first_seen: live site unreadable ({e}); using local files")
        import glob as _glob
        for f in _glob.glob(os.path.join(DATA_DIR, "*_hong-kong.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    absorb(json.load(fh), "")
            except Exception:
                continue
        print(f"  first_seen: {len(seen)} known jobs from local files")
    return seen

# ==========================================================================
# CATEGORIES — targeted for Irmin's profile
# Each category has multiple search queries to maximize coverage
# ==========================================================================
CATEGORIES = {
    # Tier 1 — Core data roles
    "data-analyst": {
        "label": "Data Analyst",
        "queries": ["data analyst", "reporting analyst", "business analyst data"],
    },
    "business-intelligence": {
        "label": "Business Intelligence",
        "queries": ["power bi", "BI analyst", "business intelligence"],
    },
    "fraud-risk-analyst": {
        "label": "Fraud & Risk Analyst",
        "queries": ["fraud analyst", "risk analyst", "AML analyst"],
    },
    "analytics-manager": {
        "label": "Analytics Manager",
        "queries": ["analytics manager", "data analytics lead", "head of analytics"],
    },

    # Tier 2 — Web dev + SEO
    "web-developer": {
        "label": "Web Developer",
        "queries": ["web developer", "PHP developer", "WordPress developer"],
    },
    "full-stack-developer": {
        "label": "Full Stack Developer",
        "queries": ["full stack developer", "javascript developer", "frontend developer"],
    },
    "seo-digital-marketing": {
        "label": "SEO & Digital Marketing",
        "queries": ["technical SEO", "SEO specialist", "SEO manager", "digital marketing analyst"],
    },

    # Tier 3 — Emerging + adjacent
    "data-engineer": {
        "label": "Data Engineer",
        "queries": ["data engineer", "analytics engineer", "data pipeline"],
    },
    "product-analyst": {
        "label": "Product Analyst",
        "queries": ["product analyst", "growth analyst", "insights analyst"],
    },
    "python-automation": {
        "label": "Python & Automation",
        "queries": ["python developer", "python analyst", "process automation"],
    },
    "ai-data-science": {
        "label": "AI & Data Science",
        "queries": ["data scientist", "machine learning analyst", "AI analyst"],
    },
    "fintech": {
        "label": "Fintech",
        "queries": ["fintech analyst", "fintech data", "fintech"],
    },
}

# ==========================================================================
# SKILL MATCH SCORING — against Irmin's actual stack
# ==========================================================================
PROFILE_SKILLS = {
    # Weight 3 — expert level
    "power bi": 3, "sql": 3, "python": 3, "excel": 3,
    "data analysis": 3, "fraud": 3, "seo": 3, "technical seo": 3,
    "reporting": 3, "dashboard": 3,

    # Weight 2 — strong
    "r": 2, "php": 2, "mysql": 2, "javascript": 2,
    "wordpress": 2, "snowflake": 2, "power automate": 2,
    "google search console": 2, "google analytics": 2,
    "bi": 2, "kpi": 2, "html": 2, "css": 2,
    "aml": 2, "kyc": 2, "risk management": 2,
    "copilot": 2, "ai builder": 2, "data governance": 2, "data quality": 2,
    "stakeholder": 1, "reconciliation": 1,

    # Weight 1 — familiar / learning
    "machine learning": 1, "pytorch": 1, "langchain": 1,
    "docker": 1, "git": 1, "api": 1, "django": 1,
    "azure": 1, "aws": 1,
    "react": 1, "node.js": 1,
    "dbt": 1, "bigquery": 1,
    # Tableau, JIRA and Agile/Scrum deliberately absent: he cannot defend them
    # in an interview (CLAUDE.md ATS rules), so they must not raise a match.
    "sem": 1, "google ads": 1,
}


def calc_skill_match(title: str, description: str, skills_found: list) -> int:
    """Score 0-100 based on how well a job matches the profile.
    Also checks job title for role-level relevance."""

    # Title-based relevance (many jobs lack descriptions)
    TITLE_KEYWORDS = {
        "data analyst": 25, "bi analyst": 25, "business intelligence": 25,
        "reporting analyst": 20, "power bi": 25, "fraud analyst": 20,
        "risk analyst": 20, "web developer": 20, "full stack": 20,
        "seo": 20, "technical seo": 25, "digital marketing": 15,
        "python": 15, "data engineer": 15, "product analyst": 15,
        "analytics": 15, "dashboard": 10, "automation": 10,
        "fintech": 10, "data scientist": 15,
    }

    title_lower = str(title or "").lower()
    title_score = 0
    for kw, pts in TITLE_KEYWORDS.items():
        if kw in title_lower:
            title_score = max(title_score, pts)

    # Skill-based scoring from description
    text = str(description or "").lower()
    skill_score = 0
    matched_skills = []

    for skill, weight in PROFILE_SKILLS.items():
        if skill in skills_found or re.search(rf'\b{re.escape(skill)}\b', text):
            skill_score += weight
            matched_skills.append(skill)

    # Combine: title relevance + skill matches
    # Title alone can give up to 25, skills can give the rest
    # Saturate at SKILL_SCORE_FULL weighted points. Dividing by the weight of the
    # WHOLE profile (~100) meant an ad asking for SQL + Python + Power BI + Excel
    # scored 9%: every job looked like a non-match and the column was noise.
    skill_pct = min(1.0, skill_score / SKILL_SCORE_FULL) * 75

    # Core skill boost
    core_matches = sum(1 for s in matched_skills if PROFILE_SKILLS.get(s, 0) >= 3)
    if core_matches >= 3:
        skill_pct = min(75, skill_pct * 1.5)
    elif core_matches >= 2:
        skill_pct = min(75, skill_pct * 1.3)

    total = title_score + skill_pct
    return min(100, int(total))


# ==========================================================================
# TTPS-FRIENDLY DETECTION
# ==========================================================================
TTPS_SIGNALS = [
    r'visa\s+sponsorship', r'work\s+permit', r'right\s+to\s+work',
    r'international\s+candidates?', r'overseas\s+candidates?',
    r'diverse\s+team', r'global\s+team', r'multicultural',
    r'international\s+environment', r'expatriate', r'expat',
    r'relocation\s+(?:package|support|assistance)',
    r'open\s+to\s+all\s+nationalit', r'regardless\s+of\s+nationality',
    r'top\s+talent\s+pass', r'\bTTPS\b', r'quality\s+migrant',
]

TTPS_FRIENDLY_COMPANIES = {
    # Global banks
    "hsbc", "standard chartered", "jp morgan", "jpmorgan", "goldman sachs",
    "morgan stanley", "citibank", "citi", "ubs", "credit suisse", "barclays",
    "bnp paribas", "deutsche bank", "nomura", "macquarie",
    # Big 4 + consulting
    "deloitte", "pwc", "pricewaterhousecoopers", "ey", "ernst & young",
    "kpmg", "mckinsey", "bcg", "bain", "accenture", "capgemini",
    # Tech giants
    "google", "meta", "microsoft", "amazon", "apple", "oracle", "ibm",
    "salesforce", "sap", "adobe",
    # Asian tech
    "alibaba", "tencent", "bytedance", "tiktok", "huawei", "xiaomi",
    "meituan", "jd.com", "baidu", "grab", "shopee", "lazada",
    # HK fintech / startups
    "revolut", "wise", "stripe", "airwallex", "welab", "zurich",
    "klook", "lalamove", "gogovan", "animoca", "animoca brands", "hashkey",
    # Exchanges / crypto
    "hkex", "hong kong exchanges",
    "binance", "okx", "bybit", "circle", "ripple", "crypto.com",
    "bending spoons", "agoda",
}


def detect_ttps_friendly(description: str, company: str) -> bool:
    """Check if job/company is likely TTPS-friendly."""
    text = str(description or "").lower()
    comp = str(company or "").lower()

    # Check company name against known TTPS-friendly list
    for name in TTPS_FRIENDLY_COMPANIES:
        if name in comp:
            return True

    # Check description for TTPS signals
    for pattern in TTPS_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


# ==========================================================================
# WORK MODE DETECTION
# ==========================================================================
def detect_work_mode(description: str, job_type: str) -> str:
    """Detect remote/hybrid/on-site from description."""
    text = str(description or "").lower() + " " + str(job_type or "").lower()

    if re.search(r'\b(fully\s+remote|100%\s+remote|work\s+from\s+home|remote\s+(?:position|role|work))\b', text):
        return "Remote"
    if re.search(r'\b(hybrid|flexible\s+work|wfh\s+\d|work\s+from\s+home\s+\d)\b', text):
        return "Hybrid"
    if re.search(r'\b(on[\s-]?site|in[\s-]?office|office[\s-]?based)\b', text):
        return "On-site"
    if re.search(r'\bremote\b', text):
        return "Remote"

    return ""


# ==========================================================================
# SENIORITY DETECTION
# ==========================================================================
def detect_seniority(title: str, description: str) -> str:
    """Detect seniority level from title and description."""
    text = str(title or "").lower()

    if re.search(r'\b(head\s+of|director|vp|vice\s+president|chief|c-level)\b', text):
        return "Director+"
    if re.search(r'\b(senior|sr\.?|lead|principal)\b', text):
        return "Senior"
    if re.search(r'\b(manager|supervisor)\b', text):
        return "Manager"
    if re.search(r'\b(junior|jr\.?|entry|intern|trainee|graduate)\b', text):
        return "Junior"

    return "Mid"


# ==========================================================================
# SALARY ESTIMATION — multi-signal model for HK market
# ==========================================================================
# Calibrated from verified sources (March 2026):
#   - JobsDB HK salary pages (hk.jobsdb.com/career-advice/role/*/salary)
#   - FastLane HR 2025 HK salary benchmarks
#   - HK Census & Statistics Dept median wages (May-Jun 2025: HK$21,200)
# All values in HKD/month.

# Base salary by seniority — calibrated from JobsDB percentiles + FastLane HR
# Junior ≈ P10-P25 of JobsDB data, Mid ≈ median, Senior ≈ P75-P90
SENIORITY_BASE = {
    "Junior":    18000,   # JobsDB P10~15K, P25~25K; FastLane entry 15-25K → midpoint
    "Mid":       30000,   # JobsDB median across analyst/dev/engineer roles: 27.5-33.5K
    "Senior":    45000,   # JobsDB P90 ~45K; FastLane senior 55-90K → lower bound
    "Manager":   65000,   # FastLane senior/manager 55-80K (finance), 60-90K (IT)
    "Director+": 95000,   # FastLane/Robert Half director+ range
}

# Category multiplier — based on JobsDB median differences between roles
# Data Analyst median: HK$30,500 (baseline = 1.0)
# Developer median: HK$33,500 → 1.10x
# Software Engineer median: HK$27,500 → 0.90x
# Business Analyst median: HK$30,250 → 1.0x
# Analyst (general) median: HK$30,000 → 1.0x
CATEGORY_MULTIPLIER = {
    "Data Analyst":           1.00,   # JobsDB: HK$30,500 median
    "Business Intelligence":  1.05,   # Slightly above analyst (BI tools premium)
    "Fraud & Risk Analyst":   1.10,   # Compliance/AML roles pay premium in HK
    "Analytics Manager":      1.05,   # Management track
    "Web Developer":          0.95,   # PHP/WordPress slightly below market
    "Full Stack Developer":   1.10,   # JobsDB developer median HK$33,500
    "SEO & Digital Marketing": 0.85,  # Marketing roles below tech
    "Data Engineer":          1.15,   # Pipeline/infra skills premium
    "Product Analyst":        1.00,   # Similar to data analyst
    "Python & Automation":    1.05,   # Python premium
    "AI & Data Science":      1.20,   # ML/AI commands highest premium
    "Fintech":                1.15,   # Finance + tech intersection
}

# Company tier — premium/discount based on publicly known compensation bands
COMPANY_TIER = {
    # Tier 1: +25% (global banks, FAANG — well-documented HK premiums)
    "hsbc": 1.25, "standard chartered": 1.25, "jp morgan": 1.25, "jpmorgan": 1.25,
    "goldman sachs": 1.25, "morgan stanley": 1.25, "ubs": 1.25, "citibank": 1.25,
    "citi": 1.25, "google": 1.30, "meta": 1.30, "microsoft": 1.25, "amazon": 1.25,
    "apple": 1.30,
    # Tier 2: +15% (crypto exchanges, Big4, major tech)
    "binance": 1.15, "okx": 1.15, "bybit": 1.15, "crypto.com": 1.15,
    "deloitte": 1.15, "pwc": 1.15, "ey": 1.15, "kpmg": 1.15,
    "alibaba": 1.15, "tencent": 1.15, "bytedance": 1.20,
    "revolut": 1.10, "wise": 1.10, "stripe": 1.20, "airwallex": 1.10,
    # Tier 3: baseline (known companies)
    "lalamove": 1.0, "klook": 1.0, "agoda": 1.05, "animoca": 1.0,
}

# Skill premium keywords — capped at +15% total
PREMIUM_SKILLS = {
    "machine learning": 0.05, "deep learning": 0.05, "pytorch": 0.05,
    "tensorflow": 0.05, "kubernetes": 0.03, "aws": 0.03, "azure": 0.03,
    "blockchain": 0.05, "smart contract": 0.05, "solidity": 0.05,
    "quantitative": 0.05, "algorithmic trading": 0.08,
}


def estimate_salary(seniority: str, company: str, category_label: str,
                    description: str, ttps: bool) -> dict:
    """Estimate monthly HKD salary when not disclosed.
    Returns {"estimate": int, "confidence": str, "basis": str}."""

    # Step 1: Seniority base
    base = SENIORITY_BASE.get(seniority, SENIORITY_BASE["Mid"])

    # Step 2: Category multiplier
    cat_mult = CATEGORY_MULTIPLIER.get(category_label, 1.0)

    # Step 3: Company tier
    comp_lower = str(company or "").lower()
    comp_mult = 1.0
    for name, mult in COMPANY_TIER.items():
        if name in comp_lower:
            comp_mult = mult
            break

    # Step 4: TTPS premium (international-hiring companies tend to pay more)
    ttps_mult = 1.05 if ttps and comp_mult == 1.0 else 1.0

    # Step 5: Skill premiums from description
    desc_lower = str(description or "").lower()
    skill_bonus = 0.0
    for skill, bonus in PREMIUM_SKILLS.items():
        if skill in desc_lower:
            skill_bonus += bonus
    skill_mult = 1.0 + min(skill_bonus, 0.15)  # Cap at +15%

    # Combine
    estimate = int(base * cat_mult * comp_mult * ttps_mult * skill_mult)

    # Round to nearest 1,000
    estimate = round(estimate / 1000) * 1000

    # Confidence based on how many signals contributed
    signals = sum([
        seniority != "Mid",       # Seniority detected (not default)
        comp_mult != 1.0,         # Known company tier
        cat_mult != 1.0,          # Category has a multiplier
        skill_bonus > 0,          # Premium skills found
    ])
    if signals >= 3:
        confidence = "High"
    elif signals >= 1:
        confidence = "Medium"
    else:
        confidence = "Low"

    # Basis explanation
    parts = [seniority + "-level"]
    if comp_mult != 1.0:
        parts.append(company.split()[0] + " tier")
    if cat_mult != 1.0:
        parts.append(category_label.split()[0] + " role")
    basis = ", ".join(parts)

    return {"estimate": estimate, "confidence": confidence, "basis": basis}


# Weighted skill points that count as a full skill match (e.g. 4 core skills + 1-2
# strong ones). Above this the score stops climbing.
SKILL_SCORE_FULL = 15

# ==========================================================================
# SCRAPING
# ==========================================================================
async def _safe_scrape(scraper, query, location, pages, name):
    """Run a scraper with error isolation."""
    try:
        return await scraper.scrape(query, location, pages)
    except Exception as e:
        print(f"    {name}: {str(e)[:80]}")
        return []


async def scrape_all(query: str, location: str, pages: int = 2):
    """Run all sources in parallel (JobSpy + JobsDB + Lever + Greenhouse)."""
    jobspy = JobSpyScraper()
    jobsdb = JobsDBScraper()
    lever = LeverScraper()
    greenhouse = GreenhouseScraper()

    results = await asyncio.gather(
        _safe_scrape(jobspy, query, location, pages, "jobspy"),
        _safe_scrape(jobsdb, query, location, pages, "jobsdb"),
        _safe_scrape(lever, query, location, pages, "lever"),
        _safe_scrape(greenhouse, query, location, pages, "greenhouse"),
        return_exceptions=True,
    )

    all_listings = []
    for r in results:
        if isinstance(r, list):
            all_listings.extend(r)
    return all_listings


async def scrape_category(category_slug: str, location: str, pages: int = 2):
    """Scrape all queries for a category and merge results."""
    cat = CATEGORIES[category_slug]
    all_listings = []

    for query in cat["queries"]:
        listings = await scrape_all(query, location, pages)
        all_listings.extend(listings)

    return deduplicate(all_listings)


# ==========================================================================
# CATEGORY RELEVANCE — how well a job title matches the selected category
# ==========================================================================
# Maps category label → title keywords that indicate strong relevance
CATEGORY_TITLE_KEYWORDS = {
    "Data Analyst":           ["data analyst", "reporting analyst", "business analyst"],
    "Business Intelligence":  ["power bi", "bi analyst", "business intelligence", "bi developer", "bi engineer"],
    "Fraud & Risk Analyst":   ["fraud", "risk analyst", "aml", "compliance", "kyc", "financial crime"],
    "Analytics Manager":      ["analytics manager", "head of analytics", "analytics lead", "analytics director"],
    "Web Developer":          ["web developer", "php developer", "wordpress", "frontend developer", "web engineer"],
    "Full Stack Developer":   ["full stack", "fullstack", "javascript developer", "frontend", "backend developer"],
    "SEO & Digital Marketing": ["seo", "digital marketing", "search engine", "content marketing", "sem"],
    "Data Engineer":          ["data engineer", "analytics engineer", "data pipeline", "etl", "data infrastructure"],
    "Product Analyst":        ["product analyst", "growth analyst", "insights analyst", "product data"],
    "Python & Automation":    ["python developer", "python engineer", "automation", "scripting", "python analyst"],
    "AI & Data Science":      ["data scientist", "machine learning", "ai engineer", "ai analyst", "ml engineer", "nlp"],
    "Fintech":                ["fintech", "blockchain", "crypto", "defi", "web3", "trading"],
}


def calc_category_relevance(title: str, description: str, category_label: str) -> int:
    """Score 0-100: how relevant is this job to the selected category."""
    keywords = CATEGORY_TITLE_KEYWORDS.get(category_label, [])
    if not keywords:
        return 0

    title_lower = str(title or "").lower()
    desc_lower = str(description or "").lower()

    # Title match is strongest signal (0-60 pts)
    title_score = 0
    for kw in keywords:
        if kw in title_lower:
            title_score = 60
            break

    # Description match is weaker (0-40 pts)
    desc_hits = sum(1 for kw in keywords if kw in desc_lower)
    desc_score = min(40, desc_hits * 15)

    return min(100, title_score + desc_score)


# ==========================================================================
# BUILD JSON
# ==========================================================================
def build_json(listings, query_label, location):
    """Convert listings to JSON with skill match + TTPS scoring."""
    jobs = []
    all_hard = []
    all_soft = []

    today = today_hk()
    kept = []
    for l in listings:
        posted = clean_posted_date(l.posting_date, today)
        if posted and (today - posted).days > MAX_AGE_DAYS:
            continue
        kept.append((l, posted))
    listings = [l for l, _ in kept]

    sal_values = []
    for l in listings:
        ms = extract_max_salary(l.salary)
        if ms > 0:
            sal_values.append(ms)

    median_sal = int(sorted(sal_values)[len(sal_values) // 2]) if sal_values else 0

    for l, posted in kept:
        mandarin = detect_mandarin(l.description)
        skills = extract_keywords(l.description, True)
        soft = [kw for kw in SOFT_SKILLS if re.search(rf'\b{re.escape(kw)}\b', str(l.description or "").lower())]
        ms = extract_max_salary(l.salary)
        match_score = calc_skill_match(l.title, l.description, skills)
        relevance = calc_category_relevance(l.title, l.description, query_label)
        ttps = detect_ttps_friendly(l.description, l.company)
        # Use work_mode from scraper if available, otherwise detect from description
        work_mode = l.work_mode if l.work_mode else detect_work_mode(l.description, l.job_type)
        seniority = detect_seniority(l.title, l.description)

        all_hard.extend(skills)
        all_soft.extend(soft)

        company = l.company if l.company and l.company != "Unknown" else "Confidential"

        # Salary: real if available, otherwise estimate
        sal_display = l.salary or ""
        sal_est = None
        sal_confidence = ""
        sal_basis = ""
        if not sal_display or "negotiable" in sal_display.lower() or "competitive" in sal_display.lower():
            est = estimate_salary(seniority, company, query_label, l.description, ttps)
            sal_display = f"~HK${est['estimate']:,}"
            sal_est = est["estimate"]
            sal_confidence = est["confidence"]
            sal_basis = est["basis"]

        jobs.append({
            "title": l.title,
            "company": company,
            "location": l.location or location,
            "salary": sal_display,
            "salary_num": ms if ms > 0 else (sal_est or 0),
            "salary_est": sal_est,
            "salary_confidence": sal_confidence,
            "salary_basis": sal_basis,
            "mandarin": mandarin or "",
            "skills": skills if skills else [],
            "type": l.job_type or "",
            "date": posted.isoformat() if posted else "",
            "first_seen": FIRST_SEEN.get(l.url) or today.isoformat(),
            "source": l.source,
            "url": l.url or "",
            "match": match_score,
            "relevance": relevance,
            "ttps": ttps,
            "work_mode": work_mode,
            "seniority": seniority,
        })

    # Salary brackets (HKD monthly) — includes estimates for chart usefulness
    brackets = {
        "<HK$15K": 0, "HK$15-25K": 0, "HK$25-35K": 0,
        "HK$35-50K": 0, "HK$50-80K": 0, "HK$80K+": 0,
    }
    all_sal_for_brackets = [j["salary_num"] for j in jobs if j["salary_num"] > 0]
    for s in all_sal_for_brackets:
        if s < 15000: brackets["<HK$15K"] += 1
        elif s < 25001: brackets["HK$15-25K"] += 1
        elif s < 35001: brackets["HK$25-35K"] += 1
        elif s < 50001: brackets["HK$35-50K"] += 1
        elif s < 80001: brackets["HK$50-80K"] += 1
        else: brackets["HK$80K+"] += 1

    # Median now uses real + estimated
    all_sal_nums = sorted([j["salary_num"] for j in jobs if j["salary_num"] > 0])
    median_sal = all_sal_nums[len(all_sal_nums) // 2] if all_sal_nums else 0
    est_count = sum(1 for j in jobs if j.get("salary_est"))

    hard_counts = Counter(all_hard).most_common(15)
    soft_counts = Counter(all_soft).most_common(5)

    top3_skills = [s for s, _ in hard_counts[:3]]
    for j in jobs:
        if not j["skills"] and top3_skills:
            j["skills"] = top3_skills

    mand_breakdown = Counter(j["mandarin"] for j in jobs if j["mandarin"])
    company_counts = Counter(j["company"] for j in jobs if j["company"] != "Confidential")

    # Sort by category relevance first, then match score as tiebreaker
    jobs.sort(key=lambda j: (j["relevance"], j["match"]), reverse=True)

    return {
        "meta": {
            "query": query_label,
            "location": location,
            "generated": datetime.now().isoformat()[:19],
            "total": len(jobs),
            "sources": list(set(j["source"] for j in jobs)),
        },
        "stats": {
            "total_jobs": len(jobs),
            "total_companies": len(set(j["company"] for j in jobs)),
            "salary_median": median_sal,
            "mandarin_total": sum(1 for j in jobs if j["mandarin"]),
            "with_salary": len(sal_values),
            "with_estimate": est_count,
            "ttps_friendly": sum(1 for j in jobs if j["ttps"]),
            "avg_match": int(sum(j["match"] for j in jobs) / len(jobs)) if jobs else 0,
        },
        "charts": {
            "salary_brackets": brackets,
            "top_skills": [{"skill": k, "count": c} for k, c in hard_counts],
            "soft_skills": [{"skill": k, "count": c} for k, c in soft_counts],
            "mandarin_levels": dict(mand_breakdown),
            "top_companies": [{"name": k, "count": c} for k, c in company_counts.most_common(10)],
        },
        "jobs": jobs,
    }


# ==========================================================================
# CACHING + FILE GENERATION
# ==========================================================================
CACHE_MAX_AGE_HOURS = 20


def is_cache_fresh(filepath: str) -> bool:
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        generated = data.get("meta", {}).get("generated", "")
        if not generated:
            return False
        gen_time = datetime.fromisoformat(generated)
        age_hours = (datetime.now() - gen_time).total_seconds() / 3600
        return age_hours < CACHE_MAX_AGE_HOURS
    except Exception:
        return False


async def generate_single(query: str, location: str, query_label: str,
                          pages: int = 2, force: bool = False, category_slug: str = None):
    """Generate data for a single category+location combo."""
    slug = f"{(category_slug or query).replace(' ', '-')}_{location.replace(' ', '-').lower()}"
    filepath = os.path.join(DATA_DIR, f"{slug}.json")

    if not force and is_cache_fresh(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            age = datetime.now() - datetime.fromisoformat(data["meta"]["generated"])
            hours = int(age.total_seconds() / 3600)
            print(f"  CACHED: {query_label} in {location} ({data['stats']['total_jobs']} jobs, {hours}h old)")
            return filepath
        except Exception:
            pass

    print(f"  Scraping: {query_label} in {location}...")

    # Use multi-query category scrape if we have a category slug
    if category_slug and category_slug in CATEGORIES:
        listings = await scrape_category(category_slug, location, pages)
    else:
        raw_listings = await scrape_all(query, location, pages)
        listings = deduplicate(raw_listings) if raw_listings else []

    if not listings:
        print(f"    No results")
        return None

    data = build_json(listings, query_label, location)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)

    print(f"    {data['stats']['total_jobs']} jobs (avg match: {data['stats']['avg_match']}%, "
          f"TTPS-friendly: {data['stats']['ttps_friendly']}) -> {os.path.basename(filepath)}")

    return filepath


def _update_index(categories_labels, data_entries, scraped_counts):
    """Write the master index.json."""
    index_path = os.path.join(DATA_DIR, "index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({
            "categories": categories_labels,
            "data": data_entries,
            "scraped_counts": scraped_counts,
            # With offset, so the page can show "updated 07:38 HKT" correctly
            "generated": datetime.now(HKT).isoformat(timespec="seconds"),
        }, f, ensure_ascii=False, indent=2)


async def fetch_salary_trends():
    """Salary trends placeholder."""
    trends_path = os.path.join(DATA_DIR, "salary_trends.json")
    if os.path.exists(trends_path):
        try:
            with open(trends_path, "r", encoding="utf-8") as f:
                return json.load(f).get("trends", {})
        except Exception:
            pass

    with open(trends_path, "w", encoding="utf-8") as f:
        json.dump({"trends": {}, "generated": datetime.now().isoformat()[:19]},
                  f, ensure_ascii=True, indent=2)
    print("  Salary trends: will build from scraped data over time")
    return {}


# ==========================================================================
# MAIN GENERATION
# ==========================================================================
CONCURRENCY = 3
LOCATION = "Hong Kong"  # Single location — HK is small, no district split


async def generate_all(pages: int = 5, force: bool = False, only: list = None, **_kwargs):
    # `only` limits the run to a subset of CATEGORIES keys. Running one process per
    # category instead would give each its own JobsDB cache and re-trigger the rate
    # limiting, so subsetting has to happen INSIDE a single process.
    cats = {k: v for k, v in CATEGORIES.items() if not only or k in only}
    FIRST_SEEN.update(load_first_seen())
    print("=" * 60)
    print("HK JOB SCRAPER — TARGETED PROFILE SEARCH")
    print(f"Categories: {len(cats)}" + (f" (subset of {len(CATEGORIES)})" if only else ""))
    print(f"Queries per category: {sum(len(c['queries']) for c in cats.values())} total search terms")
    print(f"Location: {LOCATION}")
    print(f"Cache: {'DISABLED (--force)' if force else f'skip if < {CACHE_MAX_AGE_HOURS}h old'}")
    print(f"Concurrency: {CONCURRENCY} categories at once")
    print("=" * 60)

    os.makedirs(DATA_DIR, exist_ok=True)
    await fetch_salary_trends()

    sem = asyncio.Semaphore(CONCURRENCY)
    index_entries = []

    async def process_category(cat_slug, cat_info):
        async with sem:
            path = await generate_single(
                query=cat_slug.replace("-", " "),
                location=LOCATION,
                query_label=cat_info["label"],
                pages=pages,
                force=force,
                category_slug=cat_slug,
            )
            if path:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        fdata = json.load(f)
                    return {
                        "query": cat_info["label"],
                        "location": LOCATION,
                        "file": os.path.basename(path),
                        "slug": f"{cat_slug}_{LOCATION.replace(' ', '-').lower()}",
                        "total_jobs": fdata.get("stats", {}).get("total_jobs", 0),
                    }
                except Exception:
                    pass
            return None

    results = await asyncio.gather(*[
        process_category(slug, info) for slug, info in cats.items()
    ], return_exceptions=True)

    for r in results:
        if isinstance(r, dict):
            index_entries.append(r)

    scraped_counts = {}
    for entry in index_entries:
        scraped_counts[entry["query"]] = entry["total_jobs"]

    categories_labels = [c["label"] for c in CATEGORIES.values()]
    _update_index(categories_labels, index_entries, scraped_counts)

    total_jobs = sum(e["total_jobs"] for e in index_entries)
    print(f"\nDone: {len(index_entries)} categories, {total_jobs} total jobs")

    # Per-source health. A source that quietly returns nothing used to be invisible:
    # JobsDB contributed 0 jobs for weeks because a rate-limit error was swallowed
    # and read as "no results". Never let a dead source look like a boring one.
    from scrapers.jobsdb import JOBSDB_API_ERRORS

    per_source = {}
    import glob as _glob
    for _f in _glob.glob(os.path.join(DATA_DIR, "*_hong-kong.json")):
        try:
            with open(_f, encoding="utf-8") as fh:
                for j in json.load(fh).get("jobs", []):
                    src = j.get("source") or "unknown"
                    per_source[src] = per_source.get(src, 0) + 1
        except Exception:
            continue
    for _expected in ("jobsdb", "linkedin", "indeed", "google", "lever", "greenhouse"):
        per_source.setdefault(_expected, 0)
    if per_source:
        print("\nPer-source totals:")
        for src, n in sorted(per_source.items(), key=lambda kv: -kv[1]):
            flag = "   <-- ZERO, INVESTIGATE" if n == 0 else ""
            print(f"  {src:<12} {n:>5}{flag}")

    if JOBSDB_API_ERRORS:
        print("\n" + "!" * 70)
        print("CRITICAL: JobsDB API problems this run. 0 results does NOT mean 0 jobs.")
        for msg in JOBSDB_API_ERRORS:
            print(f"  - {msg}")
        print("!" * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", help="Single query (e.g. 'data analyst')")
    parser.add_argument("--location", default="Hong Kong")
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--force", action="store_true", help="Ignore cache, re-scrape everything")
    parser.add_argument("--categories", help="Comma-separated category slugs to run "
                        "(e.g. data-analyst,business-intelligence). Default: all.")
    args = parser.parse_args()

    if args.query:
        asyncio.run(generate_single(args.query, args.location, args.query.title(),
                                    args.pages, force=args.force))
    else:
        only = [c.strip() for c in args.categories.split(",")] if args.categories else None
        if only:
            unknown = [c for c in only if c not in CATEGORIES]
            if unknown:
                parser.error(f"unknown categories: {unknown}. Valid: {list(CATEGORIES)}")
        asyncio.run(generate_all(args.pages, force=args.force, only=only))


if __name__ == "__main__":
    main()
