"""Guardrails so JobsDB can never silently contribute zero jobs again.

Background (Aug 2026): JobsDB returned nothing for weeks and nobody noticed. Two
causes, both silent:
  1. `_fetch_job` collapsed EVERY failure into None, so "the API is blocking us"
     was indistinguishable from "no job at this ID". ~9,600 refusals per run read
     as 9,600 boring empty results.
  2. The old ID scanner rescanned thousands of IDs once per query (~30x/run),
     which triggered the rate limiting. It was replaced in Oct 2026 by keyword
     search plus a per-run, budgeted cache of full-ad fetches.

These tests lock in both fixes. They use a fake transport - no network.
"""
import asyncio

import pytest

from scrapers import jobsdb as jobsdb_mod
from scrapers.jobsdb import JobsDBScraper


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    """Stands in for httpx.AsyncClient, recording how many calls were made."""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = 0

    async def post(self, url, json=None):
        self.calls += 1
        return FakeResponse(self.payload, self.status_code)


RATE_LIMITED_BODY = {
    "errors": [{
        "message": "Too many requests.",
        "extensions": {"code": "RATE_LIMITED"},
    }],
    "data": {"jobDetails": None},
}

LIVE_JOB_BODY = {
    "data": {"jobDetails": {"job": {
        "id": "93430080",
        "title": "Data Analyst",
        "abstract": "",
        "content": "",
        "sourceZone": "asia-1",
        "advertiser": {"id": "1", "name": "Example Co"},
        "salary": {"label": "HK$40,000 - 50,000"},
        "location": {"label": "Hong Kong"},
        "listedAt": {"dateTimeUtc": "2026-08-01T00:00:00Z", "shortAbsoluteLabel": "1 Aug"},
        "workTypes": {"label": "Full time"},
    }}}
}


@pytest.fixture(autouse=True)
def _clear_module_state():
    """Reset the process-wide caches between tests."""
    def reset():
        jobsdb_mod._SEARCH_CACHE.clear()
        jobsdb_mod._DETAIL_CACHE.clear()
        jobsdb_mod._detail_sem = None
        jobsdb_mod._detail_fetches = 0
        jobsdb_mod._rate_limited_streak = 0
        jobsdb_mod.JOBSDB_API_ERRORS.clear()
    reset()
    yield
    reset()


def test_rate_limited_is_not_reported_as_empty():
    """A RATE_LIMITED GraphQL error must be distinguishable from 'no job here'.

    This is THE regression: if this returns a plain None again, a blocked run looks
    identical to an empty one and the source dies silently.
    """
    scraper = JobsDBScraper()
    client = FakeClient(RATE_LIMITED_BODY)
    result = asyncio.run(scraper._fetch_job(client, "93430080"))
    assert result is not None, "rate-limited response collapsed back into None"
    assert result.get("_rate_limited") is True


def test_http_429_is_flagged_as_rate_limited():
    scraper = JobsDBScraper()
    client = FakeClient({}, status_code=429)
    result = asyncio.run(scraper._fetch_job(client, "93430080"))
    assert result is not None
    assert result.get("_rate_limited") is True


def test_missing_job_still_returns_none():
    """A genuinely empty ID must NOT be mistaken for a rate limit."""
    scraper = JobsDBScraper()
    client = FakeClient({"data": {"jobDetails": None}})
    result = asyncio.run(scraper._fetch_job(client, "1"))
    assert result is None or not result.get("_rate_limited")


def test_live_job_parses_through():
    scraper = JobsDBScraper()
    client = FakeClient(LIVE_JOB_BODY)
    result = asyncio.run(scraper._fetch_job(client, "93430080"))
    assert result and result.get("job", {}).get("sourceZone") == "asia-1"


def test_search_result_is_cached_per_query():
    """The same query must not hit the API twice in one run."""
    scraper = JobsDBScraper()
    jobsdb_mod._SEARCH_CACHE["data analyst"] = ["sentinel"]
    assert asyncio.run(scraper.scrape("Data Analyst", "Hong Kong")) == ["sentinel"]


def test_listing_date_is_hong_kong_date():
    """23:30 UTC on the 6th is already the 7th in Hong Kong."""
    assert jobsdb_mod.parse_listing_date("2026-10-06T23:30:00Z").isoformat() == "2026-10-07"
    assert jobsdb_mod.parse_listing_date("garbage") is None
    assert jobsdb_mod.parse_listing_date(None) is None


SUMMARY = {
    "id": 95121569,
    "title": "Business Analyst (Custody)",
    "advertiser": {"id": "1", "description": "ADECCO"},
    "locations": [{"label": "Central, Central and Western District"}],
    "listingDate": "2026-10-07T11:26:52Z",
    "teaser": "Support custody operations reporting.",
    "bulletPoints": ["Hybrid", "SQL"],
    "salaryLabel": "",
    "workTypes": ["Contract/Temp"],
    "workArrangements": {"data": [{"id": "3", "label": {"text": "Hybrid"}}]},
}


def test_summary_without_details_falls_back_to_teaser():
    """Past the detail budget we still get a usable, dated listing."""
    listing = JobsDBScraper()._build_listing(SUMMARY, None)
    assert listing.url == "https://hk.jobsdb.com/job/95121569"
    assert listing.company == "ADECCO"
    assert listing.posting_date.isoformat() == "2026-10-07"
    assert listing.work_mode == "Hybrid"
    assert "custody operations" in listing.description and "SQL" in listing.description
    assert listing.salary is None


def test_full_ad_text_wins_over_teaser():
    details = {"content": "<p>Must be fluent in Cantonese.</p><ul><li>Power BI</li></ul>"}
    listing = JobsDBScraper()._build_listing(SUMMARY, details)
    assert "Cantonese" in listing.description and "Power BI" in listing.description
    assert "<" not in listing.description


def test_query_words_must_all_appear():
    """Search relevance is loose; a PMO manager is not a data analyst hit."""
    m = JobsDBScraper._matches_query
    assert m("data analyst", "Junior Data Analyst", "")
    assert m("power bi", "Reporting Analyst", "build dashboards in Power BI")
    assert not m("data analyst", "Senior PMO Manager", "manage the data of projects")


def test_api_errors_are_collected_for_reporting():
    """The module must expose a place where failures surface to the run summary."""
    assert hasattr(jobsdb_mod, "JOBSDB_API_ERRORS")
    assert isinstance(jobsdb_mod.JOBSDB_API_ERRORS, list)
