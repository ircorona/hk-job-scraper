"""Generic Lever ATS scraper — multiple companies, one API pattern.

Lever is used by many tech/finance companies. The postings API is public:
  GET https://api.lever.co/v0/postings/{company_slug}?location=Hong%20Kong&mode=json

No auth, no rate limits, full descriptions.

Companies are configured in LEVER_COMPANIES below.
Add new ones by finding the slug from their careers page URL:
  e.g., https://jobs.lever.co/okx → slug = "okx"
"""

import re
from datetime import datetime
from typing import Optional
from html import unescape

import httpx

from models.job_listing import JobListing
from .base import BaseScraper


# ── Company registry ─────────────────────────────────────────────────────
# slug → display name (as shown on job listings)
# To find a slug: visit the company's careers page → look for jobs.lever.co/{slug}
# Verified March 29, 2026 — only companies with confirmed Lever presence
# OKX, Bybit, Agoda etc. are on Greenhouse instead (see greenhouse.py)
LEVER_COMPANIES = {
    "binance":        "Binance",          # 138 HK jobs
    "animocabrands":  "Animoca Brands",   # 1 HK job
    "lalamove":       "Lalamove",         # 51 HK on 2026-10-09, labelled "Hong Kong SAR"
    "crypto":         "Crypto.com",       # 24 HK on 2026-10-09
}


def is_hong_kong(job: dict) -> bool:
    """True if any of the posting's locations names Hong Kong ("Hong Kong", "Hong Kong SAR", ...)."""
    cats = job.get("categories") or {}
    locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
    return any("hong kong" in loc.lower() for loc in locs)


class LeverScraper(BaseScraper):
    SOURCE = "lever"
    API_BASE = "https://api.lever.co/v0/postings"

    def __init__(self, companies: dict | None = None):
        super().__init__()
        self.companies = companies or LEVER_COMPANIES

    async def scrape(self, query: str, location: str, max_pages: Optional[int] = None) -> list[JobListing]:
        """Fetch HK jobs from all configured Lever companies."""
        print(f"  [Lever] Scanning {len(self.companies)} companies for Hong Kong jobs...")

        all_listings = []
        async with httpx.AsyncClient(timeout=20) as client:
            for slug, display_name in self.companies.items():
                listings = await self._fetch_company(client, slug, display_name, query)
                all_listings.extend(listings)

        hit_count = len(set(l.company for l in all_listings))
        print(f"  [Lever] Done: {len(all_listings)} jobs from {hit_count} companies")
        return all_listings

    async def _fetch_company(self, client: httpx.AsyncClient, slug: str, display_name: str, query: str) -> list[JobListing]:
        """Fetch all HK postings for one company."""
        url = f"{self.API_BASE}/{slug}"
        # No server-side location filter: Lever matches it exactly, and Lalamove labels
        # HK jobs "Hong Kong SAR", so ?location=Hong Kong returned 0 of its 51 HK ads.
        params = {"mode": "json"}

        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                return []  # Company slug doesn't exist or no careers page
            if resp.status_code != 200:
                return []

            jobs_data = [j for j in resp.json() if is_hong_kong(j)]
            if not jobs_data:
                return []

        except Exception:
            return []

        listings = []
        query_lower = query.lower()
        query_terms = [t for t in query_lower.split() if len(t) > 2]
        for job in jobs_data:
            listing = self._parse_job(job, display_name)
            if listing:
                title_lower = listing.title.lower()
                desc_lower = listing.description.lower()
                # Match: full phrase in title/desc, OR all terms in title
                if not query_lower \
                   or query_lower in title_lower \
                   or query_lower in desc_lower \
                   or (query_terms and all(t in title_lower for t in query_terms)):
                    listings.append(listing)

        if listings:
            print(f"    {display_name}: {len(listings)} matching jobs (of {len(jobs_data)} total HK)")

        return listings

    def _parse_job(self, job: dict, company_name: str) -> Optional[JobListing]:
        """Parse a Lever API job posting into JobListing."""
        title = job.get("text", "").strip()
        if not title:
            return None

        # Location
        categories = job.get("categories", {})
        location = categories.get("location", "")
        all_locations = categories.get("allLocations", [])
        if not location and all_locations:
            location = ", ".join(all_locations[:3])

        # Work mode from workplaceType (remote/hybrid/onsite)
        work_mode = job.get("workplaceType", "")

        # Job type from commitment (Full-time/Part-time/Internship)
        commitment = categories.get("commitment", "")
        job_type = None
        if commitment:
            cl = commitment.lower()
            if "full-time" in cl or "full time" in cl:
                job_type = "full-time"
            elif "part-time" in cl or "part time" in cl:
                job_type = "part-time"
            elif "intern" in cl or "accelerator" in cl:
                job_type = "internship"
            elif "contract" in cl:
                job_type = "contract"

        # Description: plain text + structured lists + additional info
        desc = job.get("descriptionPlain", "")
        for section in job.get("lists", []):
            section_text = section.get("text", "")
            content = section.get("content", "")
            if content:
                clean = _strip_html(content)
                desc += f"\n\n{section_text}\n{clean}"

        additional = job.get("additionalPlain", "")
        if additional:
            desc += f"\n\n{additional}"

        desc = desc.strip()[:3000]

        # Salary (rarely populated but worth checking)
        salary = None
        sal_range = job.get("salaryRange")
        if sal_range and sal_range.get("max"):
            currency = sal_range.get("currency", "HKD")
            sal_min = sal_range.get("min", 0)
            sal_max = sal_range.get("max", 0)
            if sal_max > 0:
                salary = f"{currency} {sal_min:,} - {sal_max:,}"

        # Date
        posting_date = None
        created = job.get("createdAt")
        if created:
            try:
                posting_date = datetime.fromtimestamp(created / 1000).date()
            except Exception:
                pass

        # URL
        url = job.get("hostedUrl", "") or job.get("applyUrl", "")

        # Team metadata (useful for skill matching)
        team = categories.get("team", "")
        if team and team not in desc:
            desc = f"Team: {team}\n{desc}"

        return JobListing(
            title=title,
            company=company_name,
            location=location,
            salary=salary,
            description=desc,
            url=url,
            posting_date=posting_date,
            job_type=job_type,
            work_mode=work_mode,
            source=self.SOURCE,
        )


def _strip_html(html: str) -> str:
    """Strip HTML tags from Lever list content."""
    if not html:
        return ""
    text = re.sub(r'<li>', '• ', html)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    return text.strip()
