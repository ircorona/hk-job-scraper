"""JobsDB Hong Kong scraper - public search API + GraphQL job details.

Discovery: hk.jobsdb.com/api/jobsearch/v5/search (the JSON API behind the site's own
search page). It takes real keywords, a `dateRange` in days and a sort mode, and
returns `listingDate` per job, so every row has a trustworthy posted date.

Enrichment: hk.jobsdb.com/graphql `jobDetails(id)` returns the full ad text. We need
the full text, not the 1-line teaser, because the Chinese-language requirement and
most skills only appear in the body of the ad.

History: until Oct 2026 this scraper scanned sequential job IDs downward from a
hardcoded ceiling of 90,000,000. Live IDs passed that in January 2026, so for eight
months CI collected ~100 January ads and nothing newer. Keyword search has no ID
range to go stale.
"""

import re
import asyncio
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from html import unescape

import httpx

from models.job_listing import JobListing
from .base import BaseScraper
from utils.helpers import get_user_agent


SEARCH_URL = "https://hk.jobsdb.com/api/jobsearch/v5/search"
HKT = timezone(timedelta(hours=8))

JOBDETAILS_QUERY = """
query JobDetails($id: ID!) {
    jobDetails(id: $id) {
        job {
            id
            title
            abstract
            content
            sourceZone
            advertiser { id name }
            salary { label }
            location { label }
            listedAt { dateTimeUtc shortAbsoluteLabel }
            workTypes { label }
        }
    }
}
"""

# Only ads posted within this many days are collected. Older ads have had most of
# their applicants already; the goal is to apply within days of posting.
DATE_RANGE_DAYS = 14
PAGE_SIZE = 100

# Full-ad fetches per run. ~36 queries x 100 results dedupe to roughly 1,000-1,500
# unique ads. Past the budget we fall back to teaser + bullet points rather than
# hammer the GraphQL endpoint into RATE_LIMITED.
MAX_DETAIL_FETCHES = 2500
DETAIL_CONCURRENCY = 3
DETAIL_DELAY = 0.3

# Process-wide caches. generate_data.py calls scrape() once per query and the same ad
# shows up under many queries; fetch each ad's details once per run.
_SEARCH_CACHE: dict[str, list] = {}
_DETAIL_CACHE: dict[str, Optional[dict]] = {}
_detail_sem: Optional[asyncio.Semaphore] = None
_detail_fetches = 0
_rate_limited_streak = 0
# Populated when the API refuses us, so the run can report it instead of printing
# "0 HK jobs" and looking like a quiet, boring, empty result.
JOBSDB_API_ERRORS: list[str] = []


def _headers() -> dict:
    return {
        "User-Agent": get_user_agent(),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://hk.jobsdb.com",
        "Referer": "https://hk.jobsdb.com/",
    }


class JobsDBScraper(BaseScraper):
    SOURCE = "jobsdb"
    GRAPHQL_URL = "https://hk.jobsdb.com/graphql"
    HK_ZONE = "asia-1"

    async def scrape(self, query: str, location: str, max_pages: Optional[int] = None) -> list[JobListing]:
        """Return HK ads matching `query` posted in the last DATE_RANGE_DAYS days."""
        key = query.lower().strip()
        if key in _SEARCH_CACHE:
            return list(_SEARCH_CACHE[key])

        async with httpx.AsyncClient(headers=_headers(), timeout=20) as client:
            summaries = await self._search(client, query)
            details = await asyncio.gather(
                *[self._details(client, str(s.get("id", ""))) for s in summaries])
            listings = []
            for s, d in zip(summaries, details):
                listing = self._build_listing(s, d)
                if listing and self._matches_query(query, listing.title, listing.description):
                    listings.append(listing)

        print(f"  [JobsDB] '{query}': {len(summaries)} ads in last {DATE_RANGE_DAYS}d "
              f"-> {len(listings)} keep (detail fetches so far: {_detail_fetches})")
        _SEARCH_CACHE[key] = listings
        return list(listings)

    async def _search(self, client, query: str, pages: int = 1) -> list[dict]:
        """Keyword search, best matches first, limited to recent ads."""
        out = []
        for page in range(1, pages + 1):
            params = {
                "siteKey": "HK-Main", "sourcesystem": "houston", "locale": "en-HK",
                "keywords": query, "page": page, "pageSize": PAGE_SIZE,
                "sortmode": "KeywordRelevance", "dateRange": DATE_RANGE_DAYS,
            }
            try:
                resp = await client.get(SEARCH_URL, params=params)
            except Exception as e:
                JOBSDB_API_ERRORS.append(f"search '{query}' failed: {str(e)[:80]}")
                break
            if resp.status_code != 200:
                JOBSDB_API_ERRORS.append(f"search '{query}' HTTP {resp.status_code}")
                break
            data = (resp.json() or {}).get("data") or []
            out.extend(data)
            if len(data) < PAGE_SIZE:
                break
            await asyncio.sleep(0.5)
        return out

    async def _details(self, client, job_id: str) -> Optional[dict]:
        """Full ad via GraphQL, cached per run, within the per-run budget."""
        global _detail_sem, _detail_fetches, _rate_limited_streak
        if not job_id:
            return None
        if job_id in _DETAIL_CACHE:
            return _DETAIL_CACHE[job_id]
        if _detail_fetches >= MAX_DETAIL_FETCHES or _rate_limited_streak >= 5:
            return None
        if _detail_sem is None:
            _detail_sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async with _detail_sem:
            _detail_fetches += 1
            result = await self._fetch_job(client, job_id)
            if result and result.get("_rate_limited"):
                _rate_limited_streak += 1
                if _rate_limited_streak == 5:
                    JOBSDB_API_ERRORS.append(
                        f"job details RATE_LIMITED 5x in a row after {_detail_fetches} "
                        f"fetches; remaining ads use the search teaser only")
                await asyncio.sleep(min(2 ** _rate_limited_streak, 30))
                return None
            _rate_limited_streak = 0
            await asyncio.sleep(DETAIL_DELAY)

        job = (result or {}).get("job")
        _DETAIL_CACHE[job_id] = job
        return job

    async def _fetch_job(self, client: httpx.AsyncClient, job_id: str) -> Optional[dict]:
        """Fetch a single job via GraphQL.

        Returns {"_rate_limited": True} rather than None when the API refuses, so the
        caller can tell "this ID has no job" apart from "the API is blocking us".
        Conflating those is why JobsDB silently contributed zero jobs for weeks.
        """
        try:
            resp = await client.post(self.GRAPHQL_URL, json={
                "query": JOBDETAILS_QUERY,
                "variables": {"id": job_id},
            })
            if resp.status_code == 429:
                return {"_rate_limited": True}
            if resp.status_code != 200:
                return None
            data = resp.json()
            for err in (data.get("errors") or []):
                code = (err.get("extensions") or {}).get("code", "")
                if code == "RATE_LIMITED" or "too many requests" in err.get("message", "").lower():
                    return {"_rate_limited": True}
            return data.get("data", {}).get("jobDetails")
        except Exception:
            return None

    @staticmethod
    def _matches_query(query: str, title: str, description: str) -> bool:
        """Every query word must appear in the ad. Search relevance alone is loose:
        'data analyst' also returns PMO managers and crew rostering supervisors."""
        text = f"{title} {description}".lower()
        return all(re.search(rf"\b{re.escape(w)}\b", text) for w in query.lower().split())

    def _build_listing(self, s: dict, details: Optional[dict]) -> Optional[JobListing]:
        """Merge a search summary with (optional) full details."""
        job_id = str(s.get("id") or "")
        title = re.sub(r"<[^>]+>", "", s.get("title") or "").strip(" :|-")
        if not job_id or not title:
            return None

        company = ((s.get("advertiser") or {}).get("description")
                   or s.get("companyName") or "Unknown")
        locs = s.get("locations") or []
        location = (locs[0] or {}).get("label", "") if locs else ""

        if details and details.get("content"):
            description = self._strip_html(details["content"])
        else:
            parts = [s.get("teaser") or ""] + list(s.get("bulletPoints") or [])
            description = "\n".join(p for p in parts if p)

        arrangements = ((s.get("workArrangements") or {}).get("data") or [])
        work_mode = ((arrangements[0] or {}).get("label") or {}).get("text") if arrangements else None

        work_types = s.get("workTypes") or []
        job_type = work_types[0] if work_types else None

        return JobListing(
            title=title,
            company=company,
            location=location,
            salary=(s.get("salaryLabel") or None),
            description=description,
            url=f"https://hk.jobsdb.com/job/{job_id}",
            posting_date=parse_listing_date(s.get("listingDate")),
            job_type=job_type,
            work_mode=work_mode,
            source=self.SOURCE,
        )

    @staticmethod
    def _strip_html(html: str) -> str:
        if not html:
            return ""
        text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", html)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()[:6000]


def parse_listing_date(value: Optional[str]) -> Optional[date]:
    """'2026-10-07T13:21:27Z' -> date in HK time. None for missing or garbage."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # HKT = UTC+8, no DST. An ad posted 23:30 UTC on the 6th is the 7th in HK.
    return dt.astimezone(HKT).date()
