"""Freshness guardrails: posted dates must be honest and old ads must not survive.

Background (Oct 2026): the live site carried JobsDB ads from January and Lever reqs
from 2022, and the UI had no way to tell. A wrong date is worse than no date: it
makes a dead ad look appliable.
"""
from datetime import date

import generate_data as gd
from models.job_listing import JobListing

TODAY = date(2026, 10, 7)


def test_placeholder_and_future_dates_become_unknown():
    assert gd.clean_posted_date("0001-01-01", TODAY) is None
    assert gd.clean_posted_date("2026-12-25", TODAY) is None
    assert gd.clean_posted_date("not a date", TODAY) is None
    assert gd.clean_posted_date("", TODAY) is None


def test_real_dates_pass_through():
    assert gd.clean_posted_date("2026-10-06", TODAY) == date(2026, 10, 6)
    assert gd.clean_posted_date(date(2026, 9, 1), TODAY) == date(2026, 9, 1)
    # one day ahead is timezone skew (UTC source vs HK clock), not junk
    assert gd.clean_posted_date("2026-10-08", TODAY) == date(2026, 10, 8)


def _listing(url, posted):
    return JobListing(title="Data Analyst", company="Example Co", url=url,
                      posting_date=posted, source="test", description="SQL Power BI")


def test_old_ads_are_dropped_and_undated_kept(monkeypatch):
    monkeypatch.setattr(gd, "today_hk", lambda: TODAY)
    monkeypatch.setattr(gd, "FIRST_SEEN", {})
    data = gd.build_json([
        _listing("https://x/fresh", date(2026, 10, 5)),
        _listing("https://x/stale", date(2022, 9, 9)),
        _listing("https://x/undated", None),
    ], "Data Analyst", "Hong Kong")
    urls = {j["url"] for j in data["jobs"]}
    assert urls == {"https://x/fresh", "https://x/undated"}
    assert data["stats"]["total_jobs"] == 2


def test_first_seen_is_carried_over(monkeypatch):
    monkeypatch.setattr(gd, "today_hk", lambda: TODAY)
    monkeypatch.setattr(gd, "FIRST_SEEN", {"https://x/old": "2026-10-01"})
    data = gd.build_json([
        _listing("https://x/old", date(2026, 9, 30)),
        _listing("https://x/new", date(2026, 10, 7)),
    ], "Data Analyst", "Hong Kong")
    seen = {j["url"]: j["first_seen"] for j in data["jobs"]}
    assert seen == {"https://x/old": "2026-10-01", "https://x/new": "2026-10-07"}
