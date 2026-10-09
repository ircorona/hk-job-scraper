"""Lever Hong Kong location filter.

The scraper used to pass ?location=Hong Kong to the Lever API, which matches the label
exactly. Lalamove labels its HK jobs "Hong Kong SAR", so all 51 of its HK ads were
invisible (found 2026-10-09 when LinkedIn showed Lalamove roles the dashboard lacked).
"""
import pytest

from scrapers.lever import is_hong_kong


@pytest.mark.parametrize("cats", [
    {"location": "Hong Kong"},
    {"location": "Hong Kong SAR"},
    {"location": "Kowloon Bay, Hong Kong"},
    {"location": "", "allLocations": ["Singapore", "Hong Kong SAR"]},
])
def test_hong_kong_labels_match(cats):
    assert is_hong_kong({"categories": cats})


@pytest.mark.parametrize("job", [
    {"categories": {"location": "Singapore"}},
    {"categories": {"location": "Shenzhen", "allLocations": ["Shenzhen", "Taipei"]}},
    {"categories": {}},
    {},
])
def test_other_locations_rejected(job):
    assert not is_hong_kong(job)
